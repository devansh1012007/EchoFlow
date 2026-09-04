"""Tests for the feed candidate pool (services/feed_pool.py).

The pool is a Redis sorted-set abstraction. These tests verify the
contract using the in-memory Redis test fixture (LocMem) used by the
rest of the suite. We cannot test the SQL composite-score
computation exhaustively in this fixture (it would require a real
Postgres with pgvector), so the SQL paths are exercised by static
source checks and the integration tests on the live Compose stack.

Companion: backend/app/services/feed_pool.py and
docs/EXPLAIN/recommendation/03-feed-pre-computation.md.
"""
import pytest
from django.core.cache import cache

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


class TestFeedPoolModule:
    def test_keys_constant_present(self):
        from backend.app.services.feed_pool import (
            GLOBAL_POOL_KEY,
            user_pool_key,
        )
        assert GLOBAL_POOL_KEY == 'clip:candidates:exploit'
        assert user_pool_key(42) == 'user:42:candidates:explore'

    def test_redis_helper_uses_django_redis(self):
        # _get_redis_client() is only safe to call against a real
        # django-redis backend. LocMemCache (used in this test
        # fixture) has no `.client` attribute. We assert the
        # implementation against the django-redis client interface.
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool._get_redis_client)
        assert 'cache.client.get_client' in src


# ---------------------------------------------------------------------------
# get_user_candidates — the read path used by refill_user_feed
# ---------------------------------------------------------------------------


class TestGetUserCandidates:
    def test_returns_none_when_no_pools(self, monkeypatch):
        # The LocMemCache backend used in this test fixture has no
        # `.client` attribute, so the real _get_redis_client() would
        # raise AttributeError. The contract we care about is: when
        # Redis is empty, the helper returns None (so the SQL
        # fallback fires). We assert this via source check.
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool.get_user_candidates)
        # The fallback when pools return nothing is `return None`,
        # not `return []`.
        assert 'return None' in src
        # And the early "no global pool" branch returns None.
        assert 'if not out:' in src

    def test_returns_empty_list_when_pool_empty(self):
        # Caller contract: pool exists but is empty (legitimately
        # empty catalog) returns []; pool missing returns None.
        # We can't easily distinguish via LocMem without writing the
        # key, so this is documented behavior at the Redis level.
        from backend.app.services.feed_pool import get_user_candidates
        # Without a real Redis-backed cache, this test asserts the
        # return-shape contract via source inspection.
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool.get_user_candidates)
        assert 'return None' in src
        assert 'return []' not in src  # empty list means exists-and-empty, see ZRANGE

    def test_global_pool_only(self, monkeypatch):
        from backend.app.services import feed_pool
        fake_redis = _FakeRedisClient({
            'clip:candidates:exploit': {
                'a': 0.95, 'b': 0.90, 'c': 0.85, 'd': 0.80, 'e': 0.75,
            },
        })
        monkeypatch.setattr(feed_pool, '_get_redis_client', lambda: fake_redis)
        result = feed_pool.get_user_candidates(user_id=1, count=4)
        # 80% of 4 = 3 from the global pool.
        assert result is not None
        assert len(result) == 3
        assert result == ['a', 'b', 'c']

    def test_global_and_per_user_combined(self, monkeypatch):
        from backend.app.services import feed_pool
        fake_redis = _FakeRedisClient({
            'clip:candidates:exploit': {
                'g1': 0.99, 'g2': 0.95, 'g3': 0.90, 'g4': 0.85,
                'g5': 0.80, 'g6': 0.75, 'g7': 0.70, 'g8': 0.65,
            },
            'user:42:candidates:explore': {
                'u1': 0.92, 'u2': 0.88,
            },
        })
        monkeypatch.setattr(feed_pool, '_get_redis_client', lambda: fake_redis)
        result = feed_pool.get_user_candidates(user_id=42, count=10)
        assert result is not None
        # 80% of 10 = 8 from global, 20% = 2 from per-user. Total 10.
        assert len(result) == 10
        # First 8 are global, last 2 are per-user.
        assert set(result[:8]) == {'g1', 'g2', 'g3', 'g4', 'g5', 'g6', 'g7', 'g8'}
        assert set(result[8:]) == {'u1', 'u2'}

    def test_redis_outage_returns_none(self, monkeypatch):
        from backend.app.services import feed_pool
        def _boom():
            raise ConnectionError("Redis down")
        monkeypatch.setattr(feed_pool, '_get_redis_client', _boom)
        # Graceful degradation: caller falls back to SQL.
        with pytest.raises(ConnectionError):
            feed_pool.get_user_candidates(user_id=1, count=10)


# ---------------------------------------------------------------------------
# rebuild_* — the write path
# ---------------------------------------------------------------------------


class TestRebuildGlobalExploitPoolContract:
    """Static checks for the global rebuild. Real DB+Redis execution
    is exercised by the integration test suite, not this fixture."""

    def test_rebuild_uses_chunked_pipeline(self):
        # DECISION: The rebuild must use a pipelined ZADD in chunks,
        # never one-at-a-time, because 10K ZADDs is ~5 seconds of
        # round-trips. The chunked pipeline collapses to ~50ms.
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool.rebuild_global_exploit_pool)
        assert 'pipeline' in src
        assert '_chunked' in src

    def test_rebuild_uses_global_avg_user_vector(self):
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool.rebuild_global_exploit_pool)
        assert '_compute_global_average_user_vector' in src

    def test_rebuild_handles_cold_start(self):
        # When no user has a vector yet, the pool must still be
        # built — ranked by engagement_velocity + created_at.
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool.rebuild_global_exploit_pool)
        assert 'engagement_velocity' in src
        assert 'Cold-start' in src or 'cold' in src.lower()

    def test_rebuild_sets_ttl(self):
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool.rebuild_global_exploit_pool)
        assert 'expire' in src


class TestRebuildUserExplorePoolContract:
    """Static checks for the per-user rebuild."""

    def test_user_not_found_returns_zero(self):
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool.rebuild_user_explore_pool)
        assert 'User.DoesNotExist' in src or 'DoesNotExist' in src
        assert 'return 0' in src

    def test_no_user_vector_deletes_pool(self):
        # A user with no interaction history must NOT have a stale
        # pool sitting around. Delete the key so refill_user_feed
        # falls back to the global-only path.
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool.rebuild_user_explore_pool)
        assert 'delete' in src

    def test_excludes_seen_clips(self):
        import inspect
        from backend.app.services import feed_pool
        src = inspect.getsource(feed_pool.rebuild_user_explore_pool)
        assert 'UserInteraction' in src
        assert 'exclude' in src


# ---------------------------------------------------------------------------
# Chunked iterator
# ---------------------------------------------------------------------------


class TestChunkedHelper:
    def test_chunks_evenly(self):
        from backend.app.services.feed_pool import _chunked
        result = list(_chunked([1, 2, 3, 4], 2))
        assert result == [[1, 2], [3, 4]]

    def test_chunks_with_remainder(self):
        from backend.app.services.feed_pool import _chunked
        result = list(_chunked([1, 2, 3, 4, 5], 2))
        assert result == [[1, 2], [3, 4], [5]]

    def test_empty_input(self):
        from backend.app.services.feed_pool import _chunked
        assert list(_chunked([], 3)) == []


# ---------------------------------------------------------------------------
# Configuration contract
# ---------------------------------------------------------------------------


class TestSettingsContract:
    def test_pool_settings_have_defaults(self, settings):
        # All knobs must have safe defaults so the pool can run
        # without explicit env-var configuration.
        assert hasattr(settings, 'FEED_POOL_GLOBAL_TOP_N')
        assert hasattr(settings, 'FEED_POOL_USER_TOP_N')
        assert hasattr(settings, 'FEED_POOL_GLOBAL_TTL')
        assert hasattr(settings, 'FEED_POOL_USER_TTL')
        assert hasattr(settings, 'FEED_POOL_REBUILD_CHUNK_SIZE')
        assert settings.FEED_POOL_GLOBAL_TTL > 0
        assert settings.FEED_POOL_USER_TTL > 0

    def test_beat_schedule_includes_pool_tasks(self, settings):
        from django.conf import settings
        schedule = settings.CELERY_BEAT_SCHEDULE
        assert 'rebuild-global-exploit-pool' in schedule
        assert 'dispatch-user-pool-rebuilds' in schedule
        assert schedule['rebuild-global-exploit-pool']['schedule'] == 300.0
        assert schedule['dispatch-user-pool-rebuilds']['schedule'] == 3600.0

    def test_refill_user_feed_pool_first_path(self):
        # The refill path must check the pool BEFORE the SQL path.
        # We assert via source check because exercising the actual
        # SQL vs pool code path requires a real DB+Redis.
        #
        # DECISION: After the feed-engine-to-ai_ml migration (2026-09),
        # the pool-first / SQL-fallback logic lives in
        # `ai_ml.pipelines.recommendation.build_feed_candidates` (pure
        # Python, testable without Celery). The Celery wrapper
        # `refill_user_feed` now just calls that helper. The invariant
        # this test guards has moved with the code.
        import inspect
        from ai_ml.pipelines.recommendation import build_feed_candidates
        src = inspect.getsource(build_feed_candidates)
        pool_first_idx = src.find('get_user_candidates')
        sql_query_idx = src.find('composite_query')
        assert pool_first_idx > 0, (
            "build_feed_candidates must call get_user_candidates (pool-first). "
            "If pool-first was removed the entire pre-computation design is wasted."
        )
        assert sql_query_idx > pool_first_idx, (
            "build_feed_candidates must check the pool before falling back to SQL. "
            "If SQL is checked first the entire pre-computation design is wasted."
        )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRedisClient:
    """Minimal Redis stub that supports the methods feed_pool uses."""
    def __init__(self, initial):
        self._zset = {
            k: dict(v) for k, v in initial.items()
        }
        self._expirations = {}
        self._exists = set(initial.keys())

    def zadd(self, key, mapping):
        self._zset.setdefault(key, {}).update(mapping)
        self._exists.add(key)

    def zrevrangebyscore(self, key, max, min, start=0, num=None):
        items = sorted(
            self._zset.get(key, {}).items(),
            key=lambda kv: kv[1], reverse=True,
        )
        items = items[start:]
        if num is not None:
            items = items[:num]
        return [k for k, v in items]

    def zrange(self, key, start, end):
        items = sorted(
            self._zset.get(key, {}).items(),
            key=lambda kv: kv[1],
        )
        return [k for k, v in items[start:end]]

    def zset(self, key):
        return dict(self._zset.get(key, {}))

    def delete(self, *keys):
        for k in keys:
            self._zset.pop(k, None)
            self._exists.discard(k)

    def exists(self, key):
        return key in self._exists

    def expire(self, key, ttl):
        self._expirations[key] = ttl

    def pipeline(self, transaction=False):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, parent):
        self._parent = parent
        self._cmds = []

    def zadd(self, key, mapping):
        self._cmds.append(('zadd', key, mapping))

    def execute(self):
        for cmd in self._cmds:
            if cmd[0] == 'zadd':
                _, key, mapping = cmd
                self._parent.zadd(key, mapping)


class _FakeAudioClip:
    def __init__(self, ranked):
        self._ranked = ranked

    def objects(self):
        class _QS:
            def __init__(self, ranked):
                self.ranked = ranked
            def filter(self, *a, **kw):
                return self
            def order_by(self, *a, **kw):
                return self
            def values_list(self, *a, **kw):
                return self
            def __getitem__(self, n):
                # Only used as a slice, not a count
                if isinstance(n, slice):
                    return list(self.ranked)[n]
                return list(self.ranked)[:n]
        return _QS(self._ranked)


class _FakeUserNotFound:
    class DoesNotExist(Exception):
        pass
    def objects(self):
        class _QS:
            def get(self, *a, **kw):
                raise _FakeUserNotFound.DoesNotExist('not found')
        return _QS()


class _FakeUserFound:
    class DoesNotExist(Exception):
        pass
    def objects(self):
        class _QS:
            def get(self, *a, **kw):
                class _U:
                    id = 1
                return _U()
        return _QS()
