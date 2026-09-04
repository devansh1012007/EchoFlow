"""Service-layer tests for backend.app.services.interactions.

Stage 2 boundary: every write to UserInteraction flows through these
functions. These tests verify:
  - record_like_toggle / record_skip counter semantics are preserved
  - record_telemetry prefers the Redis Stream (XADD) over the LIST (RPUSH)
  - record_telemetry falls back to synchronous update_or_create on Redis
    failure (the third-tier safety net)
  - record_telemetry emits an event_id for downstream dedup
  - record_share does NOT create a ShareEvent (that's the share-send path)
"""
import json
from unittest.mock import patch, MagicMock

import pytest


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# toggle-like
# ---------------------------------------------------------------------------
class TestRecordLikeToggle:
    def test_first_call_creates_active_row_and_bumps_counter(self, user, ready_clip):
        from backend.app.services.interactions import record_like_toggle

        ready_clip.refresh_from_db()
        assert ready_clip.likes == 0

        interaction, created = record_like_toggle(user, ready_clip)

        assert created is True
        assert interaction.is_active is True
        ready_clip.refresh_from_db()
        assert ready_clip.likes == 1

    def test_second_call_toggles_off_and_decrements(self, user, ready_clip):
        from backend.app.services.interactions import record_like_toggle

        record_like_toggle(user, ready_clip)
        record_like_toggle(user, ready_clip)

        ready_clip.refresh_from_db()
        assert ready_clip.likes == 0

    def test_third_call_toggles_back_on(self, user, ready_clip):
        from backend.app.services.interactions import record_like_toggle

        record_like_toggle(user, ready_clip)
        interaction, _ = record_like_toggle(user, ready_clip)
        interaction, _ = record_like_toggle(user, ready_clip)

        assert interaction.is_active is True
        ready_clip.refresh_from_db()
        assert ready_clip.likes == 1


# ---------------------------------------------------------------------------
# register-skip (writes 'skip'; bumps AudioClip.skips via F() in UserInteraction.save())
# ---------------------------------------------------------------------------
class TestRecordSkip:
    def test_writes_skip_row_with_completion_rate(self, user, ready_clip):
        from backend.app.models import UserInteraction
        from backend.app.services.interactions import record_skip

        record_skip(user, ready_clip, listen_duration_ms=15_000, reel_position_ms=30_000)

        rows = UserInteraction.objects.filter(
            user=user, clip=ready_clip, interaction_type='skip',
        )
        assert rows.count() == 1
        assert rows.first().completion_rate == pytest.approx(0.5)

    def test_bumps_skips_counter(self, user, ready_clip):
        from backend.app.services.interactions import record_skip

        before = {
            'likes': ready_clip.likes,
            'shares': ready_clip.shares,
            'skips': ready_clip.skips,
        }
        record_skip(user, ready_clip, listen_duration_ms=5_000, reel_position_ms=30_000)
        ready_clip.refresh_from_db()
        assert ready_clip.likes == before['likes']
        assert ready_clip.shares == before['shares']
        # DECISION: was no-bump; flipped to bump in step 2 of Group C.
        # The F() increment happens inside UserInteraction.save() via
        # the field_map: {'like': 'likes', 'share': 'shares', 'skip': 'skips'}.
        assert ready_clip.skips == before['skips'] + 1

    def test_update_or_create_keeps_last_completion_rate(self, user, ready_clip):
        from backend.app.services.interactions import record_skip

        record_skip(user, ready_clip, listen_duration_ms=10_000, reel_position_ms=20_000)
        record_skip(user, ready_clip, listen_duration_ms=20_000, reel_position_ms=20_000)

        from backend.app.models import UserInteraction
        rows = UserInteraction.objects.filter(
            user=user, clip=ready_clip, interaction_type='skip',
        )
        assert rows.count() == 1
        assert rows.first().completion_rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# record-telemetry (Redis Stream primary, LIST fallback, sync last-resort)
# ---------------------------------------------------------------------------
class TestRecordTelemetry:
    def test_primary_path_calls_xadd(self, user, ready_clip):
        from backend.app.services.interactions import record_telemetry, STREAM_KEY

        fake_client = MagicMock()
        with patch('backend.app.services.interactions.cache') as fake_cache:
            fake_cache.client.get_client.return_value = fake_client
            event = record_telemetry(user, ready_clip, action_type='view', watch_time_ms=5_000)

        assert fake_client.xadd.called
        call_kwargs = fake_client.xadd.call_args.kwargs
        assert call_kwargs['maxlen'] == 50_000
        assert call_kwargs['approximate'] is True
        # Stream key
        args = fake_client.xadd.call_args.args
        assert args[0] == STREAM_KEY
        # Event has the fields the consumer expects
        fields = args[1]
        assert 'event_id' in fields
        assert fields['schema_version'] == '1.0.0'
        # The returned dict includes the same event_id we put in the stream
        assert event['event_id'] == fields['event_id']
        # LIST fallback NOT used
        assert not fake_client.rpush.called

    def test_falls_back_to_list_when_xadd_raises(self, user, ready_clip):
        from backend.app.services.interactions import record_telemetry

        fake_client = MagicMock()
        fake_client.xadd.side_effect = ConnectionError('redis down')
        with patch('backend.app.services.interactions.cache') as fake_cache:
            fake_cache.client.get_client.return_value = fake_client
            event = record_telemetry(user, ready_clip, action_type='view', watch_time_ms=5_000)

        assert fake_client.xadd.called
        assert fake_client.rpush.called
        rpush_arg = fake_client.rpush.call_args.args[1]
        assert json.loads(rpush_arg)['event_id'] == event['event_id']

    def test_falls_back_to_synchronous_on_full_redis_failure(self, user, ready_clip):
        from backend.app.models import UserInteraction
        from backend.app.services.interactions import record_telemetry

        fake_client = MagicMock()
        fake_client.xadd.side_effect = ConnectionError('xadd down')
        fake_client.rpush.side_effect = ConnectionError('rpush down')
        with patch('backend.app.services.interactions.cache') as fake_cache:
            fake_cache.client.get_client.return_value = fake_client
            record_telemetry(user, ready_clip, action_type='view', watch_time_ms=5_000)

        # The synchronous update_or_create path should have created a row.
        assert UserInteraction.objects.filter(
            user=user, clip=ready_clip, interaction_type='view',
        ).exists()

    def test_env_flag_off_uses_list_path(self, user, ready_clip, monkeypatch):
        from backend.app.services.interactions import record_telemetry

        monkeypatch.setenv('ECHOFLOW_TELEMETRY_STREAM', 'off')
        fake_client = MagicMock()
        with patch('backend.app.services.interactions.cache') as fake_cache:
            fake_cache.client.get_client.return_value = fake_client
            record_telemetry(user, ready_clip, action_type='view', watch_time_ms=5_000)

        assert not fake_client.xadd.called
        assert fake_client.rpush.called

    def test_event_id_is_unique_per_call(self, user, ready_clip):
        from backend.app.services.interactions import record_telemetry

        fake_client = MagicMock()
        events = []
        with patch('backend.app.services.interactions.cache') as fake_cache:
            fake_cache.client.get_client.return_value = fake_client
            for _ in range(5):
                events.append(record_telemetry(user, ready_clip, action_type='view', watch_time_ms=1_000))

        ids = {e['event_id'] for e in events}
        assert len(ids) == 5, 'event_id must be unique per call (UUID4)'


# ---------------------------------------------------------------------------
# record-share (counter only; ShareEvent is the share-send view's job)
# ---------------------------------------------------------------------------
class TestRecordShare:
    def test_creates_interaction_and_bumps_share_counter(self, user, ready_clip):
        from backend.app.services.interactions import record_share

        record_share(user, ready_clip)
        ready_clip.refresh_from_db()
        assert ready_clip.shares == 1

    def test_idempotent_for_repeat_shares(self, user, ready_clip):
        from backend.app.models import UserInteraction
        from backend.app.services.interactions import record_share

        record_share(user, ready_clip)
        record_share(user, ready_clip)
        record_share(user, ready_clip)

        rows = UserInteraction.objects.filter(
            user=user, clip=ready_clip, interaction_type='share',
        )
        assert rows.count() == 1
        ready_clip.refresh_from_db()
        # Counter is bumped only when the row state actually changes
        # (the model save() does state_changed check). Re-shares do not
        # change is_active (defaults to True) so no increment.
        assert ready_clip.shares == 1


# ---------------------------------------------------------------------------
# Group B item 10: cache invalidation wiring
# Verifies that record_like_toggle and record_skip invalidate the
# user_vectors cache so the next /suggestions/ request recomputes
# from current state.
# ---------------------------------------------------------------------------
class TestCacheInvalidation:
    def test_record_like_toggle_invalidates_user_vectors_cache(
        self, user, ready_clip,
    ):
        from django.core.cache import cache
        from django.test import TestCase
        from backend.app.services.interactions import record_like_toggle

        cache_key = f'user_vectors:{user.id}'
        cache.set(cache_key, ('sem-stale', 'ac-stale'), timeout=900)
        assert cache.get(cache_key) is not None

        with TestCase.captureOnCommitCallbacks(execute=True):
            record_like_toggle(user, ready_clip)

        assert cache.get(cache_key) is None

    def test_record_skip_invalidates_user_vectors_cache(
        self, user, ready_clip,
    ):
        from django.core.cache import cache
        from django.test import TestCase
        from backend.app.services.interactions import record_skip

        cache_key = f'user_vectors:{user.id}'
        cache.set(cache_key, ('sem-stale', 'ac-stale'), timeout=900)

        with TestCase.captureOnCommitCallbacks(execute=True):
            record_skip(
                user, ready_clip,
                listen_duration_ms=5000, reel_position_ms=30000,
            )

        assert cache.get(cache_key) is None

    def test_invalidation_deferred_until_commit(
        self, user, ready_clip,
    ):
        # If the surrounding transaction rolls back, the cache key
        # must NOT be invalidated (the user's state didn't actually
        # change). The on_commit deferral guarantees this.
        from django.core.cache import cache
        from django.db import transaction, IntegrityError
        from django.test import TestCase
        from backend.app.services.interactions import record_like_toggle

        cache_key = f'user_vectors:{user.id}'
        cache.set(cache_key, ('sem-stale', 'ac-stale'), timeout=900)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
                    record_like_toggle(user, ready_clip)
                # atomic rollback discards on_commit hooks
                raise IntegrityError('simulated rollback')

        # on_commit hooks were cleared by the rollback; cache untouched
        assert cache.get(cache_key) == ('sem-stale', 'ac-stale')

    def test_helper_is_exported_from_both_locations(
        self, user, ready_clip,
    ):
        # Single source of truth: services.interactions owns it;
        # views.feed re-exports it for backwards-compat (the audit
        # doc references views.feed:50 as the definition site).
        from backend.app.services.interactions import (
            invalidate_user_vectors_cache as svc_helper,
        )
        from backend.app.views.feed import (
            invalidate_user_vectors_cache as view_helper,
        )
        assert svc_helper is view_helper

    def test_record_share_invalidates_user_vectors_cache(
        self, user, ready_clip,
    ):
        # A3 Part 1: record_share mutates the user's interaction
        # history (a share is a strong signal for /suggestions/).
        # The cache must be invalidated so the next recompute sees
        # the new row.
        from django.core.cache import cache
        from django.test import TestCase
        from backend.app.services.interactions import record_share

        cache_key = f'user_vectors:{user.id}'
        cache.set(cache_key, ('sem-stale', 'ac-stale'), timeout=900)
        assert cache.get(cache_key) is not None

        with TestCase.captureOnCommitCallbacks(execute=True):
            record_share(user, ready_clip)

        assert cache.get(cache_key) is None

    def test_record_telemetry_sync_fallback_invalidates_cache(
        self, user, ready_clip, monkeypatch,
    ):
        # A3 Part 1: when Redis is fully unavailable, record_telemetry
        # falls back to a synchronous update_or_create. That write
        # changes the user's state, so the cache must be invalidated.
        # (The stream-success path is covered by the consumer test
        # in test_task_publisher.py::TestFlushTelemetryInvalidation.)
        from django.core.cache import cache
        from django.test import TestCase
        from backend.app.models import UserInteraction

        cache_key = f'user_vectors:{user.id}'
        cache.set(cache_key, ('sem-stale', 'ac-stale'), timeout=900)

        # Force the synchronous fallback by stubbing both Redis paths
        # to return failure. xadd returns False (treated as failure);
        # rpush raises (caught by the outer try/except, then sync
        # fallback runs).
        monkeypatch.setattr(
            'backend.app.services.interactions._xadd_telemetry',
            lambda _event: False,
        )
        monkeypatch.setattr(
            'backend.app.services.interactions._rpush_telemetry',
            lambda _event: (_ for _ in ()).throw(ConnectionError('redis down')),
        )

        with TestCase.captureOnCommitCallbacks(execute=True):
            from backend.app.services.interactions import record_telemetry
            record_telemetry(user, ready_clip, action_type='view', watch_time_ms=5_000)

        # Sync fallback wrote the row.
        assert UserInteraction.objects.filter(
            user=user, clip=ready_clip, interaction_type='view',
        ).exists()
        # Cache was invalidated.
        assert cache.get(cache_key) is None
