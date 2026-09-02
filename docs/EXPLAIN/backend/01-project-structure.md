# Backend Project Structure

## Dual Package Layout

```
backend/
├── EchoFlow/                 # Django PROJECT package (configuration)
│   ├── __init__.py          # Exports Celery app
│   ├── settings.py          # All configuration (DB, Redis, Celery, JWT, S3, scraper, CORS)
│   ├── urls.py              # Root URL config (admin + app routes)
│   ├── celery.py            # Celery app (Redis broker)
│   ├── health.py            # /health/ liveness, /ready/ readiness probes
│   ├── wsgi.py              # WSGI entrypoint (gunicorn target)
│   └── asgi.py              # ASGI entrypoint
│
├── app/                      # Django APP package (business logic)
│   ├── __init__.py
│   ├── apps.py              # AppConfig
│   ├── models.py            # User, AudioClip, Comment, ShareEvent, UserInteraction
│   ├── views.py             # ViewSets: feed, uploads, interactions, comments, share, follow, tags, profile
│   ├── serializers.py       # DRF serializers (feed, upload, comment, auth, profiles)
│   ├── urls.py              # DRF router + JWT auth endpoints
│   ├── tasks.py             # Celery tasks (HLS/AI pipeline, feed refill, metrics, vector evolution)
│   ├── db_routers.py        # Multi-DB routing stub (single DB for now)
│   ├── media_urls.py        # S3/MinIO playback URL generation
│   ├── admin.py             # Django admin registration
│   ├── management/
│   │   └── commands/
│   │       └── scrape_audio.py  # Management command for scraping
│   ├── migrations/          # Database migrations
│   ├── scrapers/            # License-aware ingestion pipeline
│   │   ├── __init__.py
│   │   ├── base.py          # robots.txt checker, rate limiter, HTTP session
│   │   ├── downloader.py    # Safe audio download (size/content-type guards)
│   │   ├── normalizer.py    # Trim + normalize audio (pydub)
│   │   ├── uploader.py      # Persist clip + provenance metadata
│   │   └── sources/         # wikimedia_commons, internet_archive, freesound, kaggle
│   └── tests/
│       └── test_scraper.py  # Unit tests for scraper components
│
├── scripts/                  # Seed scripts
│   ├── seed_db.py
│   └── seed_db2.py
│
├── media/                    # Local media (dev only, not used in Docker)
├── staticfiles/              # collectstatic output
└── __pycache__/
```

## Entry Points

### Django Settings Module
`backend/EchoFlow/settings.py` — **Single source of truth for all configuration**
- Database: `dj_database_url` from `DATABASE_URL`
- Redis: `REDIS_URL` for cache + Celery broker
- Celery: Task routes, beat schedule, worker config
- JWT: SimpleJWT lifetimes, authentication classes
- S3/MinIO: `STORAGES["default"]` with `S3Storage` backend
- Scraper: Rate limits, allowed licenses, API keys
- CORS: Explicit origins, Range headers for HLS
- Logging: JSON structured logging

### Root URL Configuration
`backend/EchoFlow/urls.py`:
```python
urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('backend.app.urls')),      # All API routes
    path('health/', health_check),              # Liveness probe
    path('ready/', readiness_check),            # Readiness probe (DB check)
    path('metrics/', ExportToDjangoView),       # Prometheus metrics
]
```

### Celery Application
`backend/EchoFlow/celery.py`:
```python
app = Celery('backend.EchoFlow')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks(['backend.app'])
```
- Exported via `backend/EchoFlow/__init__.py` for gunicorn preload
- Discovers tasks in `backend.app.tasks`

### WSGI/ASGI
- `wsgi.py` → `gunicorn -c gunicorn.conf.py backend.EchoFlow.wsgi:application`
- `asgi.py` → For future async support (not currently used)

## App Package Details

### Models (`backend/app/models.py`)
Core data models with pgvector fields and database constraints:
- **User** — Custom user model (AbstractUser + encrypted_email, vectors, following)
- **AudioClip** — Core content model (vectors, HLS URL, metrics, provenance)
- **Comment** — Nested threaded comments (auto-updates clip.comment_count)
- **ShareEvent** — Peer-to-peer shares (inbox, unread counts)
- **UserInteraction** — Likes, shares, skips, views (denormalized counters via F())

### Views (`backend/app/views.py`)
ViewSets organized by domain:
| ViewSet | Endpoint Base | Purpose |
|---------|---------------|---------|
| `AudioUploadViewSet` | `/clips/` | Upload → creates clip + enqueues processing |
| `FastFeedViewSet` | `/feed/` | Redis-backed personalized feed (LPOP) |
| `ClipInteractionViewSet` | `/interactions/{id}/` | Toggle like, register skip, log telemetry |
| `ShareViewSet` | `/share/` | Send shares, inbox, unread count |
| `CommentViewSet` | `/comments/` | CRUD + filtering by clip/parent |
| `FollowViewSet` | `/follow/{id}/` | Toggle follow/unfollow |
| `TagsViewSet` | `/tags/initialize/` | Cold-start tag-based vector bootstrapping |
| `SuggestionViewSet` | `/suggestions/` | Category-scoped vector ranking |
| `ProfileViewSet` | `/profile/` | Own/public profiles, user clips |
| `RegisterView` | `/auth/register/` | Public registration |

### Serializers (`backend/app/serializers.py`)
Key serializers:
- `AudioUploadSerializer` — File validation (type, size ≤100MB)
- `FeedClipSerializer` — Generates signed HLS URLs via `get_hls_playback_url`
- `CommentSerializer` — Nested replies, author info
- `ShareEventSerializer` — Share inbox with clip details
- `RegisterSerializer` — User creation with email validation
- `Profile serializers` — Public vs own (includes liked_clips)

### Tasks (`backend/app/tasks.py`)
Celery tasks organized by queue:
| Task | Queue | Purpose |
|------|-------|---------|
| `process_audio_to_hls` | `heavy_media` | Full AI + HLS pipeline |
| `refill_user_feed` | `fast_feed` | Composite scoring → Redis queue |
| `update_global_metrics` | `celery` (beat) | Raw SQL UPDATE on all clips |
| `evolve_long_term_user_baselines` | `celery` (beat) | Batch update user vectors |
| `scrape_and_import` | `celery` | Scraper wrapper → creates clips |

### Scrapers (`backend/app/scrapers/`)
Modular ingestion pipeline:
```
downloader.py    → Download with robots.txt + rate limit + size/type checks
normalizer.py    → pydub trim/normalize to MP3
uploader.py      → Save to S3 (audio_scraper/ prefix) + create AudioClip
sources/         → Source-specific fetch logic
    ├── wikimedia_commons.py  (API, filters audio/* MIME)
    ├── internet_archive.py   (Search + metadata, filters formats)
    ├── freesound.py          (API token, preview URLs only)
    └── kaggle.py             (Local file:// ingestion)
```

### Media URLs (`backend/app/media_urls.py`)
Critical for HLS playback:
- `get_hls_playback_url(object_key)` → **Unsigned public URL** for `hls/` prefix
- `get_signed_media_url(object_key)` → **Signed URL** for `uploads/` prefix
- Uses `PUBLIC_MEDIA_ENDPOINT_URL` (browser-facing) not `AWS_S3_ENDPOINT_URL` (internal)

### Database Routers (`backend/app/db_routers.py`)
```python
# no need now ; when u get a seprate db for stats that time u will nedd it
```
Stub for future multi-DB routing (not currently used).

## Configuration Flow

```
.env (or docker-compose env_file)
    │
    ▼
backend/EchoFlow/settings.py
    │
    ├── DATABASE_URL → dj_database_url → DATABASES['default']
    ├── REDIS_URL → CACHES['default'] + CELERY_BROKER_URL
    ├── AWS_* → STORAGES['default']['OPTIONS']
    ├── DJANGO_SECRET_KEY → SECRET_KEY (fail-fast)
    ├── FIELD_ENCRYPTION_KEY → Fernet cipher_suite (fail-fast)
    ├── HF_TOKEN → BuildKit secret (Dockerfile media stage)
    └── SCRAPER_* → Scraper behavior
```

## Key Conventions

1. **Imports**: Use `backend.app` for app code, `backend.EchoFlow` for project config
2. **User model**: Always `from django.contrib.auth import get_user_model`
3. **Vector fields**: `pgvector.django.VectorField` with dimensions
4. **Async tasks**: `@shared_task` with retry config for resilience
5. **File storage**: Always `default_storage` (S3Storage), never local filesystem
6. **HLS URLs**: Store object key in DB, sign at serialization time

---

*Source: `backend/` directory structure, `backend/EchoFlow/settings.py`, `backend/app/` modules*