# Logging Configuration

## Overview

**File:** `backend/EchoFlow/settings.py:341-378`

Structured JSON logging with per-logger level control.

---

## Configuration

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'fmt': '%(asctime)s %(name)s %(levelname)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
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
{"asctime": "2024-01-15T10:30:00.123456", "name": "backend.app.tasks", "levelname": "INFO", "message": "Extracted acoustic vector and duration for clip abc-123"}
{"asctime": "2024-01-15T10:30:01.234567", "name": "backend.app.views", "levelname": "ERROR", "message": "Failed to process clip", "exc_info": "Traceback (most recent call last): ..."}
```

### Fields
| Field | Description |
|-------|-------------|
| `asctime` | ISO timestamp with microseconds |
| `name` | Logger name (e.g., `backend.app.tasks`) |
| `levelname` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
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
{"asctime": "2024-01-15T10:30:00.123456", "name": "backend.app.tasks", "levelname": "INFO", "message": "process_audio_to_hls Task is starting..."}
{"asctime": "2024-01-15T10:30:05.234567", "name": "backend.app.tasks", "levelname": "INFO", "message": "Extracted acoustic vector and duration for clip abc-123"}
{"asctime": "2024-01-15T10:30:15.345678", "name": "backend.app.tasks", "levelname": "INFO", "message": "Extracted keywords for clip abc-123: [('comedy', 0.9), ('storytelling', 0.8)]"}
{"asctime": "2024-01-15T10:30:25.456789", "name": "backend.app.tasks", "levelname": "INFO", "message": "Uploaded HLS files for clip abc-123"}
```

### Error with Traceback
```json
{"asctime": "2024-01-15T10:30:00.123456", "name": "backend.app.tasks", "levelname": "ERROR", "message": "FFmpeg Error: Invalid data found when processing input", "exc_info": "Traceback (most recent call last):\n  File \"/app/backend/app/tasks.py\", line 300, in process_audio_to_hls\n    subprocess.run(command, check=True, ...)\n  File \"/usr/lib/python3.11/subprocess.py\", line 571, in run\n    raise CalledProcessError(...)\nsubprocess.CalledProcessError: Command '...' returned non-zero exit status 1."}
```

### Feed Refill
```json
{"asctime": "2024-01-15T10:30:00.123456", "name": "backend.app.tasks", "levelname": "INFO", "message": "Added 40 composite-ranked clips."}
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

## Current Gaps

| Gap | Impact | Fix |
|-----|--------|-----|
| No correlation IDs | Can't trace request across services | Add middleware to inject `request_id` |
| No structured context | Can't filter by user/clip | Add `extra` dict with IDs |
| No sampling | High-volume logs expensive | Add rate-limited logging for frequent events |
| No log retention | Docker stdout lost on restart | Ship to Loki/ELK |
| No audit trail | Security events not tracked | Separate audit logger |

---

## Recommended Improvements

### 1. Request Correlation Middleware
```python
# middleware/correlation.py
import uuid

class CorrelationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        request.correlation_id = request.headers.get('X-Correlation-ID', str(uuid.uuid4()))
        # Add to logging context
        logging.getLogger().addFilter(CorrelationFilter(request.correlation_id))
        response = self.get_response(request)
        response['X-Correlation-ID'] = request.correlation_id
        return response

class CorrelationFilter(logging.Filter):
    def __init__(self, correlation_id):
        self.correlation_id = correlation_id
    
    def filter(self, record):
        record.correlation_id = self.correlation_id
        return True
```

### 2. Structured Context in Tasks
```python
# tasks.py
def process_audio_to_hls(self, clip_id):
    logger = logging.getLogger(__name__)
    context = {'clip_id': clip_id, 'task_id': self.request.id}
    
    logger.info("Task started", extra=context)
    # ... processing ...
    logger.info("Acoustic vector extracted", extra={**context, 'duration_ms': duration})
    # ... 
    logger.info("Task completed", extra=context)
```

---

*Source: `backend/EchoFlow/settings.py:341-378`, `backend/app/tasks.py`*