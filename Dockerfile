# syntax=docker/dockerfile:1

###############################################################################
# EchoFlow — multi-stage build
#
#   base         : shared OS layer (apt installed ONCE) + non-root runtime user
#   py-deps-api  : populates /opt/venv from requirements-base + ./wheelhouse
#   py-deps-media: populates /opt/venv + bakes HuggingFace models (secret-fed)
#   api          : gunicorn web + default/fast_feed/celery_beat workers (small)
#   media        : heavy_media worker (FFmpeg libs + baked HF models)
#
# Build:
#   docker compose build
#   docker build --target api -t echoflow-api .
#   docker build --target media -t echoflow-media . \
#       --secret id=hf_token,env=HF_TOKEN     # NEVER pass tokens via --build-arg
#
# Design notes:
#   * Python dependencies live in an isolated /opt/venv inside builder stages.
#     Final images receive ONLY that venv — no /usr/local coupling between
#     builder and runtime OS state, no wheels, no compilers shipped.
#   * HF_TOKEN reaches the bake step exclusively via a BuildKit secret mount.
#     Secrets mounted this way never enter ARG/ENV, layer history, or cache
#     metadata, so `docker history` cannot leak them. Omitting the secret
#     falls back to anonymous downloads of these public models.
#   * Source enters final images through an explicit allowlist — context junk
#     (wheelhouse, docs, frontend, CI configs) can never ride along.
#   * Stage-specific HEALTHCHECKs keep images self-describing for bare
#     `docker run`: api probes HTTP /health/, media pings its own Celery node.
###############################################################################

FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd -g 1000 appgroup \
 && useradd -u 1000 -g appgroup -s /bin/bash -m appuser \
 && printf 'Acquire::Retries "10";\nAcquire::http::Timeout "120";\nAcquire::https::Timeout "120";\nAcquire::http::Pipeline-Depth "0";\n' \
      > /etc/apt/apt.conf.d/99custom-network \
 && apt-get update \
 && apt-get install -y --no-install-recommends --fix-missing \
        libpq-dev \
        gcc \
        postgresql-client \
        ffmpeg \
        libsndfile1 \
        libmagic1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# -----------------------------------------------------------------------------
# Dependency builders (never shipped).
#
# The venv is created against THIS image's interpreter; because every stage
# derives from the same `base`, shebangs and pyvenv.cfg remain valid after the
# COPY --from into the finals.
#
# --no-index: wheelhouse is verified complete for this pinned set — installs
# are fully offline and deterministic. If you add a dependency, regenerate the
# wheelhouse first (see AGENTS.md) or temporarily drop --no-index.
# -----------------------------------------------------------------------------

FROM base AS py-deps-api

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements-base.txt constraints.txt ./
COPY wheelhouse/ /wheelhouse/

# NOTE: no `pip install --upgrade pip` — the bundled pip works fine and
# upgrading would force a PyPI round-trip before the wheelhouse is usable.
RUN pip install --no-cache-dir \
      --default-timeout=120 --retries 10 \
      --no-index --find-links=/wheelhouse \
      -c constraints.txt \
      -r requirements-base.txt \
 && rm -rf /wheelhouse


FROM base AS py-deps-media

# Cache locations are set BEFORE baking so models land at a path we can copy
# out verbatim; the final media stage re-declares the identical values.
ENV HF_HOME=/home/appuser/.cache/huggingface \
    TORCH_HOME=/home/appuser/.cache/torch \
    SENTENCE_TRANSFORMERS_HOME=/home/appuser/.cache/huggingface

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements-base.txt requirements-media.txt constraints.txt ./
COPY wheelhouse/ /wheelhouse/

# Single resolver pass, fully offline: wheelhouse includes the CPU-only torch
# build (torch-2.8.0+cpu-cp311); constraints.txt pins torch so nothing can
# resolve to a CUDA build.
RUN pip install --no-cache-dir \
      --default-timeout=1000 --retries 10 \
      --no-index --find-links=/wheelhouse \
      -c constraints.txt \
      -r requirements-media.txt \
 && rm -rf /wheelhouse

# Bake HuggingFace models so runtime never needs network access. A failed
# download FAILS THE BUILD deliberately — a half-baked media image is worse
# than no image.
#
# Secret handling:
#   --mount=type=secret  -> file exists only during THIS RUN, never persisted
#   `set -eu` (NOT -x!)  -> xtrace would echo the exported token into build logs
#   [ -s ... ] guard     -> absent/empty secret = anonymous public download
RUN --mount=type=secret,id=hf_token \
    set -eu; \
    if [ -s /run/secrets/hf_token ]; then \
        export HF_TOKEN="$(cat /run/secrets/hf_token)"; \
    fi; \
    python -c "from faster_whisper import WhisperModel; m = WhisperModel('base', device='cpu', compute_type='int8'); del m"; \
    python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('all-MiniLM-L6-v2'); del m"; \
    python -c "from keybert import KeyBERT; m = KeyBERT(); del m"

# -----------------------------------------------------------------------------
# Final images
# -----------------------------------------------------------------------------

FROM base AS api

COPY --from=py-deps-api /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

LABEL org.opencontainers.image.title="echoflow-api" \
      org.opencontainers.image.description="EchoFlow API server and default/feed/beat Celery workers" \
      org.opencontainers.image.source="https://github.com/devansh1012007/EchoFlow"

# Explicit allowlist (NOT `COPY .`): keeps wheels, docs, frontend, CI configs
# and anything else context-resident OUT of the production image.
# --chown avoids a full layer-duplicating `chown -R` pass.
COPY --chown=appuser:appgroup backend/ ./backend/
COPY --chown=appuser:appgroup manage.py wait_for_db.py gunicorn.conf.py ./
# The feed recommendation engine lives in /ai_ml/. Required by
# backend.app.tasks (re-export shim) which does
# `from ai_ml.pipelines.feed_tasks import refill_user_feed` at
# Django app-loading time. Without this COPY the production image
# would crash on first request.
COPY --chown=appuser:appgroup ai_ml/ ./ai_ml/

# Self-describing liveness for the HTTP role (web is this image's primary
# deployment). celery/celery_feed share this image but answer queue traffic,
# so docker-compose.yml overrides their probe with the Celery-ping variant.
#
# SECURITY: We send `X-Forwarded-Proto: https` because, with
# SECURE_SSL_REDIRECT=True in production, the in-container healthcheck
# would otherwise receive a 301 to https://... and the healthcheck
# follows redirects, so the probe would pass even when the app is
# broken. By pretending the request already came from nginx, we
# exercise the same code path as a real client.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; req = urllib.request.Request('http://localhost:8000/health/', headers={'X-Forwarded-Proto': 'https'}); urllib.request.urlopen(req, timeout=4)" || exit 1

USER appuser

EXPOSE 8000


FROM base AS media

COPY --from=py-deps-media /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/home/appuser/.cache/huggingface \
    TORCH_HOME=/home/appuser/.cache/torch \
    SENTENCE_TRANSFORMERS_HOME=/home/appuser/.cache/huggingface

LABEL org.opencontainers.image.title="echoflow-media" \
      org.opencontainers.image.description="EchoFlow heavy_media Celery worker (FFmpeg + baked HuggingFace models)" \
      org.opencontainers.image.source="https://github.com/devansh1012007/EchoFlow"

# Baked-in models from the builder stage, re-owned for the runtime user.
COPY --from=py-deps-media --chown=appuser:appgroup \
     /home/appuser/.cache/huggingface /home/appuser/.cache/huggingface

# Same explicit allowlist as the api stage.
COPY --chown=appuser:appgroup backend/ ./backend/
COPY --chown=appuser:appgroup manage.py wait_for_db.py gunicorn.conf.py ./
COPY --chown=appuser:appgroup ai_ml/ ./ai_ml/

# Worker-role liveness: passes ONLY if THIS container's Celery consumer
# answers an inspect ping on its own node name (celery@$(hostname)).
HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=3 \
    CMD celery -A backend.EchoFlow inspect ping -d "celery@$(hostname)" --timeout=10 || exit 1

USER appuser
