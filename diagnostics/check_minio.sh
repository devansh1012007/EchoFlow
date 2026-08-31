#!/usr/bin/env bash
# check_minio.sh — Diagnose MinIO bucket policy, CORS, and HLS file accessibility
set -euo pipefail

echo "============================================"
echo "  EchoFlow MinIO Diagnostic Script"
echo "============================================"
echo ""

# Configuration from .env
MINIO_USER="${AWS_ACCESS_KEY_ID:-echoflow-dev}"
MINIO_PASS="${AWS_SECRET_ACCESS_KEY:-echoflow-dev-secret}"
MINIO_HOST="${PUBLIC_MEDIA_ENDPOINT_URL:-http://localhost:9000}"
MINIO_INTERNAL="http://minio:9000"
BUCKET="${AWS_STORAGE_BUCKET_NAME:-echoflow-media}"

echo "[1] Checking MinIO API accessibility..."
if curl -sf -o /dev/null -w "%{http_code}" "$MINIO_HOST/" 2>/dev/null | grep -q "200\|403\|401"; then
    echo "  -> MinIO API is reachable at $MINIO_HOST"
else
    echo "  -> ERROR: MinIO API NOT reachable at $MINIO_HOST"
    echo "  -> Trying internal Docker network..."
    if curl -sf -o /dev/null -w "%{http_code}" "$MINIO_INTERNAL/" 2>/dev/null | grep -q "200\|403\|401"; then
        echo "  -> MinIO reachable internally but NOT from host"
        echo "  -> FIX: Check docker-compose.yml ports mapping"
    else
        echo "  -> ERROR: MinIO not reachable at all!"
    fi
fi
echo ""

echo "[2] Checking if bucket '$BUCKET' exists..."
BUCKET_CHECK=$(docker compose exec minio mc stat local/$BUCKET 2>&1 || true)
if echo "$BUCKET_CHECK" | grep -q "Bucket exists"; then
    echo "  -> Bucket '$BUCKET' EXISTS"
else
    echo "  -> ERROR: Bucket '$BUCKET' does NOT exist!"
    echo "  -> FIX: Run minio-init manually:"
    echo "     docker compose run --rm minio-init"
fi
echo ""

echo "[3] Checking bucket policy on hls/ prefix..."
echo "  -> Current bucket policy:"
docker compose exec minio mc policy get local/$BUCKET 2>&1 || echo "  -> Could not get bucket policy (mc version may differ)"
echo ""

echo "[4] Checking if mc anonymous set works (deprecated in newer mc)..."
MC_VERSION=$(docker compose exec minio mc --version 2>&1 || echo "unknown")
echo "  -> mc version: $MC_VERSION"

# Test if the hls policy is set
echo "  -> Testing hls/ prefix accessibility (no auth)..."
# Try to list the hls/ prefix
docker compose exec minio mc ls local/$BUCKET/hls/ 2>&1 || echo "  -> Cannot list hls/ (might be empty or policy issue)"
echo ""

echo "[5] Checking CORS configuration on MinIO..."
# MinIO CORS is set via environment or admin config
# Check if minio-init set CORS
echo "  -> Checking minio-init logs for CORS errors..."
docker compose logs minio-init 2>&1 | head -20
echo ""

echo "[6] Checking if hls files exist in MinIO..."
# Find any clip with HLS files
HLS_FILES=$(docker compose exec minio mc find local/$BUCKET --name "hls/*/master.m3u8" 2>&1 || echo "none")
if [ "$HLS_FILES" != "none" ] && [ -n "$HLS_FILES" ]; then
    echo "  -> Found HLS master playlists:"
    echo "$HLS_FILES" | head -10
else
    echo "  -> No HLS master playlists found in MinIO"
    echo "  -> This means either:"
    echo "     a) No clips have been processed (status != ready)"
    echo "     b) Processing failed"
fi
echo ""

echo "[7] Testing direct access to HLS files from host..."
# Get a clip ID from docker compose exec to postgres
CLIP_ID=$(docker compose exec db psql -U devansh -d echoflow_db -t -c "SELECT id FROM app_audioclip WHERE status='ready' LIMIT 1;" 2>/dev/null | tr -d ' ()' || echo "")
if [ -n "$CLIP_ID" ]; then
    echo "  -> Found ready clip: $CLIP_ID"
    echo "  -> Testing master.m3u8 access..."
    MASTER_URL="$MINIO_HOST/$BUCKET/hls/$CLIP_ID/master.m3u8"
    echo "  -> URL: $MASTER_URL"
    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" "$MASTER_URL" 2>/dev/null || echo "000")
    echo "  -> HTTP status: $HTTP_CODE"
    if [ "$HTTP_CODE" = "200" ]; then
        echo "  -> SUCCESS: master.m3u8 is publicly accessible!"
        echo "  -> Content preview:"
        curl -sf "$MASTER_URL" 2>/dev/null | head -5
    elif [ "$HTTP_CODE" = "401" ] || [ "$HTTP_CODE" = "403" ]; then
        echo "  -> ERROR: HLS files are NOT public (HTTP $HTTP_CODE)"
        echo "  -> This is the ROOT CAUSE of the 401 errors in the frontend"
        echo "  -> FIX: Apply public-read policy to hls/ prefix"
    else
        echo "  -> WARNING: Unexpected response (HTTP $HTTP_CODE)"
    fi
else
    echo "  -> No ready clips found in database"
fi
echo ""

echo "[8] Checking celery_media environment variables..."
echo "  -> PUBLIC_MEDIA_ENDPOINT_URL in celery_media container:"
docker compose exec celery_media env 2>/dev/null | grep -i "PUBLIC_MEDIA\|AWS_S3\|AWS_ACCESS\|AWS_SECRET\|AWS_STORAGE" || echo "  -> Could not read env vars"
echo ""

echo "============================================"
echo "  Diagnostic Complete"
echo "============================================"
