"""Redis-backed counter store for AudioClip engagement metrics.

Architectural fix for the architecture-audit concern that the
legacy `update_global_metrics` task performs a correlated subquery
on `userinteraction` for every AudioClip row every 5 minutes
(`SELECT AVG(completion_rate) FROM userinteraction WHERE clip_id = …`).
At 1M clips × 100 views that's 100M index lookups every 5 minutes.
This module moves the hot path to O(1) Redis writes and pushes
Postgres updates into a periodic flusher that touches only dirty
clips.

Key space (all under `clip:` prefix so the legacy `KEYS clip:*` Lua
drain finds them):

  * `clip:<uuid>:likes`           — clip-global integer counter
  * `clip:<uuid>:shares`          — clip-global integer counter
  * `clip:<uuid>:skips`           — clip-global integer counter
  * `clip:<uuid>:user:<int>:completion_sum`   — per-(user,clip) float
  * `clip:<uuid>:user:<int>:completion_count` — per-(user,clip) int

The per-(user,clip) completion keys are needed because
`avg_completion_rate` is a per-(user,clip) measurement; preserving
the per-user signal keeps the recommendation engine's existing
`UserInteraction`-shaped inputs working. The flusher aggregates
those keys into a single `UserInteraction` row per `(user, clip,
'view')` tuple per beat, matching the row shape `record_skip` and
`flush_telemetry_stream` used to write synchronously.

Public API:
  * `increment(clip_id, counter_type, delta=1) -> int`
  * `add_completion(clip_id, user_id, completion_rate) -> None`
  * `drain() -> dict`
  * `clear(clip_id, counter_type=None) -> None`
  * `dual_write_enabled() -> bool`  (transitional; slated for removal)

In test environments without Redis, the module falls back to an
in-memory dict with a `threading.Lock`. The API surface is identical.

In Phase 1 of the rollout, the synchronous F() side-effect in
`UserInteraction.save()` ALSO ran. The F() is now removed; the
flusher is the only path from Redis to Postgres.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


KEY_PREFIX = 'clip'  # results in keys like 'clip:<uuid>:likes'

# Counter types that follow the simple `clip:<uuid>:<type>` shape
# (clip-global, integer-valued).
SIMPLE_COUNTER_TYPES = ('likes', 'shares', 'skips')

# Per-(user,clip) completion accumulator: two keys per (user,clip).
COMPLETION_SUM_SUFFIX = 'completion_sum'
COMPLETION_COUNT_SUFFIX = 'completion_count'


def _make_simple_key(clip_id: Any, counter_type: str) -> str:
    return f'{KEY_PREFIX}:{clip_id}:{counter_type}'


def _make_completion_sum_key(clip_id: Any, user_id: Any) -> str:
    return f'{KEY_PREFIX}:{clip_id}:user:{user_id}:{COMPLETION_SUM_SUFFIX}'


def _make_completion_count_key(clip_id: Any, user_id: Any) -> str:
    return f'{KEY_PREFIX}:{clip_id}:user:{user_id}:{COMPLETION_COUNT_SUFFIX}'


def _key_pattern() -> str:
    return f'{KEY_PREFIX}:*'


def _parse_completion_key(key: str) -> tuple[str, str] | None:
    """Parse `clip:<uuid>:user:<int>:completion_<sum|count>` -> (clip_id, user_id) or None."""
    parts = key.split(':')
    # ['clip', '<uuid with dashes>', 'user', '<int>', 'completion_sum']
    if len(parts) != 5:
        return None
    if parts[0] != KEY_PREFIX or parts[2] != 'user':
        return None
    if parts[4] not in (COMPLETION_SUM_SUFFIX, COMPLETION_COUNT_SUFFIX):
        return None
    return parts[1], parts[3]


class _RedisBackend:
    """Production backend: real Redis with Lua-atomic drain."""

    # Atomic GETALL + DEL. Concurrent INCRBY / INCRBYFLOAT between
    # the read and the reset would be lost without atomicity; Lua
    # prevents that.
    _DRAIN_SCRIPT = """
    local keys = redis.call('KEYS', KEYS[1])
    local result = {}
    for _, k in ipairs(keys) do
        local val = redis.call('GET', k)
        if val then
            table.insert(result, k)
            table.insert(result, val)
            redis.call('DEL', k)
        end
    end
    return result
    """

    def __init__(self, client):
        self._client = client
        self._drain_sha = None

    def _ensure_drain_script(self):
        if self._drain_sha is None:
            self._drain_sha = self._client.script_load(self._DRAIN_SCRIPT)

    def increment(self, clip_id, counter_type: str, delta: int = 1) -> int:
        return int(self._client.incrby(_make_simple_key(clip_id, counter_type), delta))

    def add_completion(self, clip_id, user_id, completion_rate: float) -> None:
        # Two operations; not atomic across them, but each is atomic
        # individually. The flusher divides sum/count per (user,clip)
        # so a partial write (sum without count) would skew the
        # avg_completion_rate for that user by including the count=0
        # case (no row) and excluding the count=1 case (a row with the
        # rate). In practice the gap is sub-millisecond and a missing
        # completion row is acceptable (the user's next skip
        # recomputes it).
        self._client.incrbyfloat(
            _make_completion_sum_key(clip_id, user_id), completion_rate,
        )
        self._client.incr(
            _make_completion_count_key(clip_id, user_id), 1,
        )

    def drain(self) -> dict:
        """Atomic read-and-reset of all clip:* keys.

        Returns:
          {
            'counters':  {clip_id: {counter_type: int, ...}, ...},
            'completion': {(clip_id, user_id): {
                'completion_sum': float,
                'completion_count': int,
            }, ...},
          }
        """
        self._ensure_drain_script()
        try:
            raw = self._client.evalsha(self._drain_sha, 1, _key_pattern())
        except Exception as exc:
            logger.warning("counter_store: evalsha failed (%s); reloading", exc)
            self._drain_sha = self._client.script_load(self._DRAIN_SCRIPT)
            raw = self._client.evalsha(self._drain_sha, 1, _key_pattern())

        counters: dict[str, dict[str, int]] = {}
        completion: dict[tuple[str, str], dict[str, float]] = {}
        # raw is a flat list [k1, v1, k2, v2, ...]
        for i in range(0, len(raw), 2):
            key = raw[i]
            if isinstance(key, bytes):
                key = key.decode('utf-8')
            value = raw[i + 1]
            if isinstance(value, bytes):
                value = value.decode('utf-8')

            # Try completion-key shape first: clip:<uuid>:user:<int>:completion_<sum|count>
            parsed = _parse_completion_key(key)
            if parsed is not None:
                clip_id, user_id = parsed
                slot = completion.setdefault((clip_id, user_id), {})
                if key.endswith(COMPLETION_SUM_SUFFIX):
                    slot[COMPLETION_SUM_SUFFIX] = float(value)
                else:
                    slot[COMPLETION_COUNT_SUFFIX] = int(value)
                continue

            # Else simple counter shape: clip:<uuid>:<type>
            parts = key.split(':', 2)
            if len(parts) != 3:
                continue
            _, clip_id, counter_type = parts
            if counter_type not in SIMPLE_COUNTER_TYPES:
                # Unknown key under our prefix; skip (defense-in-depth
                # for namespace collision; today the only other writer
                # is services/feed_pool.py which uses a different
                # key — clip:candidates:exploit — that is not a
                # STRING, but if a future module introduces a STRING
                # under clip: it will land here).
                continue
            counters.setdefault(clip_id, {})[counter_type] = int(value)

        return {'counters': counters, 'completion': completion}

    def clear(self, clip_id, counter_type: str | None = None) -> None:
        if counter_type is not None:
            self._client.delete(_make_simple_key(clip_id, counter_type))
            return
        for ct in SIMPLE_COUNTER_TYPES:
            self._client.delete(_make_simple_key(clip_id, ct))


class _InMemoryBackend:
    """Test backend: dict with a Lock, no Redis dependency.

    Behavior matches the Redis backend for the API surface used by
    production code: increment is atomic (under the lock), add_completion
    is atomic, drain is atomic (lock + dict copy + clear). The
    Lua-atomicity guarantee from the Redis backend is replaced by a
    single lock acquire that spans the equivalent read-and-reset.
    """

    def __init__(self):
        self._data: dict[str, int | float] = {}
        self._lock = threading.Lock()

    def increment(self, clip_id, counter_type: str, delta: int = 1) -> int:
        key = _make_simple_key(clip_id, counter_type)
        with self._lock:
            self._data[key] = int(self._data.get(key, 0)) + delta
            return int(self._data[key])

    def add_completion(self, clip_id, user_id, completion_rate: float) -> None:
        sum_key = _make_completion_sum_key(clip_id, user_id)
        count_key = _make_completion_count_key(clip_id, user_id)
        with self._lock:
            self._data[sum_key] = float(self._data.get(sum_key, 0.0)) + float(completion_rate)
            self._data[count_key] = int(self._data.get(count_key, 0)) + 1

    def drain(self) -> dict:
        counters: dict[str, dict[str, int]] = {}
        completion: dict[tuple[str, str], dict[str, float]] = {}
        with self._lock:
            for key, value in list(self._data.items()):
                parsed = _parse_completion_key(key)
                if parsed is not None:
                    clip_id, user_id = parsed
                    slot = completion.setdefault((clip_id, user_id), {})
                    if key.endswith(COMPLETION_SUM_SUFFIX):
                        slot[COMPLETION_SUM_SUFFIX] = float(value)
                    else:
                        slot[COMPLETION_COUNT_SUFFIX] = int(value)
                    del self._data[key]
                    continue
                parts = key.split(':', 2)
                if len(parts) != 3:
                    continue
                _, clip_id, counter_type = parts
                if counter_type not in SIMPLE_COUNTER_TYPES:
                    continue
                counters.setdefault(clip_id, {})[counter_type] = int(value)
                del self._data[key]
        return {'counters': counters, 'completion': completion}

    def clear(self, clip_id, counter_type: str | None = None) -> None:
        with self._lock:
            if counter_type is not None:
                self._data.pop(_make_simple_key(clip_id, counter_type), None)
                return
            for ct in SIMPLE_COUNTER_TYPES:
                self._data.pop(_make_simple_key(clip_id, ct), None)


def _build_backend() -> Any:
    """Return a Redis-backed store in prod, in-memory in tests.

    Detection: try django.core.cache's _cache.get_client() first
    (the same client the rest of the app uses); if unavailable,
    fall back to in-memory. This keeps the test env (LocMem cache)
    on the in-memory backend and the prod env (Redis cache) on the
    Redis backend without an explicit env var.
    """
    try:
        from django.core.cache import caches

        cache = caches['default']
        # django-redis exposes .client.get_client() returning a real Redis client.
        if hasattr(cache, 'client') and hasattr(cache.client, 'get_client'):
            client = cache.client.get_client()
            return _RedisBackend(client)
    except Exception as exc:
        logger.debug("counter_store: Redis client unavailable, using in-memory: %s", exc)
    return _InMemoryBackend()


_backend: Any = None
_backend_lock = threading.Lock()


def _get_backend() -> Any:
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                _backend = _build_backend()
    return _backend


def _reset_backend_for_tests() -> None:
    """Test hook: clear the cached backend so the next call rebuilds.

    Production code should NOT call this; the test suite uses it to
    reset state between tests when patching the cache backend.
    """
    global _backend
    with _backend_lock:
        _backend = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def increment(clip_id: Any, counter_type: str, delta: int = 1) -> int:
    """Increment a clip-global counter. Lock-free on the writer side.

    `counter_type` must be one of `SIMPLE_COUNTER_TYPES`
    (`likes`, `shares`, `skips`).
    """
    if counter_type not in SIMPLE_COUNTER_TYPES:
        raise ValueError(
            f"counter_type must be one of {SIMPLE_COUNTER_TYPES} for increment(); "
            f"use add_completion() for completion_rate data"
        )
    return _get_backend().increment(clip_id, counter_type, delta)


def add_completion(clip_id: Any, user_id: Any, completion_rate: float) -> None:
    """Accumulate one completion sample for a (user, clip) pair.

    Increments the per-(user,clip) completion_sum by `completion_rate`
    (a float in [0.0, 1.0]) and the completion_count by 1. The
    flusher divides sum/count to recover the average.
    """
    rate = float(completion_rate)
    if rate < 0.0 or rate > 1.0:
        # Clamp to bounds rather than reject: the upstream
        # computation is `min(watch_time_ms / clip_duration, 1.0)`,
        # which already enforces the upper bound; the lower bound
        # here is a safety net for any future caller that computes
        # completion differently.
        rate = max(0.0, min(1.0, rate))
    _get_backend().add_completion(clip_id, user_id, rate)


def drain() -> dict:
    """Atomic read-and-reset of all counter deltas.

    Returns: `{'counters': {clip_id: {counter_type: int, ...}, ...},
              'completion': {(clip_id, user_id): {sum, count}, ...}}`
    """
    return _get_backend().drain()


def clear(clip_id: Any, counter_type: str | None = None) -> None:
    """Test-only: clear a single counter (or all simple counters for a clip)."""
    _get_backend().clear(clip_id, counter_type)


# ---------------------------------------------------------------------------
# Rollout flag (kept for transitional backward compatibility)
# ---------------------------------------------------------------------------
# `ECHOFLOW_DUAL_WRITE_COUNTERS` is now always False — the F()
# side-effect in UserInteraction.save() has been removed. The flag
# is retained as a no-op so deployment configurations referencing it
# do not error; a follow-up release will delete it.
DUAL_WRITE_ENV = 'ECHOFLOW_DUAL_WRITE_COUNTERS'


def dual_write_enabled() -> bool:
    """Always False after the F() removal. Retained for backward compat.

    The legacy dual-write F() path has been removed from
    `UserInteraction.save()`. This function is a no-op kept so
    deployment configurations that still set the env var do not
    error. The default for any new env value is False.
    """
    return False
