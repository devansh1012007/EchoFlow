# Celery Workers

## Worker Types & Configuration

EchoFlow runs **4 distinct Celery worker processes** (plus Beat scheduler):

| Worker | Queue | Concurrency | Pool | Purpose |
|--------|-------|-------------|------|---------|
| `celery` | `celery` (default) | Prefork (CPU count) | Prefork | Scraping, general tasks |
| `celery_feed` | `fast_feed` | 4 | Threads | Feed refill (vector queries) |
| `celery_media` | `heavy_media` | 1 | **Solo** | HLS transcoding + AI/ML |
| `celery_beat` | — | 1 | — | Periodic tasks scheduler |

---

## Task Routing

**Configuration** (`settings.py:158-161`):
```python
CELERY_TASK_ROUTES = {
    'backend.app.tasks.process_audio_to_hls': {'queue': 'heavy_media'},
    'backend.app.tasks.refill_user_feed': {'queue': 'fast_feed'},
}
```

**Default queue** (`celery`) receives all unrouted tasks.

---

## Worker Details

### 1. Default Worker (`celery`)

**Command:**
```bash
celery -A backend.EchoFlow worker --loglevel=info
```

**Concurrency:** Prefork (default = CPU cores)
**Pool:** Prefork (multiprocessing)

**Tasks:**
- `scrape_and_import` — Audio scraping from sources
- General async tasks (future: notifications, emails, etc.)

**Resource limits (Compose):**
```yaml
deploy:
  resources:
    limits: { cpus: '1', memory: 1G, pids: 300 }
```

---

### 2. Feed Worker (`celery_feed`)

**Command:**
```bash
celery -A backend.EchoFlow worker -Q fast_feed --concurrency=4 --loglevel=info
```

**Concurrency:** 4 (explicit, thread-based)
**Pool:** Threads (default for `--concurrency` with prefork)

**Why threads?** `refill_user_feed` is I/O-bound (Redis + PostgreSQL vector queries)

**Tasks:**
- `refill_user_feed` — Composite scoring, Redis queue population

**Resource limits:**
```yaml
deploy:
  resources:
    limits: { cpus: '1', memory: 1G, pids: 300 }
```

---

### 3. Media Worker (`celery_media`)

**Command:**
```bash
celery -A backend.EchoFlow worker -Q heavy_media --pool=solo --loglevel=info
```

**Concurrency:** 1 (single process)
**Pool:** **Solo** (no forking, no threads)

**Why solo?**
- ML models loaded in process memory (~500MB)
- Forking would duplicate memory per child
- Threads share memory but ML libs not thread-safe
- Sequential processing = predictable memory

**Tasks:**
- `process_audio_to_hls` — Full AI + HLS pipeline

**Environment:**
```yaml
environment:
  HF_HOME: /home/appuser/.cache/huggingface
  HF_HUB_OFFLINE: "1"
  TRANSFORMERS_OFFLINE: "1"
```

**Resource limits:**
```yaml
deploy:
  resources:
    limits: { cpus: '4', memory: 1G, pids: 200 }
    reservations: { cpus: '2', memory: 256M }
```
**CPU: 4 cores** — FFmpeg + Whisper benefit from parallelism within process

---

### 4. Beat Scheduler (`celery_beat`)

**Command:**
```bash
celery -A backend.EchoFlow beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

**Scheduler:** `django_celery_beat` (DatabaseScheduler)
- Periodic tasks stored in DB (`django_celery_beat` tables)
- Survives restarts, manageable via Django admin

**Schedule** (`settings.py:229-238`):
```python
CELERY_BEAT_SCHEDULE = {
    'update-global-metrics': {
        'task': 'backend.app.tasks.update_global_metrics',
        'schedule': 300.0,  # Every 5 minutes
    },
    'evolve-user-baselines': {
        'task': 'backend.app.tasks.evolve_long_term_user_baselines',
        'schedule': 3600.0,  # Every hour
    },
}
```

**No healthcheck** — Image's HTTP probe would fail (no gunicorn)

---

## Celery Configuration (`settings.py:162-171`)

```python
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_WORKER_STATE_DB = None           # No persistent revokes
CELERY_WORKER_POOL = 'prefork'          # Default pool
CELERY_WORKER_PREFETCH_MULTIPLIER = 1   # One task at a time per worker
CELERY_TASK_ACKNOWLEDGE_LATE = True     # Ack after completion
CELERY_TASK_REJECT_ON_WORKER_LOST = True # Requeue if worker dies
```

### Key Settings Explained

| Setting | Value | Reason |
|---------|-------|--------|
| `PREFETCH_MULTIPLIER = 1` | 1 | Prevents worker hoarding tasks (fair distribution) |
| `ACKNOWLEDGE_LATE` | True | Task acknowledged AFTER execution (not on receipt) |
| `REJECT_ON_WORKER_LOST` | True | Requeues task if worker dies mid-execution |
| `WORKER_STATE_DB = None` | None | No persistent revoke database (simpler) |

---

## Task Definitions (`tasks.py`)

### Decorator Pattern
```python
@shared_task(
    bind=True,                          # Access to self (retry, request)
    max_retries=3,                      # Max retry attempts
    default_retry_delay=60,             # Initial delay (seconds)
    autoretry_for=RETRYABLE_ERRORS,     # Auto-retry on these exceptions
    retry_backoff=True,                 # Exponential backoff
    retry_backoff_max=600,              # Max delay 10 min
    retry_jitter=False                  # No random jitter
)
def process_audio_to_hls(self, clip_id):
    ...
```

### Retryable Errors
```python
RETRYABLE_ERRORS = (
    OperationalError,      # DB connection issues
    ConnectionError,       # Network issues
    subprocess.CalledProcessError,  # FFmpeg failures
    OSError,               # File system issues
)
```

---

## Worker Lifecycle

### Startup
1. `wait_for_db.py` — Wait for PostgreSQL
2. Celery worker starts, connects to Redis
3. Registers with broker, starts consuming queues

### Task Execution
```
Task received
    │
    ▼
Deserialize (JSON)
    │
    ▼
Execute task function
    │
    ├── Success → Acknowledge, store result
    ├── RETRYABLE_ERROR → Retry with backoff
    ├── Non-retryable → Log error, acknowledge (dead letter not configured)
    └── Worker crash → Reject on lost → Requeue
```

### Shutdown
```
SIGTERM received
    │
    ▼
Stop consuming new tasks
    │
    ▼
Wait for current task (graceful_timeout)
    │
    ▼
Acknowledge/requeue in-progress
    │
    ▼
Close connections
```

---

## Monitoring & Debugging

### Inspect Commands
```bash
# Active tasks
celery -A backend.EchoFlow inspect active

# Registered tasks
celery -A backend.EchoFlow inspect registered

# Worker stats
celery -A backend.EchoFlow inspect stats

# Queue lengths (via Redis)
redis-cli LLEN celery
redis-cli LLEN fast_feed
redis-cli LLEN heavy_media
```

### Healthcheck (Docker)
```yaml
# Default worker
healthcheck:
  test: ["CMD-SHELL", "celery -A backend.EchoFlow inspect ping -d \"celery@$(hostname)\" --timeout=10 || exit 1"]

# Media worker (same)
```

### Logs
```bash
docker compose logs -f celery_media
docker compose logs -f celery_feed
```

---

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| Task stuck in `PENDING` | No worker consuming queue | Check worker logs, queue name match |
| `process_audio_to_hls` OOM | Model memory + FFmpeg | Increase memory limit, solo pool |
| Duplicate task execution | `ACKNOWLEDGE_LATE` + worker crash | Idempotency keys in task |
| Beat not scheduling | DatabaseScheduler not synced | `celery beat --scheduler django_celery_beat` |
| Tasks not retrying | Exception not in `RETRYABLE_ERRORS` | Add exception type |

---

## Scaling Strategy

### Horizontal Scaling
```yaml
# Add more workers for same queue
celery_feed_2:
  extends: celery_feed
  hostname: feed-worker-2

celery_media_2:
  extends: celery_media
  # Needs GPU for Whisper acceleration
```

### Queue Priority (Future)
```python
# Celery supports priority queues via x-max-priority
# Requires RabbitMQ/Redis with priority support
CELERY_TASK_DEFAULT_PRIORITY = 5
CELERY_TASK_QUEUE_MAX_PRIORITY = 10
```

---

*Source: `backend/EchoFlow/settings.py:158-171`, `backend/EchoFlow/celery.py`, `backend/app/tasks.py`, `docker-compose.yml`*