# EchoFlow Deployment Topology

## Docker Compose Services (7 Services)

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         docker-compose.yml Services                         │
├──────────────┬──────────────────────┬──────────────────────────────────────┤
│ Service      │ Image/Build          │ Purpose                              │
├──────────────┼──────────────────────┼──────────────────────────────────────┤
│ db           │ pgvector/pgvector:pg16│ PostgreSQL 16 + pgvector extension  │
│ redis        │ redis:7-alpine        │ Redis 7 (broker + cache + sessions) │
│ minio        │ minio/minio:RELEASE...│ S3-compatible object storage         │
│ minio-init   │ minio/mc:RELEASE...   │ One-shot bucket creation + ACL       │
│ web          │ echoflow-api (target:api)│ Gunicorn + Django API             │
│ celery       │ echoflow-api (target:api)│ Default Celery worker             │
│ celery_feed  │ echoflow-api (target:api)│ Feed refill worker (-Q fast_feed) │
│ celery_media │ echoflow-media (target:media)│ Heavy media/AI worker        │
│ celery_beat  │ echoflow-api (target:api)│ Celery Beat scheduler             │
└──────────────┴──────────────────────┴──────────────────────────────────────┘
```

## Service Details

### 1. Database (`db`)

```yaml
image: pgvector/pgvector:pg16
environment:
  POSTGRES_DB: ${DB_NAME}
  POSTGRES_USER: ${DB_USER}
  POSTGRES_PASSWORD: ${DB_PASSWORD}
volumes:
  - postgres_data:/var/lib/postgresql/data
ports: ["5432:5432"]
healthcheck: pg_isready -U ${DB_USER} -d ${DB_NAME}
deploy:
  resources:
    limits: { cpus: '2', memory: 2G, pids: 500 }
    reservations: { cpus: '0.5', memory: 512M }
ulimits: { nofile: { soft: 65536, hard: 65536 } }
```

**Key behaviors:**
- pgvector extension auto-enabled via migration `0001_initial.py:28-32`
- `conn_max_age=600` in Django keeps persistent connections
- **No PgBouncer** — direct connections from all workers
- Healthcheck ensures dependent services wait for DB readiness

### 2. Redis (`redis`)

```yaml
image: redis:7-alpine
command: redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru
volumes:
  - redis_data:/data
ports: ["6379:6379"]
healthcheck: redis-cli ping
deploy:
  resources:
    limits: { cpus: '1', memory: 1G, pids: 300 }
    reservations: { cpus: '0.25', memory: 128M }
```

**Key behaviors:**
- Single Redis instance serves **both** Celery broker AND Django cache
- `allkeys-lru` eviction — feed queues can be evicted under memory pressure
- **No split** between broker/cache (architecture audit P0 item)
- AOF persistence enabled (`--appendonly yes`)

### 3. MinIO (`minio`)

```yaml
image: minio/minio:RELEASE.2025-09-07T16-13-09Z
command: server /data --console-address ":9001"
environment:
  MINIO_ROOT_USER: ${AWS_ACCESS_KEY_ID:-echoflow-dev}
  MINIO_ROOT_PASSWORD: ${AWS_SECRET_ACCESS_KEY:-echoflow-dev-secret}
  # CORS for HLS playback from browser
  MINIO_CORS_ALLOW_ORIGIN: "*"
  MINIO_CORS_ALLOW_METHODS: "GET,PUT,POST,DELETE,OPTIONS"
  MINIO_CORS_ALLOW_HEADERS: "Origin,Accept,Content-Type,Authorization,Range"
  MINIO_CORS_EXPOSE_HEADERS: "Date,Content-Length,Content-Range,Accept-Ranges"
  MINIO_CORS_MAX_AGE: "3600"
volumes:
  - minio_data:/data
ports: ["9000:9000", "9001:9001"]
healthcheck: mc ready local
```

**Key behaviors:**
- S3 API on port 9000, console on 9001
- CORS configured for browser HLS playback (Range headers critical)
- `minio_data` volume **only** mounted to MinIO — NOT shared with app containers

### 4. MinIO Init (`minio-init`)

```yaml
image: minio/mc:RELEASE.2025-08-13T08-35-41Z
depends_on:
  minio: { condition: service_healthy }
entrypoint: >
  sh -c "
    mc alias set local http://minio:9000 ${AWS_ACCESS_KEY_ID} ${AWS_SECRET_ACCESS_KEY} &&
    mc mb --ignore-existing local/${AWS_STORAGE_BUCKET_NAME:-echoflow-media} &&
    mc anonymous set download local/${AWS_STORAGE_BUCKET_NAME:-echoflow-media}/hls
  "
```

**Critical design decision:** Makes `hls/` prefix **public-read** (anonymous download) because:
- HLS is multi-file protocol (master → variants → segments via relative paths)
- Signed URL query string **does not carry forward** on relative references (RFC 3986)
- One signed URL cannot authorize a stream of dozens of objects
- Original uploads (`uploads/`) remain private, signed URLs generated on demand

See `storage/02-hls-playback.md` and `docs/minio-s3-architecture.md` for full analysis.

### 5. Web/API (`web`)

```yaml
image: devansh1012007/echoflow-api:${TAG:-latest}
build: { context: ., dockerfile: Dockerfile, target: api }
working_dir: /app
command: >
  sh -c "set -e && python wait_for_db.py &&
         python manage.py migrate &&
         python manage.py collectstatic --noinput &&
         gunicorn -c gunicorn.conf.py backend.EchoFlow.wsgi:application"
volumes: [".:/app"]
ports: ["8005:8000"]
depends_on:
  db: { condition: service_healthy }
  redis: { condition: service_healthy }
  minio-init: { condition: service_completed_successfully }
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/')"]
  interval: 30s, timeout: 10s, start_period: 90s, retries: 3
deploy:
  resources:
    limits: { cpus: '2', memory: 1G, pids: 500 }
    reservations: { cpus: '0.5', memory: 256M }
```

**Startup sequence:**
1. `wait_for_db.py` — polls PostgreSQL with exponential backoff (120 attempts, max 30s delay)
2. `manage.py migrate` — runs migrations
3. `collectstatic` — WhiteNoise static files
4. `gunicorn` — 4 workers × 4 threads (configurable via `GUNICORN_WORKERS/THREADS`)

**gunicorn.conf.py critical settings:**
- `preload_app = True` — loads app in master before fork (saves memory)
- `post_fork` hook — **closes ALL Django DB connections AND Celery Redis connections** after fork
  - **Why:** `EchoFlow/__init__.py` imports Celery app at module load → creates Redis connections in master
  - Without reset, forked workers inherit stale connections → "connection reset" errors
- `max_requests = 1000` + jitter — worker recycling prevents memory leaks

### 6. Default Celery Worker (`celery`)

```yaml
image: devansh1012007/echoflow-api:${TAG:-latest}
build: { target: api }
command: >
  sh -c "set -e && python wait_for_db.py &&
         celery -A backend.EchoFlow worker --loglevel=info"
healthcheck:
  test: ["CMD-SHELL", "celery -A backend.EchoFlow inspect ping -d \"celery@$(hostname)\" --timeout=10 || exit 1"]
```

**Queue:** `celery` (default)
**Tasks:** Scraping, general async tasks
**Concurrency:** Default (prefork, CPU count)

### 7. Feed Worker (`celery_feed`)

```yaml
command: >
  sh -c "set -e && python wait_for_db.py &&
         celery -A backend.EchoFlow worker -Q fast_feed --concurrency=4 --loglevel=info"
```

**Queue:** `fast_feed`
**Tasks:** `refill_user_feed` only
**Concurrency:** 4 (explicit, I/O-bound vector queries)

### 8. Media Worker (`celery_media`)

```yaml
image: devansh1012007/echoflow-media:${TAG:-latest}
build:
  target: media
  secrets: [hf_token]  # BuildKit secret for HF_TOKEN
command: >
  sh -c "set -e && python wait_for_db.py &&
         celery -A backend.EchoFlow worker -Q heavy_media --pool=solo --loglevel=info"
environment:
  HF_HOME: /home/appuser/.cache/huggingface
  HF_HUB_OFFLINE: "1"
  TRANSFORMERS_OFFLINE: "1"
deploy:
  resources:
    limits: { cpus: '4', memory: 1G, pids: 200 }
    reservations: { cpus: '2', memory: 256M }
healthcheck:
  test: ["CMD", "celery", "-A", "backend.EchoFlow", "inspect", "ping", "-d", "celery@$(hostname)", "--timeout=10"]
```

**Queue:** `heavy_media`
**Tasks:** `process_audio_to_hls` only
**Pool:** `solo` (single process — ML models are memory-heavy, no forking)
**Models:** Baked at build time via BuildKit secret `HF_TOKEN`
**Offline mode:** `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` — no runtime downloads

### 9. Celery Beat (`celery_beat`)

```yaml
build: { target: api }
command: >
  sh -c "set -e && python wait_for_db.py &&
         celery -A backend.EchoFlow beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler"
healthcheck: { disable: true }  # Image's HTTP probe would fail (no gunicorn)
```

**Scheduler:** `django_celery_beat` (DatabaseScheduler — periodic tasks in DB)
**Tasks:**
- `update_global_metrics` — every 5 minutes
- `evolve_long_term_user_baselines` — every hour

## Network Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Network                            │
│  (default bridge network created by Compose)                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  web ◄──────────────────────► db (5432)                         │
│    │                          ▲                                  │
│    │                          │                                  │
│    ▼                          │                                  │
│  redis ◄─────────────────────┘                                  │
│    │                                                            │
│    ▼                                                            │
│  minio (9000) ◄──────────────► minio-init                       │
│    │                                                            │
│    ▼                                                            │
│  celery, celery_feed, celery_media, celery_beat ──► redis, db   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

External Access:
  localhost:8005  → web (gunicorn on 8000)
  localhost:9000  → MinIO S3 API
  localhost:9001  → MinIO Console
  localhost:5432  → PostgreSQL
  localhost:6379  → Redis
```

## Endpoint Separation (Critical for HLS)

| Variable | Container Value | Browser Value | Purpose |
|----------|----------------|---------------|---------|
| `AWS_S3_ENDPOINT_URL` | `http://minio:9000` | N/A | Internal container-to-MinIO |
| `PUBLIC_MEDIA_ENDPOINT_URL` | N/A | `http://localhost:9000` | Browser-accessible HLS URLs |

**Why separate?**
- Containers resolve `minio:9000` via Docker DNS
- Browsers on host need `localhost:9000` (published port)
- In production: `AWS_S3_ENDPOINT_URL` = VPC endpoint, `PUBLIC_MEDIA_ENDPOINT_URL` = CDN domain

## Volume Architecture

```
Named Volumes (persistent):
├── postgres_data  → /var/lib/postgresql/data  (db only)
├── redis_data     → /data                      (redis only)
└── minio_data     → /data                      (minio only)

Bind Mounts (dev only):
└── .:/app  → web, celery, celery_feed, celery_beat, celery_media

NO shared media volume — every container reaches object storage via S3 API
```

**Key principle:** No container assumes shared filesystem. All media I/O goes through S3 API.

## Resource Allocation Summary

| Service | CPU Limit | Memory Limit | PIDs | Key Config |
|---------|-----------|--------------|------|------------|
| db | 2 | 2G | 500 | pgvector, conn_max_age=600 |
| redis | 1 | 1G | 300 | maxmemory 512mb, allkeys-lru |
| minio | (default) | (default) | — | CORS for HLS |
| web | 2 | 1G | 500 | 4 workers × 4 threads, preload_app |
| celery | 1 | 1G | 300 | default queue |
| celery_feed | 1 | 1G | 300 | -Q fast_feed, concurrency=4 |
| celery_media | 4 | 1G | 200 | -Q heavy_media, --pool=solo |
| celery_beat | 0.5 | 256M | 200 | DatabaseScheduler |

## Dev vs Prod Differences

| Aspect | Development (Compose) | Production Target |
|--------|----------------------|-------------------|
| Media storage | MinIO (local S3) | AWS S3 / Cloudflare R2 |
| CDN | None | CloudFront / Cloudflare |
| DB | Single PostgreSQL | RDS Aurora + read replicas + PgBouncer |
| Redis | Single instance | ElastiCache (split broker/cache) |
| Message queue | Redis | RabbitMQ → Kafka |
| Workers | Same host | Separate instance groups (CPU vs GPU) |
| Build | Multi-stage Dockerfile | Same, pushed to ECR/GHCR |
| Secrets | .env file | AWS Secrets Manager / Vault |

## Health Check Strategy

| Service | Probe Type | Implementation |
|---------|------------|----------------|
| db | TCP/pg_isready | `pg_isready -U $DB_USER -d $DB_NAME` |
| redis | TCP/redis-cli | `redis-cli ping` |
| minio | Custom/mc | `mc ready local` |
| minio-init | Exit code | Completes successfully |
| web | HTTP /health/ | `urllib.request.urlopen('http://localhost:8000/health/')` |
| celery* | Celery inspect | `celery -A backend.EchoFlow inspect ping -d "celery@$(hostname)"` |
| celery_beat | **Disabled** | Image's HTTP probe would fail (no gunicorn) |

---

*Source: `docker-compose.yml`, `Dockerfile`, `gunicorn.conf.py`, `wait_for_db.py`, `backend/EchoFlow/settings.py`*