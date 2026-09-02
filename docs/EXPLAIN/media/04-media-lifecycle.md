# Media Lifecycle

## Storage Locations

| Stage | Location | Access | Lifecycle |
|-------|----------|--------|-----------|
| **Upload (original)** | S3 `uploads/{date}/{uuid}.ext` | Private (signed URLs) | Permanent until clip deleted |
| **Processing (scratch)** | Container `/tmp/hls-{clip_id}-*` | Local only | Deleted after task |
| **HLS Output** | S3 `hls/{clip_id}/master.m3u8` + segments | Public (anonymous) | Permanent until clip deleted |
| **Scraper downloads** | Container `/tmp/...` | Local only | Deleted after import |

---

## Lifecycle States

```
┌─────────────┐     ┌──────────────┐     ┌─────────┐     ┌────────┐
│  CREATED    │────►│  PROCESSING  │────►│  READY  │     │ FAILED │
│  (API)      │     │  (Celery)    │     │  (S3)   │     │        │
└─────────────┘     └──────────────┘     └─────────┘     └────────┘
                           │                   │
                           │ (error)           │ (delete)
                           ▼                   ▼
                        FAILED ◄────────────────┘
```

### State Definitions

| Status | Meaning | Transitions |
|--------|---------|-------------|
| `processing` | Task enqueued, not started | → `ready` or `failed` |
| `ready` | HLS uploaded, vectors saved | → `failed` (reprocess) or deleted |
| `failed` | Any stage errored | → `processing` (retry) or deleted |

---

## Upload Flow (User)

```
1. POST /clips/ (multipart)
       │
       ▼
2. AudioUploadSerializer.validate_original_file()
   - Size ≤ 100MB
   - Extension in {mp3,wav,ogg,flac,m4a,aac,webm,opus}
       │
       ▼
3. AudioClip.objects.create(
       creator=request.user,
       status='processing',
       original_file=uploaded_file  → S3 uploads/...
   )
       │
       ▼
4. transaction.on_commit(
       lambda: process_audio_to_hls.delay(clip.id)
   )
       │
       ▼
5. Return 202 {clip_id, status: 'processing'}
```

**Key:** `transaction.on_commit` ensures DB commit before task enqueue.

---

## Processing Flow (Celery)

```
process_audio_to_hls(clip_id)
       │
       ▼
1. AudioClip.objects.get(id=clip_id)
   if not clip.original_file: → failed
       │
       ▼
2. Download from S3 to local temp (input_file_path)
       │
       ▼
3. normalize_to_wav(input_file_path) → normalized_path
   - FFmpeg decode → mono 22050Hz WAV
       │
       ▼
4. librosa.load(normalized_path) → y, sr
   - Extract acoustic_vector (128-dim)
   - Extract duration_ms
   - Save: acoustic_vector, duration_ms
       │
       ▼
5. Whisper transcribe(normalized_path) → transcript
   - If transcript: embedding + tags
   - Else: zero vector + ["instrumental"]
   - Save: semantic_vector, tags
       │
       ▼
6. FFmpeg HLS transcode(normalized_path) → local_hls_dir/
   - MPEG-TS, 128kbps AAC, 4s segments
       │
       ▼
7. Upload ALL files to S3 (hls/{clip_id}/)
   - default_storage.save() for each file
       │
       ▼
8. clip.hls_playlist_url = "hls/{clip_id}/master.m3u8"
   clip.status = 'ready'
   clip.save()
       │
       ▼
9. Cleanup: Remove normalized_path, local_hls_dir
```

---

## Scraper Flow

```
scrape_audio command / scrape_and_import task
       │
       ▼
1. fetch_audio() → [{url, title, license, ...}]
       │
       ▼
2. For each item:
   a. download_audio() → local temp (robots.txt, rate limit)
   b. normalize_and_trim() → MP3 (stereo 44kHz, trimmed)
   c. save_clip() → AudioClip(
         imported_via_scraper=True,
         source_name, source_url, license, attribution,
         original_source_id
       )
       original_file → S3 audio_scraper/{source}/{date}/
   d. process_audio_to_hls.delay(clip.id)
```

**Provenance preserved:** `source_name`, `source_url`, `license`, `attribution_text`, `original_source_id`

---

## Deletion Flow

```
DELETE /clips/{id}/ (AudioUploadViewSet.destroy)
       │
       ▼
1. clip = get_object()
2. clip.delete() → CASCADE deletes:
   - Comment (clip FK)
   - ShareEvent (clip FK)
   - UserInteraction (clip FK)
       │
       ▼
3. S3 objects NOT automatically deleted
   - uploads/{...} (original)
   - hls/{clip_id}/ (HLS)
   - Need: post_delete signal or manual cleanup task
```

**Gap:** No automatic S3 cleanup on clip deletion.

---

## S3 Object Structure

```
echoflow-media/ (bucket)
├── uploads/
│   └── 2024/01/15/
│       └── abc123.mp3              ← Private, signed URL
│
├── audio_scraper/
│   └── wikimedia/2024/01/15/
│       └── def456.mp3              ← Private, signed URL
│
├── hls/
│   └── clip-uuid/
│       ├── master.m3u8             ← Public (anonymous)
│       ├── index.m3u8
│       ├── segment_000.ts
│       ├── segment_001.ts
│       └── ...
│
└── avatars/
    └── user123.jpg                 ← Private, signed URL
```

---

## Access Control

| Prefix | ACL | URL Type | Use Case |
|--------|-----|----------|----------|
| `uploads/` | Private | Signed (1hr) | Original file download (admin, owner) |
| `audio_scraper/` | Private | Signed (1hr) | Scraper originals |
| `hls/` | **Public-read** | Unsigned | HLS playback (browser) |
| `avatars/` | Private | Signed (1hr) | Profile pictures |

**Why `hls/` public?**
- HLS = multi-file (master → variants → segments via relative paths)
- Signed URL query string **dropped** on relative reference resolution (RFC 3986)
- One signed URL cannot authorize stream of 10+ segments
- `minio-init`: `mc anonymous set download local/bucket/hls`

---

## CDN Integration (Future)

```
Current: Browser → MinIO/S3 (direct)
Future:  Browser → CloudFront/CDN → S3 (origin)
```

**Changes needed:**
1. `PUBLIC_MEDIA_ENDPOINT_URL` = CDN domain
2. Cache-Control headers on S3 objects
3. `master.m3u8` no-cache, segments long-cache
4. Signed cookies or token auth for `hls/` if needed

---

## Monitoring & Cleanup

### Orphaned Objects
- Failed clips: `uploads/` + partial `hls/` may remain
- Deleted clips: both prefixes remain
- **Needed:** Periodic cleanup task

### Cleanup Task (Not Implemented)
```python
@shared_task
def cleanup_orphaned_media():
    # 1. Find clips with status='failed' older than 24h
    # 2. Delete their S3 objects (uploads/ + hls/)
    # 3. Find deleted clips (if soft delete) → cleanup
    # 4. Find hls/ prefixes without clip in DB → delete
```

### S3 Lifecycle Rules (Alternative)
```xml
<LifecycleConfiguration>
  <Rule>
    <ID>CleanupFailedUploads</ID>
    <Filter><Prefix>uploads/</Prefix></Filter>
    <Expiration><Days>7</Days></Expiration>
  </Rule>
</LifecycleConfiguration>
```

---

## Discrepancy: README vs Implementation

| README Claim | Actual |
|--------------|--------|
| "HLS output stored under `media/hls/{clip_id}/` on local disk" | **S3/MinIO** `hls/{clip_id}/` (since S3 migration) |
| "Not S3-backed yet" | **S3-backed** (STORAGES config) |

---

*Source: `backend/app/tasks.py`, `backend/app/views.py:95-112`, `backend/app/scrapers/uploader.py`, `backend/EchoFlow/settings.py:270-293`, `docker-compose.yml:108`*