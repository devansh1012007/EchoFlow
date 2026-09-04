"""Tests for the Redis counter store and batched flusher (Group B item 9).

The counter_store module is the architectural fix for the viral-
contention problem on AudioClip.likes/shares/skips. Under Phase 1
(dual-write default), the F() in UserInteraction.save() ALSO
writes to Postgres; the counter_store writes to Redis; the flusher
drains Redis to keep it from growing unbounded.

The flusher's two-phase behavior is the key contract:
  - Phase 1 (dual-write ON): drain-and-discard. The Postgres counter
    is already correct from the F().
  - Phase 2 (dual-write OFF): drain-and-apply. The flusher is the
    only path from Redis to Postgres.

These tests cover both phases by toggling the env var.
"""
import os
import uuid
from unittest.mock import patch

import pytest


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# In-memory backend tests
# ---------------------------------------------------------------------------
class TestInMemoryBackend:
    def test_increment_returns_new_value(self):
        from backend.app.services import counter_store
        counter_store._reset_backend_for_tests()
        clip_id = uuid.uuid4()

        v1 = counter_store.increment(clip_id, 'likes', 1)
        v2 = counter_store.increment(clip_id, 'likes', 1)
        v3 = counter_store.increment(clip_id, 'likes', 1)

        assert v1 == 1
        assert v2 == 2
        assert v3 == 3

    def test_increment_separate_clips_have_separate_counters(self):
        from backend.app.services import counter_store
        counter_store._reset_backend_for_tests()
        clip_a = uuid.uuid4()
        clip_b = uuid.uuid4()

        counter_store.increment(clip_a, 'likes', 1)
        counter_store.increment(clip_b, 'likes', 1)
        counter_store.increment(clip_b, 'likes', 1)

        deltas = counter_store.drain()
        assert deltas[str(clip_a)] == {'likes': 1}
        assert deltas[str(clip_b)] == {'likes': 2}

    def test_drain_returns_zero_after_drain(self):
        from backend.app.services import counter_store
        counter_store._reset_backend_for_tests()
        clip_id = uuid.uuid4()
        counter_store.increment(clip_id, 'likes', 5)

        first = counter_store.drain()
        assert first[str(clip_id)] == {'likes': 5}

        # Second drain is empty (atomic read-and-reset)
        second = counter_store.drain()
        assert second == {}

    def test_drain_does_not_lose_concurrent_increment(self):
        # The atomic-drain guarantee: if increment() lands between
        # the read and reset, the increment is preserved (next drain
        # sees it). For the in-memory backend this is enforced by
        # the lock; for the Redis backend by Lua.
        from backend.app.services import counter_store
        counter_store._reset_backend_for_tests()
        clip_id = uuid.uuid4()
        counter_store.increment(clip_id, 'likes', 3)

        first = counter_store.drain()
        counter_store.increment(clip_id, 'likes', 2)  # between drains
        second = counter_store.drain()

        assert first[str(clip_id)] == {'likes': 3}
        assert second[str(clip_id)] == {'likes': 2}

    def test_increment_raises_on_unknown_counter_type(self):
        from backend.app.services import counter_store
        counter_store._reset_backend_for_tests()
        with pytest.raises(ValueError):
            counter_store.increment(uuid.uuid4(), 'views', 1)

    def test_clear_removes_specific_counter(self):
        from backend.app.services import counter_store
        counter_store._reset_backend_for_tests()
        clip_id = uuid.uuid4()
        counter_store.increment(clip_id, 'likes', 5)
        counter_store.increment(clip_id, 'shares', 3)

        counter_store.clear(clip_id, 'likes')
        deltas = counter_store.drain()
        assert deltas[str(clip_id)] == {'shares': 3}

    def test_clear_without_type_removes_all(self):
        from backend.app.services import counter_store
        counter_store._reset_backend_for_tests()
        clip_id = uuid.uuid4()
        counter_store.increment(clip_id, 'likes', 1)
        counter_store.increment(clip_id, 'shares', 1)
        counter_store.increment(clip_id, 'skips', 1)

        counter_store.clear(clip_id)
        assert counter_store.drain() == {}


# ---------------------------------------------------------------------------
# Dual-write flag tests
# ---------------------------------------------------------------------------
class TestDualWriteFlag:
    def test_default_is_true(self, monkeypatch):
        monkeypatch.delenv('ECHOFLOW_DUAL_WRITE_COUNTERS', raising=False)
        from backend.app.services import counter_store
        assert counter_store.dual_write_enabled() is True

    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv('ECHOFLOW_DUAL_WRITE_COUNTERS', 'true')
        from backend.app.services import counter_store
        assert counter_store.dual_write_enabled() is True

    def test_false_disables(self, monkeypatch):
        monkeypatch.setenv('ECHOFLOW_DUAL_WRITE_COUNTERS', 'false')
        from backend.app.services import counter_store
        assert counter_store.dual_write_enabled() is False

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv('ECHOFLOW_DUAL_WRITE_COUNTERS', '0')
        from backend.app.services import counter_store
        assert counter_store.dual_write_enabled() is False

    def test_off_disables(self, monkeypatch):
        monkeypatch.setenv('ECHOFLOW_DUAL_WRITE_COUNTERS', 'off')
        from backend.app.services import counter_store
        assert counter_store.dual_write_enabled() is False


# ---------------------------------------------------------------------------
# Flusher task tests
# ---------------------------------------------------------------------------
class TestFlushCountersToPg:
    def test_phase1_dual_write_drains_only(self, ready_clip, monkeypatch):
        """Phase 1: dual-write is ON. Flusher drains but doesn't touch Postgres."""
        from backend.app.services import counter_store
        from backend.app.tasks import flush_counters_to_pg

        counter_store._reset_backend_for_tests()
        monkeypatch.setenv('ECHOFLOW_DUAL_WRITE_COUNTERS', 'true')
        before_likes = ready_clip.likes

        # Seed the Redis counter with a delta
        counter_store.increment(ready_clip.id, 'likes', 5)

        result = flush_counters_to_pg.run()

        assert result == {'drained': 1, 'applied': 0, 'dual_write': True}
        # Postgres counter unchanged (the F() would have bumped it
        # at write time; the flusher is no-op on Postgres).
        ready_clip.refresh_from_db()
        assert ready_clip.likes == before_likes
        # Redis drained
        assert counter_store.drain() == {}

    def test_phase2_single_write_applies_to_postgres(self, ready_clip, monkeypatch):
        """Phase 2: dual-write is OFF. Flusher is the only path."""
        from backend.app.services import counter_store
        from backend.app.tasks import flush_counters_to_pg

        counter_store._reset_backend_for_tests()
        monkeypatch.setenv('ECHOFLOW_DUAL_WRITE_COUNTERS', 'false')
        before_likes = ready_clip.likes

        # Seed: 5 likes, 2 shares
        counter_store.increment(ready_clip.id, 'likes', 5)
        counter_store.increment(ready_clip.id, 'shares', 2)

        result = flush_counters_to_pg.run()

        assert result == {'drained': 2, 'applied': 1, 'dual_write': False}
        ready_clip.refresh_from_db()
        assert ready_clip.likes == before_likes + 5
        assert ready_clip.shares == 2

    def test_phase2_idempotent(self, ready_clip, monkeypatch):
        """Running the flusher twice in Phase 2: second is no-op."""
        from backend.app.services import counter_store
        from backend.app.tasks import flush_counters_to_pg

        counter_store._reset_backend_for_tests()
        monkeypatch.setenv('ECHOFLOW_DUAL_WRITE_COUNTERS', 'false')

        counter_store.increment(ready_clip.id, 'likes', 3)
        first = flush_counters_to_pg.run()
        second = flush_counters_to_pg.run()

        assert first['applied'] == 1
        assert second == {'drained': 0, 'applied': 0, 'dual_write': False}

    def test_no_op_when_no_deltas(self, ready_clip, monkeypatch):
        from backend.app.services import counter_store
        from backend.app.tasks import flush_counters_to_pg

        counter_store._reset_backend_for_tests()
        monkeypatch.setenv('ECHOFLOW_DUAL_WRITE_COUNTERS', 'false')

        result = flush_counters_to_pg.run()
        assert result == {'drained': 0, 'applied': 0, 'dual_write': False}


# ---------------------------------------------------------------------------
# Integration: service layer writes to the counter store
# ---------------------------------------------------------------------------
class TestServiceLayerIntegration:
    def test_record_like_toggle_increments_redis(self, user, ready_clip, monkeypatch):
        """Verify the service-layer write path actually goes through counter_store.

        This is the load-bearing assertion: when a like fires, the
        counter store's increment is called (Phase 1 dual-write).
        The Postgres F() is a separate path. Together they cover
        the same delta.
        """
        from backend.app.services import counter_store
        from backend.app.services.interactions import record_like_toggle
        from django.test import TestCase

        counter_store._reset_backend_for_tests()
        before = ready_clip.likes

        with TestCase.captureOnCommitCallbacks(execute=True):
            record_like_toggle(user, ready_clip)

        ready_clip.refresh_from_db()
        # F() bumped Postgres
        assert ready_clip.likes == before + 1
        # Redis counter was also bumped
        deltas = counter_store.drain()
        assert deltas[str(ready_clip.id)] == {'likes': 1}

    def test_record_skip_increments_redis(self, user, ready_clip, monkeypatch):
        from backend.app.services import counter_store
        from backend.app.services.interactions import record_skip
        from django.test import TestCase

        counter_store._reset_backend_for_tests()
        before = ready_clip.skips

        with TestCase.captureOnCommitCallbacks(execute=True):
            record_skip(
                user, ready_clip,
                listen_duration_ms=5000, reel_position_ms=30000,
            )

        ready_clip.refresh_from_db()
        assert ready_clip.skips == before + 1
        deltas = counter_store.drain()
        assert deltas[str(ready_clip.id)] == {'skips': 1}

    def test_f_skips_postgres_update_when_dual_write_off(
        self, user, ready_clip, monkeypatch,
    ):
        """Phase 2: the F() is bypassed. The Postgres counter only
        advances when the flusher runs."""
        from backend.app.services import counter_store
        from backend.app.services.interactions import record_like_toggle
        from backend.app.tasks import flush_counters_to_pg
        from django.test import TestCase

        counter_store._reset_backend_for_tests()
        monkeypatch.setenv('ECHOFLOW_DUAL_WRITE_COUNTERS', 'false')
        before = ready_clip.likes

        with TestCase.captureOnCommitCallbacks(execute=True):
            record_like_toggle(user, ready_clip)

        # F() bypassed: Postgres unchanged
        ready_clip.refresh_from_db()
        assert ready_clip.likes == before
        # Redis has the delta
        deltas = counter_store.drain()
        assert deltas[str(ready_clip.id)] == {'likes': 1}

        # Flusher applies it
        # Re-seed (drain cleared it) -- actually the drain above
        # already cleared; this is illustrative that drain + apply
        # is a one-step operation. For the test, we re-increment and
        # call the flusher to verify the apply path.
        counter_store.increment(ready_clip.id, 'likes', 1)
        result = flush_counters_to_pg.run()
        assert result['applied'] == 1
        ready_clip.refresh_from_db()
        assert ready_clip.likes == before + 1
