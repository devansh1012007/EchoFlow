# Task Reliability: Retries, Idempotency, Locking

## Retry Configuration

### Global Retryable Errors
```python
RETRYABLE_ERRORS = (
    OperationalError,           # Database connection failures
    ConnectionError,            # Network/Redis connection failures
    subprocess.CalledProcessError,  # FFmpeg failures
    OSError,                    # File system errors
)
```

### Per-Task Retry Decorators

#### `process_audio_to_hls` (Heavy Media)
```python
@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=False
)
def process_audio_to_hls(self, clip_id):
    ...
```
- **Max 3 retries** with exponential backoff: 60s → 120s → 240s
- **Max delay 10 min** (`retry_backoff_max=600`)
- **No jitter** — deterministic retry timing

#### `refill_user_feed` (Fast Feed)
```python
@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True
)
def refill_user_feed(self, user_id, count=50):
    ...
```
- **Max 2 retries**, faster initial delay (30s)
- Feed refill is idempotent (safe to retry)

#### Periodic Tasks (Beat)
```python
@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_backoff_max=600
)
def update_global_metrics(self):
    ...
```
- Same as heavy media but for periodic tasks

---

## Idempotency

### Natural Idempotency

| Task | Idempotent? | Reason |
|------|-------------|--------|
| `process_audio_to_hls` | ❌ No | Creates files, updates DB — running twice = duplicate work |
| `refill_user_feed` | ✅ Yes | Replaces Redis queue, recomputes same vectors |
| `update_global_metrics` | ✅ Yes | Same UPDATE produces same result |
| `evolve_long_term_user_baselines` | ✅ Yes | Overwrites user vectors |
| `scrape_and_import` | ❌ No | Creates new AudioClip each run |

### Idempotency Keys (Not Implemented)

**Recommended pattern for non-idempotent tasks:**
```python
@shared_task(bind=True)
def process_audio_to_hls(self, clip_id):
    # Check if already processed
    clip = AudioClip.objects.get(id=clip_id)
    if clip.status == 'ready':
        return "Already processed"
    
    # Use task ID as idempotency key
    idempotency_key = f"process_hls:{clip_id}:{self.request.id}"
    if cache.get(idempotency_key):
        return "Duplicate task"
    cache.set(idempotency_key, "1", timeout=3600)
    
    try:
        # ... processing ...
    finally:
        cache.delete(idempotency_key)
```

---

## Locking Mechanisms

### 1. Feed Refill Lock (Redis SETNX)

**In `refill_user_feed`:**
```python
lock_key = f"feed_refill_lock:{user_id}"
acquired = redis_client.set(lock_key, "1", nx=True, ex=30)
if not acquired:
    return "Refill already in progress."

try:
    # ... refill logic ...
finally:
    try:
        redis_client.delete(lock_key)
    except Exception:
        pass
```

**Properties:**
- **SETNX** — Atomic set-if-not-exists
- **TTL 30s** — Auto-release if worker crashes
- **Released in `finally`** — Guaranteed cleanup
- **Per-user** — No cross-user blocking

### 2. UserInteraction Row Lock (PostgreSQL)

**In `UserInteraction.save()`:**
```python
def save(self, *args, **kwargs):
    is_new = self.pk is None
    state_changed = False
    increment_val = 0

    if is_new:
        state_changed = True
        increment_val = 1 if self.is_active else 0
    else:
        # Lock row to prevent concurrent saves from double-counting
        with transaction.atomic():
            old_instance = UserInteraction.objects.select_for_update().get(pk=self.pk)
            if old_instance.is_active != self.is_active:
                state_changed = True
                increment_val = 1 if self.is_active else -1

    super().save(*args, **kwargs)

    if state_changed and increment_val != 0:
        field_map = {'like': 'likes', 'share': 'shares', 'skip': 'skips'}
        field_to_update = field_map.get(self.interaction_type)
        if field_to_update:
            AudioClip.objects.filter(pk=self.clip.pk).update(
                **{field_to_update: F(field_to_update) + increment_val}
            )
```

**Why `select_for_update()`?**
- Prevents race condition: two concurrent toggles could both read `is_active=True`, both flip to `False`, but only one decrement applied
- Row-level lock held until transaction commits

---

## Failure Handling Patterns

### 1. Graceful Degradation (Media Processing)

```python
try:
    # 1. Acoustic vector
    clip.acoustic_vector = extract_acoustic_vector(y, sr)
    clip.duration_ms = int(librosa.get_duration(y=y, sr=sr) * 1000)
    clip.save(update_fields=['acoustic_vector', 'duration_ms'])
    
    # 2. Transcription + Embedding + Tags
    model = get_whisper_model()
    segments, info = model.transcribe(normalized_path, beam_size=5)
    # ...
    
    # 3. HLS Transcoding
    subprocess.run(ffmpeg_cmd, check=True)
    # Upload to S3
    # ...
    
    clip.status = 'ready'
    clip.save()
    
except Exception as e:
    clip.status = 'failed'
    clip.save()
    logger.exception("Processing failed: %s", e)
    return  # Celery will retry based on decorator
```

**Pattern:** Each stage saves progress; failure at any point → status='failed'

### 2. Scratch Space Cleanup (Always)

```python
try:
    # ... processing using local temp files ...
finally:
    try:
        os.remove(normalized_path)
    except OSError:
        pass
    shutil.rmtree(local_hls_dir, ignore_errors=True)
```

**Guarantees:** No leaked temp files even on crash/OOM

### 3. Scraper Cleanup

```python
finally:
    for p in (local_input, tmp_out):
        try:
            if p and os.path.exists(p) and not p.startswith(settings.MEDIA_ROOT):
                os.remove(p)
        except Exception as e:
            logger.error("Failed to clean up temp file %s: %s", p, e)
```

---

## Duplicate Execution Prevention

### Current Gaps

| Scenario | Risk | Mitigation |
|----------|------|------------|
| Worker crashes mid-task, task requeued | `process_audio_to_hls` runs twice | None — creates duplicate HLS, overwrites clip |
| Beat scheduler runs twice | `update_global_metrics` runs concurrently | DB lock (but both may wait) |
| Network partition | Client retries API, double enqueue | None for uploads |

### Recommended: Idempotency Keys

```python
# For upload endpoint
def create(self, request, *args, **kwargs):
    idempotency_key = request.headers.get('Idempotency-Key')
    if idempotency_key:
        existing = cache.get(f"upload:{idempotency_key}")
        if existing:
            return Response(existing, status=202)
    
    # ... create clip ...
    
    if idempotency_key:
        cache.set(f"upload:{idempotency_key}", response.data, timeout=3600)
```

---

## Circuit Breaker (Not Implemented)

**Needed for:** Redis outage → fallback feed

```python
# Pseudocode for FastFeedViewSet
def list(self, request):
    try:
        # Try Redis feed
        clip_ids = redis.lpop(...)
    except RedisConnectionError:
        # Circuit breaker open
        if not circuit_breaker.is_open():
            circuit_breaker.open()
            logger.error("Redis unavailable, opening circuit breaker")
        
        # Fallback: cached global trending
        return get_fallback_feed(request)
    
    if circuit_breaker.is_open() and redis_healthy():
        circuit_breaker.close()
```

---

## Monitoring Task Health

### Key Metrics

| Metric | Target | Alert If |
|--------|--------|----------|
| Task success rate | > 99% | < 95% |
| Avg retry count | < 0.1 | > 0.5 |
| Task duration (p95) | < 60s (media) | > 300s |
| Queue depth | < 100 | > 1000 |
| Worker count | Expected | < Expected |

### Celery Events
```bash
# Monitor in real-time
celery -A backend.EchoFlow events

# Or with Flower (if installed)
celery -A backend.EchoFlow flower
```

---

## Best Practices Summary

1. **Always use `autoretry_for`** with specific exception tuples
2. **Set `retry_backoff_max`** to prevent unbounded delays
3. **Use `select_for_update()`** for counter updates
4. **Clean up scratch space in `finally`** blocks
5. **Lock per-user, not global** for feed refills
6. **Make tasks idempotent** or add idempotency keys
7. **Monitor queue depths** and worker counts
8. **Test failure scenarios** (kill worker mid-task)

---

*Source: `backend/app/tasks.py`, `backend/app/models.py:181-205`, `backend/app/views.py:128-135`*