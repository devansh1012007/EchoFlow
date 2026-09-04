"""Feed candidate pool — pre-computed Redis sorted sets.

The /feed/ hot path used to run a composite-distance SQL query on
every queue-empty refill. At 10K concurrent users, that's ~83
SQL/sec on the primary. This module replaces that pattern with
pre-computed Redis sorted sets:

  * `feed:exploit_pool`               — global top-N by composite
    score, scored against a global-average user vector. Refreshed
    every 5 minutes.
  * `user:{id}:candidates:explore`    — per-user top-N by composite
    score, scored against the user's blended vector. Refreshed
    hourly, fanned across the hour.

`refill_user_feed` becomes a ZREVRANGEBYSCORE against these sets
instead of a SQL query. Cold-start / Redis-outage falls back to the
on-demand SQL path with a 200ms budget.

Design doc: `docs/EXPLAIN/recommendation/03-feed-pre-computation.md`.

Namespace note: the global pool key was renamed from
`clip:candidates:exploit` to `feed:exploit_pool` so the
`counter_store` keyspace (`clip:<uuid>:<type>` and
`clip:<uuid>:user:<id>:completion_*`) is no longer sharing a
prefix with the feed-pool ZSET. A future migration of the
counter-store drain to SCAN (instead of Lua KEYS) is now safe.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Count, F, FloatField, ExpressionWrapper
from pgvector.django import CosineDistance

from ..models import AudioClip, UserInteraction, User
from ai_ml.pipelines.recommendation import calculate_time_decayed_vectors

logger = logging.getLogger(__name__)


# Redis keys
GLOBAL_POOL_KEY = 'feed:exploit_pool'


def user_pool_key(user_id) -> str:
    return f'user:{user_id}:candidates:explore'


# DECISION: Global average user vector is a runtime-mean of
# long_term_semantic / long_term_acoustic across all users with a
# non-null vector. The Python-side mean avoids shipping 100K vectors
# to Python just to compute the mean. The result is cached for the
# same TTL as the global pool so they re-derive together.
GLOBAL_AVG_USER_KEY = 'global_avg_user_vector'


def _get_redis_client():
    return cache.client.get_client()


def _settings_int(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def _settings_float(name: str, default: float) -> float:
    return float(getattr(settings, name, default))


# ---------------------------------------------------------------------------
# Global pool
# ---------------------------------------------------------------------------


def _compute_global_average_user_vector() -> tuple[Optional[list], Optional[list]]:
    """Mean of long_term_semantic and long_term_acoustic across active users.

    Returns (semantic_vec, acoustic_vec); either may be None if no users
    have a non-null vector yet (cold-start). The mean is computed in
    Python by aggregating per-row vectors; for 100K users this is
    ~10s. Cache the result for the global-pool TTL so we don't
    recompute on every rebuild.
    """
    import numpy as np

    cached = cache.get(GLOBAL_AVG_USER_KEY)
    if cached is not None:
        if cached == 'EMPTY':
            return None, None
        return cached.get('sem'), cached.get('ac')

    sems = []
    acs = []
    # Stream in chunks of 1000 to bound memory.
    qs = User.objects.filter(
        long_term_semantic__isnull=False,
        long_term_acoustic__isnull=False,
    ).values_list('long_term_semantic', 'long_term_acoustic')
    chunk: list = []
    for sem, ac in qs.iterator(chunk_size=1000):
        chunk.append((sem, ac))
        if len(chunk) >= 1000:
            sems.extend(s[0] for s in chunk)
            acs.extend(s[1] for s in chunk)
            chunk = []
    if chunk:
        sems.extend(s[0] for s in chunk)
        acs.extend(s[1] for s in chunk)

    if not sems:
        cache.set(GLOBAL_AVG_USER_KEY, 'EMPTY',
                  timeout=_settings_int('FEED_POOL_GLOBAL_TTL', 300))
        return None, None

    sem_mean = np.mean(np.array(sems), axis=0).tolist()
    ac_mean = np.mean(np.array(acs), axis=0).tolist()
    cache.set(
        GLOBAL_AVG_USER_KEY,
        {'sem': sem_mean, 'ac': ac_mean},
        timeout=_settings_int('FEED_POOL_GLOBAL_TTL', 300),
    )
    return sem_mean, ac_mean


def rebuild_global_exploit_pool() -> int:
    """Rebuild the global `feed:exploit_pool` ZSET.

    Returns the number of members written. Called by Celery Beat
    every 5 minutes.
    """
    top_n = _settings_int('FEED_POOL_GLOBAL_TOP_N', 10_000)
    ttl = _settings_int('FEED_POOL_GLOBAL_TTL', 300)
    chunk_size = _settings_int('FEED_POOL_REBUILD_CHUNK_SIZE', 1000)

    sem_query, ac_query = _compute_global_average_user_vector()

    base = AudioClip.objects.filter(status='ready')
    if sem_query is not None and ac_query is not None:
        ranked = base.annotate(
            sem_dist=CosineDistance('semantic_vector', sem_query),
            ac_dist=CosineDistance('acoustic_vector', ac_query),
            vector_similarity=ExpressionWrapper(
                1.0 - ((F('sem_dist') + F('ac_dist')) / 4.0),
                output_field=FloatField(),
            ),
            composite_score=ExpressionWrapper(
                (F('vector_similarity') * 0.45)
                + (F('avg_completion_rate') * 0.30)
                + (F('engagement_velocity') * 0.25),
                output_field=FloatField(),
            ),
        ).order_by('-composite_score').values_list('id', 'composite_score')[:top_n]
    else:
        # Cold-start catalog — rank by engagement velocity only.
        ranked = base.order_by(
            '-engagement_velocity', '-created_at'
        ).values_list('id', 'engagement_velocity')[:top_n]

    redis_client = _get_redis_client()
    redis_client.delete(GLOBAL_POOL_KEY)

    pipe = redis_client.pipeline(transaction=False)
    written = 0
    for chunk in _chunked(ranked, chunk_size):
        for clip_id, score in chunk:
            pipe.zadd(GLOBAL_POOL_KEY, {str(clip_id): float(score or 0.0)})
            written += 1
        pipe.execute()
    if ttl:
        redis_client.expire(GLOBAL_POOL_KEY, ttl)
    logger.info("rebuild_global_exploit_pool: wrote %d members", written)
    return written


# ---------------------------------------------------------------------------
# Per-user pool
# ---------------------------------------------------------------------------


def rebuild_user_explore_pool(user_id) -> int:
    """Rebuild a single user's `user:{id}:candidates:explore` ZSET.

    Returns the number of members written. Called by the hourly
    `dispatch_user_pool_rebuilds` Beat task, fanned out across the
    hour. Idempotent: callers can re-run without consequences.
    """
    top_n = _settings_int('FEED_POOL_USER_TOP_N', 1_000)
    ttl = _settings_int('FEED_POOL_USER_TTL', 86_400)
    chunk_size = _settings_int('FEED_POOL_REBUILD_CHUNK_SIZE', 1000)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return 0

    sem_query, ac_query = calculate_time_decayed_vectors(user)
    if sem_query is None or ac_query is None:
        # User has no interaction history yet. Skip — they hit the
        # global-only path.
        _get_redis_client().delete(user_pool_key(user_id))
        return 0

    seen_ids = list(
        UserInteraction.objects.filter(user=user).values_list(
            'clip_id', flat=True
        )
    )

    ranked = (
        AudioClip.objects.filter(status='ready')
        .exclude(id__in=seen_ids)
        .annotate(
            sem_dist=CosineDistance('semantic_vector', sem_query),
            ac_dist=CosineDistance('acoustic_vector', ac_query),
            vector_similarity=ExpressionWrapper(
                1.0 - ((F('sem_dist') + F('ac_dist')) / 4.0),
                output_field=FloatField(),
            ),
            composite_score=ExpressionWrapper(
                (F('vector_similarity') * 0.45)
                + (F('avg_completion_rate') * 0.30)
                + (F('engagement_velocity') * 0.25),
                output_field=FloatField(),
            ),
        )
        .order_by('-composite_score')
        .values_list('id', 'composite_score')[:top_n]
    )

    redis_client = _get_redis_client()
    key = user_pool_key(user_id)
    redis_client.delete(key)

    pipe = redis_client.pipeline(transaction=False)
    written = 0
    for chunk in _chunked(ranked, chunk_size):
        for clip_id, score in chunk:
            pipe.zadd(key, {str(clip_id): float(score or 0.0)})
            written += 1
        pipe.execute()
    if ttl:
        redis_client.expire(key, ttl)
    logger.info(
        "rebuild_user_explore_pool: user=%s wrote=%d", user_id, written
    )
    return written


# ---------------------------------------------------------------------------
# Refill helper
# ---------------------------------------------------------------------------


def get_user_candidates(user_id, count: int) -> Optional[list[str]]:
    """Return up to `count` candidate clip UUIDs for a user.

    Reads from the global + per-user pools. Returns None if neither
    pool is populated — the caller (refill_user_feed) should fall
    back to the on-demand SQL path. Returns [] if the pools exist
    but are empty (legitimately empty catalog).
    """
    redis_client = _get_redis_client()
    exploit_count = int(count * 0.8)
    explore_count = count - exploit_count

    out: list[str] = []
    if exploit_count > 0:
        out.extend(
            cid.decode() if isinstance(cid, bytes) else cid
            for cid in redis_client.zrevrangebyscore(
                GLOBAL_POOL_KEY, '+inf', '-inf',
                start=0, num=exploit_count,
            )
        )
    if explore_count > 0:
        key = user_pool_key(user_id)
        if redis_client.exists(key):
            out.extend(
                cid.decode() if isinstance(cid, bytes) else cid
                for cid in redis_client.zrevrangebyscore(
                    key, '+inf', '-inf',
                    start=0, num=explore_count,
                )
            )

    if not out:
        return None
    return out


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _chunked(iterable, size):
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
