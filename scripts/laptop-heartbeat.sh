#!/bin/bash
# =============================================================================
# EchoFlow — Laptop Heartbeat Script
#
# Writes the `media_worker:alive` key to the broker Redis every 30 seconds.
# The key has a 60-second TTL, so if this script stops (worker crash,
# laptop sleep, Tailscale disconnect), the key expires and the API
# endpoint correctly reports the worker as offline.
#
# Usage:
#   nohup bash scripts/laptop-heartbeat.sh > /tmp/heartbeat.log 2>&1 &
#
# To stop: kill the process or pkill -f "laptop-heartbeat.sh"
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.laptop.yml"
ENV_FILE="${PROJECT_DIR}/.env.laptop"

# Use a complete URL when supplied, otherwise construct one from the same
# split host/port/password variables used by the worker.  Percent encoding is
# required because the generated Redis password may contain URL-reserved bytes.
if [ -z "${REDIS_BROKER_URL:-}" ] && [ -n "${REDIS_BROKER_PASSWORD:-}" ]; then
    REDIS_URL="$(python3 - "${REDIS_BROKER_HOST:-172.28.0.2}" "${REDIS_BROKER_PORT:-6379}" "${REDIS_BROKER_PASSWORD}" <<'PY'
from sys import argv
from urllib.parse import quote
print(f"redis://:{quote(argv[3], safe='')}@{argv[1]}:{argv[2]}/0")
PY
)"
else
    REDIS_URL="${REDIS_BROKER_URL:-redis://172.28.0.2:6379/0}"
fi

echo "Starting EchoFlow heartbeat..."
echo "  Interval: 30s"
echo "  TTL: 60s"
echo ""

# Ensure redis-cli is available (fallback to a python one-liner if not)
if command -v redis-cli &>/dev/null; then
    while true; do
        redis-cli -u "${REDIS_URL}" SET media_worker:alive "$(date +%s)" EX 60
        echo "  [$(date '+%Y-%m-%d %H:%M:%S')] Heartbeat OK"
        sleep 30
    done
else
    echo "  redis-cli not found — writing through the media container"
    while true; do
        if docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" exec -T celery_media \
            python3 -c "from redis import Redis; from backend.EchoFlow.settings import REDIS_BROKER_URL; Redis.from_url(REDIS_BROKER_URL).set('media_worker:alive', __import__('time').time(), ex=60)"; then
            echo "  [$(date '+%Y-%m-%d %H:%M:%S')] Heartbeat OK"
        else
            echo "  [$(date '+%Y-%m-%d %H:%M:%S')] Heartbeat FAILED" >&2
        fi
        sleep 30
    done
fi
