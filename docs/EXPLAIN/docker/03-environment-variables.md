# Environment Variables

## Required Variables

| Variable | Purpose | Example | Required |
|----------|---------|---------|----------|
| `DJANGO_SECRET_KEY` | Django signing, JWT signing | `django-insecure-xyz...` | ✅ |
| `DATABASE_URL` | PostgreSQL connection | `postgres://user:pass@db:5432/echoflow_db` | ✅ |
| `REDIS_URL` | Redis connection | `redis://redis:6379/1` | ✅ |
| `FIELD_ENCRYPTION_KEY` | Fernet key for email encryption | `gAAAAABl7...` | ✅ |
| `HF_TOKEN` | HuggingFace token (build secret) | `hf_xxx` | Build only |
| `AWS_ACCESS_KEY_ID` | S3/MinIO access key | `echoflow-dev` | ✅ |
| `AWS_SECRET_ACCESS_KEY` | S3/MinIO secret key | `echoflow-dev-secret` | ✅ |
| `AWS_STORAGE_BUCKET_NAME` | S3 bucket name | `echoflow-media` | ✅ |
| `AWS_S3_ENDPOINT_URL` | Internal S3 endpoint | `http://minio:9000` | ✅ |
| `PUBLIC_MEDIA_ENDPOINT_URL` | Browser-facing media endpoint | `http://localhost:9000` | ✅ |

---

## Optional Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DJANGO_DEBUG` | `False` | Django DEBUG mode |
| `DJANGO_ALLOWED_HOSTS` | `localhost` | Comma-separated allowed hosts |
| `DJANGO_CORS_ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:5173` | CORS origins |
| `DJANGO_CORS_ALL` | `False` | Allow all origins (unsafe) |
| `GUNICORN_WORKERS` | `4` | Gunicorn worker processes |
| `GUNICORN_THREADS` | `4` | Gunicorn threads per worker |
| `GUNICORN_LOG_LEVEL` | `info` | Gunicorn log level |
| `AWS_S3_REGION_NAME` | `auto` | S3 region |
| `AWS_S3_QUERYSTRING_EXPIRE` | `3600` | Signed URL TTL (seconds) |
| `LOG_LEVEL` | `INFO` | Root logger level |
| `DJANGO_LOG_LEVEL` | `INFO` | Django logger level |
| `APP_LOG_LEVEL` | `INFO` | App logger level |
| `CELERY_LOG_LEVEL` | `INFO` | Celery logger level |
| `SCRAPER_USER_AGENT` | `EchoFlowScraper/1.0` | Scraper UA |
| `SCRAPER_CONTACT_EMAIL` | `` | Scraper contact |
| `SCRAPER_MAX_DOWNLOADS_PER_MIN` | `30` | Scraper rate limit |
| `SCRAPER_ALLOW_LICENSES` | `CC0,CC-BY,CC-BY-SA,CC-BY-NC` | Allowed licenses |
| `SCRAPER_DEFAULT_CLIP_SECONDS` | `300` | Default clip length |
| `SCRAPER_KAGGLE_LOCAL_PATH` | `` | Kaggle local path |
| `FREESOUND_API_KEY` | `` | Freesound API key |
| `OPENAI_API_KEY` | `` | OpenAI API key (unused) |
| `SEED_AUTH_TOKEN` | `` | Seed script auth token |

---

## Dev vs Prod

### Development (`.env`)
```bash
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
DJANGO_CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
DJANGO_CORS_ALL=False
GUNICORN_WORKERS=4
GUNICORN_THREADS=4
AWS_S3_ENDPOINT_URL=http://minio:9000
PUBLIC_MEDIA_ENDPOINT_URL=http://localhost:9000
```

### Production
```bash
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=api.echoflow.com,www.echoflow.com
DJANGO_CORS_ALLOWED_ORIGINS=https://app.echoflow.com,https://www.echoflow.com
DJANGO_CORS_ALL=False
GUNICORN_WORKERS=8
GUNICORN_THREADS=4
AWS_S3_ENDPOINT_URL=https://s3.us-east-1.amazonaws.com  # or VPC endpoint
PUBLIC_MEDIA_ENDPOINT_URL=https://cdn.echoflow.com  # CloudFront
# Real AWS credentials
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_STORAGE_BUCKET_NAME=echoflow-prod-media
AWS_S3_REGION_NAME=us-east-1
```

---

## Docker Compose Variable Substitution

```yaml
# docker-compose.yml uses ${VAR} syntax
environment:
  - DATABASE_URL=${DATABASE_URL}
  - REDIS_URL=${REDIS_URL}
  - DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}
  - DJANGO_DEBUG=${DJANGO_DEBUG:-False}
  - DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS:-localhost}
  - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-echoflow-dev}
  - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-echoflow-dev-secret}
```

**Defaults in Compose:** `${VAR:-default}` provides fallback.

---

## Settings.py Variable Usage

```python
# settings.py
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is not set.")

DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() == 'true'

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')

CORS_ALLOWED_ORIGINS = os.environ.get('DJANGO_CORS_ALLOWED_ORIGINS', 'http://localhost:3000,http://localhost:5173').split(',')

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")

FIELD_ENCRYPTION_KEY = os.environ.get('FIELD_ENCRYPTION_KEY')
if not FIELD_ENCRYPTION_KEY:
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is missing.")

# S3/MinIO
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["AWS_STORAGE_BUCKET_NAME"],
            "endpoint_url": os.getenv("AWS_S3_ENDPOINT_URL") or None,
            "access_key": os.environ["AWS_ACCESS_KEY_ID"],
            "secret_key": os.environ["AWS_SECRET_ACCESS_KEY"],
            ...
        }
    }
}

PUBLIC_MEDIA_ENDPOINT_URL = os.getenv("PUBLIC_MEDIA_ENDPOINT_URL") or os.getenv("AWS_S3_ENDPOINT_URL") or None
```

---

## Build-Time Variables (Dockerfile)

### Build Args
```dockerfile
# Dockerfile
ARG TAG=latest
# Used in: docker compose build --build-arg TAG=dev
```

### Build Secrets (Not Args!)
```dockerfile
# HF_TOKEN as BuildKit secret (NEVER as ARG)
RUN --mount=type=secret,id=hf_token \
    set -eu; \
    if [ -s /run/secrets/hf_token ]; then \
        export HF_TOKEN="$(cat /run/secrets/hf_token)"; \
    fi; \
    python -c "..."
```

---

## Runtime Variables (Container)

### Media Worker Offline Mode
```yaml
# docker-compose.yml celery_media
environment:
  HF_HOME: /home/appuser/.cache/huggingface
  HF_HUB_OFFLINE: "1"
  TRANSFORMERS_OFFLINE: "1"
```

### Scraper Configuration
```python
SCRAPER_MAX_DOWNLOADS_PER_MIN = int(os.getenv('SCRAPER_MAX_DOWNLOADS_PER_MIN', '30'))
SCRAPER_ALLOW_LICENSES = os.getenv('SCRAPER_ALLOW_LICENSES', 'CC0,CC-BY,CC-BY-SA,CC-BY-NC').split(',')
SCRAPER_DEFAULT_CLIP_SECONDS = int(os.getenv('SCRAPER_DEFAULT_CLIP_SECONDS', '300'))
FREESOUND_API_KEY = os.getenv('FREESOUND_API_KEY', '')
SCRAPER_KAGGLE_LOCAL_PATH = os.getenv('SCRAPER_KAGGLE_LOCAL_PATH', '')
```

---

## Secrets Management

### Development
```bash
# .env file (gitignored)
DJANGO_SECRET_KEY=...
FIELD_ENCRYPTION_KEY=...
HF_TOKEN=hf_xxx
```

### Production (Recommended)
```bash
# AWS Secrets Manager
# Docker secrets
# Kubernetes secrets
# HashiCorp Vault

# Example: Docker secrets
secrets:
  django_secret_key:
    external: true
  field_encryption_key:
    external: true
```

---

## Variable Validation

### Fail-Fast Pattern
```python
# settings.py
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is not set.")

FIELD_ENCRYPTION_KEY = os.environ.get('FIELD_ENCRYPTION_KEY')
if not FIELD_ENCRYPTION_KEY:
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is missing.")
```

### Optional with Defaults
```python
DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() == 'true'
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")
```

---

## Complete Variable Reference

| Variable | Used In | Fail-Fast | Default |
|----------|---------|-----------|---------|
| `DJANGO_SECRET_KEY` | settings.py, JWT | ✅ | — |
| `FIELD_ENCRYPTION_KEY` | models.py (Fernet) | ✅ | — |
| `DATABASE_URL` | settings.py (dj_database_url) | ✅ | — |
| `REDIS_URL` | settings.py (cache, Celery) | ❌ | `redis://localhost:6379/1` |
| `DJANGO_DEBUG` | settings.py | ❌ | `False` |
| `DJANGO_ALLOWED_HOSTS` | settings.py | ❌ | `localhost` |
| `DJANGO_CORS_ALLOWED_ORIGINS` | settings.py | ❌ | `localhost:3000,localhost:5173` |
| `AWS_ACCESS_KEY_ID` | settings.py (S3), MinIO | ✅ | `echoflow-dev` |
| `AWS_SECRET_ACCESS_KEY` | settings.py (S3), MinIO | ✅ | `echoflow-dev-secret` |
| `AWS_STORAGE_BUCKET_NAME` | settings.py (S3), MinIO init | ✅ | `echoflow-media` |
| `AWS_S3_ENDPOINT_URL` | settings.py (S3), MinIO | ❌ | `http://minio:9000` |
| `PUBLIC_MEDIA_ENDPOINT_URL` | media_urls.py | ❌ | `AWS_S3_ENDPOINT_URL` |
| `AWS_S3_REGION_NAME` | settings.py (S3) | ❌ | `auto` |
| `AWS_S3_QUERYSTRING_EXPIRE` | settings.py (S3) | ❌ | `3600` |
| `HF_TOKEN` | Dockerfile (build secret) | Build only | — |
| `FREESOUND_API_KEY` | freesound.py | ❌ | `` |
| `SCRAPER_KAGGLE_LOCAL_PATH` | kaggle.py | ❌ | `` |
| `OPENAI_API_KEY` | tasks.py (OpenAI pipeline) | ❌ | `` |
| `SEED_AUTH_TOKEN` | seed_db.py | ❌ | `` |

---

*Source: `backend/EchoFlow/settings.py`, `backend/app/models.py`, `backend/app/media_urls.py`, `docker-compose.yml`, `Dockerfile`, `.env.example`*