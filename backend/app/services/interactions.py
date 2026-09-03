"""Interaction service layer.

Stage 2 of the relational-to-event-driven plan: every write to
`UserInteraction` flows through these functions, never directly through
ORM in a view. Today each function delegates to the same ORM call the
view previously made; tomorrow the F() counter side-effect can be
moved to a Redis INCRBY + batcher without touching any view.

Behavior contract (preserved from pre-refactor views/interactions.py):
  * toggle_like: get_or_create + toggle is_active; bumps AudioClip.likes
    on state change via UserInteraction.save()'s F() side-effect.
  * register_skip: writes an interaction_type='view' row (NOT 'skip'),
    no counter is bumped (view is excluded from the field_map in
    UserInteraction.save()).
  * record_telemetry: emits a JSON event to a Redis Stream (primary)
    with a Redis list as fallback. The stream consumer
    (tasks.flush_telemetry_stream) bulk-inserts UserInteraction rows.
    On Redis failure it falls back to the synchronous update_or_create
    so the event is not dropped.
  * record_share: get_or_create the share interaction (bumps shares)
    AND creates a ShareEvent row.

STREAM DETAILS:
  Stream key:    stream:interaction.events
  Consumer grp:  cg:telemetry-flush
  Approx cap:    MAXLEN ~ 50000 (bounds RAM; messages are short-lived
                 telemetry, replay window only matters for at-least-once)
  Dedup key:     processed_event:{event_id} SETNX EX 86400
  DLQ stream:    stream:interaction.events:dlq
  Fallback list: telemetry:queue  (drained by flush_telemetry_legacy)
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from django.core.cache import cache
from django.db import transaction

from ..models import AudioClip, UserInteraction

logger = logging.getLogger(__name__)


STREAM_KEY = 'stream:interaction.events'
CONSUMER_GROUP = 'cg:telemetry-flush'
STREAM_MAXLEN = 50_000


def _use_stream() -> bool:
    """Env-gated feature flag. Set ECHOFLOW_TELEMETRY_STREAM=off to force LIST path."""
    return os.environ.get('ECHOFLOW_TELEMETRY_STREAM', 'on').lower() not in ('off', '0', 'false')


def _xadd_telemetry(event: dict) -> bool:
    """XADD a telemetry event. Returns True on success, False on any Redis error."""
    from .. import metrics
    try:
        client = cache.client.get_client()
        # DECISION: cache_get_set_duration_seconds times the XADD
        # operation. Result label is 'ok' (success) or 'error'
        # (Redis hiccup). The op label is 'set' because XADD is a
        # write.
        with metrics.time_cache(op='set') as timer:
            client.xadd(
                STREAM_KEY,
                {
                    'event_id': event['event_id'],
                    'schema_version': '1.0.0',
                    'payload': json.dumps(event),
                },
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
        return True
    except Exception as exc:
        logger.warning("telemetry: xadd failed (%s); will fall back to list", exc)
        return False


def _rpush_telemetry(event: dict) -> None:
    """LIST fallback path. Raises on Redis failure so the caller can run the
    synchronous update_or_create last-resort fallback."""
    from .. import metrics
    with metrics.time_cache(op='set') as timer:
        cache.client.get_client().rpush('telemetry:queue', json.dumps(event))


def record_like_toggle(user, clip: AudioClip) -> tuple[UserInteraction, bool]:
    """Toggle the user's like on `clip`. Returns (interaction, created)."""
    # DECISION: Instrumented with the toggle_like histogram. This is
    # the F() counter hot path (UserInteraction.save() fires the
    # update on AudioClip.likes). The metric labels distinguish
    # 'success' from 'race_lost' (F() under contention) — when
    # race_lost rate climbs, the atomic-block fix in the model is
    # being exercised and contention is real.
    from .. import metrics
    with metrics.time_toggle_like() as timer:
        interaction, created = UserInteraction.objects.get_or_create(
            user=user,
            clip=clip,
            interaction_type='like',
            defaults={'is_active': True},
        )
        if not created:
            interaction.is_active = not interaction.is_active
            interaction.save()
        # HACK: We can't easily detect "row-lock contention" from
        # inside this function — Postgres just makes the UPDATE
        # wait. So the race_lost label is rarely observed in
        # practice; it's a hook for future explicit contention
        # tracking if we add a "did I wait on a row lock?" check.
    return interaction, created


def record_skip(
    user,
    clip: AudioClip,
    listen_duration_ms: int,
    reel_position_ms: int,
) -> UserInteraction:
    """Register a skip event. Writes an interaction_type='view' row, not 'skip'.

    Pre-refactor quirk preserved: the endpoint is named register-skip but
    the row stores interaction_type='view'. Views never bump a denormalized
    counter, so this is observability-only.
    """
    expected_duration = reel_position_ms if reel_position_ms > 0 else 60000
    completion_rate = min(listen_duration_ms / expected_duration, 1.0)
    interaction, _ = UserInteraction.objects.update_or_create(
        user=user,
        clip=clip,
        interaction_type='view',
        defaults={
            'completion_rate': completion_rate,
            'is_active': True,
        },
    )
    return interaction


def record_telemetry(
    user,
    clip: AudioClip,
    action_type: str,
    watch_time_ms: int,
) -> dict[str, Any]:
    """Buffer a telemetry event for async flush.

    Path priority:
      1. Redis Stream XADD (env-gated by ECHOFLOW_TELEMETRY_STREAM, default on)
      2. Redis list RPUSH (legacy)
      3. Synchronous update_or_create (last-resort; event must not be lost)

    Every event carries an `event_id` (UUID4) so the consumer can
    deduplicate via SETNX processed_event:{event_id} EX 86400.
    """
    clip_duration = max(clip.duration_ms, 1)
    completion_rate = min(watch_time_ms / clip_duration, 1.0)
    event = {
        'event_id': str(uuid.uuid4()),
        'user_id': str(user.id),
        'clip_id': str(clip.id),
        'action_type': action_type,
        'watch_time_ms': watch_time_ms,
        'completion_rate': completion_rate,
    }
    try:
        if _use_stream():
            if _xadd_telemetry(event):
                return event
        _rpush_telemetry(event)
    except Exception as exc:
        logger.warning(
            "telemetry: redis enqueue failed (%s); falling back to synchronous write",
            exc,
        )
        UserInteraction.objects.update_or_create(
            user=user, clip=clip, interaction_type=action_type,
            defaults={
                'watch_time_ms': watch_time_ms,
                'completion_rate': completion_rate,
                'is_active': True,
            },
        )
    return event


def record_share(user, clip: AudioClip) -> UserInteraction:
    """Log a share interaction. Bumps AudioClip.shares via the F() side-effect.

    Does NOT create a ShareEvent — that is the inbox fan-out, owned by
    the share-send view (callers do both: this function for the counter,
    ShareEvent.objects.create for the inbox).
    """
    interaction, _ = UserInteraction.objects.get_or_create(
        user=user, clip=clip, interaction_type='share',
    )
    return interaction
