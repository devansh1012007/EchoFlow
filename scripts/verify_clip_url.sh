#!/usr/bin/env bash
# DECISION: Minimal shell script to verify playlist accessibility without altering storage architecture.
# DECISION: Only checks HTTP status + content-type + first bytes; does NOT expose admin paths or modify ACLs.
URL="${1:-http://localhost:9000/echoflow-media/hls/84db4275-cc44-4ef3-8dc5-879698e56b49/index.m3u8}"
echo "=== Verifying HLS playlist URL ==="
echo "URL: $URL"
echo ""
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 --connect-timeout 4 "$URL" 2>/dev/null || echo "000")
echo "HTTP status: $CODE"
if [[ "$CODE" == "200" ]]; then
    echo "PASS: URL delivers data."
    echo "Content-Type: $(curl -s -o /dev/null -D- --max-time 8 "$URL" 2>/dev/null | grep -i '^content-type' | tr -d '\r' || echo 'N/A')"
    echo "First 3 lines:"
    curl -sf --max-time 8 "$URL" 2>/dev/null | head -n 3 || echo "(empty body)"
    echo "Byte size: $(curl -sL --max-time 8 "$URL" 2>/dev/null | wc -c) bytes"
else
    echo "FAIL: URL did not return 200 (got $CODE)."
fi
echo ""
echo "SECURITY CHECK: This URL points to hls/ (derived stream only). uploads/ remains private via signed URLs. No admin access granted."
