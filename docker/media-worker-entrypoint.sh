#!/bin/sh
# Run the heavy-media Celery worker and keep the existing health endpoint's
# Redis heartbeat alive in the same container. When Celery exits, Docker stops
# the container and the heartbeat expires within 60 seconds.
set -eu

python wait_for_db.py

heartbeat() {
    while true; do
        if ! python -c "from redis import Redis; from backend.EchoFlow.settings import REDIS_BROKER_URL; Redis.from_url(REDIS_BROKER_URL).set('media_worker:alive', __import__('time').time(), ex=60)"; then
            echo "media worker heartbeat failed" >&2
        fi
        sleep 30
    done
}

heartbeat &

exec celery -A backend.EchoFlow worker \
    -Q heavy_media \
    --pool=prefork \
    --concurrency="${MEDIA_WORKER_CONCURRENCY:-1}" \
    --max-tasks-per-child="${MEDIA_WORKER_MAX_TASKS_PER_CHILD:-5}" \
    --loglevel=info
