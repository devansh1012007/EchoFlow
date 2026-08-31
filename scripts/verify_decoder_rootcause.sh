#!/usr/bin/env bash
# DECISION: Root-cause verification script — checks codec/format without altering storage architecture.
# DECISION: Uses ffmpeg (if present) to inspect segment, not to rewrite storage; does NOT grant admin or drop S3.
set -euo pipefail
SEG_URL="${1:-http://localhost:9000/echoflow-media/hls/84db4275-cc44-4ef3-8dc5-879698e56b49/index1.ts}"
echo "=== Root-cause verification (decoder error) ==="
echo "Segment URL: $SEG_URL"
echo ""
# Download segment to temp
TMP=$(mktemp /tmp/check_seg.XXXXXX.ts)
curl -sf -o "$TMP" --max-time 10 "$SEG_URL" 2>/dev/null || { echo "FAIL: Could not download segment"; exit 1; }
echo "Downloaded: $(wc -c < "$TMP") bytes"
echo "File magic (first 4 bytes hex): $(xxd -l 4 -p "$TMP")"
echo "Expected MPEG-TS sync: 47401111 (or similar 0x47 start)"
if command -v ffmpeg >/dev/null 2>&1; then
    echo ""
    echo "=== ffmpeg codec inspection ==="
    ffmpeg -hide_banner -loglevel error -i "$TMP" -f null - 2>&1 | grep -iE 'Stream|Audio:|Video:' || echo "(ffmpeg output empty — check install)"
else
    echo "ffmpeg not installed; install for full codec verification."
fi
echo ""
echo "=== Conclusion ==="
# 4740 = MPEG-TS sync; anything else (e.g., ftyp for fMP4) indicates wrong format
FIRST=$(xxd -l 4 -p "$TMP")
if [[ "$FIRST" == "4740"* ]]; then
    echo "PASS: Segment starts with MPEG-TS sync byte (0x47) — format is correct."
    echo "If playback still fails with DECODER_ERROR_NOT_SUPPORTED, root cause is stale fMP4 master/variant playlists OR audio codec params from original encode — trigger Celery reprocess."
else
    echo "FAIL: Segment does NOT start with MPEG-TS sync. Expected fMP4/stale format -> reprocess required."
fi
rm -f "$TMP"
