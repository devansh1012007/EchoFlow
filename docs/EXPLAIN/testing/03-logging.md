# Logging Configuration

## Overview

**File:** `backend/EchoFlow/settings.py:480-528`

Structured JSON logging with per-logger level control and per-request
correlation IDs.

Last verified against `settings.py` on 2026-09-04. If this doc drifts,
update it from the source of truth — do not let the file-line anchor
go stale again.

---

## Configuration

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        # Inject the per-request correlation_id (set by CorrelationIdMiddleware
        # via contextvars) into every log record. Empty string outside a
        # request scope (e.g., Celery workers) — see celery.py to set it
        # from task headers.
        'correlation': {
            '()': 'backend.EchoFlow.logging_filters.CorrelationIdFilter',
        },
    },
    'formatters': {
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'fmt': '%(asctime)s %(name)s %(levelname)s %(correlation_id)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
            'filters': ['correlation'],
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.environ.get('LOG_LEVEL', 'INFO'),
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'backend.app': {
            'handlers': ['console'],
            'level': os.environ.get('APP_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'celery': {
            'handlers': ['console'],
            'level': os.environ.get('CELERY_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
    },
}
```

---

## Output Format

### JSON Lines (stdout)
```json
{"asctime": "2024-01-15T10:30:00.123456", "name": "backend.app.tasks", "levelname": "INFO", "correlation_id": "a1b2c3d4-...", "message": "Extracted acoustic vector and duration for clip abc-123"}
{"asctime": "2024-01-15T10:30:01.234567", "name": "backend.app.views", "levelname": "ERROR", "correlation_id": "a1b2c3d4-...", "message": "Failed to process clip", "exc_info": "Traceback (most recent call last): ..."}
```

### Fields
| Field | Description |
|-------|-------------|
| `asctime` | ISO timestamp with microseconds |
| `name` | Logger name (e.g., `backend.app.tasks`) |
| `levelname` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `correlation_id` | Per-request ID (UUID4) from `X-Request-ID` header, or `-` outside request scope |
| `message` | Log message |
| `exc_info` | Exception traceback (if error) |

---

## Logger Hierarchy

| Logger | Level (Env Var) | Default | Purpose |
|--------|-----------------|---------|---------|
| `root` | `LOG_LEVEL` | `INFO` | Catch-all |
| `django` | `DJANGO_LOG_LEVEL` | `INFO` | Framework logs |
| `backend.app` | `APP_LOG_LEVEL` | `INFO` | Application code |
| `celery` | `CELERY_LOG_LEVEL` | `INFO` | Celery tasks |

### Propagation
- All loggers: `propagate: False` — no duplicate logs
- Each has own `console` handler with JSON formatter

---

## Environment Variables

| Variable | Default | Example |
|----------|---------|---------|
| `LOG_LEVEL` | `INFO` | `DEBUG` |
| `DJANGO_LOG_LEVEL` | `INFO` | `WARNING` |
| `APP_LOG_LEVEL` | `INFO` | `DEBUG` |
| `CELERY_LOG_LEVEL` | `INFO` | `DEBUG` |

**Usage:**
```bash
# Debug specific module
APP_LOG_LEVEL=DEBUG docker compose up web

# Quiet Django, verbose app
DJANGO_LOG_LEVEL=WARNING APP_LOG_LEVEL=DEBUG docker compose up
```

---

## Example Log Output

### Task Processing
```json
{"asctime": "2024-01-15T10:30:00.123456", "name": "backend.app.tasks", "levelname": "INFO", "correlation_id": "a1b2c3d4-...", "message": "process_audio_to_hls Task is starting..."}
{"asctime": "2024-01-15T10:30:05.234567", "name": "backend.app.tasks", "levelname": "INFO", "correlation_id": "a1b2c3d4-...", "message": "Extracted acoustic vector and duration for clip abc-123"}
{"asctime": "2024-01-15T10:30:15.345678", "name": "backend.app.tasks", "levelname": "INFO", "correlation_id": "a1b2c3d4-...", "message": "Extracted keywords for clip abc-123: [('comedy', 0.9), ('storytelling', 0.8)]"}
{"asctime": "2024-01-15T10:30:25.456789", "name": "backend.app.tasks", "levelname": "INFO", "correlation_id": "a1b2c3d4-...", "message": "Uploaded HLS files for clip abc-123"}
```

### Error with Traceback
```json
{"asctime": "2024-01-15T10:30:00.123456", "name": "backend.app.tasks", "levelname": "ERROR", "correlation_id": "a1b2c3d4-...", "message": "FFmpeg Error: Invalid data found when processing input", "exc_info": "Traceback (most recent call last):\n  File \"/app/backend/app/tasks.py\", line 300, in process_audio_to_hls\n    subprocess.run(command, check=True, ...)\n  File \"/usr/lib/python3.11/subprocess.py\", line 571, in run\n    raise CalledProcessError(...)\nsubprocess.CalledProcessError: Command '...' returned non-zero exit status 1."}
```

### Feed Refill
```json
{"asctime": "2024-01-15T10:30:00.123456", "name": "backend.app.tasks", "levelname": "INFO", "correlation_id": "a1b2c3d4-...", "message": "Added 40 composite-ranked clips."}
```

---

## Integration with Log Aggregation

### Loki (Grafana Labs)
```yaml
# promtail-config.yml
clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: docker
    static_configs:
      - targets: [localhost]
        labels:
          job: echoflow
          __path__: /var/lib/docker/containers/*/*-json.log
    pipeline_stages:
      - json:
          expressions:
            level: levelname
            logger: name
            message: message
            timestamp: asctime
            correlation_id: correlation_id
```

### Elasticsearch (Filebeat)
```yaml
# filebeat.yml
filebeat.inputs:
  - type: container
    paths:
      - '/var/lib/docker/containers/*/*.log'
    processors:
      - decode_json_fields:
          fields: ["log"]
          target: ""
          overwrite_keys: true
      - add_docker_metadata: ~
output.elasticsearch:
  hosts: ["elasticsearch:9200"]
```

---

## Structured Logging Best Practices (Applied)

### 1. Consistent Fields
```python
# Good: Structured data in message
logger.info("Feed refilled", extra={'user_id': user_id, 'clips_added': 40})
# {"message": "Feed refilled", "user_id": 123, "clips_added": 40}

# Avoid: String concatenation
logger.info(f"Feed refilled for user {user_id} with {count} clips")
# {"message": "Feed refilled for user 123 with 40 clips"}  # Hard to parse
```

### 2. Log Levels
| Level | When to Use |
|-------|-------------|
| `DEBUG` | Detailed diagnostic (variable values, flow) |
| `INFO` | Normal operations (task start/end, user actions) |
| `WARNING` | Unexpected but handled (retry, fallback) |
| `ERROR` | Failure requiring attention (task failed, API error) |
| `CRITICAL` | System-threatening (DB down, config missing) |

### 3. Exception Logging
```python
try:
    process_audio_to_hls(clip_id)
except Exception as e:
    logger.exception("Processing failed for clip %s", clip_id)
    # Includes full traceback in exc_info
```

---

## Correlation IDs (Currently Shipped)

Per-request correlation IDs are **already implemented** in the codebase.
The previous version of this doc listed "No correlation IDs" as a gap
and recommended adding them; that recommendation has been implemented.

### Implementation Files

- **`backend/EchoFlow/correlation.py`** — ContextVars-based store for
  the per-request correlation_id. `set_correlation_id()`,
  `get_correlation_id()`, `clear_correlation_id()`. Uses `contextvars`
  (not thread-local) so the id is correctly scoped per request even
  under async or gunicorn sync workers.

- **`backend/EchoFlow/middleware.py`** — `CorrelationIdMiddleware`
  reads `X-Request-ID` from the request headers (or generates a UUID4
  if absent), calls `set_correlation_id()`, and echoes
  `X-Request-ID` in the response. Registered in
  `settings.py:MIDDLEWARE`.

- **`backend/EchoFlow/logging_filters.py`** — `CorrelationIdFilter`
  reads the current correlation_id from the contextvars store and
  attaches it to every log record. Used by the `correlation` filter
  in the `console` handler's `filters` list.

### How It Works

1. Request arrives at Django. `CorrelationIdMiddleware` extracts or
   generates the correlation_id and calls `set_correlation_id()`.
2. All log records emitted during request processing pick up the
   `correlation_id` via the filter and include it in the JSON output.
3. The same id is echoed back in the response header so clients can
   correlate logs with their request.
4. After the request completes, the contextvar is cleared (or
   garbage-collected on the next request in the same thread).

### Celery Workers

Celery workers do not have a request scope. The correlation_id field
will be `-` (placeholder) unless the worker explicitly sets it from
task headers. This is a known limitation; the recommended pattern is
to pass `correlation_id` as a task header from the publisher and
extract it in a `task_prerun` signal handler. Not yet implemented;
see "Open Gaps" below.

---

## Open Gaps

| Gap | Impact | Fix |
|-----|--------|-----|
| Celery workers don't propagate correlation_id | Can't trace async tasks end-to-end | Add `task_prerun` signal that reads `correlation_id` from task headers and calls `set_correlation_id()` |
| No structured context | Can't filter by user/clip from logs | Add `extra` dict with IDs in hot-path log calls (partially done in `services/interactions.py`) |
| No sampling | High-volume logs expensive | Add rate-limited logging for frequent events (e.g., feed refill) |
| No log retention | Docker stdout lost on restart | Ship to Loki/ELK (config examples above) |
| No audit trail | Security events not tracked | Separate audit logger writing to dedicated sink |

---

*Source: `backend/EchoFlow/settings.py:480-528`, `backend/EchoFlow/correlation.py`, `backend/EchoFlow/middleware.py`, `backend/EchoFlow/logging_filters.py`, `backend/app/tasks.py`*
