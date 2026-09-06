# Multi-Stage Dockerfile

## Overview

**File:** `Dockerfile` — 6 stages, 2 final images (`api`, `media`)

```mermaid
graph TB
    base[base: python:3.11-slim + apt deps + non-root user]
    wheelbase[wheelhouse-base: requirements-*.txt + offline wheelhouse/]
    base --> wheelbase
    wheelbase --> pyapi[py-deps-api: requirements-base → /opt/venv]
    wheelbase --> pymedia[py-deps-media: requirements-base+media → /opt/venv + bake HF models]
    pyapi --> api[api: gunicorn + celery + beat]
    pymedia --> media[media: celery_media + baked HF models]
```

The `wheelhouse-base` stage exists so the ~511 MB `./wheelhouse/` directory enters the layer graph **exactly once** per build. Both `py-deps-api` and `py-deps-media` `FROM wheelhouse-base` instead of `COPY wheelhouse/` themselves; the wheels travel through `COPY --from=wheelhouse-base` when the final images pick up `/opt/venv`. See [## BuildKit Cache Mounts](#buildkit-cache-mounts) below for the named caches that make subsequent builds skip the expensive network round-trips.

---

## Stage 1: `base` (Shared OS Layer)

```dockerfile
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root user
RUN groupadd -g 1000 appgroup \
 && useradd -u 1000 -g appgroup -s /bin/bash -m appuser

# Tighter network timeouts + retry policy for slow Debian mirrors
RUN printf 'Acquire::Retries "10";\nAcquire::http::Timeout "120";\nAcquire::https::Timeout "120";\nAcquire::http::Pipeline-Depth "0";\n' \
      > /etc/apt/apt.conf.d/99custom-network

# Apt deps (ONCE for all stages). The `echoflow-apt` BuildKit cache mount
# (see ## BuildKit Cache Mounts) persists downloaded .deb files across builds.
# /var/lib/apt/lists is deliberately NOT cached — stale package indexes can
# serve vulnerable .deb files; `apt-get update` re-runs every build so
# security updates always flow in.
RUN --mount=type=cache,id=echoflow-apt,target=/var/cache/apt/archives,sharing=locked \
    apt-get update \
 && apt-get install -y --no-install-recommends --fix-missing \
      libpq-dev gcc postgresql-client ffmpeg libsndfile1 libmagic1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
```

### Installed Packages
| Package | Purpose |
|---------|---------|
| `libpq-dev` | PostgreSQL client library (psycopg2) |
| `gcc` | Compile C extensions during `pip install` |
| `postgresql-client` | `pg_isready` for healthcheck / `wait_for_db.py` |
| `ffmpeg` | Audio processing (HLS, normalize) |
| `libsndfile1` | Audio file I/O (soundfile) |
| `libmagic1` | `python-magic` file-type detection at upload (`serializers.py:128-133`) |

---

## Stage 2: `wheelhouse-base` (Offline Wheels — shared by both py-deps stages)

```dockerfile
FROM base AS wheelhouse-base

COPY requirements-base.txt requirements-media.txt constraints.txt ./
COPY wheelhouse/ /wheelhouse/
```

### Why a dedicated stage?
- The offline `./wheelhouse/` directory is ~511 MB (hundreds of `.whl` files including a CPU-only `torch` build). Before this stage existed, both `py-deps-api` and `py-deps-media` did their own `COPY wheelhouse/`, doubling the wheelhouse's bytes in the layer graph.
- Now both py-deps stages `FROM wheelhouse-base AS …`. Wheels travel through `COPY --from=wheelhouse-base /opt/venv` when the final images assemble, so wheelhouse bytes enter the layer graph exactly **once** per build regardless of how many downstream stages need them.
- This stage is re-evaluated only when `wheelhouse/`, `requirements-base.txt`, `requirements-media.txt`, or `constraints.txt` change.

---

## Stage 3: `py-deps-api` (API Dependencies)

```dockerfile
FROM wheelhouse-base AS py-deps-api

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# The `echoflow-pip` BuildKit cache mount (see ## BuildKit Cache Mounts)
# persists pip's HTTP/wheel metadata index across builds. Even with
# --no-index, pip still does resolver work (PEP 517 build-deps, constraint
# checks, METADATA reads); caching /root/.cache/pip makes subsequent
# installs in the same cache ID near-instant after the first cold run.
RUN --mount=type=cache,id=echoflow-pip,target=/root/.cache/pip,sharing=locked \
    pip install --no-cache-dir \
      --default-timeout=120 --retries 10 \
      --no-index --find-links=/wheelhouse \
      -c constraints.txt \
      -r requirements-base.txt
```

### Key Points
- **Offline install** — `--no-index --find-links=/wheelhouse`
- **Constraints** — Version pinning via `constraints.txt`
- **No pip upgrade** — Bundled pip works, avoids PyPI round-trip
- **Output:** `/opt/venv` with API deps only

---

## Stage 4: `py-deps-media` (Media Dependencies + HF Models)

```dockerfile
FROM wheelhouse-base AS py-deps-media

# Cache locations BEFORE baking (copied to media stage)
ENV HF_HOME=/home/appuser/.cache/huggingface \
    TORCH_HOME=/home/appuser/.cache/torch \
    SENTENCE_TRANSFORMERS_HOME=/home/appuser/.cache/huggingface

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Single resolver pass (CPU torch via extra-index-url in Dockerfile)
RUN --mount=type=cache,id=echoflow-pip,target=/root/.cache/pip,sharing=locked \
    pip install --no-cache-dir \
      --default-timeout=1000 --retries 10 \
      --no-index --find-links=/wheelhouse \
      -c constraints.txt \
      -r requirements-media.txt

# BAKE HUGGINGFACE MODELS
#   * --mount=type=secret,id=hf_token — HF_TOKEN reaches this step as a
#     secret file; it never enters ARG/ENV/layer history.
#   * --mount=type=cache,id=echoflow-hf,uid=1000,gid=1000 — persists the
#     baked model artifacts (~250 MB: Whisper base + sentence-transformers
#     + KeyBERT) across media builds on this host. The uid/gid are required
#     because the python -c lines run as the user they were loaded under
#     (root inside the builder), and the cache target is /home/appuser/...
#     which is owned by UID 1000.
#   * sharing=locked — two concurrent builds never race on a half-written
#     model file.
RUN --mount=type=secret,id=hf_token \
    --mount=type=cache,id=echoflow-hf,target=/home/appuser/.cache/huggingface,sharing=locked,uid=1000,gid=1000 \
    set -eu; \
    if [ -s /run/secrets/hf_token ]; then \
        export HF_TOKEN="$(cat /run/secrets/hf_token)"; \
    fi; \
    python -c "from faster_whisper import WhisperModel; m = WhisperModel('base', device='cpu', compute_type='int8'); del m"; \
    python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('all-MiniLM-L6-v2'); del m"; \
    python -c "from keybert import KeyBERT; m = KeyBERT(); del m"
```

### Key Points
- **Extra index URL** for CPU torch (in build command)
- **BuildKit secret** for `HF_TOKEN` — never in layers
- **Model baking** — downloads + caches at build time
- **Cache env vars** set before baking (copied to final)
- **`set -eu` not `-x`** — prevents token leak in logs

---

## Stage 5: `api` (Final API Image)

```dockerfile
FROM base AS api

COPY --from=py-deps-api /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

LABEL org.opencontainers.image.title="echoflow-api" \
      org.opencontainers.image.description="EchoFlow API server and default/feed/beat Celery workers"

# Explicit allowlist (NOT COPY .)
COPY --chown=appuser:appgroup backend/ ./backend/
COPY --chown=appuser:appgroup manage.py wait_for_db.py gunicorn.conf.py ./

# Healthcheck: HTTP /health/
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/', timeout=4)" || exit 1

USER appuser
EXPOSE 8000
```

### Copied Files (Explicit Allowlist)
```
backend/           # All app code
manage.py          # Django management
wait_for_db.py     # DB polling
gunicorn.conf.py   # Gunicorn config
```

**NOT copied:** `frontend/`, `docs/`, `wheelhouse/`, `.github/`, `Dockerfile`, etc.

### Healthcheck
- Probes `http://localhost:8000/health/` **with `X-Forwarded-Proto: https` header**
- Used by `web` service
- **Overridden** for Celery workers (uses Celery ping)

---

## Stage 6: `media` (Final Media Image)

```dockerfile
FROM base AS media

COPY --from=py-deps-media /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/home/appuser/.cache/huggingface \
    TORCH_HOME=/home/appuser/.cache/torch \
    SENTENCE_TRANSFORMERS_HOME=/home/appuser/.cache/huggingface

LABEL org.opencontainers.image.title="echoflow-media" \
      org.opencontainers.image.description="EchoFlow heavy_media Celery worker (FFmpeg + baked HuggingFace models)"

# Baked models from builder
COPY --from=py-deps-media --chown=appuser:appgroup \
     /home/appuser/.cache/huggingface /home/appuser/.cache/huggingface

# Same explicit allowlist
COPY --chown=appuser:appgroup backend/ ./backend/
COPY --chown=appuser:appgroup manage.py wait_for_db.py gunicorn.conf.py ./

# Healthcheck: Celery inspect ping
HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=3 \
    CMD celery -A backend.EchoFlow inspect ping -d "celery@$(hostname)" --timeout=10 || exit 1

USER appuser
```

### Key Differences from `api`
| Aspect | `api` | `media` |
|--------|-------|---------|
| Healthcheck | HTTP `/health/` | Celery ping |
| Models | Not baked | **Baked HF models** |
| FFmpeg | From base | From base |
| Use case | Web, default worker, feed, beat | Heavy media worker |

### Offline Runtime
```dockerfile
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1
```
- No network calls at runtime
- Models loaded from baked cache

---

## Build Commands

### Docker Compose (All — 12 services: db, pgbouncer, redis_broker, redis_cache, minio, minio-init, nginx, web, celery, celery_feed, celery_media, celery_beat)
```bash
docker compose build
```

### Manual Build
```bash
# API image
docker build --target api -t echoflow-api .

# Media image (requires HF_TOKEN secret)
export HF_TOKEN=hf_xxx
docker build --target media -t echoflow-media . --secret id=hf_token,env=HF_TOKEN

# Or from file
docker build --target media -t echoflow-media . --secret id=hf_token,src=./hf_token.txt
```

### Build Args
```bash
# Override tag
docker compose build --build-arg TAG=dev
```

---

## Wheelhouse (Offline Installs)

### Structure
```
wheelhouse/
├── Django-5.2.17-py3-none-any.whl
├── torch-2.8.0+cpu-cp311-cp311-linux_x86_64.whl
├── sentence_transformers-6.0.0-py3-none-any.whl
└── ... (all deps)
```

### Regeneration (Run in Python 3.11 container)
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

### Version Constraints
| Package | Constraint | Reason |
|---------|------------|--------|
| `django` | `==5.2.17` | 6.x needs Python 3.12+ |
| `librosa` | `==0.11.0` | 1.x needs Python 3.12+ |
| `torch` | `==2.8.0` | CPU build via extra-index-url |

---

## BuildKit Cache Mounts

Three **named** BuildKit cache mounts persist across builds on the same host. Without them, the `base` stage re-downloads ~200 MB of `.deb` files and the `py-deps-media` stage re-downloads ~250 MB of HuggingFace models on every cold build. With them, only the first build pays that cost; subsequent builds see a BuildKit cache hit and skip the network round-trip entirely.

| Cache ID | Mounted at | Stage that declares it | Holds |
|---|---|---|---|
| `echoflow-apt` | `/var/cache/apt/archives` | `base` (apt-get install) | Downloaded `.deb` files for `libpq-dev`, `gcc`, `postgresql-client`, `ffmpeg`, `libsndfile1`, `libmagic1` |
| `echoflow-pip` | `/root/.cache/pip` | `py-deps-api` and `py-deps-media` | pip's HTTP/wheel metadata index (resolver cache). Helps on repeated installs within the same build; not the actual wheels (those come from the local `wheelhouse/`) |
| `echoflow-hf` | `/home/appuser/.cache/huggingface` | `py-deps-media` (HF bake) | Baked model artifacts: Whisper `base`, `all-MiniLM-L6-v2`, KeyBERT |

All three mounts use `sharing=locked` so two concurrent builds never race on a half-written file. The `echoflow-hf` mount additionally specifies `uid=1000,gid=1000` because the cache target lives under `appuser`'s home directory.

### Why a `wheelhouse-base` stage (not a cache mount)?

A natural alternative is `--mount=type=cache,target=/wheelhouse` instead of `COPY wheelhouse/ /wheelhouse/` in each py-deps stage. We chose the dedicated stage instead because:

- **Cache mounts are per-stage.** Two stages can't share a single cache mount at the same `target`. Two `COPY wheelhouse/` calls would each need their own mount.
- **Layer-graph dedup is more important than disk dedup.** A `wheelhouse-base` stage + `COPY --from=wheelhouse-base /opt/venv` makes the wheels travel through the build graph exactly once, regardless of how many downstream stages need them. A cache mount would let `pip` re-read the same bytes multiple times.
- **Wheelhouse is part of the source tree.** It's reproducible from `requirements-*.txt` + `constraints.txt` via the regen script (see `AGENTS.md` "Offline wheelhouse" section), so we don't need to cache it as a long-lived artifact — the cache mount would persist it on the host indefinitely.

### Inspecting and managing the caches

```bash
docker buildx du                                    # show every named cache and its size
docker buildx du --filter id=echoflow-apt           # one specific cache
docker buildx du --filter type=buildkit             # only BuildKit-managed caches

# Pruning — caches survive `docker builder prune` by default; only `docker
# buildx prune` with an explicit filter removes them.
docker buildx prune --filter type=buildkit          # safe; does NOT touch named caches
docker buildx prune --filter id=echoflow-hf         # nuke the HF cache (after upgrading a model)
docker builder prune                                # CAREFUL — wipes dangling builders; named caches survive
```

### What is intentionally NOT cached

- **`/var/lib/apt/lists`** — stale package indexes can silently serve vulnerable `.deb` files. `apt-get update` re-runs on every build; security wins over re-download speed. Only `/var/cache/apt/archives` (the downloaded files) is cached.
- **The `./wheelhouse/` directory itself** — see "Why a `wheelhouse-base` stage (not a cache mount)?" above.

### Cache invalidation matrix

| Change | Invalidates | Caches re-populated by |
|---|---|---|
| Add/remove a package in the `apt-get install` list | `base` | Next build re-downloads; caches refill transparently |
| Change `requirements-base.txt` or `constraints.txt` | `wheelhouse-base`, `py-deps-api`, `py-deps-media` | First build re-resolves pip; second build reuses cache |
| Add a wheel to `wheelhouse/` | `wheelhouse-base` | Same as above |
| Upgrade HuggingFace model | `py-deps-media` bake layer only — but BuildKit can't tell | Manual `docker buildx prune --filter id=echoflow-hf`, then rebuild |
| Change `Dockerfile` syntax / stage layout | All dependent stages | Transparent re-population |
| Edit source under `backend/` or `ai_ml/` | `api` and `media` final stages only — `wheelhouse-base`, `py-deps-*`, and `base` are untouched | All caches persist |

### CI runners

GitHub Actions and other CI runners start with **empty BuildKit caches** — the first CI build is always cold. Subsequent jobs on the same runner can reuse caches if you add `cache-from` and `cache-to` attributes to a `docker/build-push-action@v6` step (e.g. `type=registry,ref=ghcr.io/<owner>/echoflow-buildcache`). This is **not currently configured** — see `unfixed-issues-2026-09-03.md` for the open item.

---

## Security: HF_TOKEN as BuildKit Secret

### Why Not `--build-arg`?
```dockerfile
# BAD - leaks in layer history
ARG HF_TOKEN
ENV HF_TOKEN=$HF_TOKEN
# `docker history` shows the token!
```

### Correct: BuildKit Secret Mount
```dockerfile
# Dockerfile
RUN --mount=type=secret,id=hf_token \
    set -eu; \
    if [ -s /run/secrets/hf_token ]; then \
        export HF_TOKEN="$(cat /run/secrets/hf_token)"; \
    fi; \
    python -c "..."

# docker-compose.yml
secrets:
  hf_token:
    environment: HF_TOKEN

# Build command
docker build --target media . --secret id=hf_token,env=HF_TOKEN
```

**Secret never in:**
- Image layers
- Build cache
- `docker history` output
- `docker inspect`

---

## Pop!_OS / Docker Compose V2 Note

```bash
# Use docker compose (V2 plugin), NOT docker-compose (V1)
docker compose build
docker compose up

# If docker-compose installed from old PPA:
sudo apt remove docker-compose
# Then use: docker compose
```

---

*Source: `Dockerfile`, `docker-compose.yml`, `AGENTS.md`*