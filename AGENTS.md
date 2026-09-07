# EchoFlow — Agent Quick-Start

## Stack
Django 5.2 / DRF 3.18 · PostgreSQL 16 + pgvector (HNSW) · Redis 7 · Celery + Celery Beat · FFmpeg (HLS) · Vite/React (frontend/) · nginx 1.27 (TLS terminator) · Prometheus + Grafana (observability) · Sentry (errors, ready-to-configure)

> **Docker is the only supported way to run EchoFlow locally.** There is no bare-metal install path. The `Dockerfile` and `docker-compose.yml` provision every dependency (Postgres+pgvector, Redis, MinIO, all Celery queues, ffmpeg, Python 3.11, ML libs, nginx, Prometheus, Grafana) in a single `docker compose up --build`. For production at small scale (~$6/month), use the hybrid deployment: `docker-compose.vps.yml` on a VPS + `docker-compose.laptop.yml` on a laptop + Cloudflare R2 for object storage. See [docs/EXPLAIN/DEPLOYMENT/01-hybrid-deployment-overview.md](docs/EXPLAIN/DEPLOYMENT/01-hybrid-deployment-overview.md).

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

> **All tests run inside the Docker `web` container against PostgreSQL.**
> The test database is `echoflow_test` — auto-created by conftest on first
> run. No SQLite, no stub migrations, no bare-metal test mode.

### Quick start (full stack + tests)

```bash
# Build and start test stack (db, redis, minio, web)
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build -d

# Run the full test suite (conftest auto-creates echoflow_test DB)
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/ --tb=short

# Run a single test file
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/test_adversarial_pass3.py -v

# Run a single test class
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/test_adversarial_pass3.py::TestN1CommentAuthorization -v

# Run the migration / config / static checks that CI runs
docker compose exec web python manage.py migrate --noinput
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py check --fail-level WARNING
docker compose exec web python manage.py collectstatic --noinput --dry-run

# Inspect coverage (with the pytest-cov plugin — installed in the image)
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/ --cov=backend.app --cov-report=term-missing

# Tear down test stack
docker compose -f docker-compose.yml -f docker-compose.test.yml down -v
```

### Hybrid Deployment (production at small scale)

The hybrid deployment splits services across a VPS (light services) and a
laptop (heavy media worker), with Cloudflare R2 for object storage:

```bash
# VPS: light services (8 containers)
git checkout feat/hybrid-vps
cp .env.vps.example .env
# Edit .env with real values
bash scripts/vps-deploy.sh

# Laptop: heavy media worker (1 container)
git checkout feat/hybrid-laptop
cp .env.laptop.example .env
# Edit .env with real values (DJANGO_SECRET_KEY must match VPS)
bash scripts/laptop-deploy.sh
```

- `docker-compose.vps.yml` — slimmed 8-service compose (removes pgbouncer,
  minio, celery_media, prometheus, grafana)
- `docker-compose.laptop.yml` — single-service compose (celery_media only)
- `scripts/vps-deploy.sh` — one-shot VPS deploy (build, migrate, collectstatic,
  Tailscale setup, daily backup cron)
- `scripts/laptop-deploy.sh` — one-shot laptop deploy (build media image,
  start worker, start heartbeat)
- `scripts/laptop-heartbeat.sh` — background heartbeat writer to Redis
- `backend/app/views/system_health.py` — `/api/v1/health/media-worker/`
  endpoint reporting laptop worker liveness

See [docs/EXPLAIN/DEPLOYMENT/](docs/EXPLAIN/DEPLOYMENT/) for full setup guides.

### Production stack + tests (when you need nginx/MinIO endpoints)

```bash
# Start full stack (db, pgbouncer, redis, minio, nginx, web, celery, etc.)
docker compose up --build -d

# Run tests against the full stack
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/ --tb=short

# Tear down
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

### BuildKit cache (named, persistent across builds)

The `Dockerfile` declares three **named** BuildKit cache mounts so cold builds skip the expensive network round-trips on subsequent runs:

| Cache ID | Mounted at | What it holds | Saved per build |
|---|---|---|---|
| `echoflow-apt` | `/var/cache/apt/archives` | Downloaded `.deb` files (`libpq-dev`, `gcc`, `ffmpeg`, `libsndfile1`, `libmagic1`, `postgresql-client`) | ~200 MB; ~1-2 min |
| `echoflow-pip`  | `/root/.cache/pip`     | pip's HTTP/wheel metadata index (resolver cache) | Seconds — only helps repeated installs in the same build |
| `echoflow-hf`   | `/home/appuser/.cache/huggingface` | Baked HF model artifacts (Whisper `base`, `all-MiniLM-L6-v2`, KeyBERT) | ~250 MB; ~5 min on `media` rebuild |

A dedicated `wheelhouse-base` stage owns the offline `./wheelhouse/` so both `py-deps-api` and `py-deps-media` reference it via `COPY --from=wheelhouse-base` — wheelhouse bytes enter the layer graph exactly once per build.

**Inspect / manage the caches:**

```bash
docker buildx du                                    # show every named cache and its size
docker buildx du --filter type=buildkit             # only BuildKit-managed caches
docker buildx prune --filter type=buildkit          # safe — never touches named caches by default
docker buildx prune --filter id=echoflow-apt        # nuke one specific cache (e.g. after adding a new apt package)
docker builder prune                                # CAREFUL — wipes dangling builders; named caches survive by default
```

**Cache invalidation rules:**
- Adding/changing a package in `Dockerfile` apt-get list invalidates the `base` stage → next build re-downloads everything → caches are repopulated transparently.
- The `wheelhouse/` directory changing (new wheels added) invalidates `wheelhouse-base` → both py-deps stages rebuild.
- HuggingFace model upgrade → invalidate manually with `docker buildx prune --filter id=echoflow-hf`. There is no automatic signal from inside the build that the upstream model changed.
- CI runners (GitHub Actions) start with empty caches — first CI build is always cold. Subsequent jobs on the same runner can reuse caches if you enable `cache-from` / `cache-to` in a CI step (not currently configured).

**What is intentionally NOT cached:**
- `/var/lib/apt/lists/` — stale package indexes can silently serve vulnerable `.deb` files. `apt-get update` runs on every build; security wins over re-download speed.

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
| `HF_TOKEN` | HuggingFace token (model baking at build time). See [docs/EXPLAIN/operations/hf-token-rotation.md](docs/EXPLAIN/operations/hf-token-rotation.md) for the rotation runbook. |
| `OPENAI_API_KEY` | Optional — reserved for OpenAI pipeline branch |
| `FREESOUND_API_KEY` | Required only for freesound scraper |
| `SEED_AUTH_TOKEN` | Auth token for `seed_db.py` |
| `GUNICORN_WORKERS` | Default gunicorn workers (default: 4) |
| `GUNICORN_THREADS` | Default gunicorn threads (default: 4) |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts. Must include every host the nginx terminator is reached at (`localhost`, your prod hostname, any Tailscale/CNAMES). Default: `localhost`. |
| `DJANGO_CORS_ALLOWED_ORIGINS` | Comma-separated **https://** origins. Every browser-reachable origin MUST be `https://` once the terminator is live — `http://` here causes mixed-content / CORS preflight failures. |
| `PUBLIC_MEDIA_ENDPOINT_URL` | Browser-facing MinIO origin for HLS playback. **Must be `https://`** (e.g. `https://localhost:9443` in dev). `AWS_S3_ENDPOINT_URL` (containers' in-network URL) stays `http://minio:9000`. |
| `MEDIA_TOKEN_SECRET` | HMAC signing key for HLS playback tokens. Shared between Django (issuance) and the Cloudflare Worker or nginx njs (validation). Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Must match the Worker secret set via `npx wrangler secret put MEDIA_TOKEN_SECRET`. See `docs/EXPLAIN/storage/04-hls-token-protection.md`. |
| `MEDIA_TOKEN_TTL_SECONDS` | HLS token time-to-live in seconds. Default `600` (10 min). |
| `MEDIA_TOKEN_COOKIE_DOMAIN` | Cookie `Domain` attribute for the HLS token cookie. Set to parent domain (e.g. `.echo-flow.in`) for cross-subdomain cookies in production. Leave empty for dev (`localhost`). |
| `SENTRY_DSN` | Optional. When set, the `sentry-sdk` in each process captures uncaught exceptions. Get a DSN from sentry.io (free tier works). |
| `SENTRY_ENV` | Sentry environment tag (e.g. `production`, `staging`). Default: `production`. |
| `SENTRY_TRACES_SAMPLE_RATE` | Fraction of requests traced (0.0-1.0). Default: `0.1`. Lower for high-traffic. |
| `SENTRY_PROFILES_SAMPLE_RATE` | Fraction of profiled requests. Default: `0.05`. |
| `GRAFANA_ADMIN_PASSWORD` | Initial admin password for Grafana (first-boot only). Required — Grafana v11 refuses to start without one. |
| `GRAFANA_ADMIN_PASSWORD` | Initial admin password for Grafana (first-boot only). Required — Grafana v11 refuses to start without one. |
| `TERMS_VERSIONS` | Comma-separated consent versions (e.g. `v1.0,v1.1`). Used by `RegisterSerializer` and `ConsentAudit` (`terms_version_id`). Default: `v1.0`. See `settings.py:622`. |
| `COMPLIANCE_OFFICER_NAME` | Chief Compliance Officer name (IT Rules 2021 Rule 4(1)(b)). Served by `/legal/compliance/`. Default: `EchoFlow Compliance Officer`. |
| `COMPLIANCE_OFFICER_EMAIL` | CCO email. Default: `compliance@echoflow.in`. |
| `GRIEVANCE_OFFICER_NAME` | Grievance Officer name (IT Rules 2021 Rule 4(1)(a)). Default: `EchoFlow Grievance Officer`. |
| `GRIEVANCE_OFFICER_EMAIL` | Grievance email. Default: `grievance@echoflow.in`. |
| `NODAL_CONTACT_NAME` | Nodal Contact name (IT Rules 2021 Rule 4(1)(c)). Default: `EchoFlow Nodal Contact`. |
| `NODAL_CONTACT_EMAIL` | Nodal email. Default: `nodal@echoflow.in`. |
| `AWS_S3_REGION_NAME` | **Must be `ap-south-1`** (or `ap-south-2`) for DPDP cross-border + RBI data-localisation compliance. `STORAGES` uses this (`settings.py:467`). Default in `.env.example`: `auto` — production must override. |
| `PHYSICAL_ADDRESS` | Registered office / physical address (IT Rules 2021 / Consumer Protection). Not yet exposed in `/legal/compliance/` endpoint (open). |
| `REVENUECAT_SECRET_KEY` | **Backend-only secret.** RevenueCat secret API key from dashboard → Project Settings → API keys. Never expose to frontend. Required for REST API polling. |
| `REVENUECAT_PUBLIC_KEY` | **Frontend-safe public key.** Used by `@revenuecat/purchases-js` SDK initialization. Safe to expose via `VITE_REVENUECAT_PUBLIC_KEY`. |
| `REVENUECAT_PROJECT_TOKEN` | RevenueCat project token (SDK identifier). |
| `REVENUECAT_ENTITLEMENT_ID` | Entitlement ID string in RevenueCat dashboard (default: `pro`). |
| `REVENUECAT_SYNC_INTERVAL_MINUTES` | Poll interval for Celery Beat task (default: `360` = 6 hours). |
| `REVENUECAT_CUSTOMER_PORTAL_URL` | Optional override for Customer Portal base URL. |
| `REVENUECAT_WEBHOOK_SECRET` | HMAC secret for webhook verification (Phase 2 — configure when enabling webhooks). |
| `REVENUECAT_DAILY_UPLOAD_LIMIT_FREE` | Free tier daily upload limit (default: `5`). |
| `REVENUECAT_UPLOAD_MAX_SIZE_MB_FREE` | Free tier max upload size in MB (default: `10`). |
| `REVENUECAT_CLIP_DURATION_LIMIT_FREE` | Free tier max clip duration in seconds (default: `60`). |
| `REVENUECAT_HD_QUALITY_BLOCKED_FREE` | Block HD quality for free users (default: `True`). |


## Indian Regulatory Compliance — Backend Changes

This section documents all backend changes made to comply with:
- **DPDP Act 2023** (Digital Personal Data Protection Act) — consent, children's data, DPO, breach notification, cross-border
- **IT Rules 2021** (Intermediary Guidelines) — grievance officer, nodal contact, compliance officer, traceability, content moderation
- **CERT-In Directions 2022** — 180-day log retention, 6-hour breach notification
- **Copyright Act 1957** — user upload licensing, attribution
- **Consumer Protection (E-Commerce) Rules 2020** — grievance redressal, country of origin
- **RBI Data Localisation** — financial data must reside in India

### Phase A — DPDP Consent & Age Gating (COMPLETED)

**Models (`backend/app/models.py`):**
- Added `User.is_minor` (BooleanField, default=False) — computed from DOB at registration
- Added `User.minor_consent_verified` (BooleanField, default=False) — parent consent for minors
- Added `User.consent_accepted` (BooleanField, default=False) — explicit consent flag
- Added `User.dob` (DateField, nullable) — date of birth for age gate
- Added `User.parent_email` (EmailField, nullable) — for minor consent flow
- Added `ConsentAudit` model (lines 61-77) — immutable audit trail: `user`, `consent_issued_at`, `terms_version_id`, `privacy_version_id`, `ip_address`, `user_agent`, `withdrawn_at`, `identity_retained_until` (CERT-In 180-day retention)
- Added `CheckConstraint` on `AudioClip.likes`, `shares`, `skips`, `comment_count` >= 0 (DB-level negative counter prevention)

**Serializers (`backend/app/serializers.py`):**
- `RegisterSerializer` now requires `consent_accepted` (BooleanField, required=True) and `terms_version` (validated against `TERMS_VERSIONS` env var)
- Added `dob` and `parent_email` fields for age gate
- Validation logic computes `is_minor` from DOB; if minor, `minor_consent_verified` defaults False (requires parent flow)
- Creates `ConsentAudit` row on successful registration (audit trail persists even if user creation rolls back)
- Magic-byte audio validation (lines 16-21, 128-133) — pure-Python allowlist + python-magic layer-2 check before ffmpeg
- Copyright acknowledgment enforcement (lines 178-191) — user must acknowledge before DB persistence
- Duration probe at upload (lines 236-251) — prevents 24h WAV abuse via pydub/ffprobe
- Comment text sanitization (lines 350-365) — null-byte / control-char stripping
- `watch_time_ms` capped at 10h (lines 373-376) — prevents viewbot inflation

**Views (`backend/app/views/auth.py`):**
- Registration endpoint accepts consent fields, creates `ConsentAudit` via serializer
- `/auth/register/` returns access + refresh tokens with consent confirmation

**Tests (`backend/app/tests/test_auth_regulatory.py`, `test_security_and_validation.py`):**
- `test_register_success` validates consent fields required
- `test_user_has_dob_and_computed_is_minor` uses `date()` objects for DOB
- Compliance endpoint requires auth + returns JSON

### Phase B — Grievance & Compliance Officers (COMPLETED)

**Models (`backend/app/models.py`):**
- Added `Grievance` model (lines 269-295) — DB table per audit: `user`, `category`, `description`, `status`, `assigned_officer`, `resolution`, `created_at`, `resolved_at`, `escalated`, `ip_address`, `user_agent`
- `Grievance.category` choices: `content`, `privacy`, `account`, `payment`, `other`
- `Grievance.status` choices: `open`, `in_progress`, `resolved`, `rejected`, `escalated`
- Added `AuditLog` model (lines 297-320) — CERT-In 180-day log retention: `user`, `action`, `resource_type`, `resource_id`, `metadata`, `ip_address`, `user_agent`, `created_at`
- `AuditLog` indexes on `(user, -created_at)` and `(resource_type, resource_id)`

**Settings (`backend/EchoFlow/settings.py`):**
- Env-driven regulatory contacts (lines 643-657): `COMPLIANCE_OFFICER_EMAIL`, `GRIEVANCE_OFFICER_EMAIL`, `NODAL_CONTACT_EMAIL` (with defaults)
- `TERMS_VERSIONS` env var (comma-separated) for consent versioning
- `AWS_S3_REGION_NAME` assertion for `ap-south-1` / `ap-south-2` (DPDP + RBI)

**Views (`backend/app/views/data_subject.py`):**
- `/legal/compliance/` — returns officer contacts (IT Rules 4(1)(a)(b)(c))
- `/auth/consent/withdraw/` — sets `ConsentAudit.withdrawn_at`, triggers 30-day cooling-off soft-delete (DPDP §14)
- `/auth/data/export/` — DPDP §14 data portability: exports all user data as JSON
- `/auth/data/delete/` — DPDP §14 right to erasure with CERT-In retention override

**Tests (`backend/app/tests/test_system_health.py`, `test_auth_regulatory.py`):**
- Grievance endpoint validation
- Compliance endpoint requires auth + returns JSON

### Phase C — Content Moderation Pipeline (COMPLETED)

**Services (`backend/app/services/content_moderation.py`):**
- v1 offline moderation: `sha256` fingerprint of normalized file + blocked-phrase list against lowercase transcript + AI tags
- `AudioClip.moderation_approved` boolean gate (models.py:113) — HLS generation only runs when True
- `process_audio_to_hls` task checks `moderation_approved` before processing
- `FINGERPRINT_BLOCKLIST` module-level set (TODO: move to Redis for production)

**Uploads (`backend/app/services/uploads.py`):**
- `trigger_hls_processing` enqueues task only after moderation approval
- `finalize_upload` no longer enqueues HLS task (flow changed)

### CERT-In 180-Day Log Retention (COMPLETED)

**Models (`backend/app/models.py`):**
- `AuditLog` with `identity_retained_until = created_at + 180 days` (CERT-In §5(1))
- `ConsentAudit.identity_retained_until = consent_issued_at + 180 days`
- `Grievance` retains user identity for 180 days post-resolution

**Middleware (`backend/app/middleware.py`):**
- Request/response audit logging (lines 292-293) — DB write overhead accepted for audit trail
- Correlation ID propagation for cross-service tracing

### S3 Region Enforcement (COMPLETED)

**Settings (`backend/EchoFlow/settings.py`):**
- `STORAGES["default"]["OPTIONS"]["region_name"]` asserted to `ap-south-1` / `ap-south-2` / `auto` (lines 492-498)
- Signed S3 URLs instead of public bucket (lines 467-479)

### Environment Variables Required (see above)

| Variable | Purpose | Default |
|---|---|---|
| `TERMS_VERSIONS` | Comma-separated consent versions (e.g. `v1.0,v1.1`) | `v1.0` |
| `COMPLIANCE_OFFICER_EMAIL` | CCO email (IT Rules 4(1)(b)) | `compliance@echoflow.in` |
| `GRIEVANCE_OFFICER_EMAIL` | Grievance email (IT Rules 4(1)(a)) | `grievance@echoflow.in` |
| `NODAL_CONTACT_EMAIL` | Nodal contact email (IT Rules 4(1)(c)) | `nodal@echoflow.in` |
| `AWS_S3_REGION_NAME` | **Must be `ap-south-1` or `ap-south-2`** for DPDP/RBI | `auto` (prod must override) |
| `PHYSICAL_ADDRESS` | Registered office (IT Rules / Consumer Protection) | Not yet exposed |

### Remaining Gaps (Open)

- Public clip endpoint needs `moderation_approved` filter (TODO in `docs/INDIA-REGULATORY-READINESS.md`)
- Multilingual India-specific prohibited-content database to replace blocked phrase list (TODO in `services/content_moderation.py:19-20`)
- Transcript text persistence from `process_audio_to_hls` task (TODO in `services/content_moderation.py:167-175`)
- Takedown workflow endpoint (`POST /clips/{id}/takedown/`)
- `pydub` temp-file stream for memory pressure (TODO in `serializers.py:250`)


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

# RevenueCat Pro subscription management (Phase 1: REST polling only)
GET  /subscription/           # Current Pro status + usage limits
POST /subscription/sync/      # Trigger immediate sync with RevenueCat (rate-limited)
GET  /subscription/manage/    # RevenueCat Customer Portal URL for self-service
POST /webhooks/revenuecat/    # Webhook endpoint (Phase 2 — HMAC verified when REVENUECAT_WEBHOOK_SECRET set)
```

## Architecture Notes
- **Dual `EchoFlow/`**: Project package (`backend/EchoFlow/settings.py`, `urls.py`, `celery.py`) vs app package (`backend/app/`). Don't confuse them.
- **Custom user model**: `backend.app.User` (extends `AbstractUser`). Set via `AUTH_USER_MODEL = 'backend.app.User'`.
- **Recommendation engine**: Composite scoring = 45% vector similarity + 30% avg completion rate + 25% engagement velocity. 80% exploit / 20% explore feed mixing.
- **Redis feed queues**: Per-user `user_feed:{id}` lists. `FastFeedViewSet` pops 10 at a time; refills trigger when queue < 15.
- **Vector fields**: `semantic_vector` (384-dim, from transcript via sentence-transformers), `acoustic_vector` (128-dim, from librosa). HNSW indexes (`m=16, ef_construction=64`) on both.
- **Celery task routing**: `process_audio_to_hls` → `heavy_media` queue; `refill_user_feed` → `fast_feed` queue; `cleanup_orphan_hls` → 03:00 UTC daily; `flush_counters_to_pg` → every 300s (defined in `backend/EchoFlow/settings.py` `CELERY_TASK_ROUTES` + `CELERY_BEAT_SCHEDULE`).
- **ML models lazy-loaded**: `get_whisper_model()`, `get_embedding_model()`, `get_kw_model()` in `backend/app/tasks.py` — initialized on first task call, not at import time.
- **`update_global_metrics`** is a no-op stub (deprecated 2026-09). All three responsibilities (counter deltas, avg_completion_rate, engagement_velocity) live in `flush_counters_to_pg`. The Celery Beat entry is kept for one cycle so a missing task name surfaces as a deployment error.
- **Event-driven metrics pipeline**: user interactions (`record_like_toggle`, `record_skip`, `record_share`, `record_telemetry` Tier-3 fallback) write to Redis via `counter_store.increment` / `add_completion` (O(1) on the request path). `flush_counters_to_pg` (every 5 min) drains the deltas and applies them to Postgres in batched UPDATEs that touch only the dirty clip set. No correlated subquery, no full-table scan. See [docs/EXPLAIN/decisions/event-driven-metrics.md](docs/EXPLAIN/decisions/event-driven-metrics.md).
- **Read replica routing**: `backend/app/db_routers.py` is a 71-line `ReadRouter` with 4 hooks (db_for_read/db_for_write/allow_relation/allow_migrate). Auto-activates when `READ_DATABASE_URL` is set; the `if not atomic and not SELECT FOR UPDATE` guard prevents stale-read races inside write transactions. See [docs/EXPLAIN/database/05-read-replica-design.md](docs/EXPLAIN/database/05-read-replica-design.md).
- **Per-session DB timeouts**: `backend/EchoFlow/settings.py` sets `statement_timeout=30s`, `idle_in_transaction_session_timeout=60s`, `lock_timeout=10s`, `connect_timeout=10s` on the default connection via libpq `options` string. Critical behind PgBouncer (25-conn pool); a slow query that held a backend connection could otherwise exhaust the pool. Gated on `ENGINE.endswith('postgresql')` so non-Postgres backends are unaffected.
- **Cache invalidation**: `services/interactions.py::invalidate_user_vectors_cache` is called from `record_like_toggle`, `record_skip`, `record_share`, and `record_telemetry`'s sync fallback via `transaction.on_commit`. The `flush_telemetry_stream` consumer invalidates each unique user's cache after a successful `bulk_create`. Stale-vector window collapsed from 15 min to near-zero for all user-state-mutating paths.
- **Counter store (event-driven, no dual-write)**: `services/counter_store.py` writes user-engagement counters to Redis (`INCRBY` for likes/shares/skips; `INCRBYFLOAT` + `INCR` for per-(user,clip) completion). The `UserInteraction.save()` F() side-effect was removed in the 2026-09 metrics rewrite; `flush_counters_to_pg` is the only path from Redis to Postgres. `ECHOFLOW_DUAL_WRITE_COUNTERS` is now a no-op (always False) and slated for deletion. See [docs/EXPLAIN/decisions/event-driven-metrics.md](docs/EXPLAIN/decisions/event-driven-metrics.md).
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

## RevenueCat Pro Subscription Integration

EchoFlow uses **RevenueCat Billing** (Stripe-backed) for Pro subscription management. This section covers the architecture, gating strategy, and operational details.

### Architecture Overview

| Component | Technology | Notes |
|---|---|---|
| Billing engine | RevenueCat Billing (Stripe) | Indian users excluded per policy |
| Subscription tier | Single "Pro" entitlement | `REVENUECAT_ENTITLEMENT_ID=pro` |
| Sync mechanism | REST API polling (Celery Beat) | Every 6h (configurable), no webhooks in Phase 1 |
| App User ID mapping | `User.uuid` (UUID4) | Immutable, survives username/email changes |
| Pro state cache | Django User model fields | `has_pro_entitlement`, `pro_expires_at`, `pro_grace_until` |
| Webhook support | Forward-compatible endpoint | HMAC-SHA256 verification gated on `REVENUECAT_WEBHOOK_SECRET` |

### Data Flow

```
Frontend (purchases-js SDK)        Backend                        RevenueCat API
        │                            │                                    │
        ├─── purchase ──────────────→│                                    │
        │                             │                                    │
        ├─── identify(app_user_id) ──→│                                    │
        │                             │                                    │
        │                             │─── GET /subscribers/{id} ─────────→│
        │                             │                                ←───┤
        │                             │─── update user fields ────────────→│(DB)
        │                             │                                    │
        │←─── show Pro features ───────│                                    │
        │                             │                                    │
        ├─── GET /subscription ───────→│                                    │
        │←─── 200 {is_pro: true} ──────│                                    │
        │                             │                                    │
        ├─── GET /manage ─────────────→│                                    │
        │←─── {url: "https://..."} ────│                                    │
```

### Gating Strategy: Usage Limits (Option A)

Pro gating is enforced via usage limits checked at request time — no feature flags, just hard limits:

| Feature | Free Limit | Pro Limit |
|---|---|---|
| Daily uploads | 5 clips | Unlimited |
| Max clip duration | 60 seconds | 300 seconds (MAX_DURATION_SECONDS) |
| Upload file size | 10 MB | 100 MB |
| HD quality (48kHz+) | Blocked | Allowed |
| Audio quality | 128 kbps | 320 kbps |

**Enforcement points:**
- `AudioUploadSerializer.validate()` — free-tier file size limit (before DB)
- `AudioUploadViewSet.create()` — daily upload count limit (before serializer)
- `process_audio_to_hls` task — clip duration + quality limits
- Feed views — HD quality filtering

### Grace Period Handling

During RevenueCat's billing grace period (3 days for annual/monthly), `User.is_pro()` returns `True` until the grace period ends AND the subscription is in a non-active state.

```python
def is_pro(self) -> bool:
    now = timezone.now()
    if self.has_pro_entitlement and self.pro_expires_at and self.pro_expires_at > now:
        return True
    if self.pro_grace_until and self.pro_grace_until > now:
        return True
    return False
```

The grace period end date is stored in `pro_grace_until` so Pro features continue during grace even if polling is delayed.

### Sync Strategy: REST API Polling

No webhooks in Phase 1 (free RevenueCat plan). A Celery Beat task polls the REST API every `REVENUECAT_SYNC_INTERVAL_MINUTES` (default 360 = 6h):

```
GET https://api.revenuecat.com/v1/subscribers/{app_user_id}
```

Response parsed for:
- Active entitlements (`is_active`, `expires_date_ms`, `grace_period_expire_date_ms`)
- Updates `has_pro_entitlement`, `pro_expires_at`, `pro_grace_until`, `pro_last_synced`

If polling indicates subscription inactive AND grace period expired, `has_pro_entitlement` is set to `False`.

### Webhook Placeholder (Phase 2)

```
POST /api/v1/webhooks/revenuecat/
```

Currently returns 200 and logs payload. Full HMAC-SHA256 verification will be implemented when upgrading to RevenueCat Pro plan. The `X-RevenueCat-Signature` header is verified against `REVENUECAT_WEBHOOK_SECRET`.

### Public vs Secret API Keys

| Key | Location | Safe for Frontend? |
|---|---|---|
| `REVENUECAT_PUBLIC_KEY` | Frontend `.env` (`VITE_REVENUECAT_PUBLIC_KEY`), backend `.env` | Yes — SDK initialization only |
| `REVENUECAT_SECRET_KEY` | Backend `.env` only | **No** — REST API polling, never expose |

### Environment Variables

All RevenueCat env vars are documented in the [Environment Variables](#environment-variables-required) table above. Key production settings:

```bash
REVENUECAT_SECRET_KEY=sk_live_xxx       # Backend-only, required
REVENUECAT_PUBLIC_KEY=pk_live_xxx       # Frontend-safe
REVENUECAT_ENTITLEMENT_ID=pro
REVENUECAT_SYNC_INTERVAL_MINUTES=360    # 6 hours
```

### Testing

- Backend: `backend/app/tests/test_revenuecat.py` — 21 tests covering `is_pro()`, `sync_entitlements`, views, webhook, free-tier limits
- Frontend: `frontend/sample_frontend/src/stores/__tests__/subscription.test.tsx` + `src/components/subscription/__tests__/Paywall.test.tsx` — 6 tests using vitest + @testing-library/react

### Operational Notes

- **Manual sync**: `POST /subscription/sync/` triggers immediate RevenueCat poll (rate-limited: 10/hour)
- **Customer Portal**: `GET /subscription/manage/` returns RevenueCat-hosted management URL
- **App User ID**: Maps to `User.uuid` (stringified). Created on user registration via `revenuecat_app_user_id` field
- **Frontend SDK init**: `Purchases.setup(publicKey, appUserId)` in `main.tsx` after auth context available
- **Paywall**: `Paywall.tsx` component shows upgrade CTA, calls `subscriptionAPI.sync()` then redirects to Customer Portal

## Testing & Linting
- Test framework: **pytest** + `pytest-django`, installed in the `api` image. Run via `docker compose exec web pytest …` — see [Running Tests](#running-tests) for the full command set.
- Test files live under `backend/app/tests/` (23 files: `test_adversarial_pass3.py`, `test_counter_store.py`, `test_db_router.py`, `test_feed_pool.py`, `test_https_termination.py`, `test_integration_concurrency.py`, `test_integration_pgvector.py`, `test_metrics_endpoint.py`, `test_metrics.py`, `test_observability_tui.py`, `test_orphan_cleanup.py`, `test_scraper.py`, `test_security_and_validation.py`, `test_sentry.py`, `test_services_comments.py`, `test_services_follows.py`, `test_services_interactions.py`, `test_services_shares.py`, `test_services_uploads.py`, `test_settings.py`, `test_smoke.py`, `test_system_health.py`, `test_task_publisher.py`).
- All tests run against PostgreSQL in Docker. No SQLite fallback.
- No linting/formatter config (no `.eslintrc` at root, no `pyproject.toml`, no `ruff.toml`).
- CI: `.github/workflows/django.yml` runs migrations + the test suite via Docker. Blocks merges on failure.
- **Root cause of 178 `auth_group does not exist` errors:** The old conftest.py used a SQLite override hack that bypassed real migrations. The fix was to make Docker/Postgres the only test environment. The new `conftest.py` auto-creates `echoflow_test` DB, installs pgvector on `template1`, and handles session teardown.
- **docker-compose.test.yml** — test-only stack (db, redis, minio, web). No nginx, no celery workers. Run with: `docker compose -f docker-compose.yml -f docker-compose.test.yml up --build -d` then `docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/ --tb=short`.
- **HNSW index EXPLAIN test gotcha:** `SET LOCAL enable_seqscan = OFF` requires an active transaction. Wrap it in `transaction.atomic()` to ensure it takes effect. Also verify the index type via `pg_am.amname` as a primary check (not just the EXPLAIN plan, which may choose Seq Scan for small tables).
- **S3 storage in tests:** Use `default_storage.exists(clip.original_file.name)` instead of `os.path.exists(clip.original_file.path)` — `.path` raises `NotImplementedError` on S3 storage backends (MinIO).
- **Conditional skip pattern for system binaries:** Use `@unittest.skipUnless(_ffmpeg_available, "requires ffmpeg on PATH")` where `_ffmpeg_available = shutil.which('ffmpeg') is not None`. This passes in Docker (ffmpeg installed) and skips on bare-metal dev.
- **F() expressions for atomic updates in concurrency tests:** Use `F('likes') + 1` instead of read-modify-write patterns (`obj.likes = obj.likes + 1`) to avoid race conditions.
- **date() objects for DOB fields:** Use `date(1990, 1, 1)` instead of string literals for date fields to avoid type errors.
- **trigger_hls_processing vs finalize_upload:** The upload flow changed; use `trigger_hls_processing` instead of the old `finalize_upload` in test fixtures.
- **cache import in adversarial tests:** Some test files need `from django.core.cache import cache` to work with Django's test cache backend.
- **postgresql assertion in smoke tests:** Changed from `sqlite3` to `postgresql` in smoke test assertions to match the Docker-only test environment.

### Known Skipped / Disabled Tests (environmental, not regressions)

The following tests are **conditionally skipped** with `@unittest.skipUnless(_ffmpeg_available, ...)` because they require `ffmpeg` on `PATH`. The `api` Docker image already installs ffmpeg (in the `base` stage of the Dockerfile), so these tests **pass in Docker**. If running on a bare-metal dev machine without ffmpeg, they will be skipped:

| Test | Reason | How to enable locally |
|------|--------|------------------------|
| `backend/app/tests/test_scraper.py::ScraperUnitTests::test_normalizer_trims_to_max_seconds` | Requires `ffmpeg` on `PATH` (used by `pydub` for MP3 export) | `sudo apt install ffmpeg` (Debian/Ubuntu/Pop!_OS) or `brew install ffmpeg` (macOS) |
| `backend/app/tests/test_scraper.py::ScraperUnitTests::test_uploader_creates_audioclip` | Same — `ffmpeg` required for `normalizer.normalize_and_trim` | Same as above |

The following nginx HTTPS termination tests require the `nginx` container (not part of the test stack):

| Test | Reason |
|------|--------|
| `backend/app/tests/test_https_termination.py::TestNginxConfig::test_nginx_parses_with_no_errors` | Requires `nginx` on `PATH` (only in the full Docker stack) |
| `backend/app/tests/test_https_termination.py::TestLiveNginxTerminator::*` | Live HTTP/HTTPS requests against nginx (not in test stack) |

These use `@unittest.skip(...)` for nginx (binary not available) and the full `docker compose up` stack for the live terminator tests. They pass when running the full stack.

If you add a test that needs a system binary not present in the Docker image, follow the same pattern: `@unittest.skip("requires <binary> on PATH; see AGENTS.md")`.

**Do NOT** comment-out or remove tests that fail for reasons you don't understand. If a test fails and the cause is unclear, debug it: run with `pytest --tb=long`, read the traceback, search the codebase for the operation being tested, and check whether the test environment matches the AGENTS.md prerequisites (Python 3.11, Postgres 16, Redis 7, FFmpeg on `PATH`, `docker compose` running). Only after you understand WHY a test fails — and the cause is environmental, not a code bug — should you add a skip with a clear reason.

### Local `.env` discipline

- `.env` is **gitignored**. Do not commit it. The boilerplate is `.env.example`, `.env.vps.example`, and `.env.laptop.example`; copy one of those to `.env` and edit locally. The `.gitignore` allows committing `*.example` files (see `.gitignore` exception rules for `.env.*.example`).
- Tracked env files must have `DJANGO_DEBUG=False`. CI runs `scripts/check_no_tracked_env.sh` on every PR; a tracked env file with `DJANGO_DEBUG=True` will block the merge.
- `HF_TOKEN` and `DJANGO_SECRET_KEY` in your local `.env` are real secrets. If you accidentally commit them, rotate them immediately.

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
- **HLS token cookies**: The `ef_hls_token` cookie must have `SameSite=Lax` (not `Strict`) so it's sent on top-level navigation from `app.echo-flow.in` to `media.echo-flow.in` (SameSite=Lax permits cookies on same-site top-level navigations, but blocks cross-site). `Secure` requires HTTPS on both `api.echo-flow.in` and `media.echo-flow.in`. In dev, `Domain` attribute must be empty (localhost doesn't support domain cookies). See `docs/EXPLAIN/storage/04-hls-token-protection.md`.
- **HLS token secret sync**: In production, `MEDIA_TOKEN_SECRET` must be **identical** in the VPS `.env` (Django issuance) and the Cloudflare Worker secret (`npx wrangler secret put MEDIA_TOKEN_SECRET`). If these diverge, all HLS playback returns 403.
- **RFC 3986 §5.2.2 — Signed URLs don't work for HLS**: The master playlist references variant playlists and segments via relative paths. RFC 3986 §5.2.2 strips query strings during relative-reference resolution, so signed URLs (which rely on query parameters) fail on the second and subsequent HLS requests. **Signed cookies are the only viable token mechanism for HLS.** This applies to any multi-file streaming protocol (HLS, DASH, Smooth Streaming).
- **fetch `credentials: 'include'` for Set-Cookie**: When using `fetch()` to call an endpoint that sets an HttpOnly cookie via `Set-Cookie`, the fetch request **must** include `credentials: 'include'` (or `'same-origin'`). Without it, the browser silently discards the Set-Cookie header. This is a common gotcha when building token-issuance endpoints.
- **HLS token endpoint returns Set-Cookie, not JSON body**: The `/media/playback-token/<clip_id>/` endpoint sets the token as a cookie and returns `{"status": "ok"}`. The frontend must NOT read the token from the response body — it's set as an HttpOnly cookie and auto-sent by the browser on all `/hls/*` requests.

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
- `docs/EXPLAIN/DEPLOYMENT/` — Hybrid deployment documentation (VPS + laptop + Cloudflare R2 + Tailscale)
- `docs/EXPLAIN/storage/04-hls-token-protection.md` — Short-lived HLS play token design (signed cookies + Cloudflare Worker / nginx njs)

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

Understand the architecture and how everything works to make sure you write code. Have a very detailed understanding of the code implimentation and be sure to discus these details with operator at very high verbosity and clarity.

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
git worktree list
```

Preserve uncommitted user changes.

Never revert or overwrite unrelated work.

For risky changes:

1. Always create a dedicated branch/Worktree from the current working branch/Worktree
2. make the smallest required change
3. validate
4. review the complete diff
5. commit coherently
6. push only when explicitly authorized

Never push automatically.
Command to add a new worktree : git worktree add <path-to-new-directory> -b <new-branch-name>
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

### DECISION / HACK / SECURITY / TODO tag patterns used in Agents 1-4

The following patterns were applied across changed files (`serializers.py`, `middleware.py`, `models.py`, `settings.py`, `services/content_moderation.py`, `urls.py`, `views/auth.py`, `tests/test_auth_regulatory.py`):

- **`DECISION:`** — `models.py:62-65` (DB audit table over file logs); `models.py:103-105` (DB-level negative counter constraints); `models.py:145-149` (CheckConstraint migration required); `middleware.py:292-293` (DB write overhead accepted for audit); `serializers.py:240-244` (pydub over ffprobe); `services/content_moderation.py:7` (v1 sha256 + blocked phrase list, offline); `services/content_moderation.py:92-94` (sha256 fingerprint sufficient for v1); `serializers.py:124` (serializer-level file validation before model); `settings.py:172-189` (psycopg2 `options` string for timeouts); `settings.py:204-233` (read-replica activation only when `READ_DATABASE_URL` set); `settings.py:245-252` (split Redis to prevent feed-spike eviction of queued tasks); `settings.py:454-456` (STORAGES dict over deprecated STATICFILES_STORAGE); `settings.py:621-629` (env-driven regulatory contacts); `AGENTS.md` (this note).
- **`SECURITY:`** — `serializers.py:16-21` (pure-Python magic-byte allowlist as first defense); `serializers.py:128-133` (python-magic layer-2 check); `serializers.py:178-191` (copyright acknowledgment enforcement before DB persistence); `serializers.py:236-251` (duration probe at upload time to prevent 24h WAV abuse); `serializers.py:373-376` (watch_time_ms capped at 10h to prevent viewbot inflation); `serializers.py:350-365` (comment text null-byte / control-char stripping); `middleware.py:60` (audit DB failure never breaks request); `settings.py:86-88` (token_blacklist + rotation); `settings.py:467-479` (signed S3 URLs instead of public bucket); `models.py:192-195` (user identity retention for CERT-In); `services/content_moderation.py:12-14` (blocked-phrase check against lowercase transcript); `tests/test_auth_regulatory.py:52-60` (compliance endpoint requires auth + returns JSON).
- **`HACK:`** — `models.py:294-295` (audit endpoint uses path only, no query params, to limit PII); `middleware.py:47-48` (audit DB write in finally block may fail silently if DB down — acceptable tradeoff); `serializers.py:246-250` (reading full upload into memory for pydub; needs temp-file stream if memory pressure grows); `services/content_moderation.py:16-18` (fingerprint blocklist is module-level set, not DB/Redis — production upgrade needed); `services/content_moderation.py:167-175` (AudioClip has no `transcript_text` field; moderation skips transcript check if missing — proper integration requires task-level transcript persistence).
- **`TODO:`** — `serializers.py:250` (temp-file stream for pydub); `services/content_moderation.py:19-20` (multilingual India-specific prohibited-content database; replace blocked phrase list); `settings.py:378-379` (remove `flush_telemetry_legacy` after one stable cycle); `services/content_moderation.py:167-175` (transcript text persistence from `process_audio_to_hls` task); `docs/INDIA-REGULATORY-READINESS.md` (public clip endpoint needs `moderation_approved` filter).

### Content Moderation Pipeline Design Note (Agent 2 / v1)

The moderation pipeline (`services/content_moderation.py`) uses:

1. `sha256` fingerprint of normalized file content (`DECISION`: sufficient for v1, no external dependency).
2. Blocked-phrase substring match against lowercase transcript and AI tags (`SECURITY`: defense-in-depth before HLS generation).
3. `AudioClip.moderation_approved` boolean (`models.py:113`) as gate: `process_audio_to_hls` should NOT run until `True`.

Production upgrade options (open):
- Move fingerprint blocklist to Redis (`_FINGERPRINT_BLOCKLIST` currently module-level set; `HACK` at line 16).
- Replace blocked-phrase list with multilingual classifier or external moderation API (`TODO` at line 19).
- Implement `StagingClip` promotion (audit doc proposes Option B) instead of `moderation_approved=False` on `AudioClip`.

No extra `docs/EXPLAIN/` document is required unless the user explicitly requests one; the design notes above are sufficient per `AGENTS.md` §15.



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
* HLS playback uses a **token-gated** `hls/` storage path (signed cookies validate at the Cloudflare Worker or nginx edge)
* original `uploads/` remain private (signed S3 URLs)
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

## Interactive Design Review Protocol

**This is a standing rule for all future sessions.** It applies whenever a task touches any of the trigger conditions in §5 (Approval Gates) of the Agent Engineering Rules:
- architecture / public APIs / database schemas / persistent data
- authentication / authorization / security boundaries / compatibility
- deployment / production behavior / dependencies / resource requirements
- irreversible operations

**For small, self-contained tasks that do NOT trigger §5 (e.g., typo fixes, single-test additions, doc updates), this protocol may be skipped.**

---

### The Protocol (Mandatory Sequence)

1. **READ DEEPLY** — Before any proposal, inspect:
   - Referenced spec/design doc (if any)
   - Relevant source files, tests, migrations, configs, deployment files
   - Callers, consumers, dependencies, data flows, failure paths
   - Existing tests — what they guarantee and what they don't

2. **QUESTION RELENTLESSLY** — Block progress with explicit questions until:
   - Every ambiguity is resolved
   - Every architectural decision is explicitly made
   - Edge cases, failure modes, rollback paths are discussed
   - You confirm the approach (or redirect)

3. **PRODUCE DESIGN DOC** — Write a per-task design document at:
   `docs/EXPLAIN/decisions/YYYY-MM-DD-<feature-slug>.md`
   
   The doc **must** contain these sections (matching your spec):
   - **Changes Needed** — exhaustive list of what changes
   - **How Changes Will Be Made** — step-by-step implementation approach
   - **Why This & Not Anything Else** — tradeoffs, alternatives considered, rationale; include `DECISION:`, `SECURITY:`, `HACK:`, `TODO:` tags inline
   - **Files Affected** — exact paths, symbols, line ranges where known
   - **Architecture & Data Flow** — before/after diagrams (text), control/data flow traces
   - **Test Cases** — existing coverage, gaps, new tests required
   - **Edge Cases & Critical Code Details** — only the important ones
   - **Atomic Commit Plan** — pre-listed commit units (one logical change per commit)

4. **YOUR APPROVAL** — I do not write code until you explicitly greenlight the design doc.

5. **IMPLEMENT** — Follow the atomic commit plan; update the design doc if reality diverges.

6. **LOG LEARNINGS** — At session end, append to the Session Learnings section (see below).

---

### Anti-Patterns (What This Protocol Prevents)

| Anti-pattern | Protocol enforcement |
|--------------|----------------------|
| Code written before design approved | Step 4 is a hard gate |
| Design doc missing edge cases | Template requires Edge Cases section |
| Design doc not created | Step 3 is mandatory for §5-triggering tasks |
| Assumptions silent | Step 2 forces explicit Q&A |
| Commits not atomic | Atomic Commit Plan in design doc |
| Learnings lost | Session Learnings section updated every session |

---

### Linkage to Existing Rules

- **§3 Understand Before Changing** — this protocol operationalizes it
- **§5 Approval Gates** — the trigger conditions are identical
- **§13 Testing** — design doc must specify validation strategy
- **§15 Documentation** — design doc *is* the decision record; AGENTS.md stays lean
- **Multi-Agent Protocol §4** — planning-before-implementation aligns with Step 3 here

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

---
## Session Learnings & Known Things

**This section accumulates durable knowledge across sessions.** Every session that touches non-trivial code **must** append an entry here before ending.

### Entry Format

```markdown
### YYYY-MM-DD — <short feature/fix slug>

**Context:** 1–2 sentences — what was the task, what triggered it.

**What Was Learned (Durable):**
- Concrete facts about the codebase, architecture, data flows, failure modes, configs, dependencies, test gaps, deployment gotchas, performance characteristics, security boundaries — things the next agent should *not* have to rediscover.
- Use `DECISION:`, `SECURITY:`, `HACK:`, `TODO:` tags where appropriate.

**What Changed:**
- Files modified (paths), migrations added, configs changed, tests added/removed.

**Open Questions / Unresolved Risks:**
- Things not fully verified, deferred decisions, known limitations.

**Design Doc Reference:** `docs/EXPLAIN/decisions/YYYY-MM-DD-<feature-slug>.md` (if applicable)
```

### Rules

- **One entry per session** — append, never overwrite.
- **Be specific** — "the feed refill uses Redis lists" is useless; "feed refill pops 10 from `user_feed:{id}` list, triggers refill when `< 15`, refill task is `refill_user_feed` on `fast_feed` queue" is durable.
- **No transient state** — don't log "I fixed a bug today"; log "the bug was X in Y, root cause Z, fix commits A-B-C".
- **Reference the design doc** — if a design doc was produced for this session, link it.
- **This section is read-only for future agents** — they read it to avoid re-learning; they do not edit past entries.

---

### Example Entry (template)

```markdown
### 2026-09-06 — hls-token-protection

**Context:** Implemented short-lived signed-cookie HLS playback tokens per audit finding B19. Django issues `ef_hls_token` cookie; Cloudflare Worker validates at edge.

**What Was Learned (Durable):**
- RFC 3986 §5.2.2 strips query strings on relative HLS references → signed URLs *cannot* work for HLS; signed cookies are the only viable mechanism. **DECISION:** cookie-based tokens only.
- `MEDIA_TOKEN_SECRET` must be identical in Django (issuance) and Cloudflare Worker (validation). Divergence = 403 on all playback. **SECURITY:** secret sync is a deployment invariant.
- Cookie must be `SameSite=Lax` (not `Strict`) for cross-subdomain top-level nav (`app.echo-flow.in` → `media.echo-flow.in`). `Domain` attribute empty for `localhost` dev.
- `fetch()` calls to token endpoint **must** use `credentials: 'include'` or browser discards `Set-Cookie`. **HACK:** documented in AGENTS.md Gotchas.
- Token endpoint returns `{"status": "ok"}` — token is HttpOnly cookie, NOT in JSON body.

**What Changed:**
- `backend/app/views/media_playback.py` (new)
- `backend/EchoFlow/settings.py` (MEDIA_TOKEN_* settings)
- `docker/nginx.conf` (proxy pass for /hls/* to MinIO)
- `docker-compose.yml` (nginx service)
- `docs/EXPLAIN/storage/04-hls-token-protection.md`

**Open Questions / Unresolved Risks:**
- Token TTL (currently 600s) — may need tuning for slow starts on mobile.
- No revocation mechanism yet — token valid until TTL expires.

**Design Doc Reference:** `docs/EXPLAIN/decisions/2026-09-06-hls-token-protection.md`
```
