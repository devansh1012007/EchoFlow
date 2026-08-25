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
                        ┌───────────────────────────────┐
                        │       API Gateway (gunicorn)  │
                        │   Django + Django REST FW     │
                        └──────┬───────────┬────────────┘
                               │           │
                 ┌─────────────▼─────┐   ┌──▼──────────────────┐
                 │   PostgreSQL 16   │   │   Redis 7            │
                 │   + pgvector      │   │  • cache (django-redis)│
                 │  (clips, users,   │   │  • user feed queues   │
                 │   vectors, HNSW)  │   │  • Celery broker      │
                 └─────────────┬─────┘   └──┬──────────────────┘
                               │            │
                 ┌─────────────▼────────────▼───────────────────┐
                 │                Celery Workers                │
                 │  ┌─────────────┬──────────────┬────────────┐  │
                 │  │ default     │ fast_feed    │ heavy_media│  │
                 │  │ worker      │ (feed refill)│ (HLS/AI)   │  │
                 │  └─────────────┴──────────────┴────────────┘  │
                 │  Celery Beat ── periodic jobs (metrics,       │
                 │  long-term vector evolution)                  │
                 └───────────────────────────────────────────────┘
```

**Backend:** Django 5 / DRF · **API auth:** JWT (SimpleJWT, access + refresh) · **DB:** PostgreSQL 16 + pgvector (HNSW ANN indexes) · **Cache/Queue:** Redis 7 · **Async:** Celery + Celery Beat · **Media:** FFmpeg HLS transcoding · **ML:** faster-whisper, sentence-transformers, librosa, keybert · **Serving:** gunicorn, WhiteNoise

### Data Flow
1. **Upload** → `POST /clips/` creates an `AudioClip` in `processing` status and enqueues `process_audio_to_hls` via `transaction.on_commit`.
2. **Process** → Celery (heavy_media queue) extracts acoustic features, transcribes, embeds, tags, and transcodes to ABR HLS. Status flips to `ready`.
3. **Serve feed** → `GET /feed/` pops clip IDs from the user's Redis feed queue; the queue is refilled by the `fast_feed` worker using vector/composite scoring.
4. **Engage** → likes/shares/telemetry are recorded as `UserInteraction` rows, incrementing denormalized counters via `F()` expressions.
5. **Evolve** → Celery Beat periodically recalculates `engagement_velocity`, `avg_completion_rate`, and users' long-term preference vectors.

## Backend Setup Without Docker

### Prerequisites
- Python 3.11+
- PostgreSQL 16 with the `pgvector` extension
- Redis 7
- FFmpeg installed and on `PATH` (required for HLS transcoding and audio normalization)

### Steps

```bash
# 1. Clone & enter the repo
git clone https://github.com/devansh1012007/EchoFlow.git
cd EchoFlow

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

Create your environment file (a committed `.env.example`-style file is not present, so copy `.env` and edit it):

```bash
cp .env .env.local   # or edit .env directly
```

Required variables:

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Django signing key (required — app fails fast without it) |
| `DATABASE_URL` | `postgres://USER:PASSWORD@HOST:5432/echoflow_db` |
| `REDIS_URL` | `redis://localhost:6379/1` |
| `FIELD_ENCRYPTION_KEY` | Fernet key used to encrypt user emails |
| `HF_TOKEN` | HuggingFace token for offline model loading |
| `OPENAI_API_KEY` | Optional — reserved for the OpenAI-based pipeline branch |
| `FREESOUND_API_KEY` | Required only for the `freesound` scraper source |
| `SEED_AUTH_TOKEN` | Auth token used by `seed_db.py` |

```bash
# 4. Run database migrations
python manage.py migrate

# 5. Start the API server (Django dev server)
python manage.py runserver

# 6. In a separate terminal — start Celery workers
celery -A EchoFlow worker --loglevel=info

# 7. Feed-refill worker (dedicated queue)
celery -A EchoFlow worker -Q fast_feed --concurrency=4 --loglevel=info

# 8. Media/AI processing worker (heavy queue, single-process)
celery -A EchoFlow worker -Q heavy_media --pool=solo --loglevel=info

# 9. Periodic scheduler (metrics + vector evolution)
celery -A EchoFlow beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Backend Setup With Docker

Docker Compose provisions seven services exactly as defined in `docker-compose.yml`: `db` (pgvector/pgvector:pg16), `redis` (redis:7-alpine), `web` (gunicorn on `0.0.0.0:8000`, exposed on host port `8005`), plus `celery`, `celery_feed`, `celery_media`, and `celery_beat`.

```bash
# 1. Ensure .env exists and is populated (DB_*, REDIS_URL, DJANGO_SECRET_KEY, HF_TOKEN, ...)
#    docker-compose reads ${VAR} substitutions from your .env file.

# 2. Build and start all services
docker-compose up --build

# 3. View logs for a specific service (e.g. media worker)
docker-compose logs -f celery_media

# 4. Run a management command inside the web container
docker-compose exec web python manage.py migrate

# 5. Tear everything down
docker-compose down
```

The `web` container automatically runs `wait_for_db.py`, `migrate`, and `collectstatic` before starting gunicorn with 4 workers × 2 threads. Persistence is handled by named volumes: `postgres_data`, `media_data`, and `huggingface_cache`.

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
python manage.py scrape_audio --source=wikimedia --limit=3 --clip-length=30

# Same ingestion, but as a Celery task
python -c "from backend.app.tasks import scrape_and_import; scrape_and_import.delay('internet_archive', limit=5)"
```

Scraped clips are stored under `media/audio_scraper/{source}/YYYY/MM/DD/`, provenance/license metadata is attached, and each clip is then processed through the full AI + HLS pipeline automatically.

FFmpeg **must** be installed for scraping to work (used by audio normalization and HLS generation). See [Key Features](#key-features) for the full pipeline.

## Project Structure

```
EchoFlow/
├── backend/                   # Django application
│   ├── EchoFlow/              # Project config (settings, urls, celery, wsgi/asgi)
│   │   ├── settings.py        # All config: DB, Redis, Celery, JWT, scraper, CORS
│   │   ├── urls.py            # Root URL config (admin + app routes)
│   │   ├── celery.py          # Celery app (Redis broker)
│   │   └── wsgi.py
│   ├── app/                   # Core Django app
│   │   ├── models.py          # User, AudioClip, Comment, ShareEvent, UserInteraction
│   │   ├── views.py           # 9 ViewSets + pagination (feed, uploads, social, profile)
│   │   ├── serializers.py     # DRF serializers (feed, upload, comment, auth, profiles)
│   │   ├── urls.py            # DRF router + JWT auth endpoints
│   │   ├── tasks.py           # Celery tasks (HLS/AI, feed refill, metrics, baseline evolution)
│   │   ├── scrapers/          # Ingestion pipeline
│   │   │   ├── base.py        # robots.txt checker, rate limiter, HTTP session
│   │   │   ├── downloader.py  # Safe audio download (size/content-type guards)
│   │   │   ├── normalizer.py  # Trim + normalize audio (pydub)
│   │   │   ├── uploader.py    # Persist clip + provenance metadata
│   │   │   └── sources/       # wikimedia, internet_archive, freesound, kaggle
│   │   ├── management/commands/  # scrape_audio management command
│   │   └── migrations/
│   └── scripts/               # Seed scripts (seed_db.py)
├── frontend/                  # Vite/React client for the API
├── ai-ml/                     # ML pipeline stubs (models, pipelines, eval)
├── docker-compose.yml         # 7 services: db, redis, web, celery, feed, media, beat
├── Dockerfile                 # python:3.11-slim + ffmpeg + libsndfile + deps
├── requirements.txt           # All Python dependencies
├── wait_for_db.py             # DB readiness check for container startup
└── docs/                      # Architecture, audit, and scaling documentation
```

## Development / Future Vision

Natural next steps that follow directly from the existing architecture:

- **Media to object storage** — move HLS output and uploads from local disk to S3-compatible storage (django-storages + boto3 are already dependencies)
- **CDN delivery** — serve HLS segments through a CDN for global low-latency streaming
- **Real-time notifications** — push events for shares/inbox via websockets or a streaming broker
- **Recommendation at scale** — replace brute-force cosine scans with a candidate-generation + ANN tier as the catalog grows
- **Event-driven message bus** — migrate the Celery/Redis broker to a durable event stream for idempotent, retryable processing
- **Rate limiting & throttling** — add DRF throttling and distributed rate limits to the API layer
- **Observability stack** — structured logging, metrics, tracing, and error tracking for production confidence
- **CI/CD pipeline** — automated test + build + deploy stages for the Docker stack

---

**Stack at a glance:** `Django 5` · `DRF` · `PostgreSQL + pgvector` · `Redis` · `Celery` · `FFmpeg/HLS` · `faster-whisper` · `sentence-transformers` · `librosa` · `Docker Compose`
