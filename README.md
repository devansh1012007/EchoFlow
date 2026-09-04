# EchoFlow

**TikTok for your ears.** An audio-first, short-form content platform — comedy roasts, song snippets, science bites, quotes, and motivation — designed for hands-free, screen-free consumption while you walk, work, or chill.

Built on a production-oriented Django backend with a vector-based recommendation engine, async AI media processing, HLS streaming, and a license-aware audio ingestion pipeline.

---

## Why EchoFlow?

Modern short-form content is trapped on screens. Reels, Shorts, and TikToks demand visual attention — which is impossible while commuting, exercising, cooking, or driving.

EchoFlow inverts that constraint: it's a short-form feed you **listen** to, not watch. Auto-playing audio reels with earphone-skip controls give the "infinite scroll" dopamine loop without requiring a single glance at a screen. It reclaims dead time and turns it into entertainment, learning, and discovery.

## Vision

EchoFlow is designed to evolve from a personalization engine into a full audio-native social platform:

- **Mood-aware recommendation system** — real-time user vectors that shift with recent listening behavior, not static profiles
- **Social graph discovery** — shares, follows, and friend-inbox that surface content through networks rather than only algorithms
- **Creator ecosystem** — uploads, licensing provenance, and attribution as first-class concepts
- **Global content library** — scalable ingestion from openly-licensed audio archives to seed a massive catalog
- **Path to scale** — architecture deliberately staged so the monolith can decompose into services (feed, identity, media, engagement) as traffic grows

## Key Features

### AI-Powered Media Pipeline
When an audio file is uploaded, a Celery task (`process_audio_to_hls`) runs automatically:

| Stage | Technology | Output |
|-------|-----------|--------|
| Acoustic feature extraction | `librosa` (MFCC + chroma + mel spectrogram) | 128-dim acoustic vector |
| Transcription | `faster-whisper` | Text transcript |
| Semantic embedding | `sentence-transformers` (`all-MiniLM-L6-v2`) | 384-dim semantic vector |
| Tag extraction | `keybert` | Auto-generated genre tags |
| HLS transcoding | `ffmpeg` (AAC, 192/128/64 kbps ABR) | Adaptive HLS stream with `master.m3u8` |

### Vector-Based Recommendation Engine
- **Hybrid similarity ranking** in PostgreSQL via `pgvector` (HNSW indexes): cosine distance over semantic + acoustic vectors
- **Composite scoring** — 45% vector similarity + 30% avg completion rate + 25% engagement velocity
- **Time-decayed user vectors** — 80% exploit / 20% explore feed mixing, plus a social "follow wedge"
- **Cold-start onboarding** — tag-based vector bootstrapping (`/tags/initialize/`) seeds a new user's baseline before any interactions exist
- **Redis-backed fast feed** — pre-computed per-user queues for low-latency playback, refilled asynchronously

### Social & Engagement
- Likes, skips, view telemetry with completion-rate tracking (`/interactions/`)
- Peer-to-peer sharing with an inbox and unread counts (`/share/`)
- Nested threaded comments with cursor pagination (`/comments/`)
- Follow / unfollow graph (`/follow/`) and public + private profiles (`/profile/`)

### License-Aware Audio Scraping
A `robots.txt`-respecting, rate-limited scraper ingests openly-licensed audio from multiple archives, normalizes/trims it, and feeds it through the same AI pipeline (see [Audio Scraping](#audio-scraping--ingestion)).

## Architecture / Tech Stack

```
                        ┌──────────────────────────────────────────┐
                        │  nginx 1.27  (TLS terminator)           │
                        │  :80 → :443 redirect                     │
                        │  :443 (HTTPS) → Django                   │
                        │  :9443 (HTTPS) → MinIO (HLS segments)    │
                        └──────┬────────────────────┬──────────────┘
                               │ HTTP               │ HTTP
                 ┌─────────────▼─────────┐ ┌────────▼─────────────┐
                 │  gunicorn (Django)   │ │   MinIO (S3)         │
                 │  + DRF + JWT         │ │  hls/  public-read   │
                 │  + Prometheus /metrics│ │  uploads/ private    │
                 └──────┬────────┬──────┘ └──────────────────────┘
                        │        │
              ┌──────────▼───┐ ┌──▼──────────────────┐
              │ PostgreSQL 16│ │   Redis 7            │
              │  + pgvector  │ │  • cache (django-redis)│
              │  (clips,     │ │  • user feed queues   │
              │   users,     │ │  • Celery broker      │
              │   HNSW idx)  │ │  • telemetry stream   │
              └──────────────┘ └──────────┬──────────┘
                                          │
              ┌───────────────────────────▼────────────────────┐
              │                Celery Workers                   │
              │  ┌─────────────┬──────────────┬──────────────┐  │
              │  │ default     │ fast_feed    │ heavy_media  │  │
              │  │ worker      │ (feed refill)│ (HLS / AI)   │  │
              │  └─────────────┴──────────────┴──────────────┘  │
              │  Celery Beat ── periodic jobs (metrics,         │
              │  vector evolution, counter flush, orphan cleanup)│
              └─────────────────────────────────────────────────┘
```

**Backend:** Django 5 / DRF · **API auth:** JWT (SimpleJWT, access + refresh) · **DB:** PostgreSQL 16 + pgvector (HNSW ANN indexes) · **Cache/Queue:** Redis 7 · **Async:** Celery + Celery Beat · **Media:** FFmpeg HLS transcoding · **ML:** faster-whisper, sentence-transformers, librosa, keybert · **Serving:** gunicorn, WhiteNoise · **TLS:** nginx 1.27 (`:80` redirect, `:443` Django, `:9443` MinIO) · **Observability:** Prometheus + Grafana (primary), Sentry (errors, ready-to-configure)

### Data Flow
1. **TLS termination** — every client request enters through `nginx:443`, which terminates TLS, sets `X-Forwarded-Proto: https`, and forwards plain HTTP to gunicorn (`web:8000`). `nginx:9443` serves browser HLS segments over HTTPS (mixed-content safety).
2. **Upload** → `POST /clips/` creates an `AudioClip` in `processing` status and enqueues `process_audio_to_hls` via `transaction.on_commit`.
3. **Process** → Celery (heavy_media queue) extracts acoustic features, transcribes, embeds, tags, and transcodes to ABR HLS. Status flips to `ready`.
4. **Serve feed** → `GET /feed/` pops clip IDs from the user's Redis feed queue; the queue is refilled by the `fast_feed` worker using vector/composite scoring.
5. **Engage** → likes/shares/telemetry are recorded as `UserInteraction` rows, incrementing denormalized counters via `F()` expressions.
6. **Evolve** → Celery Beat periodically recalculates `engagement_velocity`, `avg_completion_rate`, and users' long-term preference vectors.

## Backend Setup

**Docker is the only supported way to run EchoFlow locally.** The `Dockerfile` and `docker-compose.yml` provision every dependency (Postgres+pgvector, Redis, MinIO, all Celery queues, ffmpeg, Python 3.11, ML libs) in a single `docker compose up --build`. There is no bare-metal install path.

```bash
# 1. Ensure .env exists and is populated (DB_*, REDIS_URL, DJANGO_SECRET_KEY, HF_TOKEN, ...)
#    docker compose reads ${VAR} substitutions from your .env file.

# 2. Build and start all services (14: db, pgbouncer, redis_broker, redis_cache, minio, minio-init,
#    nginx, web, celery, celery_feed, celery_media, celery_beat, prometheus, grafana)
docker compose up --build

# 3. View logs for a specific service (e.g. media worker)
docker compose logs -f celery_media

# 4. Run a management command inside the web container
docker compose exec web python manage.py migrate

# 5. Verify HTTPS is live (the nginx terminator runs on :80, :443, :9443)
curl -kI https://localhost/health/

# 6. Verify Prometheus is scraping (web target should be UP)
open http://localhost:9090/targets

# 7. Verify Grafana dashboards (admin / ${GRAFANA_ADMIN_PASSWORD})
open http://localhost:3000

# 8. Tear everything down
docker compose down
```

## Audio Scraping / Ingestion

EchoFlow includes a license-aware scraper for seeding the catalog from public, openly-licensed archives. It respects `robots.txt`, enforces per-host rate limits, validates content type, enforces a max download size, and normalizes/trims audio via pydub.

**Supported sources** (from `backend/app/scrapers/sources/`):

| Source | Requirement | License enforcement |
|--------|-------------|---------------------|
| `wikimedia` | None | Filters to `audio/*` MIME |
| `internet_archive` | None | Allowed-license filter |
| `freesound` | `FREESOUND_API_KEY` env var | Filters to allowed licenses |
| `kaggle` | `SCRAPER_KAGGLE_LOCAL_PATH` | Local `file://` ingestion |

Allowed licenses are configurable via `SCRAPER_ALLOW_LICENSES` (default: `CC0, CC-BY, CC-BY-SA, CC-BY-NC`).

```bash
# Import 3 clips from Wikimedia Commons, trimmed to 30s
docker compose exec web python manage.py scrape_audio --source=wikimedia --limit=3 --clip-length=30

# Same ingestion, but as a Celery task (enqueue from inside the web container)
docker compose exec web python -c "from backend.app.tasks import scrape_and_import; scrape_and_import.delay('internet_archive', limit=5)"
```

Scraped clips are stored under `media/audio_scraper/{source}/YYYY/MM/DD/`, provenance/license metadata is attached, and each clip is then processed through the full AI + HLS pipeline automatically.

FFmpeg **must** be installed for scraping to work (used by audio normalization and HLS generation). See [Key Features](#key-features) for the full pipeline.

## Project Structure

```
EchoFlow/
├── .github/workflows/          # CI: django.yml (tests/migrations/static), docker-image.yml (image build+push), codeql.yml
├── backend/                    # Django application
│   ├── EchoFlow/               # Project package — don't confuse with the app package below
│   │   ├── settings.py         # All config: DB, Redis, Celery, JWT, scraper, CORS
│   │   ├── urls.py             # Root URL config (admin + app routes)
│   │   ├── celery.py           # Celery app (Redis broker)
│   │   ├── health.py           # /health/ liveness and /ready/ readiness probes
│   │   ├── wsgi.py             # WSGI entrypoint (gunicorn target)
│   │   └── asgi.py
│   ├── app/                    # Core Django app
│   │   ├── models.py           # User, AudioClip, Comment, ShareEvent, UserInteraction
│   │   ├── views.py            # ViewSets: feed, uploads, interactions, comments, share, follow, tags, profile
│   │   ├── serializers.py      # DRF serializers (feed, upload, comment, auth, profiles)
│   │   ├── urls.py             # DRF router + JWT auth endpoints
│   │   ├── tasks.py            # Celery tasks (HLS/AI pipeline, feed refill, metrics, vector evolution, counter flush, orphan cleanup)
│   │   ├── db_routers.py       # Multi-DB routing (read-replica; auto-activates when READ_DATABASE_URL is set)
│   │   ├── services/           # Service layer: interactions, shares, follows, comments, uploads, feed_pool, counter_store, sentry, task_publisher
│   │   ├── scrapers/           # License-aware ingestion pipeline
│   │   │   ├── base.py         # robots.txt checker, rate limiter, HTTP session
│   │   │   ├── downloader.py   # Safe audio download (size/content-type guards)
│   │   │   ├── normalizer.py   # Trim + normalize audio (pydub)
│   │   │   ├── uploader.py     # Persist clip + provenance metadata
│   │   │   └── sources/        # wikimedia_commons, internet_archive, freesound, kaggle
│   │   ├── management/
│   │   │   └── commands/       # scrape_audio management command
│   │   ├── migrations/
│   │   └── tests/              # 20 pytest files (security, services, adversarial, integration, etc.)
│   ├── scripts/                # Seed scripts (seed_db.py, seed_db2.py)
│   └── staticfiles/            # collectstatic output (generated)
├── frontend/                   # Sample Vite/React client (HLS.js playback)
├── ai_ml/                      # ML pipeline experiments
│   ├── models/                 # Whisper / embedding / KeyBERT / acoustic wrappers
│   ├── pipelines/              # audio_ingest, cold_start, recommendation
│   └── eval/                   # feed_metrics, vector_quality
├── docs/                       # Architecture audits, EXPLAIN/, scaling analysis, deployment notes
├── docker/                     # nginx.conf, prometheus/, grafana/, certs/
├── docker-compose.yml          # 14 services (db, pgbouncer, redis_broker, redis_cache, minio, minio-init, nginx, web, celery, celery_feed, celery_media, celery_beat, prometheus, grafana)
├── Dockerfile                  # Multi-stage build → api + media images, offline wheelhouse installs
├── requirements.txt            # Aggregate for local dev (-r base + media)
├── requirements-base.txt       # Core Django/API deps (used by api image)
├── requirements-media.txt      # ML deps: faster-whisper, sentence-transformers, librosa, keybert (media image)
├── constraints.txt             # Shared version pins for both requirement sets
├── manage.py
├── gunicorn.conf.py            # preload_app + post_fork DB-connection reset
├── wait_for_db.py              # DB readiness poll for container startup
├── .env.example                # Template of required environment variables
└── SECURITY.md
```

Generated at runtime, never committed: `media/` (uploads + HLS output), `wheelhouse/` (offline pip wheels), root `staticfiles/`, `cache/`.

## Development / Future Vision

Natural next steps that follow directly from the existing architecture:

- **Media to object storage** — move HLS output and uploads from local disk to S3-compatible storage (django-storages + boto3 are already dependencies)
- **Real CDN front of MinIO** — the nginx config is ready for `Cache-Control: public, max-age=31536000, immutable` on `.ts` segments and `no-cache, must-revalidate` on `.m3u8` manifests; activation is via `PUBLIC_MEDIA_ENDPOINT_URL`. See [docs/EXPLAIN/storage/](docs/EXPLAIN/storage/) for the real-CDN activation playbook.
- **Read-replica activation** — set `READ_DATABASE_URL` and the `ReadRouter` auto-activates. Activation playbook: [docs/EXPLAIN/database/05-read-replica-design.md](docs/EXPLAIN/database/05-read-replica-design.md).
- **Real-time notifications** — push events for shares/inbox via websockets or a streaming broker
- **Recommendation at scale** — replace brute-force cosine scans with a candidate-generation + ANN tier as the catalog grows
- **Event-driven message bus** — migrate the Celery/Redis broker to a durable event stream for idempotent, retryable processing
- **Sentry production credentials** — DSN is env-gated; ship `SENTRY_DSN` and `SENTRY_ENV` in staging/prod `.env` to start capturing errors with full correlation_id tracing
- **Prometheus alert rules** — design proposed in [docs/EXPLAIN/observability/03-prometheus-grafana-design.md](docs/EXPLAIN/observability/03-prometheus-grafana-design.md); ship when an escalation path (Slack/on-call) is set
- **Rate limiting & throttling** — add DRF throttling and distributed rate limits at the API layer
- **CI/CD pipeline** — automated test + build + deploy stages for the Docker stack

---

## HTTPS / TLS
Every external request enters through an `nginx:1.27-alpine` reverse proxy that terminates TLS. Three listeners:
- **`:80`** — permanent 301 redirect to `https://` (no plaintext responses leave the edge).
- **`:443`** — TLS 1.2/1.3, HSTS 1-year+includeSubDomains+preload, proxies to gunicorn with `X-Forwarded-Proto: https` (so Django's `SECURE_SSL_REDIRECT` and `Secure` cookie flag activate).
- **`:9443`** — TLS in front of MinIO so `hls.js` can fetch segments over HTTPS without tripping the browser's mixed-content blocker.

The cert + key live in `docker/certs/` and are bind-mounted read-only into the nginx container — they never enter the app image, so cert renewal never requires a web-container rebuild. Dev uses a self-signed cert; production swaps in Let's Encrypt material and runs `docker compose exec nginx nginx -s reload`.

Full design: [docs/EXPLAIN/docker/05-https-tls-termination.md](docs/EXPLAIN/docker/05-https-tls-termination.md). Release checklist (12 sections covering certs, HSTS hardening, nginx hardening, runbooks, compliance): [docs/EXPLAIN/docker/06-https-production-readiness.md](docs/EXPLAIN/docker/06-https-production-readiness.md). Test coverage: 32 tests in `backend/app/tests/test_https_termination.py`.

---

**Stack at a glance:** `Django 5` · `DRF` · `PostgreSQL + pgvector` · `Redis` · `Celery` · `FFmpeg/HLS` · `faster-whisper` · `sentence-transformers` · `librosa` · `nginx 1.27 (TLS terminator)` · `Docker Compose` (14 services: db, pgbouncer, redis_broker, redis_cache, minio, minio-init, nginx, web, celery, celery_feed, celery_media, celery_beat, prometheus, grafana)

## Storage (MinIO / S3-compatible)
Derived HLS streams live in object storage (MinIO locally / S3 in prod) with the `hls/` prefix public-read for multi-file playback; original uploads (`uploads/`) stay private via signed URLs. The public `hls/` endpoint is served over HTTPS via nginx `:9443` (see [docs/EXPLAIN/docker/05-https-tls-termination.md](docs/EXPLAIN/docker/05-https-tls-termination.md)). Full architecture, failure analysis, and verification scripts are documented in `docs/minio-s3-architecture.md`.

## Observability
Two stacks are available after `docker compose up`:

- **Prometheus + Grafana (primary)** — Prometheus (`http://localhost:9090`) scrapes `web:8005/metrics/` every 15s. Two pre-built Grafana dashboards (admin at `http://localhost:3000`, default dashboards: `EchoFlow / 01-feed-and-suggestions` and `02-celery-health`) show p95 of the 4 application histograms, cache hit rate, and Celery task throughput. Activation: [docs/EXPLAIN/observability/04-prometheus-grafana-setup.md](docs/EXPLAIN/observability/04-prometheus-grafana-setup.md). Full design: [docs/EXPLAIN/observability/03-prometheus-grafana-design.md](docs/EXPLAIN/observability/03-prometheus-grafana-design.md).
- **Sentry (errors, ready-to-configure)** — `sentry-sdk[django,celery]==2.18.0` is installed; `init_sentry()` runs in each process's `App1Config.ready()`. Set `SENTRY_DSN` in `.env` to start capturing errors with the request's `correlation_id` (from Group B item 11) attached as a Sentry tag. `send_default_pii=False` — user IPs, cookies, and auth headers are NOT sent. PII-free by default.

The stdlib-based `scripts/observability_tui.py` is still available for quick spot-checks when no browser is handy.

## Testing
**Current count: 230 passed, 9 skipped, 0 failed** (9 skipped = 2 ffmpeg-environmental + 6 integration-on-SQLite + 1 live-nginx-environmental).

The test suite lives under `backend/app/tests/` (22 files) and uses `pytest` + `pytest-django`. Run via `docker compose exec web pytest …`. See [AGENTS.md](AGENTS.md) → "Running Tests" for the full command set.

Integration tests that need real Postgres + Redis + S3 (pgvector HNSW indexes, row-level locks, Redis Streams, concurrent transactions) are marked with `@pytest.mark.integration`. They auto-skip on the local SQLite + LocMem test environment and run in CI where the workflow provisions real services. Run them locally: `pytest backend/app/tests/ -m integration`.

## Docs
- [docs/EXPLAIN/](docs/EXPLAIN/) — 69 architecture deep-dives (data flow, frontend, backend, APIs, AI/ML, recommendations, Redis/Celery, media/HLS, object storage, scraping, auth, deployment, observability, testing, failure modes)
- [docs/backend-bug-fixs.md](docs/backend-bug-fixs.md) — audit + Group A/B/C/D/partial-issues fix reports (4 parts)
- [docs/EXPLAIN/decisions/partial-issues-completion-plan.md](docs/EXPLAIN/decisions/partial-issues-completion-plan.md) — plan + completion record for the 7 partially-addressed items (A1, A3, A5, A8, B13, B14, B17) + B19 docstring
