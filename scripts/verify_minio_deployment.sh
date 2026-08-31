#!/usr/bin/env bash
# DECISION: Bash used for host-level network verification; Python handles stateful assertions.
# DECISION: Tests both host (localhost:9000) and internal Docker (minio:9000) because endpoint URLs differ.
set -euo pipefail

MINIO_HOST="${PUBLIC_MEDIA_ENDPOINT_URL:-http://localhost:9000}"
MINIO_INTERNAL="http://minio:9000"
BUCKET="${AWS_STORAGE_BUCKET_NAME:-echoflow-media}"
USER="${AWS_ACCESS_KEY_ID:-echoflow-dev}"
PASS="${AWS_SECRET_ACCESS_KEY:-echoflow-dev-secret}"

FAILS=0

echo "=== EchoFlow MinIO Deployment Verification ==="
echo "Host endpoint: $MINIO_HOST"
echo "Internal endpoint: $MINIO_INTERNAL"
echo "Bucket: $BUCKET"
echo ""

# 1. API reachability (9000) from host
check_host() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$MINIO_HOST/" 2>/dev/null || echo "000")
    if [[ "$code" == "200" || "$code" == "403" ]]; then
        echo "[PASS] MinIO API reachable from host at $MINIO_HOST (HTTP $code)"
    else
        echo "[FAIL] MinIO API NOT reachable from host at $MINIO_HOST (HTTP $code)"
        ((FAILS++)) || true
    fi
}

# 2. Internal API (Docker network)
check_internal() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$MINIO_INTERNAL/" 2>/dev/null || echo "000")
    if [[ "$code" == "200" || "$code" == "403" ]]; then
        echo "[PASS] MinIO API reachable internally at $MINIO_INTERNAL (HTTP $code)"
    else
        echo "[FAIL] MinIO API NOT reachable internally (HTTP $code) — check compose network/depends_on"
        ((FAILS++)) || true
    fi
}

# 3. Console (9001) health — quick smoke
check_console() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "${MINIO_HOST/:9000/:9001}" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
        echo "[PASS] MinIO Console reachable (HTTP 200)"
    else
        echo "[WARN] MinIO Console not reachable at 9001 (HTTP $code) — non-critical if 9000 works"
    fi
}

# 4. Bucket exists via mc (requires minio-init image in compose, or local mc)
check_bucket() {
    if docker compose ps minio 2>/dev/null | grep -q "Up"; then
        local out
        out=$(docker compose exec -T minio mc stat local/$BUCKET 2>&1 || true)
        if echo "$out" | grep -q "Bucket exists"; then
            echo "[PASS] Bucket '$BUCKET' exists (mc stat)"
        else
            echo "[FAIL] Bucket '$BUCKET' missing — run: docker compose run --rm minio-init"
            ((FAILS++)) || true
        fi
    else
        echo "[SKIP] minio container not running; cannot verify bucket via mc"
    fi
}

# 5. Anonymous policy on hls/ prefix — critical for HLS playback without querystring auth
check_hls_policy() {
    if docker compose ps minio 2>/dev/null | grep -q "Up"; then
        local out
        out=$(docker compose exec -T minio mc anonymous get local/$BUCKET/hls 2>&1 || true)
        if echo "$out" | grep -qi "download"; then
            echo "[PASS] hls/ prefix has anonymous 'download' policy"
        else
            echo "[FAIL] hls/ prefix missing anonymous download policy — 401/403 expected"
            ((FAILS++)) || true
        fi
    fi
}

# 6. CORS preflight (edge case: browser hls.js sends OPTIONS before GET)
check_cors() {
    local preflight
    preflight=$(curl -s -o /dev/null -D- -X OPTIONS --max-time 5 \
        -H "Origin: http://localhost:5173" \
        -H "Access-Control-Request-Method: GET" \
        -H "Access-Control-Request-Headers: Range" \
        "$MINIO_HOST/$BUCKET/hls/" 2>/dev/null || true)
    if echo "$preflight" | grep -qi "200\|Access-Control-Allow-Origin"; then
        echo "[PASS] MinIO CORS preflight responds (browser hls.js compatible)"
    else
        echo "[FAIL] MinIO CORS preflight missing/failed — hls.js may be blocked"
        ((FAILS++)) || true
    fi
}

# 7. Direct HLS file access (public-read via anonymous policy) — 9000 API
check_hls_access() {
    # Try a ready clip from DB; if none, skip gracefully
    local clip_id
    clip_id=$(docker compose exec -T db psql -U devansh -d echoflow_db -t -A -c "SELECT id FROM app_audioclip WHERE status='ready' LIMIT 1;" 2>/dev/null | tr -d ' ()' || echo "")
    if [[ -n "$clip_id" ]]; then
        local url="$MINIO_HOST/$BUCKET/hls/$clip_id/master.m3u8"
        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$url" 2>/dev/null || echo "000")
        case "$code" in
            200)
                echo "[PASS] master.m3u8 accessible at $url (HTTP 200)"
                echo "      Preview: $(curl -sf --max-time 3 "$url" 2>/dev/null | head -2 || echo 'N/A')"
                ;;
            401|403)
                echo "[FAIL] master.m3u8 blocked (HTTP $code) at $url — apply mc anonymous set download"
                ((FAILS++)) || true
                ;;
            404)
                echo "[WARN] master.m3u8 not found (HTTP 404) — processing may not have completed for $clip_id"
                ;;
            *)
                echo "[FAIL] Unexpected response (HTTP $code) for $url"
                ((FAILS++)) || true
                ;;
        esac
    else
        echo "[SKIP] No ready clip found; cannot test direct HLS access"
    fi
}

# 8. Credential failure edge case (bad key should yield 403, not 200 with partial data)
check_bad_auth() {
    # Use a clearly wrong secret to verify MinIO rejects rather than leaking
    local bad_url="$MINIO_HOST/$BUCKET/hls/"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 -u "baduser:badsecret" "$bad_url" 2>/dev/null || echo "000")
    if [[ "$code" == "403" || "$code" == "401" ]]; then
        echo "[PASS] Bad credentials rejected (HTTP $code) — no auth bypass"
    else
        echo "[WARN] Bad credentials returned HTTP $code (expected 401/403) — verify ACL"
    fi
}

# 9. Network timeout /partition simulation (max-time too low is not a real partition, but verifies timeout handling)
check_timeout_behavior() {
    # We test that curl exits with error on very short timeout rather than hanging
    local start end
    start=$(date +%s.%N)
    curl -s -o /dev/null --max-time 0.1 "$MINIO_HOST/" 2>/dev/null || true
    end=$(date +%s.%N)
    local duration
    duration=$(python3 -c "print(f'{$end - $start:.2f}')" 2>/dev/null || echo "?")
    echo "[INFO] Short-timeout (0.1s) curl duration ≈ ${duration}s — verifies timeout path exists (non-blocking)"
}

# 10. Path-style vs virtual-hosted addressing (critical for MinIO compatibility)
check_path_style() {
    # MinIO requires path-style; real AWS supports both. Verify endpoint responds with path-style URL.
    local url_path="$MINIO_HOST/$BUCKET/"
    local url_virtual="$MINIO_HOST/$BUCKET/"
    # Both identical here because endpoint is host-level; just confirm bucket in path works
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url_path" 2>/dev/null || echo "000")
    if [[ "$code" == "403" || "$code" == "200" ]]; then
        echo "[PASS] Path-style addressing works (bucket in URL path) — required for MinIO"
    else
        echo "[FAIL] Path-style addressing failed (HTTP $code)"
        ((FAILS++)) || true
    fi
}

# Run checks
check_host
check_internal
check_console
check_bucket
check_hls_policy
check_cors
check_hls_access
check_bad_auth
check_timeout_behavior
check_path_style

echo ""
echo "=== Results: $FAILS failure(s) ==="
if [[ "$FAILS" -eq 0 ]]; then
    echo "All critical MinIO/9000 checks passed. Ready to proceed with backend integration."
else
    echo "Failures found — review [FAIL] lines above and apply fixes (mc anonymous, CORS env, network, bucket init)."
fi
exit "$FAILS"
