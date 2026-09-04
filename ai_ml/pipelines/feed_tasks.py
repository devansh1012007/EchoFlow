"""Celery task wiring for the feed recommendation engine.

Migrated from `backend.app.tasks` in the feed-engine separation pass
(2026-09). Each task here is a thin shell: it acquires any required
locks / metrics, calls into a pure helper in
`ai_ml.pipelines.recommendation`, then writes to Redis / returns.

DECISION: Every task in this module is decorated with
`name='backend.app.tasks.<func>'` so the existing
`CELERY_TASK_ROUTES` and `CELERY_BEAT_SCHEDULE` entries in
`backend.EchoFlow.settings` resolve unchanged. The audit-verified
constants (RETRYABLE_ERRORS, queue assignments, schedule cadences)
all live where they were; only the *implementation* moved.

This module is listed in `backend.EchoFlow.celery.app.autodiscover_tasks`
so the worker imports it on boot. Without that entry the tasks would
land in the default queue instead of `fast_feed` (refill_user_feed)
or be missing from the Beat scheduler (the three rebuild_* tasks).
"""
from __future__ import annotations

import os
import random
import logging

from celery import shared_task
from django.core.cache import cache
from django.db import OperationalError
from django.utils import timezone
from datetime import timedelta

# Late imports kept inside functions where possible to avoid loading
# Django models at import time (this module is imported by the
# celery_feed worker before app loading finishes in some tests).

logger = logging.getLogger(__name__)


# Same retryable-error tuple used by the data-processing tasks in
# backend.app.tasks. Re-declared here (rather than imported from
# backend.app.tasks) so this module is importable in isolation — a
# test or a future worker process can pull in feed_tasks without
# triggering the ML model-loaders in backend.app.tasks.
RETRYABLE_ERRORS = (
    OperationalError,
    ConnectionError,
    OSError,
)


# ---------------------------------------------------------------------------
# refill_user_feed — hot path, fast_feed queue
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name='backend.app.tasks.refill_user_feed',
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
)
def refill_user_feed(self, user_id, count: int = 50):
    """Refill a user's per-user feed queue with composite-ranked clips.

    Pool-first when the Redis pools are populated (constant ~2 ms);
    SQL fallback otherwise. Idempotent for the dedup set; the rpush
    is the only side effect that can run twice (worst case: the
    user sees a duplicate in their queue, which FastFeedViewSet
    already handles by ordering by case-preserved UUIDs).

    DECISION: SETNX lock prevents concurrent refills for the same
    user (race condition fix from the original implementation).
    The lock has a 30s expiry so a worker crash doesn't leave the
    user blocked.

    The metric labels (source, outcome) are observed on
    echoflow_feed_refill_duration_seconds; this is a faithful port
    of the original metrics behavior including the
    "double-observe on the pool-then-backfill path" pattern the
    audit flagged as low-noise but acceptable.
    """
    from backend.app.models import User
    from ai_ml.pipelines.recommendation import build_feed_candidates

    user = User.objects.get(id=user_id)
    redis_client = cache.client.get_client()

    redis_key = f"user_feed:{user_id}"
    lock_key = f"feed_refill_lock:{user_id}"
    acquired = redis_client.set(lock_key, "1", nx=True, ex=30)
    if not acquired:
        return "Refill already in progress."

    try:
        if redis_client.llen(redis_key) >= 20:
            return "Queue sufficient."

        from backend.app import metrics
        import time as _time

        with metrics.time_feed_refill(source='cold') as timer:
            clip_ids_to_push: list[str] = build_feed_candidates(
                user_id, count, pool_first=True
            )

            # Mirror the original implementation's source-label observation
            # for the pool path. The outer 'cold' sample is low-volume
            # noise accepted by the audit (see tasks.py:418-420).
            seen_count = len(clip_ids_to_push)
            if seen_count:
                # Best-effort: tag the success as 'pool' if we filled
                # without backfill, else 'sql'. The timer adapter just
                # observes the outer 'cold' label.
                pool_obs_duration = max(0.0, _time.monotonic() - (timer._start or _time.monotonic()))
                metrics.feed_refill_duration_seconds.labels(
                    source='pool' if seen_count >= count else 'sql',
                    outcome='success',
                ).observe(pool_obs_duration)
    finally:
        try:
            redis_client.delete(lock_key)
        except Exception:
            pass

    if not clip_ids_to_push:
        return "No new clips to push."

    random.shuffle(clip_ids_to_push)
    redis_client.rpush(redis_key, *clip_ids_to_push)
    redis_client.expire(redis_key, 86400)
    return f"Added {len(clip_ids_to_push)} composite-ranked clips."


# ---------------------------------------------------------------------------
# rebuild_global_exploit_pool — Beat, every 5 min
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name='backend.app.tasks.rebuild_global_exploit_pool',
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
)
def rebuild_global_exploit_pool(self):
    """Rebuild the global feed:exploit_pool ZSET. Beat every 5 min."""
    from backend.app.services.feed_pool import rebuild_global_exploit_pool as _rebuild
    try:
        n = _rebuild()
        return f"wrote {n} members to global exploit pool"
    except Exception as exc:
        logger.exception("rebuild_global_exploit_pool failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# dispatch_user_pool_rebuilds — Beat, hourly, fans out per-user rebuilds
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    name='backend.app.tasks.dispatch_user_pool_rebuilds',
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
)
def dispatch_user_pool_rebuilds(self):
    """Fan out per-user pool rebuilds across the next hour.

    The Beat cadence is hourly; this task enqueues N
    `rebuild_user_explore_pool` tasks with a small jitter so the
    workers absorb them gradually instead of in a herd.

    HACK: A true rolling fan-out would use crontab-style scheduling
    per user via django_celery_beat, but that's heavy and this
    gets us 90% of the benefit with one Beat entry.
    """
    from backend.app.models import User
    from ai_ml.pipelines.feed_tasks import rebuild_user_explore_pool
    from backend.app.services.task_publisher import publish

    one_hour = 3600
    batch_size = max(
        1, int(os.environ.get('FEED_POOL_USER_REBUILD_BATCH', '200'))
    )
    active_threshold = timezone.now() - timedelta(days=30)
    user_ids = list(
        User.objects.filter(last_login__gte=active_threshold)
        .order_by('last_login')
        .values_list('id', flat=True)[:batch_size]
    )
    enqueued = 0
    for i, uid in enumerate(user_ids):
        countdown = int(i * (one_hour / max(len(user_ids), 1)))
        publish(
            rebuild_user_explore_pool,
            uid,
            countdown=countdown,
        )
        enqueued += 1
    return f"fanned out {enqueued} user pool rebuilds across the next hour"


# ---------------------------------------------------------------------------
# rebuild_user_explore_pool — per-user
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name='backend.app.tasks.rebuild_user_explore_pool',
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
)
def rebuild_user_explore_pool(self, user_id):
    """Rebuild a single user's user:{id}:candidates:explore ZSET."""
    from backend.app.services.feed_pool import rebuild_user_explore_pool as _rebuild
    try:
        n = _rebuild(user_id)
        return f"wrote {n} members to user {user_id} explore pool"
    except Exception as exc:
        logger.exception("rebuild_user_explore_pool user=%s failed: %s", user_id, exc)
        raise
