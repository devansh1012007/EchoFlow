#!/bin/bash
# =============================================================================
# EchoFlow — Laptop Media Worker Deploy Script
#
# Run this on your laptop to build the media image and start the
# celery_media worker. The worker connects to VPS services via
# Tailscale private network.
#
# Prerequisites:
#   - Docker installed
#   - Tailscale installed and running (connected to the same tailnet as VPS)
#   - VPS already deployed and advertising 172.28.0.0/16 subnet
#   - HF_TOKEN set (in environment or .env)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.laptop.yml"
ENV_FILE="${PROJECT_DIR}/.env.laptop"

echo "=== EchoFlow Laptop Deploy ==="

cd "${PROJECT_DIR}"

# ---- Step 0: Pre-flight checks ----
echo "Step 0: Pre-flight checks..."

if [ ! -f "${ENV_FILE}" ]; then
    if [ -f ".env.laptop.example" ]; then
        echo "  Copying .env.laptop.example to .env.laptop..."
        cp .env.laptop.example "${ENV_FILE}"
        echo "  EDIT .env.laptop WITH YOUR REAL VALUES (DB_*, REDIS_*, R2 creds, HF_TOKEN)."
    else
        echo "  ERROR: No .env file found. Create one from .env.laptop.example."
        exit 1
    fi
fi

# Check HF_TOKEN (required for media image build)
if [ -z "${HF_TOKEN:-}" ]; then
    HF_TOKEN="$(grep '^HF_TOKEN=' "${ENV_FILE}" | head -1 | cut -d= -f2-)"
    export HF_TOKEN
    if [ -z "${HF_TOKEN}" ] || [[ "${HF_TOKEN}" == '<'* ]]; then
        echo "  ERROR: HF_TOKEN is not set to a real value in .env."
        echo "  Get a token from https://huggingface.co/settings/tokens"
        exit 1
    fi
fi

if ! command -v docker &>/dev/null; then
    echo "  ERROR: Docker is not installed."
    exit 1
fi

if ! docker compose version &>/dev/null 2>&1; then
    echo "  ERROR: Docker Compose plugin is not installed."
    exit 1
fi

# Check Tailscale connectivity to VPS
echo "  Checking Tailscale connectivity to VPS services..."
DB_HOST=$(grep '^DB_HOST=' "${ENV_FILE}" | head -1 | cut -d= -f2-)
if [ -n "${DB_HOST}" ] && [[ "${DB_HOST}" != '<'* ]]; then
    if command -v nc >/dev/null 2>&1 && nc -z -w 3 "${DB_HOST}" 5432 && nc -z -w 3 172.28.0.2 6379; then
        echo "  Can reach VPS PostgreSQL and Redis over Tailscale."
    else
        echo "  WARNING: TCP check to VPS PostgreSQL or Redis failed."
        echo "  Verify the approved 172.28.0.0/16 route and the VPS nftables exception before starting the worker."
    fi
else
    echo "  WARNING: Could not read DB_HOST from .env.laptop."
fi

echo "  ✅ Docker and required tools are available."

# ---- Step 1: Build media image ----
echo "Step 1: Building media image (this bakes in Whisper + SentenceTransformer + KeyBERT)..."
# If you change any model version, rebuild the media image with --no-cache.
docker build --target media -t echoflow-media:laptop --secret id=hf_token,env=HF_TOKEN .

# ---- Step 2: Start the media worker ----
echo "Step 2: Starting celery_media worker..."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d

# ---- Step 3: Start heartbeat (background process) ----
echo "Step 3: Starting heartbeat script..."
# The heartbeat writes media_worker:alive to Redis every 30s (60s TTL).
# Run it as a background process.
if command -v nohup &>/dev/null; then
    read_env() {
        sed -n "s/^$1=//p" "${ENV_FILE}" | head -n 1
    }
    REDIS_BROKER_HOST="$(read_env REDIS_BROKER_HOST)" \
    REDIS_BROKER_PORT="$(read_env REDIS_BROKER_PORT)" \
    REDIS_BROKER_PASSWORD="$(read_env REDIS_BROKER_PASSWORD)" \
    nohup bash "${PROJECT_DIR}/scripts/laptop-heartbeat.sh" > /tmp/echoflow-heartbeat.log 2>&1 &
    echo "  ✅ Heartbeat started (PID: $!)"
    echo "  Logs: /tmp/echoflow-heartbeat.log"
else
    echo "  WARNING: nohup not available. Run this manually:"
    echo "    nohup bash ${PROJECT_DIR}/scripts/laptop-heartbeat.sh > /tmp/echoflow-heartbeat.log 2>&1 &"
fi

# ---- Step 4: Show logs ----
echo ""
echo "=== Laptop Deploy Complete ==="
echo ""
echo "Worker logs (Ctrl+C to detach):"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs -f celery_media
