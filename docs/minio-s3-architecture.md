# MinIO / S3-Compatible Storage — Full Architecture, Problems, Lessons

// DECISION: Documenting here rather than in code comments because this involves deployment, security policy, and multi-service network topology that changes slowly.

## 1. What It Is

EchoFlow uses django-storages `S3Storage` (`STORAGES["default"]`) pointed at either:
- **Dev:** MinIO (`http://minio:9000` internally, `http://localhost:9000` externally) via `docker-compose.yml`
- **Prod:** AWS S3 (or Cloudflare R2) via `AWS_S3_ENDPOINT_URL`

Key env vars: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_ENDPOINT_URL`, `PUBLIC_MEDIA_ENDPOINT_URL`.

## 2. Architecture Design

### 2.1 Split ACL: `hls/` public, `uploads/` private

// DECISION: The `hls/` prefix is made anonymous (`mc anonymous set download`) because HLS is a multi-file protocol (master → variant → segments via relative paths). Per RFC 3986, resolving a relative reference against a signed master URL does NOT carry the query signature forward. Therefore a single signed URL cannot authorize a stream of dozens of objects; this is true for AWS S3 too, not just MinIO.

- `mc anonymous set download local/echoflow-media/hls` (minio-init)
- Original uploads stay at default private ACL; `media_urls.py` generates real `generate_presigned_url()` only for `uploads/`

### 2.2 Endpoint separation

- `AWS_S3_ENDPOINT_URL` = internal container/network endpoint (`http://minio:9000`)
- `PUBLIC_MEDIA_ENDPOINT_URL` = browser-facing endpoint (`http://localhost:9000` or CDN)
- `get_hls_playback_url()` uses `PUBLIC_MEDIA_ENDPOINT_URL` so browsers never see `minio:9000`

### 2.3 Path-style addressing

`addressing_style: "path"` required for MinIO; also accepted by real AWS.

## 3. Problems Faced

### 3.1 fMP4 decoder error (`DECODER_ERROR_NOT_SUPPORTED`)

- `tasks.py` originally used `-hls_segment_type fmp4`
- Chrome MSE decoder rejected AAC config produced by FFmpeg fMP4
- Fix: switched to `mpegts`; verified via `docs/hls-playback-fix.md`
- Segment magic confirmed: `47401111` (MPEG-TS sync byte)

### 3.2 Signed URL failure for HLS

- Attempting to use `get_signed_media_url()` for `master.m3u8` caused every `.ts` segment to return 403 because relative URLs lose signatures
- Fix: `get_hls_playback_url()` returns unsigned public URL for `hls/`; sign only `uploads/`

### 3.3 CORS / browser blockage

- MinIO CORS configured via env vars (`MINIO_CORS_ALLOW_ORIGIN`, etc.)
- Preflight (`OPTIONS`) returns `204` with `Access-Control-Allow-Origin`
- Direct segment access returns CORS headers correctly

### 3.4 Internal vs host endpoint confusion

- Scripts running inside container defaulted to `localhost:9000` which is unreachable from inside Docker network
- Fix: detection/override to `minio:9000` when `localhost` fails

## 4. Verification Scripts

| Script | Purpose |
|--------|---------|
| `scripts/verify_minio_deployment.sh` | Host + internal API, bucket, anonymous policy, CORS, direct `.m3u8` access |
| `scripts/test_minio_edge_cases.py` | Concurrent reads, bad auth, timeout, large upload, signed URL expiration, Django storage init |
| `scripts/verify_clip_url.sh` | Quick 200-check + content preview for any HLS URL |
| `scripts/verify_decoder_rootcause.sh` | Downloads `.ts`, checks `4740...` magic, reports codec if `ffmpeg` present |
| `scripts/verify_hls_playback.html` | Isolate frontend lifecycle from source format (direct `<video>` + hls.js) |

## 5. What Was Learned

- **Do not sign HLS streams per-object.** The architecture requires a public-read derived stream.
- **Do not mix internal/network endpoints in browser URLs.** `PUBLIC_MEDIA_ENDPOINT_URL` must be explicitly separate.
- **fMP4 is not universally supported for AAC streams in Chrome MSE.** `mpegts` is safer for compatibility.
- **Container network context matters for verification.** Scripts must handle both host (`localhost`) and internal (`minio`) endpoints.

## 6. Security Notes

- `hls/` is intentionally public for playback; this is not a data leak — originals remain private
- No admin/backend access is granted through MinIO public access
- `mc anonymous set download` is scoped to `/hls/` prefix only

## 7. References

- `docker-compose.yml` (minio, minio-init, CORS env)
- `backend/EchoFlow/settings.py` (STORAGES, MEDIA_URL)
- `backend/app/media_urls.py` (get_hls_playback_url / get_signed_media_url)
- `backend/app/tasks.py` (mpegts change at line 264)
- `docs/hls-playback-fix.md`
- `diagnostics/check_minio.sh`
