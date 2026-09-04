# EchoFlow Technical Documentation

This directory contains comprehensive, code-grounded technical documentation for the EchoFlow audio-first short-form content platform.

**Source of Truth:** All documentation is derived from the actual implementation in the repository. Where existing documentation conflicts with code, the actual behavior is documented and discrepancies are explicitly noted.

## Documentation Structure

### System-Level Architecture
- [01-system-overview.md](architecture/01-system-overview.md) — Overall architecture, data flows, and component relationships
- [02-deployment-topology.md](architecture/02-deployment-topology.md) — Docker Compose services, networking, and runtime behavior
- [03-design-decisions.md](architecture/03-design-decisions.md) — Key architectural decisions, trade-offs, and rationale

### Backend (Django/DRF)
- [01-project-structure.md](backend/01-project-structure.md) — Dual `EchoFlow/` package layout, settings, and entry points
- [02-models.md](backend/02-models.md) — Custom User, AudioClip, Comment, ShareEvent, UserInteraction schemas
- [03-serializers.md](backend/03-serializers.md) — DRF serializers, validation, and HLS URL generation
- [04-views-api.md](backend/04-views-api.md) — ViewSets, endpoints, permissions, and pagination
- [05-urls-routing.md](backend/05-urls-routing.md) — URL configuration and API endpoint map
- [06-auth-permissions.md](backend/06-auth-permissions.md) — JWT authentication, SimpleJWT, and permission classes
- [07-media-urls.md](backend/07-media-urls.md) — S3/MinIO playback URL generation for HLS vs uploads

### Frontend (React/Vite)
- [01-architecture.md](frontend/01-architecture.md) — Component hierarchy, state management, and data flow
- [02-api-layer.md](frontend/02-api-layer.md) — API client, token management, and error handling
- [03-stores.md](frontend/03-stores.md) — Context providers (Auth, Player, Toast, Theme)
- [04-playback-hls.md](frontend/04-playback-hls.md) — HLS.js integration, MiniPlayer, and audio handling
- [05-pages-components.md](frontend/05-pages-components.md) — Page components, ReelCard, ReelList, and UI flows

### AI/ML Pipeline
- [01-overview.md](ai-ml/01-overview.md) — Pipeline stages, models, and integration points
- [02-feature-extraction.md](ai-ml/02-feature-extraction.md) — Acoustic (librosa) and semantic (sentence-transformers) vectors
- [03-transcription-tagging.md](ai-ml/03-transcription-tagging.md) — Whisper transcription and KeyBERT tag extraction
- [04-recommendation-engine.md](ai-ml/04-recommendation-engine.md) — Composite scoring, vector blending, feed mixing
- [05-cold-start.md](ai-ml/05-cold-start.md) — Tag-based vector bootstrapping for new users
- [06-ml-models-lazy-loading.md](ai-ml/06-ml-models-lazy-loading.md) — Thread-safe model initialization in Celery workers

### Redis & Celery
- [01-redis-usage.md](redis-celery/01-redis-usage.md) — Cache, feed queues, broker, and session storage
- [02-celery-workers.md](redis-celery/02-celery-workers.md) — Queue routing, concurrency, and worker types
- [03-periodic-tasks.md](redis-celery/03-periodic-tasks.md) — Celery Beat schedule, metrics, vector evolution
- [04-task-reliability.md](redis-celery/04-task-reliability.md) — Retries, idempotency, locking, and failure handling

### PostgreSQL & pgvector
- [01-schema.md](postgresql/01-schema.md) — Table definitions, constraints, and indexes
- [02-vector-indexes.md](postgresql/02-vector-indexes.md) — HNSW configuration, cosine similarity, ANN queries
- [03-migrations.md](postgresql/03-migrations.md) — Migration history and schema evolution
- [04-raw-sql-operations.md](postgresql/04-raw-sql-operations.md) — `update_global_metrics` and performance implications

### Audio Processing & HLS
- [01-pipeline-overview.md](media/01-pipeline-overview.md) — End-to-end media processing flow
- [02-ffmpeg-hls.md](media/02-ffmpeg-hls.md) — HLS transcoding, MPEG-TS segments, ABR variants
- [03-audio-normalization.md](media/03-audio-normalization.md) — FFmpeg decode, librosa processing, scratch space
- [04-media-lifecycle.md](media/04-media-lifecycle.md) — Upload → processing → HLS → object storage → cleanup

### Storage (MinIO/S3-Compatible)
- [01-s3-architecture.md](storage/01-s3-architecture.md) — Split ACL design, endpoint separation, path-style addressing
- [02-hls-playback.md](storage/02-hls-playback.md) — Why HLS cannot use signed URLs, public `hls/` prefix
- [03-bucket-policies.md](storage/03-bucket-policies.md) — MinIO init, CORS, anonymous download policy

### Scraping & Ingestion
- [01-sources.md](scraping/01-sources.md) — Wikimedia, Internet Archive, Freesound, Kaggle connectors
- [02-pipeline.md](scraping/02-pipeline.md) — Download → normalize → upload → AI processing
- [03-licensing-safety.md](scraping/03-licensing-safety.md) — License filtering, robots.txt, rate limiting, provenance

### Authentication & Security
- [01-jwt-auth.md](auth/01-jwt-auth.md) — SimpleJWT configuration, token lifetimes, refresh flow
- [02-pii-encryption.md](auth/02-pii-encryption.md) — Fernet email encryption, key management
- [03-cors-csrf.md](auth/03-cors-csrf.md) — CORS configuration, Range headers for HLS
- [04-rate-limiting.md](auth/04-rate-limiting.md) — DRF throttling, current limits, gaps

### Docker & Deployment
- [01-multi-stage-dockerfile.md](docker/01-multi-stage-dockerfile.md) — Build stages, wheelhouse, secret handling
- [02-docker-compose.md](docker/02-docker-compose.md) — 12 services (see discrepancy note in file), health checks, resource limits — **NOTE: original doc describes 7 services; current compose has 12**
- [03-environment-variables.md](docker/03-environment-variables.md) — Required vars, dev vs prod differences
- [04-gunicorn-wait-for-db.md](docker/04-gunicorn-wait-for-db.md) — Preload app, post_fork connection reset, DB polling
- [05-https-tls-termination.md](docker/05-https-tls-termination.md) — nginx TLS terminator: why, how, pros, cons, failure modes (added 2026-09-04)
- [06-https-production-readiness.md](docker/06-https-production-readiness.md) — 12-section release checklist for HTTPS deployment (added 2026-09-04)

### Testing & Observability
- [01-current-state.md](testing/01-current-state.md) — Existing tests, missing test framework, no CI/CD
- [02-metrics-health.md](testing/02-metrics-health.md) — Prometheus metrics, health/readiness endpoints
- [03-logging.md](testing/03-logging.md) — JSON structured logging configuration

### Failure Handling & Recovery
- [01-distributed-systems.md](failure/01-distributed-systems.md) — Duplicate execution, retries, idempotency, race conditions
- [02-media-processing.md](failure/02-media-processing.md) — FFmpeg failures, model loading, scratch cleanup
- [03-feed-resilience.md](failure/03-feed-resilience.md) — Redis outage fallback, circuit breakers
- [04-telemetry-contention.md](failure/04-telemetry-contention.md) — PostgreSQL lock contention, batching needs

### Decision Logs
- [01-key-decisions.md](decisions/01-key-decisions.md) — Centralized DECISION/SECURITY/HACK/TODO log from code
- [02-discrepancies.md](decisions/02-discrepancies.md) — Where documentation conflicts with implementation

---

## Quick Reference: API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/auth/register/` | Public | User registration |
| POST | `/auth/login/` | Public | JWT obtain pair |
| POST | `/auth/token/refresh/` | Public | JWT refresh |
| POST | `/clips/` | ✓ | Upload audio → triggers `process_audio_to_hls` |
| GET | `/feed/` | ✓ | Redis-backed personalized feed |
| POST | `/interactions/{id}/toggle-like/` | ✓ | Toggle like/unlike |
| POST | `/interactions/{id}/register-skip/` | ✓ | Register skip/view telemetry |
| POST | `/interactions/{id}/log-telemetry/` | ✓ | Log detailed watch-time telemetry |
| GET | `/comments/?clip={id}` | ✓ | Filter comments by clip |
| POST | `/comments/` | ✓ | Create comment |
| POST | `/share/{id}/send-share/` | ✓ | Send clip to another user |
| GET | `/share/inbox/` | ✓ | Get user's share inbox |
| POST | `/follow/{id}/toggle-follow/` | ✓ | Follow/unfollow user |
| POST | `/tags/initialize/` | ✓ | Cold-start tag-based vector bootstrapping |
| GET | `/suggestions/?category=X` | ✓ | Category-scoped vector ranking |
| GET | `/profile/me/` | ✓ | Own profile |
| GET | `/profile/{id}/` | ✓ | Public profile |

---

## Key Files to Understand First

1. **`backend/EchoFlow/settings.py`** — All configuration: DB, Redis, Celery, JWT, S3, scraper, CORS
2. **`backend/app/models.py`** — Core data models with pgvector fields and constraints
3. **`backend/app/tasks.py`** — Celery tasks: HLS/AI pipeline, feed refill, metrics, vector evolution
4. **`backend/app/views.py`** — API ViewSets: feed, uploads, interactions, comments, share, follow, tags
5. **`backend/app/serializers.py`** — DRF serializers with HLS URL signing logic
6. **`docker-compose.yml`** — 7-service deployment topology
7. **`Dockerfile`** — Multi-stage build with offline wheelhouse and HF model baking

---

## Discrepancy Tracking

This documentation explicitly notes where:
- **README/AGENTS.md claims** differ from **actual code behavior**
- **Architecture audit docs** describe intended/future state vs **current implementation**
- **Comments in code** are outdated or inaccurate

Search for `DISCREPANCY:` markers throughout the docs.

---

*Generated from source code analysis. Last updated: 2026-09-03*