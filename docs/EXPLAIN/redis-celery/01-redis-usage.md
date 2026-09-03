# Redis Usage

## Overview

Single Redis 7 instance serves **multiple roles**:
1. **Celery Broker** — Task queue messaging
2. **Django Cache** — `django-redis` backend
3. **Feed Queues** — Per-user `user_feed:{id}` lists
4. **Session Storage** — Django sessions (if configured)
5. **Rate Limiting** — DRF throttling backend
6. **Telemetry Stream** — `stream:interaction.events` + consumer group `cg:telemetry-flush` (see [02-telemetry-stream.md](02-telemetry-stream.md))
7. **Telemetry DLQ** — `stream:interaction.events:dlq` (poison-message triage)
8. **Legacy Telemetry List** — `telemetry:queue` (fallback during the stream migration; slated for removal)
9. **Dedup keys** — `processed_event:{event_id}` SETNX with 24h TTL

---

## Configuration (`settings.py`)

```python
REDIS_URL_DEFAULT = 'redis://localhost:6379/1'
REDIS_URL = os.getenv("REDIS_URL", REDIS_URL_DEFAULT)

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
```

**Database:** `/1` (not default `/0`) — isolates EchoFlow from other apps

---

## Redis Data Structures

### 1. Feed Queues (Primary Custom Usage)

**Key pattern:** `user_feed:{user_id}`
**Type:** LIST (LPUSH/RPOP)
**TTL:** 24 hours (86400s)

```python
# FastFeedViewSet.list()
redis_key = f"user_feed:{user_id}"
clip_ids_bytes = redis_client.lpop(redis_key, 10)  # Atomic pop 10

# refill_user_feed()
redis_client.rpush(redis_key, *clip_ids_to_push)
redis_client.expire(redis_key, 86400)
```

**Flow:**
```
User requests feed
       │
       ▼
LPOP 10 clip IDs from user_feed:{id}
       │
       ├── If empty → trigger refill_user_feed.delay()
       └── If <15 remaining → trigger refill (removed duplicate)
       │
       ▼
Fetch AudioClip objects preserving order
       │
       ▼
Return serialized clips
```

### 2. Feed Refill Lock

**Key pattern:** `feed_refill_lock:{user_id}`
**Type:** STRING (SETNX with TTL)
**TTL:** 30 seconds

```python
lock_key = f"feed_refill_lock:{user_id}"
acquired = redis_client.set(lock_key, "1", nx=True, ex=30)
if not acquired:
    return "Refill already in progress."
# ... do refill ...
finally:
    redis_client.delete(lock_key)
```

**Purpose:** Prevent concurrent refills for same user (race condition).

---

### 3. Celery Broker Queues

**Queues:**
| Queue | Purpose | Worker |
|-------|---------|--------|
| `celery` | Default (scraping, general) | `celery` |
| `fast_feed` | Feed refill | `celery_feed` |
| `heavy_media` | HLS/AI processing | `celery_media` |

**Routing** (`settings.py:158-161`):
```python
CELERY_TASK_ROUTES = {
    'backend.app.tasks.process_audio_to_hls': {'queue': 'heavy_media'},
    'backend.app.tasks.refill_user_feed': {'queue': 'fast_feed'},
}
```

### 4. Celery Result Backend

**Keys:** `celery-task-meta-{task_id}`
**TTL:** Default (configurable via `result_expires`)

```python
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
```

### 5. Django Cache

**Keys:** `django_cache:{key}`
**Used for:** View caching, throttling, generic caching

```python
from django.core.cache import cache
cache.get(key)
cache.set(key, value, timeout=300)
```

### 6. DRF Throttling

**Keys:** `throttle_{scope}_{ident}`
**Scopes:** `anon` (100/hr), `user` (1000/hr)

```python
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
    },
}
```

### 7. Telemetry Stream (Primary, 2026-09)

**Key:** `stream:interaction.events`
**Consumer group:** `cg:telemetry-flush`
**Approximate cap:** `MAXLEN ~ 50000` (enforced on every XADD; bounds RAM)
**Dedup key:** `processed_event:{event_id}` SETNX EX 86400
**DLQ:** `stream:interaction.events:dlq` (poison messages, unbounded, alerted on)

See [02-telemetry-stream.md](02-telemetry-stream.md) for the full producer/consumer
contract, idempotency reasoning, operational signals, and rollback steps.

### 8. Legacy Telemetry List (Fallback, slated for removal)

**Key:** `telemetry:queue`
**Type:** LIST (RPUSH / LPOP)
**Drained by:** `flush_telemetry_legacy` Celery Beat task (every 30s)

Only contains events when:
- `ECHOFLOW_TELEMETRY_STREAM=off`, OR
- the XADD producer's call to Redis raised and the service fell back
  to the list (the producer's `try/except` in
  `services/interactions.py:record_telemetry`)

**TODO:** remove `flush_telemetry_legacy` and this key after one
operational cycle of the stream consumer proving stable.

---

## Connection Management

### Django-Redis Client
```python
from django.core.cache import cache
redis_client = cache.client.get_client()  # Raw redis-py client
```

### Celery Connection Pool
```python
# Celery manages its own connection pool
# Configurable via:
CELERY_BROKER_POOL_LIMIT = 10  # Default
CELERY_BROKER_CONNECTION_TIMEOUT = 30
```

---

## Memory & Eviction

### Redis Config (`docker-compose.yml`)
```yaml
redis:
  command: redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru
```

| Setting | Value | Impact |
|---------|-------|--------|
| `maxmemory` | 512MB | Hard limit |
| `maxmemory-policy` | `allkeys-lru` | Evicts least recently used across ALL keys |

**Risk:** Feed queues (`user_feed:*`) can be evicted under memory pressure, same as broker queues.

---

## Architecture Audit Finding: Split Redis

**Current:** Single Redis for broker + cache + feeds
**Recommended:** Two Redis instances

```
┌─────────────────────────────────────────────────────────────┐
│                     Current (Single)                         │
│  Redis (512MB)                                               │
│  ├── Celery broker queues                                   │
│  ├── Django cache                                           │
│  ├── user_feed:* lists (1M users × 50 clips ≈ 2GB!)        │
│  └── Rate limit keys                                        │
│  → allkeys-lru evicts feeds when broker spikes              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                     Recommended (Split)                      │
│  Redis Broker (256MB)          Redis Cache (1-4GB)          │
│  ├── celery queue              ├── user_feed:* lists        │
│  ├── fast_feed queue           ├── Django cache             │
│  └── heavy_media queue         ├── Rate limits              │
│  → noeviction                  → allkeys-lru (feeds safe)   │
└─────────────────────────────────────────────────────────────┘
```

---

## Monitoring Keys

### Key Patterns to Monitor
```bash
# Feed queue lengths
redis-cli --scan --pattern "user_feed:*" | xargs -I {} redis-cli LLEN {}

# Broker queue depths
redis-cli LLEN celery
redis-cli LLEN fast_feed
redis-cli LLEN heavy_media

# Memory usage
redis-cli INFO memory

# Key count
redis-cli DBSIZE
```

### Critical Metrics
| Metric | Healthy | Warning |
|--------|---------|---------|
| `used_memory` | < 400MB | > 450MB |
| `connected_clients` | < 100 | > 200 |
| `user_feed:*` avg length | 20-50 | < 5 or > 100 |
| Broker queue depth | < 100 | > 1000 |
| `XLEN stream:interaction.events` | < 50,000 | > 50,000 sustained 5 min |
| `XPENDING stream:interaction.events cg:telemetry-flush` | < 1,000 | > 1,000 sustained 5 min |
| `XLEN stream:interaction.events:dlq` | 0 | > 0 |
| `LLEN telemetry:queue` | 0 | > 0 (legacy path running) |

---

## Failure Scenarios

| Scenario | Impact | Mitigation |
|----------|--------|------------|
| Redis OOM | `allkeys-lru` evicts feeds + broker keys | Split Redis, increase memory |
| Redis crash | All queues lost, feeds empty | AOF persistence, replica |
| Network partition | Workers can't enqueue/dequeue | Circuit breaker, fallback feed |
| Feed queue eviction | Users get "caught up" incorrectly | Monitor queue health, alert |

---

## Scaling Considerations

### At 1M Users
- Feed queues: 1M × 50 clips × 36 bytes ≈ **1.8GB**
- Current 512MB limit → **constant eviction**
- Need: Redis Cluster (sharded by user_id) or dedicated feed Redis

### Redis Cluster (Future)
```yaml
# 16384 slots across nodes
# user_feed:* → hash_slot(user_id) → specific shard
# Enables horizontal scaling
```

---

*Source: `backend/EchoFlow/settings.py`, `backend/app/views.py:118-159`, `backend/app/tasks.py:512-592`, `docker-compose.yml:35-62`*