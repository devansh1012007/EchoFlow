#!/bin/bash
set -e

echo "=== EchoFlow Audio Playback Diagnostics ==="
echo ""

# Step 1: Check MinIO health
echo "[1/6] Checking MinIO connectivity..."
# NOTE: previous version used `nc`, which isn't installed in this image
# (Dockerfile only installs libpq-dev/gcc/postgresql-client/ffmpeg/
# libsndfile1) — that gave a false "unreachable" result for what was
# actually just a missing binary. Python's socket module is guaranteed to
# exist since this is the app's own interpreter.
CONNECT_TEST=$(docker compose exec -T celery_media python -c "
import socket
try:
    s = socket.create_connection(('minio', 9000), timeout=3)
    s.close()
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
" 2>&1)
echo "  $CONNECT_TEST"
if echo "$CONNECT_TEST" | grep -q "OK"; then
    echo "✓ MinIO is reachable on :9000 from celery_media"
else
    echo "✗ FAIL: Cannot reach MinIO on :9000 from celery_media"
    echo "  Checking if minio service is even running..."
    docker compose ps minio
    exit 1
fi

# Step 2: Check AWS credentials in containers
echo "[2/6] Checking AWS credentials in containers..."
WEB_KEY=$(docker compose exec -T web sh -c "python -c \"import os; print(os.getenv('AWS_ACCESS_KEY_ID', 'UNSET'))\"")
MEDIA_KEY=$(docker compose exec -T celery_media sh -c "python -c \"import os; print(os.getenv('AWS_ACCESS_KEY_ID', 'UNSET'))\"")
echo "  web: AWS_ACCESS_KEY_ID=$WEB_KEY"
echo "  celery_media: AWS_ACCESS_KEY_ID=$MEDIA_KEY"
if [ "$WEB_KEY" = "UNSET" ] || [ "$MEDIA_KEY" = "UNSET" ]; then
    echo "✗ FAIL: AWS credentials not set in containers"
    exit 1
fi

# Step 3: List objects in MinIO
echo "[3/6] Listing objects in MinIO..."
OBJECTS=$(docker compose exec -T celery_media sh -c "python manage.py shell -c \"
from django.core.files.storage import default_storage
try:
    # NOTE: default_storage.connection is the boto3 RESOURCE, not the
    # client — list_objects_v2 only exists on the client. Use the
    # resource's Bucket.objects.filter() instead.
    found = list(default_storage.bucket.objects.filter(Prefix='hls/'))
    if found:
        for obj in found:
            print(f'{obj.key} ({obj.size} bytes)')
    else:
        print('NO_OBJECTS')
except Exception as e:
    print(f'ERROR: {e}')
\"" 2>&1)
echo "$OBJECTS"
if echo "$OBJECTS" | grep -qE "NO_OBJECTS|ERROR"; then
    echo "✗ FAIL: No HLS files found in MinIO (or the check itself errored — see above)"
    echo ""
    echo "Debugging: Check celery_media logs for ffmpeg errors"
    docker compose logs --tail=50 celery_media | grep -i "error\|fail" || echo "(no errors found in logs)"
    exit 1
fi
echo "✓ HLS files found in MinIO"

# Step 4: Check clip status and database
echo "[4/6] Checking clip status in database..."
CLIP_INFO=$(docker compose exec -T web sh -c "python manage.py shell -c \"
from backend.app.models import AudioClip
clip = AudioClip.objects.order_by('-created_at').first()
if clip:
    print(f'ID={clip.id}')
    print(f'Status={clip.status}')
    print(f'hls_url={clip.hls_playlist_url}')
else:
    print('NO_CLIP')
\"" 2>&1)
echo "$CLIP_INFO"
if echo "$CLIP_INFO" | grep -q "NO_CLIP"; then
    echo "✗ FAIL: No clips found in database"
    exit 1
fi
CLIP_STATUS=$(echo "$CLIP_INFO" | grep "Status=" | cut -d= -f2)
if [ "$CLIP_STATUS" != "ready" ]; then
    echo "✗ FAIL: Clip status is '$CLIP_STATUS', not 'ready'"
    exit 1
fi
echo "✓ Clip exists and status is 'ready'"

# Step 5: Generate signed URL (via the actual helper the serializer uses —
# NOT default_storage.url() directly, which bakes in the internal minio:9000
# endpoint and is exactly what we replaced)
echo "[5/6] Generating signed URL (browser-facing, via get_signed_media_url)..."
SIGNED_URL=$(docker compose exec -T web sh -c "python manage.py shell -c \"
from backend.app.media_urls import get_signed_media_url
from backend.app.models import AudioClip
clip = AudioClip.objects.order_by('-created_at').first()
if clip and clip.hls_playlist_url:
    try:
        url = get_signed_media_url(clip.hls_playlist_url)
        print(url)
    except Exception as e:
        print(f'ERROR: {e}')
\"" 2>&1)
echo "$SIGNED_URL"
if echo "$SIGNED_URL" | grep -q "ERROR"; then
    echo "✗ FAIL: Cannot generate signed URL"
    exit 1
fi
if ! echo "$SIGNED_URL" | grep -q "http"; then
    echo "✗ FAIL: Signed URL is not a valid HTTP URL"
    exit 1
fi
echo "✓ Signed URL generated successfully"

# Step 6: Test signed URL accessibility
echo "[6/6] Testing signed URL accessibility..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$SIGNED_URL")
echo "  HTTP Status: $HTTP_CODE"
if [ "$HTTP_CODE" = "200" ]; then
    echo "✓ Signed URL is accessible"
    echo ""
    echo "=== ALL CHECKS PASSED ==="
    echo "Audio should play. If it doesn't, check the frontend HLS player implementation."
    exit 0
elif [ "$HTTP_CODE" = "404" ]; then
    echo "✗ FAIL: Signed URL returned 404 — file not found in MinIO"
    echo "  This means the HLS upload succeeded but the file is in the wrong path"
    exit 1
else
    echo "✗ FAIL: Signed URL returned $HTTP_CODE (expected 200)"
    exit 1
fi