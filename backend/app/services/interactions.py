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
  * record_telemetry: emits a JSON event to the Redis telemetry queue.
    On Redis failure it falls back to synchronous update_or_create so
    the event is not dropped.
  * record_share: get_or_create the share interaction (bumps shares)
    AND creates a ShareEvent row.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from django.core.cache import cache
from django.db import transaction

from ..models import AudioClip, UserInteraction

logger = logging.getLogger(__name__)


def record_like_toggle(user, clip: AudioClip) -> tuple[UserInteraction, bool]:
    """Toggle the user's like on `clip`. Returns (interaction, created)."""
    interaction, created = UserInteraction.objects.get_or_create(
        user=user,
        clip=clip,
        interaction_type='like',
        defaults={'is_active': True},
    )
    if not created:
        interaction.is_active = not interaction.is_active
        interaction.save()
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

    Primary path: append to the Redis 'telemetry:queue' list. The
    flush_telemetry Beat task drains it every 30s and bulk-inserts
    UserInteraction rows, eliminating the per-request row lock.

    Fallback path: if Redis is unreachable, perform the synchronous
    update_or_create so the event is not lost. In practice this means
    the request takes ~60ms instead of ~5ms; the alternative is a
    dropped event.
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
        cache.client.get_client().rpush('telemetry:queue', json.dumps(event))
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
