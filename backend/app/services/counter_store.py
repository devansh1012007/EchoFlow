"""Redis-backed counter store for AudioClip.likes/shares/skips.

Architectural fix for Group B item 9 (docs/backend-bug-fixs.md §10.2):
the F() counter side-effect in UserInteraction.save() creates row-level
contention on AudioClip under viral load (500 concurrent likes = 500
serialized row locks). The P0 architectural fix is to move counter
increments out of the synchronous request path entirely.

This module provides:
  - increment(clip_id, counter_type, delta=1) -> int
    Lock-free on the writer side. INCRBY on Redis.
  - drain() -> dict[clip_id][counter_type] -> int
    Atomic read-and-reset of all counter deltas. Implemented as a
    Lua script so concurrent INCRBY calls between read and reset
    are preserved (the script does GETALL + DEL atomically from
    Redis's perspective).
  - bulk_drain(clip_ids) -> dict[clip_id][counter_type] -> int
    Pipelined version of drain() for the flusher.
  - clear(clip_id) -> None
    Used by tests to reset state; production code should never call this.

In test environments without Redis, the module falls back to an
in-memory dict with a threading.Lock. The API surface is identical.

Rollout:
  - Phase 1: dual-write. record_*_toggle() writes BOTH to Postgres
    (via the F() in save()) AND to Redis. The flusher runs and
    starts accumulating. ECHOFLOW_DUAL_WRITE_COUNTERS=True (default).
  - Phase 2: flip the flag. ECHOFLOW_DUAL_WRITE_COUNTERS=False.
    record_*_toggle() writes ONLY to Redis. The flusher is the only
    path to Postgres.
  - Phase 3: remove the F() code from UserInteraction.save().
    Done as a follow-up commit after Phase 2 runs cleanly in prod.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


KEY_PREFIX = 'clip'  # results in keys like 'clip:<uuid>:likes'
COUNTER_TYPES = ('likes', 'shares', 'skips')


def _make_key(clip_id: Any, counter_type: str) -> str:
    return f'{KEY_PREFIX}:{clip_id}:{counter_type}'


def _key_pattern() -> str:
    return f'{KEY_PREFIX}:*'


class _RedisBackend:
    """Production backend: real Redis with Lua-atomic drain."""

    # Lua: HGETALL + DEL, atomic from Redis's POV. Returns the
    # HGETALL result. Concurrent INCRBY between HGETALL and DEL
    # would be lost without atomicity; Lua prevents that.
    #
    # We do NOT use this for single-key increments; INCRBY is already
    # atomic. The Lua is only for the read-and-reset drain.
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
        return int(self._client.incrby(_make_key(clip_id, counter_type), delta))

    def drain(self) -> dict[str, dict[str, int]]:
        """Atomic read-and-reset of all clip:* keys.

        Returns: {clip_id: {counter_type: int, ...}, ...}
        """
        self._ensure_drain_script()
        try:
            raw = self._client.evalsha(self._drain_sha, 1, _key_pattern())
        except Exception as exc:
            logger.warning("counter_store: evalsha failed (%s); reloading", exc)
            self._drain_sha = self._client.script_load(self._DRAIN_SCRIPT)
            raw = self._client.evalsha(self._drain_sha, 1, _key_pattern())

        result: dict[str, dict[str, int]] = {}
        # raw is a flat list [k1, v1, k2, v2, ...]
        for i in range(0, len(raw), 2):
            key = raw[i]
            if isinstance(key, bytes):
                key = key.decode('utf-8')
            value = int(raw[i + 1])
            # Parse 'clip:<uuid>:<type>'
            parts = key.split(':', 2)
            if len(parts) != 3:
                continue
            _, clip_id, counter_type = parts
            result.setdefault(clip_id, {})[counter_type] = value
        return result

    def clear(self, clip_id, counter_type: str | None = None) -> None:
        if counter_type is None:
            for ct in COUNTER_TYPES:
                self._client.delete(_make_key(clip_id, ct))
        else:
            self._client.delete(_make_key(clip_id, counter_type))


class _InMemoryBackend:
    """Test backend: dict with a Lock, no Redis dependency.

    Behavior matches the Redis backend for the API surface used by
    production code: increment is atomic (under the lock), drain is
    atomic (lock + dict copy + clear). The Lua-atomicity guarantee
    from the Redis backend is replaced by a single lock acquire
    that spans the equivalent read-and-reset.
    """

    def __init__(self):
        self._data: dict[str, int] = {}
        self._lock = threading.Lock()

    def increment(self, clip_id, counter_type: str, delta: int = 1) -> int:
        key = _make_key(clip_id, counter_type)
        with self._lock:
            self._data[key] = self._data.get(key, 0) + delta
            return self._data[key]

    def drain(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        with self._lock:
            # Snapshot + clear under the lock. Equivalent to the
            # Lua-atomic GETALL+DEL in the Redis backend.
            for key, value in list(self._data.items()):
                parts = key.split(':', 2)
                if len(parts) != 3:
                    continue
                _, clip_id, counter_type = parts
                result.setdefault(clip_id, {})[counter_type] = value
                del self._data[key]
        return result

    def clear(self, clip_id, counter_type: str | None = None) -> None:
        with self._lock:
            if counter_type is None:
                for ct in COUNTER_TYPES:
                    self._data.pop(_make_key(clip_id, ct), None)
            else:
                self._data.pop(_make_key(clip_id, counter_type), None)


def _build_backend() -> Any:
    """Return a Redis-backed store in prod, in-memory in tests.

    Detection: try django.core.cache's _cache.get_client() first
    (the same client the rest of the app uses); if unavailable,
    fall back to in-memory. This keeps the test env (LocMem cache)
    on the in-memory backend and the prod env (Redis cache) on
    the Redis backend without an explicit env var.
    """
    try:
        from django.core.cache import caches
        from django.conf import settings

        cache = caches[settings.CACHES['default']['BACKEND'].endswith('RedisCache') and 'default' or 'default']
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
    """Increment a counter. Lock-free on the writer side.

    Returns the new value (not particularly useful to callers, but
    matches the redis-py INCRBY return type for debugging).
    """
    if counter_type not in COUNTER_TYPES:
        raise ValueError(f"counter_type must be one of {COUNTER_TYPES}")
    return _get_backend().increment(clip_id, counter_type, delta)


def drain() -> dict[str, dict[str, int]]:
    """Atomic read-and-reset of all counter deltas.

    Returns: {clip_id: {counter_type: int, ...}, ...}
    """
    return _get_backend().drain()


def clear(clip_id: Any, counter_type: str | None = None) -> None:
    """Test-only: clear a single counter (or all 3 for a clip)."""
    _get_backend().clear(clip_id, counter_type)


# ---------------------------------------------------------------------------
# Rollout flag (Phase 1 -> Phase 2)
# ---------------------------------------------------------------------------
# When ECHOFLOW_DUAL_WRITE_COUNTERS is True (the default in this
# commit), record_*_toggle() writes BOTH to Redis (via this module)
# AND to Postgres (via the F() in UserInteraction.save()). The flusher
# is dormant; the F() is the source of truth.
#
# When the env var is set to False, the F() is removed at runtime
# (via the field_map check in UserInteraction.save() reading the same
# env var) and the flusher becomes the only path to Postgres.
#
# Phase 3 (post-rollout) removes the env var check and the F() code
# from save() permanently.
DUAL_WRITE_ENV = 'ECHOFLOW_DUAL_WRITE_COUNTERS'


def dual_write_enabled() -> bool:
    """True if the F() side-effect should also run (Phase 1 default)."""
    return os.environ.get(DUAL_WRITE_ENV, 'true').lower() not in ('false', '0', 'off')
