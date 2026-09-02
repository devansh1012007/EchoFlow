# Distributed Systems Failure Handling

## Overview

EchoFlow uses Django, PostgreSQL/pgvector, Redis, Celery, S3/MinIO, FFmpeg, and ML processing. This document covers failure modes, retry strategies, idempotency, and recovery patterns.

---

## Failure Taxonomy

| Category | Examples | Recovery Strategy |
|----------|----------|-------------------|
| **Transient** | Network blip, DB deadlock, Redis timeout | Retry with backoff |
| **Permanent** | Invalid input, corrupt file, missing license | Fail fast, alert, manual intervention |
| **Resource** | OOM, disk full, CPU throttle | Scale, circuit breaker, degrade gracefully |
| **Logical** | Race condition, duplicate execution | Idempotency keys, locking |

---

## Component Failure Modes

### 1. PostgreSQL Failures

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Connection lost | `OperationalError` on query | Celery retry (exponential backoff) |
| Deadlock | `OperationalError: deadlock detected` | Retry (different lock order) |
| Lock timeout | `lock_timeout` exceeded | Retry, optimize queries |
| Disk full | `ERROR: could not write` | Alert, cleanup, scale storage |
| Replica lag | Stale reads | Route reads to primary |

**Celery Retry Config:**
```python
RETRYABLE_ERRORS = (OperationalError, ConnectionError, ...)
@shared_task(autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, max_retries=3)
```

### 2. Redis Failures

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Connection refused | `ConnectionError` | Celery retry, fallback feed |
| OOM eviction | Keys missing, `maxmemory` hit | Monitor memory, split broker/cache |
| Persistence loss | AOF/RDB corruption | Replica promotion, rebuild |
| Network partition | Timeout on commands | Circuit breaker, degrade |

**Critical Gap:** No fallback feed when Redis down
```python
# FastFeedViewSet.list() - NO fallback
clip_ids_bytes = redis_client.lpop(redis_key, 10)
if not clip_ids_bytes:
    refill_user_feed.delay(user_id)  # Also needs Redis!
    # If Redis down → exception → 500
```

### 3. Celery Worker Failures

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Worker crash (OOM) | Task never completes | `REJECT_ON_WORKER_LOST=True` → requeue |
| Task timeout | `SoftTimeLimitExceeded` | Retry, optimize |
| Exception | Task raises | Retry per `autoretry_for` |
| Duplicate execution | Worker restarts mid-task | Idempotency keys (not fully implemented) |

**Settings:**
```python
CELERY_TASK_ACKNOWLEDGE_LATE = True      # Ack after completion
CELERY_TASK_REJECT_ON_WORKER_LOST = True # Requeue if worker dies
CELERY_WORKER_PREFETCH_MULTIPLIER = 1    # One task at a time
```

### 4. S3/MinIO Failures

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Upload timeout | `ConnectTimeoutError` | Retry with backoff |
| 403 Forbidden | Signature expired/invalid | Regenerate signed URL |
| 404 Not Found | Object deleted | Return 404, log |
| Bucket policy | Anonymous access denied | Check `minio-init` ran |
| CORS error | Browser blocks | Check MinIO CORS config |

### 5. FFmpeg Failures

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Invalid input | `CalledProcessError` | Mark clip failed, alert |
| Codec not supported | `Invalid data found` | Normalize first (already done) |
| OOM during encode | Process killed | Worker solo pool, limit concurrency |
| Disk full | `No space left` | Cleanup, scale storage |

**Implementation:**
```python
try:
    subprocess.run(ffmpeg_cmd, check=True, ...)
except subprocess.CalledProcessError as e:
    clip.status = 'failed'
    clip.save()
    logger.error("FFmpeg Error: %s", e.stderr.decode())
    return  # Celery retries transient
```

### 6. ML Model Failures

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Model load OOM | `MemoryError` | Solo pool, model baking |
| Model download fail | `ConnectionError` | Baked in Docker image |
| Inference error | Exception | Mark clip failed, retry |
| GPU not available | `CUDA error` | CPU fallback (int8) |

**Model Loading (Lazy + Baked):**
```python
def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        with _model_lock:
            if whisper_model is None:
                whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return whisper_model
```

---

## Idempotency Patterns

### Current Implementation

| Operation | Idempotent? | Mechanism |
|-----------|-------------|-----------|
| `process_audio_to_hls` | ❌ | None — re-processing creates duplicate HLS |
| `refill_user_feed` | ✅ | Replaces Redis queue |
| `update_global_metrics` | ✅ | Same UPDATE produces same result |
| `evolve_long_term_user_baselines` | ✅ | Overwrites user vectors |
| `UserInteraction.save()` | ✅ | `select_for_update` + state check |
| `Comment.save()/delete()` | ⚠️ | Counter updates not transactional |

### Recommended: Idempotency Keys

```python
# For non-idempotent tasks (process_audio_to_hls)
def process_audio_to_hls(self, clip_id):
    idempotency_key = f"process_hls:{clip_id}:{self.request.id}"
    
    # Check if already processed
    if cache.get(idempotency_key):
        return "Already processed"
    
    # Mark as in-progress (TTL > max task time)
    cache.set(idempotency_key, "processing", timeout=3600)
    
    try:
        # ... actual processing ...
        cache.set(idempotency_key, "completed", timeout=86400)
    except Exception:
        cache.delete(idempotency_key)  # Allow retry
        raise
```

---

## Retry Strategies

### Celery Task Retry Configuration

| Task | Max Retries | Initial Delay | Backoff | Max Delay | Jitter |
|------|-------------|---------------|---------|-----------|--------|
| `process_audio_to_hls` | 3 | 60s | Exponential | 600s | No |
| `refill_user_feed` | 2 | 30s | Exponential | — | No |
| `update_global_metrics` | 3 | 60s | Exponential | 600s | No |
| `evolve_long_term_user_baselines` | 3 | 60s | Exponential | 600s | No |
| `scrape_and_import` | 3 | 60s | Exponential | 600s | No |

### Retryable Errors
```python
RETRYABLE_ERRORS = (
    OperationalError,      # DB connection
    ConnectionError,       # Network/Redis
    subprocess.CalledProcessError,  # FFmpeg
    OSError,               # File system
)
```

### Non-Retryable (Fail Fast)
- `ValidationError` — Invalid input
- `PermissionError` — Auth/authorization
- `DoesNotExist` — Missing resource
- License violations — Policy violation

---

## Circuit Breaker Pattern (Not Implemented)

### Needed For
1. **Redis outage** → Fallback to cached global trending feed
2. **ML service down** → Skip AI features, serve basic feed
3. **S3 outage** → Return cached HLS URLs, queue uploads

### Implementation Sketch
```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure = None
        self.state = 'closed'  # closed, open, half-open
    
    def call(self, func, *args, **kwargs):
        if self.state == 'open':
            if time.time() - self.last_failure > self.timeout:
                self.state = 'half-open'
            else:
                raise CircuitOpenError()
        
        try:
            result = func(*args, **kwargs)
            self.on_success()
            return result
        except Exception as e:
            self.on_failure()
            raise
    
    def on_success(self):
        self.failures = 0
        self.state = 'closed'
    
    def on_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.failure_threshold:
            self.state = 'open'
```

### Usage in FastFeedViewSet
```python
def list(self, request):
    try:
        clip_ids = redis_client.lpop(...)
    except RedisError:
        if not redis_circuit_breaker.call(lambda: True):
            # Fallback: global trending
            return get_fallback_feed(request)
```

---

## Data Consistency

### Distributed Transactions (Not Used)
EchoFlow avoids distributed transactions. Instead:
- **Eventual consistency** for feed updates
- **Atomic counters** via `F()` expressions
- **Idempotent retries** for async tasks

### Saga Pattern (Not Implicit)
For multi-step operations (upload → process → notify):
```python
# Current: Fire-and-forget
transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))

# Better: Explicit saga with compensation
def upload_and_process(clip):
    try:
        clip = create_clip(...)
        task = process_audio_to_hls.delay(clip.id)
        return clip
    except Exception:
        # Compensate: delete clip, cleanup S3
        cleanup(clip)
        raise
```

---

## Disaster Recovery

### Backup Strategy (Not Configured)
| Data | Frequency | Method | Retention |
|------|-----------|--------|-----------|
| PostgreSQL | Daily | `pg_dump` / PITR | 30 days |
| Redis | Hourly | RDB/AOF | 24 hours |
| S3/MinIO | Continuous | Versioning + CRR | 90 days |
| Config | On change | Git / Secrets manager | Indefinite |

### Recovery Procedures (Not Documented)
1. **DB restore** → Point-in-time recovery
2. **Redis rebuild** → Recompute feeds from DB
3. **S3 restore** → Versioning + cross-region replication
4. **Full stack** → Terraform + Docker images + DB restore

---

## Monitoring for Failures

### Key Alerts (Not Configured)
| Alert | Condition | Severity |
|-------|-----------|----------|
| Task failure rate | > 5% in 5m | Critical |
| Celery queue depth | > 1000 | Warning |
| Redis memory | > 80% | Warning |
| DB connections | > 80% max | Critical |
| S3 4xx/5xx rate | > 1% | Warning |
| FFmpeg failure rate | > 10% | Warning |
| Feed latency P95 | > 1s | Warning |

---

*Source: `backend/app/tasks.py`, `backend/app/views.py`, `backend/app/models.py`, `backend/EchoFlow/settings.py`, `docker-compose.yml`*