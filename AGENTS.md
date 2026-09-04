# EchoFlow — Agent Quick-Start

## Stack
Django 5.2 / DRF 3.18 · PostgreSQL 16 + pgvector (HNSW) · Redis 7 · Celery + Celery Beat · FFmpeg (HLS) · Vite/React (frontend/) · nginx 1.27 (TLS terminator) · Prometheus + Grafana (observability) · Sentry (errors, ready-to-configure)

> **Docker is the only supported way to run EchoFlow locally.** There is no bare-metal install path. The `Dockerfile` and `docker-compose.yml` provision every dependency (Postgres+pgvector, Redis, MinIO, all Celery queues, ffmpeg, Python 3.11, ML libs, nginx, Prometheus, Grafana) in a single `docker compose up --build`. Do not introduce a non-Docker runbook.

## Docker
```bash
docker compose up --build          # 14 services: db, pgbouncer, redis_broker, redis_cache, minio, minio-init, nginx, web, celery, celery_feed, celery_media, celery_beat, prometheus, grafana
docker compose down                # tear down
docker compose logs -f celery_media
docker compose exec web python manage.py migrate

# Observability endpoints (after `docker compose up`):
#   Prometheus: http://localhost:9090
#   Grafana:    http://localhost:3000  (admin / ${GRAFANA_ADMIN_PASSWORD})
```

> **nginx is the only public-facing entrypoint.** It terminates TLS on `:80` (redirect) / `:443` (Django) / `:9443` (MinIO HLS) and forwards plain HTTP to the in-network backends. The `web:8000` and `minio:9000` ports are NOT directly reachable from the host anymore (except `web:8005` which is published as a debug escape hatch). To verify the stack is up: `curl -kI https://localhost/health/`. See [docs/EXPLAIN/docker/05-https-tls-termination.md](docs/EXPLAIN/docker/05-https-tls-termination.md) for the full design.

## Running Tests

> **All tests must be run inside the Docker `web` container.** The local SQLite + LocMem environment can run the suite, but it cannot exercise: pgvector/HNSW indexes, Postgres row-level locks, real Redis Streams, ffmpeg in `PATH`, the MinIO storage backend, or cross-thread concurrency. The container has every dependency baked in.

```bash
# Start the stack (only first time, or after a config change)
docker compose up --build -d

# Run the full test suite via pytest (inside the web container).
# PYTHONPATH=/app is required: pytest 9+ no longer auto-prepends the
# rootdir to sys.path, so the conftest's relative `import backend...`
# fails without it. This will be removed when we move to a proper
# pytest `pythonpath` config setting in pytest.ini.
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/ --tb=short

# Run a single test file
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/test_adversarial_pass3.py -v

# Run a single test class
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/test_adversarial_pass3.py::TestN1CommentAuthorization -v

# Run the Django built-in test runner (alternative to pytest)
docker compose exec web python manage.py test backend.app --verbosity 2

# Run the migration / config / static checks that CI runs
docker compose exec web python manage.py migrate --noinput
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py check --fail-level WARNING
docker compose exec web python manage.py collectstatic --noinput --dry-run

# Inspect coverage (with the pytest-cov plugin — installed in the image)
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/ --cov=backend.app --cov-report=term-missing

# Tail logs while a test runs against a live worker
docker compose logs -f celery celery_feed celery_media

# Tear down after testing
docker compose down
```

## Observability

Two stacks are available after `docker compose up`:

### Prometheus + Grafana (primary)
```bash
# Prometheus: scrape target, query PromQL
open http://localhost:9090/targets      # confirm web target is UP
open http://localhost:9090/graph        # PromQL query editor

# Grafana: dashboards, datasources auto-provisioned
open http://localhost:3000              # admin / ${GRAFANA_ADMIN_PASSWORD}
#   Dashboards > EchoFlow > 01-feed-and-suggestions
#   Dashboards > EchoFlow > 02-celery-health
```

The scraper reads `/metrics/` from `web` every 15s. Two dashboards ship pre-built:
- **01-feed-and-suggestions.json** — p95 of `feed_refill_duration_seconds`, `suggestion_ranking_duration_seconds`, cache hit/miss rate.
- **02-celery-health.json** — `rate(celery_tasks_processed_total[5m])` by queue/task, p95 of `hls_processing_duration_seconds`.

Alert rules are intentionally not shipped in this pass — the audit doc proposes them; this is a follow-up. Add them in `docker/prometheus/alerts.yml` and reload Prometheus when ready.

Full design: [docs/EXPLAIN/observability/03-prometheus-grafana-design.md](docs/EXPLAIN/observability/03-prometheus-grafana-design.md). Activation runbook: [docs/EXPLAIN/observability/04-prometheus-grafana-setup.md](docs/EXPLAIN/observability/04-prometheus-grafana-setup.md).

### Sentry (error capture, ready-to-configure)
`sentry-sdk[django,celery]==2.18.0` is installed; `init_sentry()` runs in each process's `App1Config.ready()`. Errors from web + all 4 celery services are captured when `SENTRY_DSN` is set in `.env`. Gated on `DJANGO_DEBUG=False` so dev/test paths are no-ops.

```bash
# Local: capture_exception is a no-op (no DSN, debug=True)
# Staging/prod: set SENTRY_DSN, SENTRY_ENV in .env
echo 'SENTRY_DSN=https://abc123@sentry.io/456' >> .env
echo 'SENTRY_ENV=production' >> .env
docker compose up -d --force-recreate web celery celery_feed celery_media celery_beat
```

The `capture_exception(exc, **context)` wrapper in `backend/app/services/sentry.py` attaches the current request's `correlation_id` (from `backend.EchoFlow.correlation`) as a Sentry tag, so production errors cross-reference with the worker's correlation_id (Group B item 11). `send_default_pii=False` — user IPs, cookies, and auth headers are NOT sent.

### Observability TUI (dev fallback)
```bash
# Run inside the web container (TUI requires urllib; stdlib only, no extra deps)
docker compose exec web python scripts/observability_tui.py

# One-shot snapshot to stdout (useful for scripting)
docker compose exec web python scripts/observability_tui.py --once

# Point at a different /metrics/ URL (e.g. against a staging server)
docker compose exec web python scripts/observability_tui.py --url http://staging:8005/metrics/

# Refresh every 2 seconds instead of 5
docker compose exec web python scripts/observability_tui.py --interval 2
```

The TUI reads `/metrics/` from the running `web` container and prints a text dashboard of the 6 custom application metrics (`echoflow_feed_refill_duration_seconds`, `echoflow_suggestion_ranking_duration_seconds`, `echoflow_toggle_like_duration_seconds`, `echoflow_cache_get_set_duration_seconds`, `echoflow_hls_processing_duration_seconds`, `echoflow_celery_tasks_processed_total`). Refreshes every N seconds (default 5). It is now a dev fallback — Grafana is the primary observability tool.

**Why `docker compose exec web` and not `docker compose run web`?**
- `exec` runs the command in the already-running `web` service (uses its env, mounted volumes, and depends_on the DB/Redis/MinIO). This matches the actual production-like runtime.
- `run` would spin up a fresh container that doesn't have the dependent services linked unless you pass `--service-ports` and explicitly `depends_on` them — which complicates the command for no benefit.

**When a test genuinely needs bare-metal (rare, e.g. debugging an ML model locally):** run the wheelhouse install documented in commit history of the audit-pass-3 dump, set the same env vars the container uses (DATABASE_URL, REDIS_BROKER_URL, REDIS_CACHE_URL, etc.), and run pytest directly. Document the divergence in the PR description.

### Dockerfile architecture
Single multi-stage `Dockerfile` with five stages (two are build-only):

| Stage | Shipped? | Purpose |
|---|---|---|
| `base` | parent of all | apt union (libpq-dev, gcc, postgresql-client, ffmpeg, libsndfile1), appuser (UID 1000) |
| `py-deps-api` | no | installs requirements-base.txt offline from wheelhouse into site-packages |
| `py-deps-media` | no | requirements-media.txt + bakes HuggingFace models to `/home/appuser/.cache/huggingface` |
| `api` | yes | web, celery, celery_feed, celery_beat — small image, no wheels/models |
| `media` | yes | celery_media — adds baked HF models; runtime `HF_HOME=/home/appuser/.cache/huggingface` |

Final images receive dependencies via `COPY --from=py-deps-* /opt/venv /opt/venv`
and source via an explicit allowlist (`backend/` — incl. `wait_for_db.py`
and `gunicorn.conf.py`, `manage.py`) — never a blanket `COPY .`. Stage-specific HEALTHCHECKs are
baked in: `api` probes `GET /health/` (compose overrides it to a Celery ping
for the worker services sharing that image); `media` pings its own Celery node.
HF_TOKEN is delivered ONLY via BuildKit secret mount
(`--mount=type=secret,id=hf_token`) — never `--build-arg`, which would persist
the token in builder layer history readable by `docker history`.

```bash
# Build all targets
docker compose build

# Build a single target manually (media consumes HF_TOKEN as a build SECRET)
docker build --target api   -t echoflow-api  .
export HF_TOKEN=hf_xxx   # or: --secret id=hf_token,src=./hf_token.txt
docker build --target media -t echoflow-media . --secret id=hf_token,env=HF_TOKEN

# Override image tag
docker compose build --build-arg TAG=dev
```

### Offline wheelhouse
All pip installs use `--no-index --find-links=/wheelhouse`. The wheelhouse is a local directory of pre-built wheels that makes builds fully offline and deterministic.

**Regenerate the wheelhouse** (run inside a py3.11 container):
```bash
mkdir -p wheelhouse-new
docker run --rm \
  -v "$PWD/requirements-base.txt:/req/requirements-base.txt:ro" \
  -v "$PWD/requirements-media.txt:/req/requirements-media.txt:ro" \
  -v "$PWD/constraints.txt:/req/constraints.txt:ro" \
  -v "$PWD/wheelhouse-new:/out" \
  python:3.11-slim-bookworm sh -c "\
    pip wheel --no-deps -w /out 'dj-rest-auth==7.2.0' && \
    pip download --prefer-binary --retries 10 --timeout 120 \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -c /req/constraints.txt -r /req/requirements-base.txt -d /out && \
    pip download --prefer-binary --retries 10 --timeout 120 \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -c /req/constraints.txt -r /req/requirements-media.txt -d /out"

rm -rf wheelhouse && mv wheelhouse-new wheelhouse
```

**Important rules:**
- After regenerating, `rm -rf wheelhouse && mv wheelhouse-new wheelhouse` swaps the directory.
- If you add/change any pin in `requirements-base.txt`, `requirements-media.txt`, or `constraints.txt`, **re-run the regen script first**.
- The `pip wheel --no-deps dj-rest-auth` step pre-builds a wheel for dj-rest-auth (it is sdist-only on PyPI).
- `librosa==0.11.0` — do NOT bump to 1.x (requires Python >= 3.12).
- `django==5.2.17` — do NOT bump to 6.x (requires Python >= 3.12).
- `sentry-sdk[django,celery]==2.18.0` — added when Sentry integration landed (Group B partial-issues, PR 2 of 3). The wheelhouse regen script must include this.

### Pop!_OS note
Uses Docker Compose V2 (`docker compose`, not `docker-compose`). If you have `docker-compose` installed from an old PPA, it conflicts with the V2 plugin — remove it with `sudo apt remove docker-compose` and use `docker compose` instead.

### Runtime notes
- Web container runs: `backend/wait_for_db.py → migrate → collectstatic → gunicorn -c backend/gunicorn.conf.py`.
- `gunicorn.conf.py` uses `preload_app=True` with a `post_fork` hook that resets Django DB connections (critical because `EchoFlow/__init__.py` imports Celery, which creates Redis connections in the master process before fork).
- Health checks: `GET /health/` (liveness), `GET /ready/` (readiness — checks DB), `GET /metrics/` (Prometheus).
- Resource limits defined per-service in `docker-compose.yml` under `deploy.resources`.
- Override gunicorn workers/threads with `GUNICORN_WORKERS` and `GUNICORN_THREADS` env vars.
- **Do NOT** mount `huggingface_cache` volume on `celery_media` — models are baked into the image at build time.
- **PgBouncer (Phase 1.0):** web/celery services connect via `pgbouncer:6432` (transaction pool mode, `AUTH_TYPE=scram-sha-256`). Non-Docker dev (`pip install + runserver`) skips pgbouncer and connects to `localhost:5432` directly via `DATABASE_URL` — both paths work.
- **Split Redis (Phase 1.0):** Docker runs `redis_broker` (noeviction, 512MB, `REDIS_BROKER_URL`) and `redis_cache` (LRU, 1GB, `REDIS_CACHE_URL`) as separate services. Non-Docker dev sets `REDIS_URL` only — both URLs fall back to it.

## Environment Variables (required)
| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django signing key — app fails without it |
| `DJANGO_DEBUG` | **Must be `False` in any environment behind the nginx terminator.** Enables the `if not DEBUG:` block (`SECURE_SSL_REDIRECT`, HSTS, secure cookies). Default: `False` in `.env.example`. |
| `DATABASE_URL` | Docker: `postgres://user:pass@pgbouncer:6432/echoflow_db`. Non-Docker dev: `postgres://user:pass@localhost:5432/echoflow_db` |
| `READ_DATABASE_URL` | Optional. When set, activates the read-replica routing in `backend/app/db_routers.py`. Postgres URL of the streaming replica. See [docs/EXPLAIN/database/05-read-replica-design.md](docs/EXPLAIN/database/05-read-replica-design.md) for the activation playbook. |
| `REDIS_URL` | Non-Docker dev: `redis://localhost:6379/1` (single Redis). Optional in Docker. |
| `REDIS_BROKER_URL` | Docker: `redis://redis_broker:6379/0`. Falls back to `REDIS_URL`. |
| `REDIS_CACHE_URL` | Docker: `redis://redis_cache:6379/0`. Falls back to `REDIS_URL`. |
| `FIELD_ENCRYPTION_KEY` | Fernet key for email encryption |
| `HF_TOKEN` | HuggingFace token (model baking at build time). See [docs/EXPLAIN/operations/hf-token-rotation.md](docs/EXPLAIN/operations/hf-token-rotation.md) for the rotation runbook. |
| `OPENAI_API_KEY` | Optional — reserved for OpenAI pipeline branch |
| `FREESOUND_API_KEY` | Required only for freesound scraper |
| `SEED_AUTH_TOKEN` | Auth token for `seed_db.py` |
| `GUNICORN_WORKERS` | Default gunicorn workers (default: 4) |
| `GUNICORN_THREADS` | Default gunicorn threads (default: 4) |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts. Must include every host the nginx terminator is reached at (`localhost`, your prod hostname, any Tailscale/CNAMES). Default: `localhost`. |
| `DJANGO_CORS_ALLOWED_ORIGINS` | Comma-separated **https://** origins. Every browser-reachable origin MUST be `https://` once the terminator is live — `http://` here causes mixed-content / CORS preflight failures. |
| `PUBLIC_MEDIA_ENDPOINT_URL` | Browser-facing MinIO origin for HLS playback. **Must be `https://`** (e.g. `https://localhost:9443` in dev). `AWS_S3_ENDPOINT_URL` (containers' in-network URL) stays `http://minio:9000`. |
| `SENTRY_DSN` | Optional. When set, the `sentry-sdk` in each process captures uncaught exceptions. Get a DSN from sentry.io (free tier works). |
| `SENTRY_ENV` | Sentry environment tag (e.g. `production`, `staging`). Default: `production`. |
| `SENTRY_TRACES_SAMPLE_RATE` | Fraction of requests traced (0.0-1.0). Default: `0.1`. Lower for high-traffic. |
| `SENTRY_PROFILES_SAMPLE_RATE` | Fraction of profiled requests. Default: `0.05`. |
| `GRAFANA_ADMIN_PASSWORD` | Initial admin password for Grafana (first-boot only). Required — Grafana v11 refuses to start without one. |

## HTTPS / TLS Termination
The stack now ships with an nginx reverse proxy in front of every other service. TLS is terminated at the edge; internal hops (nginx→gunicorn, nginx→minio) stay plain HTTP on the docker bridge. No application code knows TLS exists.

| Concern | Where it lives | Notes |
|---|---|---|
| Public-facing entrypoint | `docker-compose.yml:nginx` (image `nginx:1.27-alpine`) | Three listeners: `:80` (HTTP→HTTPS redirect), `:443` (Django), `:9443` (MinIO for browser HLS). |
| TLS cert + key | `docker/certs/localhost.{crt,key}` (self-signed dev) | Bind-mounted read-only into nginx; never enters the app image. Production swaps in Let's Encrypt material via the same path. |
| TLS config | `docker/nginx.conf` | TLS 1.2/1.3 only, HSTS 1y+includeSubDomains+preload, `X-Forwarded-Proto https` on every upstream block. |
| Django TLS contract | `backend/EchoFlow/settings.py:529-539` `if not DEBUG:` block | `SECURE_SSL_REDIRECT=True`, `SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')`, `SESSION/CSRF_COOKIE_SECURE=True`, HSTS 1 year. **Requires `DJANGO_DEBUG=False`** — set this in `.env` before going anywhere public. |
| In-container healthcheck | `Dockerfile` + `docker-compose.yml` (web service) | Sends `X-Forwarded-Proto: https` so `SECURE_SSL_REDIRECT` doesn't loop the in-container probe. |
| Test coverage | `backend/app/tests/test_https_termination.py` (32 tests) | Cert, nginx config, prod settings, proxy header, public media endpoint, live terminator. |

**Operating rules:**
- **Do NOT change `SECURE_PROXY_SSL_HEADER` to anything other than `('HTTP_X_FORWARDED_PROTO', 'https')`** without also updating every `proxy_set_header X-Forwarded-Proto https;` line in `docker/nginx.conf`. Mismatch = redirect loop or insecure cookies.
- **The `8005:8000` host port mapping on the `web` service is a debug escape hatch**, not the supported path. Attackers on the same network can hit gunicorn directly and spoof `X-Forwarded-Proto: https` to themselves. In prod, drop that port mapping entirely.
- **Cert rotation is `docker compose exec nginx nginx -s reload`** — no app rebuild, no container restart. The bind-mount picks up the new files.
- **Full design + production-readiness checklist:** `docs/EXPLAIN/docker/05-https-tls-termination.md` and `docs/EXPLAIN/docker/06-https-production-readiness.md`.

## API Endpoints
```
POST /auth/register/          # Register (public)
POST /auth/login/             # JWT obtain pair
POST /auth/token/refresh/     # JWT refresh

POST /clips/                  # Upload audio (auth) → triggers Celery `process_audio_to_hls`
GET  /feed/                   # Redis-backed personalized feed (auth)
POST /interactions/{id}/toggle-like/
POST /interactions/{id}/register-skip/
POST /interactions/{id}/log-telemetry/
GET  /comments/?clip={id}     # Filter by clip
POST /share/{id}/send-share/
GET  /follow/{id}/toggle-follow/
POST /tags/initialize/        # Cold-start: bootstrap user vectors from tags
GET  /suggestions/?category=X # Category-scoped vector ranking
GET  /profile/me/             # Own profile
GET  /profile/{id}/           # Public profile
```

## Architecture Notes
- **Dual `EchoFlow/`**: Project package (`backend/EchoFlow/settings.py`, `urls.py`, `celery.py`) vs app package (`backend/app/`). Don't confuse them.
- **Custom user model**: `backend.app.User` (extends `AbstractUser`). Set via `AUTH_USER_MODEL = 'backend.app.User'`.
- **Recommendation engine**: Composite scoring = 45% vector similarity + 30% avg completion rate + 25% engagement velocity. 80% exploit / 20% explore feed mixing.
- **Redis feed queues**: Per-user `user_feed:{id}` lists. `FastFeedViewSet` pops 10 at a time; refills trigger when queue < 15.
- **Vector fields**: `semantic_vector` (384-dim, from transcript via sentence-transformers), `acoustic_vector` (128-dim, from librosa). HNSW indexes (`m=16, ef_construction=64`) on both.
- **Celery task routing**: `process_audio_to_hls` → `heavy_media` queue; `refill_user_feed` → `fast_feed` queue; `cleanup_orphan_hls` → 03:00 UTC daily; `flush_counters_to_pg` → every 300s (defined in `backend/EchoFlow/settings.py` `CELERY_TASK_ROUTES` + `CELERY_BEAT_SCHEDULE`).
- **ML models lazy-loaded**: `get_whisper_model()`, `get_embedding_model()`, `get_kw_model()` in `backend/app/tasks.py` — initialized on first task call, not at import time.
- **`update_global_metrics`** uses raw SQL (not ORM) — updates every ready clip. Will lock on large tables; needs batching at scale.
- **Read replica routing**: `backend/app/db_routers.py` is a 71-line `ReadRouter` with 4 hooks (db_for_read/db_for_write/allow_relation/allow_migrate). Auto-activates when `READ_DATABASE_URL` is set; the `if not atomic and not SELECT FOR UPDATE` guard prevents stale-read races inside write transactions. See [docs/EXPLAIN/database/05-read-replica-design.md](docs/EXPLAIN/database/05-read-replica-design.md).
- **Per-session DB timeouts**: `backend/EchoFlow/settings.py` sets `statement_timeout=30s`, `idle_in_transaction_session_timeout=60s`, `lock_timeout=10s`, `connect_timeout=10s` on the default connection via libpq `options` string. Critical behind PgBouncer (25-conn pool); a slow query that held a backend connection could otherwise exhaust the pool. Gated on `ENGINE.endswith('postgresql')` so SQLite tests are unaffected.
- **Cache invalidation**: `services/interactions.py::invalidate_user_vectors_cache` is called from `record_like_toggle`, `record_skip`, `record_share`, and `record_telemetry`'s sync fallback via `transaction.on_commit`. The `flush_telemetry_stream` consumer invalidates each unique user's cache after a successful `bulk_create`. Stale-vector window collapsed from 15 min to near-zero for all user-state-mutating paths.
- **Counter store (Phase 1 dual-write)**: `services/counter_store.py` writes user-engagement counters to Redis (`INCR clip:<uuid>:<type>`) AND to Postgres (via the existing `F()` expression) simultaneously. The `ECHOFLOW_DUAL_WRITE_COUNTERS` env var (default `True`) gates the `F()` path. Phase 2 will flip the env to `False` to retire the `F()` path. See [docs/EXPLAIN/decisions/partial-issues-completion-plan.md](docs/EXPLAIN/decisions/partial-issues-completion-plan.md) for the rollout playbook.
- **HLS output**: Stored under `media/hls/{clip_id}/` on local disk. Not S3-backed yet. `cleanup_orphan_hls` Celery task (daily 03:00 UTC) prunes directories older than 1 day that are not in the `AudioClip` table — bounded to 1000 keys/run.

## Scraping / Ingestion
```bash
# Management command
python manage.py scrape_audio --source=wikimedia --limit=3 --clip-length=30

# Celery task
python -c "from backend.app.tasks import scrape_and_import; scrape_and_import.delay('internet_archive', limit=5)"
```
Sources: wikimedia, internet_archive, freesound (needs `FREESOUND_API_KEY`), kaggle (needs `SCRAPER_KAGGLE_LOCAL_PATH`). Respects `robots.txt`. Allowed licenses configurable via `SCRAPER_ALLOW_LICENSES`.

## Frontend (sample only)
```bash
cd frontend
npm install
npm run dev      # Vite dev server on port 5173
npm run build
```
Uses HLS.js for playback. This is an example client — the production frontend may differ.

## Testing & Linting
- Test framework: **pytest** + `pytest-django`, installed in the `api` image. Run via `docker compose exec web pytest …` — see [Running Tests](#running-tests) for the full command set.
- Test files live under `backend/app/tests/` (22 files: `test_adversarial_pass3.py`, `test_counter_store.py`, `test_db_router.py`, `test_feed_pool.py`, `test_https_termination.py`, `test_integration_concurrency.py`, `test_integration_pgvector.py`, `test_metrics_endpoint.py`, `test_metrics.py`, `test_observability_tui.py`, `test_orphan_cleanup.py`, `test_scraper.py`, `test_security_and_validation.py`, `test_sentry.py`, `test_services_comments.py`, `test_services_follows.py`, `test_services_interactions.py`, `test_services_shares.py`, `test_services_uploads.py`, `test_settings.py`, `test_smoke.py`, `test_task_publisher.py`).
- Current count: **230 passed, 9 skipped, 0 failed** (9 skipped = 2 ffmpeg-environmental + 6 integration-on-SQLite + 1 live-nginx-environmental).
- No linting/formatter config (no `.eslintrc` at root, no `pyproject.toml`, no `ruff.toml`).
- CI: `.github/workflows/django.yml` runs migrations + the unit test suite + the integration test suite (`pytest -m integration`) in separate steps. Blocks merges on failure.

### Integration test marker

Tests that need real Postgres + Redis (pgvector HNSW indexes, row-level locks, Redis Streams, concurrent transactions) are marked with `@pytest.mark.integration`. They are auto-skipped on the local SQLite + LocMem test environment (see `_skip_integration_without_real_services` autouse fixture in `conftest.py`) and run in CI where the workflow provisions real services.

```bash
# Run only the integration-marked tests (CI does this in a separate step)
docker compose exec web pytest backend/app/tests/ -m integration --tb=short
```

### Known Skipped Tests (environmental, not regressions)

The following tests are **explicitly skipped** with `@unittest.skip(...)` because they require system binaries that are not present on the dev machine (only inside the Docker image). They are NOT broken and should NOT be "fixed" by removing the skip — the failure mode is environmental, not a code bug.

| Test | Reason | How to enable locally |
|------|--------|------------------------|
| `backend/app/tests/test_scraper.py::ScraperUnitTests::test_normalizer_trims_to_max_seconds` | Requires `ffmpeg` on `PATH` (used by `pydub` for MP3 export) | `sudo apt install ffmpeg` (Debian/Ubuntu/Pop!_OS) or `brew install ffmpeg` (macOS) |
| `backend/app/tests/test_scraper.py::ScraperUnitTests::test_uploader_creates_audioclip` | Same — `ffmpeg` required for `normalizer.normalize_and_trim` | Same as above |

The skips carry inline reasons and a pointer back to this section. If you add a test that needs a system binary not present in `docker/base`, follow the same pattern: `@unittest.skip("requires <binary> on PATH; see AGENTS.md")`.

**Do NOT** comment-out or remove tests that fail for reasons you don't understand. If a test fails and the cause is unclear, debug it: run with `pytest --tb=long`, read the traceback, search the codebase for the operation being tested, and check whether the test environment matches the AGENTS.md prerequisites (Python 3.11, Postgres 16, Redis 7, FFmpeg on `PATH`, `docker compose` running). Only after you understand WHY a test fails — and the cause is environmental, not a code bug — should you add a skip with a clear reason.

### Local `.env` discipline

- `.env` is **gitignored**. Do not commit it. The boilerplate is `.env.example` and `env.example`; copy one of those to `.env` and edit locally.
- Tracked env files must have `DJANGO_DEBUG=False`. CI runs `scripts/check_no_tracked_env.sh` on every PR; a tracked env file with `DJANGO_DEBUG=True` will block the merge.
- `FIELD_ENCRYPTION_KEY`, `HF_TOKEN`, and `DJANGO_SECRET_KEY` in your local `.env` are real secrets. If you accidentally commit them, rotate them immediately.

### `AGENTS.md` is tracked

`AGENTS.md` (this file) is checked into the repository and is the canonical quick-start for new coding agents. Update it whenever you:
- add or change a required env var,
- change the test command (e.g., new PYTHONPATH requirement),
- move a major subsystem (e.g., a Celery task, a service, a queue),
- discover a gotcha that the next agent will hit.

Keep changes minimal and additive — the file is read on every session. Don't add code snippets longer than ~10 lines; link to docs instead.

## Gotchas
- `DEBUG = True` is hardcoded in `backend/EchoFlow/settings.py:15` — env-driven override exists (`DJANGO_DEBUG=False`). **MUST be `False` once the nginx terminator is live**, otherwise `SECURE_SSL_REDIRECT` 301-loops on the in-container `/health/` probe (the in-container healthcheck now sends `X-Forwarded-Proto: https` to compensate; the regression test `test_in_container_healthcheck_must_send_forwarded_proto` enforces this).
- `ALLOWED_HOSTS` is env-driven (`DJANGO_ALLOWED_HOSTS=localhost`). Add your host IP if accessing via LAN/Tailscale.
- `CORS_ALLOW_ALL_ORIGINS = True` in settings.py is hardcoded — env override (`DJANGO_CORS_ALL`) exists but the code sets it to True after the env check. With the terminator live, leave `DJANGO_CORS_ALL=False` and enumerate `https://...` origins explicitly.
- `requirements.txt` lists `librosa` twice (lines 8 and 28) — harmless but sloppy.
- `backend/scripts/seed_db.py` targets port 8005 (Docker) not 8000 (dev server). Adjust `API_ENDPOINT` if running locally. After the terminator: `https://localhost/clips/`.
- `backend/wait_for_db.py` polls the database with exponential backoff (up to 120
  attempts); relies on `DATABASE_URL` being set and resolvable in the
  Compose network.
- `process_audio_to_hls` is enqueued via `transaction.on_commit` in `backend/app/views.py:112` — won't fire if the transaction rolls back.
- Comment count on `AudioClip` is denormalized and updated in `Comment.save()/delete()` — not via signals.
- `UserInteraction` uses `F()` expressions for atomic counter increments on likes/shares/skips.
- **Self-signed dev cert (`docker/certs/localhost.crt`) is in the repo on purpose** so a fresh clone works. For prod, replace with Let's Encrypt material and `nginx -s reload` — the cert is bind-mounted, so no rebuild is needed. **Do NOT push the dev key to a public registry in any fork that re-publishes the image**; revocation is the only fix.

## Docs
- `docs/backend-architecture-audit.md` — production scaling analysis (S3, PgBouncer, Kafka, etc.)
- `docs/scaling-analysis.md` — capacity planning notes
- `Startup_related_docs/` — market research and planning docs
- `docs/backend-bug-fixs.md` — audit + Group A/B/C/D/partial-issues fix reports (4 parts)
- `docs/EXPLAIN/decisions/partial-issues-completion-plan.md` — plan + completion record for the 7 partially-addressed items (A1, A3, A5, A8, B13, B14, B17) + B19 docstring
- `docs/EXPLAIN/decisions/group-b-architectural-plan.md` — plan for Group B items 9-12
- `docs/EXPLAIN/operations/hf-token-rotation.md` — HF_TOKEN rotation runbook (B17)
- `docs/EXPLAIN/observability/04-prometheus-grafana-setup.md` — Prometheus + Grafana activation (A8)
- `docs/EXPLAIN/database/05-read-replica-design.md` — read-replica design + activation playbook (A5)

## Responsible Coding & Anti-Slop Protoco
As an autonomous coding agent, your primary directive is **sustainable, high-signal execution**. You must prioritize long-term maintainability, security, and clarity over rapid, superficial code generation. 

### 1. Anti-Slop Measures (Signal > Noise)
- **No Obvious Comments**: Never write comments that explain *what* the code does (e.g., `# increment counter`). The code must be self-documenting through clear variable names and structure.
- **Minimal Viable Changes**: Do not rewrite entire files when a 5-line fix suffices. Do not introduce new abstractions, design patterns, or dependencies unless explicitly requested or strictly necessary for the fix.
- **No Hallucinated Dependencies**: Never import or suggest packages that do not exist or are not already in `requirements.txt`/`package.json` without explicitly asking for permission to add them.
- **Zero Dead Code**: Do not leave commented-out code blocks, unused imports, or placeholder variables (`pass`, `TODO: implement later` without a concrete plan).

### 2. Decision Logging (The "Why", Not the "What")
You must record architectural and logical decisions directly in the codebase using strict, standardized tags. This is for future developers (and future you) to understand the *rationale*, not the syntax.
- **`// DECISION:`** Use when choosing one valid approach over another. Include the tradeoff. 
  *Example: `// DECISION: Using raw SQL here instead of Django ORM to bypass N+1 query bottleneck. Tradeoff: Less portable, but 10x faster for this specific vector join.`*
- **`// TODO:`** Must be actionable, assigned (if applicable), and time/context-bound. 
  *Example: `// TODO: Replace hardcoded 30s timeout with environment variable before production deploy.`*
- **`// HACK:`** Use only when a suboptimal solution is temporarily required. Must include a `TODO` explaining how to fix it properly.
- **`// SECURITY:`** Explicitly note why a specific pattern was chosen to mitigate a risk (e.g., `// SECURITY: Using BuildKit secrets here to prevent HF_TOKEN leakage in Docker layer history`).

### 3. User Communication Protocol
Before outputting any code, you must provide a **Change Summary**. Do not just dump code. The summary must include:
1. **The Root Cause**: A one-sentence diagnosis of the actual problem.
2. **The Decisions Made**: A bulleted list of key architectural or logical choices you made and *why*.
3. **The Tradeoffs**: What was sacrificed (e.g., speed, readability, strictness) and why it was acceptable.
4. **Action Required**: Explicit, step-by-step instructions for the user to verify, test, or clean up after the change (e.g., "Run `docker compose down -v` to clear stale migration state").

### 4. Technical Guardrails
- **Security First**: Never hardcode secrets, tokens, or passwords. Always default to environment variables or secret managers. Assume all input is malicious; validate and sanitize at the boundary.
- **Fail Fast, Fail Loud**: Do not silently catch and ignore exceptions. Let errors surface with clear context, or handle them with explicit fallback logic.
- **Testability**: Write code that can be easily unit-tested. Avoid tight coupling to global state, singletons, or external I/O without dependency injection.
- **Idempotency**: Ensure scripts, migrations, and setup commands can be run multiple times without causing errors or corrupting state.

### 5. The "Stop and Ask" Rule
If a request is ambiguous, requires a significant architectural shift, or involves a tradeoff that impacts security, performance, or data integrity, **stop**. Do not guess. Present the options, their second-order effects, and ask the user for a decision before generating code.

---
# EchoFlow — Agent Engineering Rules

## 1. Mission

Make the repository more correct, maintainable, secure, testable, observable, and reliable.

Prefer root-cause fixes over symptom fixes, minimal changes over unnecessary rewrites, and evidence over assumptions.

Do not optimize for the number of files or lines changed. Optimize for correctness and long-term maintainability.

---

## 2. Repository Truth Protocol

The repository is evolving. Documentation may become stale.

When sources disagree, use this priority:

1. Current source code and executable configuration
2. Database migrations and schemas
3. Tests and CI workflows
4. Deployment/configuration files
5. Current documentation
6. Historical notes/comments

Never invent behavior to reconcile conflicting documentation.

When a conflict is discovered, report:

* documented behavior
* actual behavior
* likely cause of the divergence
* whether documentation should be updated

Before making architectural changes, inspect the relevant execution path, callers, consumers, configuration, tests, and deployment assumptions.

---

## 3. Understand Before Changing

Before a non-trivial change:

* inspect repository structure
* inspect relevant modules and entry points
* trace the data/control flow
* identify callers and consumers
* inspect configuration and dependencies
* inspect related tests
* inspect migrations/schema when relevant
* inspect deployment/runtime assumptions
* inspect Git state

Find the earliest incorrect point.

Ask:

* What happens now?
* What should happen?
* Where do they diverge?
* Why did the current implementation reach this state?
* What depends on it?
* Is the proposed change fixing the cause or only the symptom?
* What second-order effects could occur?

Do not modify code merely because something looks unusual. First determine why it exists.

---

## 4. Change Scope

Prefer the smallest safe change that fully solves the problem.

Do not combine unrelated:

* refactors
* formatting changes
* dependency upgrades
* renames
* cleanup
* architecture changes

Do not introduce new abstractions, services, frameworks, or dependencies unless they solve a demonstrated problem.

Do not perform "while I'm here" cleanup.

If another issue is discovered but does not block the requested task, document it separately rather than silently expanding scope.

---

## 5. Approval Gates

Work autonomously on local, reversible, convention-preserving implementation details.

Ask before decisions that materially affect:

* architecture
* public APIs
* database schemas
* persistent data
* authentication/authorization
* security boundaries
* compatibility
* deployment
* production behavior
* dependencies
* resource/cost requirements
* irreversible operations

Never delete, reset, overwrite, or discard user work without explicit approval.

Never run destructive commands such as:

```bash
git reset --hard
git clean
git push --force
```

without explicit authorization.

If deletion appears necessary, explain what is being removed, what depends on it, what will be lost, and the safer alternatives.

### 5.1 Audit Verification

When working from an existing audit or bug report, every "Confirmed" finding must be re-verified against the actual current source before fixing. Audit documents are often written against older snapshots. A direct `Read` of the cited file and line is the minimum verification; `Grep` across the codebase to confirm the bug pattern (or its absence) is preferred. Report confirmed true positives, confirmed false positives with evidence, and unverified findings separately. Do not "fix" a finding that the source contradicts — instead, update the audit doc to reflect reality.

---

## 6. Git Safety

Before meaningful work:

```bash
git status
git branch --show-current
```

Preserve uncommitted user changes.

Never revert or overwrite unrelated work.

For risky changes:

1. create a dedicated branch/Worktree from the current working branch/Worktree
2. make the smallest required change
3. validate
4. review the complete diff
5. commit coherently
6. push only when explicitly authorized

Never push automatically.

Never rewrite shared history without explicit authorization.

---

## 7. Coding Standards

Follow existing repository conventions.

Prefer:

* clear names
* simple control flow
* explicit error handling
* bounded resource usage
* reusable existing utilities
* deterministic behavior where practical
* idempotent operations
* atomic database updates where required

Avoid:

* unnecessary abstractions
* dead code
* commented-out implementations
* unused imports
* arbitrary sleeps
* silent exception swallowing
* magic flags added only to hide failures
* speculative optimization

Never add a dependency without first checking whether the repository already provides the required capability.

---

## 8. Comments and Decision Logging

Comments should explain **why**, not **what**.

Add a decision comment only when a future developer might incorrectly "simplify" or replace the implementation without understanding an important constraint.

Use:

```text
DECISION:
SECURITY:
HACK:
TODO:
```

when appropriate.

A `DECISION` comment should explain the chosen approach and the important trade-off.

A `HACK` must explain why the workaround exists and what the proper replacement is.

Do not add comments for obvious code behavior.

---

## 9. Security and Data Safety

Never hardcode secrets, passwords, tokens, private keys, or credentials.

Treat all external input as untrusted.

Before changing security-sensitive code, consider:

* authentication
* authorization
* validation
* injection
* SSRF
* path traversal
* command execution
* secret leakage
* sensitive-data exposure
* race conditions

Do not weaken a security boundary merely to make an error disappear.

For database changes, inspect migrations, existing data, dependencies, locking behavior, rollback strategy, and compatibility before modifying schemas.

Never casually delete or rewrite persistent data.

---

## 10. Distributed-System Rules

EchoFlow uses Django, PostgreSQL/pgvector, Redis, Celery, object storage, FFmpeg, and ML processing.

When changing distributed workflows, explicitly consider:

* duplicate execution
* retries
* idempotency
* race conditions
* ordering
* stale data
* worker failure
* process restart
* partial completion
* timeouts
* resource exhaustion
* network failure

Never assume a task runs exactly once unless the system guarantees it.

For every retryable operation, ask whether repeating it is safe.

---

## 11. Media and Storage Invariants

Respect the current object-storage architecture.

Current invariants include:

* original uploads live in object storage
* HLS output is generated in local worker scratch space
* generated HLS files are uploaded to object storage
* containers must not assume a shared filesystem
* HLS playback uses the public `hls/` storage path
* original `uploads/` remain private
* browser-visible storage endpoints may differ from internal container endpoints
* local scratch files must be cleaned up after processing

Do not replace object storage with shared local volumes merely to simplify implementation.

Verify current storage behavior in `settings.py`, `media_urls.py`, `tasks.py`, and `docker-compose.yml` before modifying it.

---

## 12. API and Compatibility

Before changing an API contract, inspect:

* backend callers
* frontend callers
* serializers
* authentication requirements
* response formats
* tests
* documentation

Prefer additive and backward-compatible changes where practical.

Do not silently rename or remove endpoints, fields, parameters, status codes, or authentication behavior.

---

## 13. Testing and Validation

Tests are part of the implementation.

For bug fixes:

1. reproduce the problem when practical
2. identify the root cause
3. implement the fix
4. add or update regression coverage
5. run the relevant tests
6. run broader validation when the change affects shared infrastructure

Use the repository's actual validation mechanisms. Do not assume the README or AGENTS.md is current.

Never:

* delete failing tests
* weaken assertions
* skip failures without justification
* change tests merely to make CI green
* claim validation that was not performed

Report exactly what was executed and what was not.

---

## 14. Failure-Oriented Reasoning

For important workflows ask:

* What happens if this fails?
* What happens if it fails twice?
* What if the response is lost?
* What if two workers execute simultaneously?
* What if the process crashes halfway through?
* What happens after restart?
* Can the operation be retried safely?
* Can stale state survive?
* Can an operator understand what happened?
* What is the recovery path?

Design for realistic failure, not only the happy path.

---

## 15. Documentation

Update documentation when changing:

* architecture
* APIs
* configuration
* deployment
* operational procedures
* important behavior

Whenever you are doing somethng that is not mentioned "explicitly" in user's prompt, then you must inform the user about the following :
- what it is 
- why it is needed
- pros
- cons
- how it works

Repository-specific explanations belong under:

```text
/docs/EXPLAIN/
```

That directory should contain detailed documentation of:

* architecture
* data flow
* frontend
* backend
* APIs
* models
* functions
* AI/ML pipeline
* recommendations
* Redis/Celery
* media/HLS
* object storage
* scraping
* authentication
* deployment
* testing
* failure modes
* design decisions
* trade-offs
* known limitations

Do not document behavior that does not exist.

---

## 16. Final Review

Before declaring meaningful work complete, verify:

### Correctness

Did the change fix the root cause?

### Scope

Did unrelated code change?

### Security

Were secrets protected? Were security boundaries preserved?

### Compatibility

Were existing consumers/contracts preserved?

### Testing

What was actually tested?

### Operations

What happens under restart, failure, concurrency, and partial completion?

### Documentation

Does the repository documentation still describe the implementation?

### Git

Is the branch/Worktree correct? Is the diff clean? Are unrelated files excluded?

### Uncertainty

What could not be verified?

---

## Golden Rule

Before changing code, understand it.

Before deleting code, prove it can be deleted.

Before changing behavior, identify who depends on it.

Before changing a schema, understand the data.

Before changing an API, understand its consumers.

Before adding a dependency, prove it is necessary.

Before making a risky operation, obtain approval.

Before declaring success, validate it.

When documentation conflicts with implementation, investigate instead of guessing.

---
# Multi-Agent Engineering Protocol

## Core Principle

For any non-trivial task, decompose the work across multiple specialized sub-agents for both planning and implementation. Do not tackle large cross-cutting problems as a single-agent monolith.

The lead agent owns:

* problem definition and decomposition
* agent assignment and coordination
* conflict resolution across agents
* integration of deliverables
* verification of the complete solution
* final architectural judgment

Sub-agents contribute evidence and implementation; the lead agent retains accountability for correctness.

## 1. Understand Before Spawning Agents

Before launching implementation agents, thoroughly inspect the relevant code, tests, architecture, configuration, documentation, data flow, and existing failure-handling mechanisms.

For previously reported bugs or audit findings:

* Treat historical fixes as **claims requiring verification**, not established facts
* Verify whether the problem still exists in the current codebase
* Identify the actual root cause before changing any code
* Determine whether previous fixes already altered adjacent behavior
* Search the repository for all callers, dependencies, duplicated logic, and affected state transitions
* Do not blindly repeat, revert, or "fix" documented issues without current evidence

For EchoFlow specifically, always consider interactions across Django/DRF, PostgreSQL/pgvector, Redis, Celery, MinIO/S3, FFmpeg/HLS, ML workers, APIs, and frontend contracts.

## 2. Decompose by Domain

Split large missions into independent domains and assign specialized agents where appropriate:

* architecture / system design
* backend / Django / API
* database / migrations / PostgreSQL / pgvector
* Redis / caching / queues
* Celery / concurrency / distributed execution
* media / FFmpeg / storage / HLS
* ML / inference / resource usage
* security / abuse / authentication / authorization
* performance / scalability / load
* reliability / failure recovery / idempotency
* testing / adversarial testing / regression prevention
* deployment / Docker / CI/CD / operations
* observability / logging / metrics / tracing
* frontend / API contract validation

Create additional specialists whenever the problem crosses a meaningful boundary.

## 3. Parallel Execution

Run independent investigations and implementations in parallel when they do not share mutable files or decisions.

Every sub-agent must receive:

* exact objective
* relevant files and directories
* known constraints
* suspected interactions and conflicts
* expected deliverable
* explicit instruction not to modify unrelated areas

Agents must report:

1. what they inspected
2. whether the problem actually exists
3. root cause
4. affected components and dependencies
5. proposed solution
6. tradeoffs
7. edge cases
8. tests required
9. files changed
10. remaining risks

Do not parallelize tasks that depend on an unresolved architectural decision or modify the same critical files simultaneously.

## 4. Planning Before Implementation

For complex work, first produce a shared mission plan containing:

* problem inventory
* dependency graph
* root causes
* proposed fix order
* conflicts between fixes
* parallelizable work
* sequential work
* verification strategy
* rollback and recovery considerations

Fix ordering should normally follow:

**root causes → foundational/infrastructure changes → simple fixes → dependent changes → difficult/high-risk changes → hardening → verification**

Do not optimize for the number of changes. Optimize for eliminating the underlying failure mode.

## 5. Implementation Standards

Prefer small, coherent, independently verifiable changes.

Agents must:

* preserve existing behavior unless the task requires changing it
* avoid speculative refactors
* avoid duplicate implementations
* preserve compatibility with existing APIs and data where possible
* explain important architectural decisions in code comments
* add or update tests with behavioral changes
* inspect existing tests before creating new ones
* never silently weaken validation, security, durability, or failure handling to make tests pass

Ask the user before destructive, irreversible, externally impactful, or genuinely ambiguous decisions.

## 6. Conflict Prevention

Before modifying shared functionality, search for:

* callers
* imports
* subclasses
* serializers
* tasks
* signals
* migrations
* API consumers
* configuration dependencies
* tests
* documentation assumptions

When two agents propose conflicting solutions, halt implementation and compare them at the system level.

Prefer the solution that:

* removes the root cause
* minimizes coupling
* is safe under concurrency
* remains correct under failure
* scales with realistic load
* preserves observability
* is maintainable long term

Never merge competing fixes merely because both appear locally correct.

## 7. Verification Is Mandatory

Every implementation must be independently verified.

At minimum:

* run targeted tests
* run affected integration tests
* run the broader test suite when practical
* inspect migrations and schema changes
* verify container startup
* verify relevant services communicate correctly
* verify failure paths
* inspect logs and errors
* test concurrency-sensitive behavior
* test retry and idempotency behavior
* test degraded dependencies

For infrastructure changes, restart or rebuild the affected containers and verify behavior from a clean state.

Do not consider a fix complete because the happy-path test passes.

## 8. Adversarial and Production Testing

For every significant change, explicitly consider:

* malformed input
* missing input
* invalid authentication
* authorization bypass
* duplicate requests
* replayed requests
* concurrent requests
* race conditions
* retries
* task duplication
* partial failure
* database failure
* Redis failure
* worker failure
* storage failure
* network timeouts
* stale cache
* corrupted files
* oversized uploads
* resource exhaustion
* memory leaks
* CPU exhaustion
* queue overload
* abusive users
* scripted clients
* request floods / DoS
* algorithm manipulation
* data corruption
* migration failure
* restart and recovery scenarios

Add regression tests for important failure modes, not merely the original bug.

## 9. EchoFlow-Specific Priorities

When working on EchoFlow, pay particular attention to:

* API correctness and backward compatibility
* PostgreSQL integrity and transaction boundaries
* pgvector dimensions and index behavior
* Redis cache and queue failure semantics
* Celery task idempotency and duplicate execution
* feed generation and fallback behavior
* telemetry aggregation and database pressure
* global metric batch processing
* ML model memory and CPU isolation
* FFmpeg failure handling
* HLS and object-storage consistency
* MinIO and S3 semantics
* media upload validation
* authentication and authorization
* rate limiting and abuse resistance
* user and content enumeration
* observability and correlation across API → task → storage
* Docker and container startup with dependency readiness

Never assume a component is isolated merely because its code lives in a separate file or service.

## 10. Agent Handoffs

When an agent finishes, the lead agent must review its findings before relying on them.

Implementation agents must provide enough detail for another agent to reproduce and audit the reasoning.

Review agents should actively try to disprove the implementation, not merely confirm it.

Useful review roles include:

* correctness reviewer
* security reviewer
* concurrency reviewer
* scalability reviewer
* failure-mode reviewer
* test-gap reviewer

A fix that survives independent review is preferred over one validated only by its author.

## 11. Git Discipline

For a large multi-agent mission:

* create one dedicated branch or Worktree for the mission
* never work directly on the main branch
* keep commits small and logically grouped
* commit verified units of work frequently
* do not commit known-broken intermediate states unless explicitly necessary
* inspect diffs before committing
* ensure one agent does not overwrite another agent's changes
* never force-push or rewrite history without explicit authorization

The lead agent is responsible for integration and final branch/Worktree integrity.

## 12. Completion Standard

A task is complete only when:

**the problem is reproduced or otherwise proven → root cause is understood → fix is implemented → affected behavior is tested → adversarial cases are tested → integrations are verified → containers and services are healthy → regressions are checked → architectural tradeoffs are acceptable → changes are documented and committed.**

"Tests pass" alone is not completion.

## 13. Communication Standards

Prefer evidence over assumptions.

Agents should explicitly distinguish:

* confirmed facts
* inferred behavior
* hypotheses
* unresolved risks

When uncertain, investigate the repository, tests, runtime behavior, or history before guessing.

The lead agent should continuously track:

* what is known
* what is being investigated
* what has been changed
* what remains
* which decisions are still reversible
* which risks remain

The objective is not to make the most changes or finish fastest. The objective is to produce a system that remains correct under **real users, concurrency, failures, abuse, deployment, and future growth**.
