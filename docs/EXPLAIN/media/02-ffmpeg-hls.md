# FFmpeg HLS Transcoding

## Overview

**Single-quality HLS** (128kbps AAC) with **MPEG-TS segments** for Chrome compatibility.

**Current implementation** (`tasks.py:285-297`):
```python
command = [
    'ffmpeg', '-y', '-i', normalized_path,
    '-c:a', 'aac', '-ar', '44100', '-ac', '2', '-b:a', '128k',
    '-f', 'hls', '-hls_time', '4', '-hls_playlist_type', 'vod',
    '-hls_segment_type', 'mpegts',
    '-master_pl_name', 'master.m3u8',
    os.path.join(local_hls_dir, 'index.m3u8')
]
```

**Commented ABR version** (`tasks.py:337-417`) — 3 quality tiers (192/128/64kbps).

---

## FFmpeg Parameters Explained

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `-y` | — | Overwrite output without asking |
| `-i` | `normalized_path` | Input: mono 22050Hz WAV |
| `-c:a` | `aac` | AAC codec (universal browser support) |
| `-ar` | `44100` | Output sample rate 44.1kHz |
| `-ac` | `2` | Stereo output |
| `-b:a` | `128k` | Constant bitrate 128kbps |
| `-f` | `hls` | HLS muxer |
| `-hls_time` | `4` | Segment duration: 4 seconds |
| `-hls_playlist_type` | `vod` | Video-on-demand (static, cacheable) |
| `-hls_segment_type` | `mpegts` | **MPEG-TS containers** (critical for Chrome) |
| `-master_pl_name` | `master.m3u8` | Master playlist filename |

---

## Why MPEG-TS (Not fMP4)?

### Problem
```bash
# Original (broken)
-hls_segment_type fmp4
```

### Root Cause
- Chrome MSE decoder rejects fMP4 with certain AAC configurations
- Error: `DECODER_ERROR_NOT_SUPPORTED` / `DecoderStatus::kUnsupportedConfig`
- fMP4 AAC codec signaling inconsistent across FFmpeg versions

### Solution
```bash
-hls_segment_type mpegts
```
- MPEG-TS universally supported by HLS.js and all browsers
- Segment magic bytes: `47 40 11 11` (MPEG-TS sync byte)
- Verified via `scripts/verify_decoder_rootcause.sh`

### Trade-offs
| Aspect | MPEG-TS | fMP4 |
|--------|---------|------|
| Compatibility | Universal | Chrome issues |
| Overhead | ~10% larger | Smaller |
| Seeking | Good | Better |
| Modern standard | Legacy | Current |

**Decision:** Compatibility > efficiency for MVP.

---

## Output Structure

```
local_hls_dir/
├── master.m3u8          # Master playlist (references variants)
├── index.m3u8           # Variant playlist (single quality)
├── segment_000.ts       # MPEG-TS segments (~512KB each at 128kbps)
├── segment_001.ts
├── segment_002.ts
└── ...
```

### Master Playlist (`master.m3u8`)
```m3u
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=128000,AVERAGE-BANDWIDTH=128000
index.m3u8
```

### Variant Playlist (`index.m3u8`)
```m3u
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:4
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:4.000000,
segment_000.ts
#EXTINF:4.000000,
segment_001.ts
#EXTINF:4.000000,
segment_002.ts
...
#EXT-X-ENDLIST
```

---

## Segment Details

| Property | Value |
|----------|-------|
| Duration | 4 seconds (target) |
| Codec | AAC-LC |
| Bitrate | 128 kbps CBR |
| Sample Rate | 44.1 kHz |
| Channels | 2 (stereo) |
| Container | MPEG-TS |
| Size per segment | ~64 KB (128kbps × 4s) |
| 30s clip → ~8 segments | ~512 KB total |

---

## S3 Upload

```python
# tasks.py:302-313
storage_prefix = f"hls/{clip.id}"
for root, _dirs, files in os.walk(local_hls_dir):
    for fname in files:
        local_path = os.path.join(root, fname)
        rel_path = os.path.relpath(local_path, local_hls_dir)
        storage_key = f"{storage_prefix}/{rel_path}".replace(os.sep, '/')
        with open(local_path, 'rb') as fh:
            default_storage.save(storage_key, fh)

clip.hls_playlist_url = f"{storage_prefix}/master.m3u8"
```

**Resulting S3 keys:**
```
hls/{clip_id}/master.m3u8
hls/{clip_id}/index.m3u8
hls/{clip_id}/segment_000.ts
hls/{clip_id}/segment_001.ts
...
```

---

## ABR Version (Commented)

**Three quality tiers** (`tasks.py:337-417`):
```bash
ffmpeg -y -i input.wav \
  -c:a aac -ar 44100 \
  -map 0:a -map 0:a -map 0:a \
  -b:a:0 192k -b:a:1 128k -b:a:2 64k \
  -f hls -hls_time 4 -hls_playlist_type vod \
  -var_stream_map 'a:0,agroup:audio,default:yes a:1,agroup:audio a:2,agroup:audio' \
  -master_pl_name master.m3u8 \
  output_dir/%v/index.m3u8
```

**Output:**
```
hls/{clip_id}/
├── master.m3u8
├── 0/index.m3u8 (192kbps)
├── 0/segment_*.ts
├── 1/index.m3u8 (128kbps)
├── 1/segment_*.ts
├── 2/index.m3u8 (64kbps)
└── 2/segment_*.ts
```

**Not enabled because:**
- Single quality sufficient for MVP
- MPEG-TS + ABR needs more testing
- Storage cost 3x

---

## Verification Scripts

| Script | Purpose |
|--------|---------|
| `scripts/verify_decoder_rootcause.sh` | Downloads .ts, checks `47401111` magic, reports codec |
| `scripts/verify_hls_playback.html` | Browser test with hls.js |
| `docs/hls-playback-fix.md` | Documents the fMP4 → MPEG-TS fix |

---

## Browser Playback

### HLS.js (Chrome/Firefox/Edge)
```javascript
import Hls from 'hls.js';

if (Hls.isSupported()) {
  const hls = new Hls({ startLevel: -1, maxBufferLength: 30 });
  hls.loadSource(masterM3u8Url);
  hls.attachMedia(audioElement);
  hls.on(Hls.Events.MANIFEST_PARSED, () => audioElement.play());
}
```

### Native (Safari/iOS)
```javascript
if (audioElement.canPlayType('application/vnd.apple.mpegurl')) {
  audioElement.src = masterM3u8Url;
  audioElement.play();
}
```

---

## CDN Considerations (Future)

### Cache Behavior
| File Type | Cache TTL | Reason |
|-----------|-----------|--------|
| `master.m3u8` | 0 (no cache) | May change if variants added |
| `index.m3u8` | 0 (no cache) | May change if segments added |
| `segment_*.ts` | 24h-1yr | Immutable once created |

### CloudFront Config
```yaml
CacheBehaviors:
  - PathPattern: "*/master.m3u8"
    TTL: 0
  - PathPattern: "*/index.m3u8"
    TTL: 0
  - PathPattern: "*/segment_*.ts"
    TTL: 86400
```

---

## Verification Commands

```bash
# Check segment format
ffprobe -show_streams hls/clip_id/segment_000.ts

# Verify MPEG-TS magic
xxd hls/clip_id/segment_000.ts | head -1
# Should show: 4740 1111 ... (MPEG-TS sync)

# Test playback
# Open scripts/verify_hls_playback.html in browser
```

---

*Source: `backend/app/tasks.py:285-321, 337-417`, `docs/hls-playback-fix.md`, `scripts/verify_decoder_rootcause.sh`*