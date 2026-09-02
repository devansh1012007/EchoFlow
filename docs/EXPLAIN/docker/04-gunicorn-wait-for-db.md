# Gunicorn & wait_for_db.py

## wait_for_db.py

### Purpose
Polls PostgreSQL until ready before starting application. Prevents startup race conditions.

### Implementation (`wait_for_db.py`)

```python
import os
import sys
import time
import psycopg2

MAX_RETRIES = 120
RETRY_DELAY = 1
BACKOFF_FACTOR = 2

def wait_for_db(max_retries=MAX_RETRIES, initial_delay=RETRY_DELAY):
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Waiting for database at {url}...")
    delay = initial_delay
    attempt = 0

    while attempt < max_retries:
        attempt += 1
        try:
            conn = psycopg2.connect(url)
            conn.close()
            print(f"Database is ready after {attempt} attempt(s).")
            return True
        except psycopg2.OperationalError as e:
            print(
                f"Attempt {attempt}/{max_retries} failed: {e}. "
                f"Retrying in {delay}s...",
                file=sys.stderr,
            )
            time.sleep(delay)
            delay = min(delay * BACKOFF_FACTOR, 30)

    print(
        f"ERROR: Database not available after {max_retries} attempts. Giving up.",
        file=sys.stderr,
    )
    sys.exit(1)

if __name__ == "__main__":
    wait_for_db()
```

### Parameters
| Parameter | Value | Description |
|-----------|-------|-------------|
| `MAX_RETRIES` | 120 | Maximum connection attempts |
| `RETRY_DELAY` | 1s | Initial delay |
| `BACKOFF_FACTOR` | 2 | Exponential backoff multiplier |
| `MAX_DELAY` | 30s | Cap on delay |

### Behavior
```
Attempt 1: wait 1s
Attempt 2: wait 2s
Attempt 3: wait 4s
...
Attempt 6: wait 30s (capped)
Attempt 7-120: wait 30s each
Total max wait: ~55 minutes
```

### Usage in Docker Compose
```yaml
# web service
command: >
  sh -c "set -e && python wait_for_db.py &&
         python manage.py migrate &&
         python manage.py collectstatic --noinput &&
         gunicorn -c gunicorn.conf.py backend.EchoFlow.wsgi:application"

# All Celery workers also run wait_for_db.py first
```

---

## gunicorn.conf.py

### Configuration (`gunicorn.conf.py`)

```python
import os
import multiprocessing

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
backlog = 2048

# Worker processes
workers = int(os.environ.get('GUNICORN_WORKERS', 4))
threads = int(os.environ.get('GUNICORN_THREADS', 4))
worker_class = 'gthread'  # Threaded worker class

# Timeout
timeout = 120  # 2 minutes for vector computation requests
graceful_timeout = 30  # Graceful shutdown timeout
keepalive = 5

# Process naming
proc_name = 'echoflow'

# Server mechanics
preload_app = True  # Load app before forking workers (saves memory)
daemon = False
pidfile = None
user = None
group = None

# Logging
accesslog = '-'  # Log to stdout
errorlog = '-'   # Log to stderr
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')

# Worker cleanup - restart workers periodically to prevent memory leaks
max_requests = 1000  # Restart worker after 1000 requests
max_requests_jitter = 50  # Add jitter to prevent all workers restarting at once
```

---

## Critical: post_fork Hook

### Problem
`backend/EchoFlow/__init__.py` imports Celery app at module load:
```python
# backend/EchoFlow/__init__.py
from .celery import app as celery_app
__all__ = ('celery_app',)
```

When `preload_app = True`:
1. Master process loads Django → imports Celery → creates Redis connections
2. Master forks workers
3. Workers **inherit stale Redis connections** → "connection reset" errors

### Solution: post_fork Hook
```python
# gunicorn.conf.py:44-65
def post_fork(server, worker):
    """Called just after a worker has been forked."""
    # Reset ALL shared connections after fork.
    # Critical because EchoFlow/__init__.py imports Celery app at module load time.
    # When preload_app=True, the Celery app's Redis connections AND Django's DB
    # connections are established in the master process. Without resetting,
    # forked workers inherit stale connections, which leads to "connection reset"
    # errors and silent failures.
    from django.db import connections
    for conn in connections.all():
        conn.close()

    # Reset Celery/Redis connections
    try:
        from backend.EchoFlow.celery import app as celery_app
        if hasattr(celery_app.connection, 'pool'):
            celery_app.connection.pool.disconnect()
    except Exception:
        pass

    server.log.info(f"Worker spawned (pid: {worker.pid}) - DB & Redis connections reset")
```

### What It Resets
1. **Django DB connections** — `connections.all()` → `close()`
2. **Celery Redis connections** — `celery_app.connection.pool.disconnect()`

### Why This Matters
- Without: Workers fail with "connection reset by peer" on first Redis/DB use
- With: Clean connections per worker, no inheritance issues

---

## Other Hooks

```python
def on_exit(server):
    """Called on graceful shutdown."""
    print("EchoFlow: Shutting down gracefully...")
    print("EchoFlow: Allowing workers to finish current requests...")

def pre_exec(server):
    """Called just before a new master process is forked."""
    server.log.info("Forked child, re-executing.")

def when_ready(server):
    """Called just after the server is started."""
    server.log.info("Server is ready. Spawning workers")

def worker_int(worker):
    """Called when a worker receives the INT or QUIT signal."""
    worker.log.info("worker received INT or QUIT signal")

def worker_abort(worker):
    """Called when a worker receives the SIGABRT signal."""
    worker.log.info("worker received SIGABRT signal")
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8000` | Bind port |
| `GUNICORN_WORKERS` | `4` | Worker processes |
| `GUNICORN_THREADS` | `4` | Threads per worker |
| `GUNICORN_LOG_LEVEL` | `info` | Log level |

---

## Worker Model: gthread

```python
worker_class = 'gthread'  # Threaded worker class
workers = 4
threads = 4
```

**Total concurrency:** 4 workers × 4 threads = 16 concurrent requests

### Why gthread?
- **I/O bound** work (DB, Redis, S3) — threads release GIL
- **Memory efficient** — shared process memory vs multiprocessing
- **Vector computation** — releases GIL during NumPy operations

### Not Suitable For
- CPU-bound Python code (GIL contention)
- ML inference in same process (use separate worker)

---

## Memory Management

```python
# Restart worker after 1000 requests (±50 jitter)
max_requests = 1000
max_requests_jitter = 50
```

### Why?
- Prevents memory leaks from accumulating
- Python doesn't always release memory to OS
- Periodic restart = fresh memory state

---

## Production Tuning

### Worker Count Formula
```
workers = (2 * CPU cores) + 1
# Or for I/O bound: workers = CPU cores * 2-4
```

### Thread Count
```
threads = 2-4  # Per worker
# Total concurrency = workers * threads
```

### Timeouts
```python
timeout = 120          # Request timeout (2 min)
graceful_timeout = 30  # Shutdown grace period
keepalive = 5          # Keep-alive connections
```

### Logging
```python
accesslog = '-'  # stdout
errorlog = '-'   # stderr
loglevel = 'info'
# JSON logging via python-json-logger in settings.py
```

---

## Discrepancy: preload_app Trade-off

| Benefit | Cost |
|---------|------|
| **Memory savings** (shared code) | **post_fork complexity** required |
| Faster worker startup | Stale connections if hook missing |
| Copy-on-write memory | Celery/Redis must be reset |

**Verdict:** Worth it for memory-constrained deployments, but hook is critical.

---

*Source: `gunicorn.conf.py`, `wait_for_db.py`, `backend/EchoFlow/__init__.py`, `docker-compose.yml`*