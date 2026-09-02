# Media Processing Pipeline Overview

## End-to-End Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MEDIA PROCESSING PIPELINE                             │
└─────────────────────────────────────────────────────────────────────────────┘

USER UPLOAD                          SCRAPER IMPORT
     │                                     │
     ▼                                     ▼
POST /clips/                    management: scrape_audio
     │                                     │
     ▼                                     ▼
AudioUploadViewSet.create()         Scraper pipeline
     │                                     │
     ├── Validate file                   ├── fetch_audio() → [{url, title, license...}]
     ├── Create AudioClip                ├── download_audio() → local temp
     │   status='processing'             ├── normalize_and_trim() → MP3
     │   Save to S3 (uploads/)           ├── save_clip() → AudioClip + S3
     │                                     │
     └── transaction.on_commit()         └── process_audio_to_hls.delay()
          │                                     │
          ▼                                     ▼
    ┌─────────────────────────────────────────────────────────────┐
    │              Celery heavy_media queue (solo)                 │
    │              process_audio_to_hls(clip_id)                   │
    └─────────────────────────────────────────────────────────────┘
          │
          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ 1. DOWNLOAD FROM S3                                         │
    │    original_file (uploads/) → local temp                    │
    └─────────────────────────────────────────────────────────────┘
          │
          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ 2. FFMPEG NORMALIZE (Authoritative Decode)                  │
    │    Input: any format (mp3/wav/ogg/webm...)                  │
    │    Output: mono 22050Hz WAV (normalized_path)               │
    │    Command: ffmpeg -ac 1 -ar 22050 -f wav                   │
    └─────────────────────────────────────────────────────────────┘
          │
          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ 3. ACOUSTIC FEATURES (librosa)                              │
    │    Load normalized WAV                                       │
    │    MFCC (40) + Chroma (12) + Mel (76) = 128-dim vector     │
    │    Extract duration_ms                                       │
    │    Save: acoustic_vector, duration_ms                       │
    └─────────────────────────────────────────────────────────────┘
          │
          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ 4. TRANSCRIPTION (faster-whisper)                           │
    │    WhisperModel("base", cpu, int8)                          │
    │    beam_size=5                                              │
    │    Save: transcript_text                                    │
    └─────────────────────────────────────────────────────────────┘
          │
          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ 5. SEMANTIC EMBEDDING (sentence-transformers)               │
    │    all-MiniLM-L6-v2 → 384-dim vector                        │
    │    Save: semantic_vector                                    │
    └─────────────────────────────────────────────────────────────┘
          │
          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ 6. TAG EXTRACTION (KeyBERT)                                 │
    │    Top 3 unigrams from transcript                           │
    │    Save: tags (JSON array)                                  │
    └─────────────────────────────────────────────────────────────┘
          │
          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ 7. HLS TRANSCODING (FFmpeg)                                 │
    │    Input: normalized WAV                                    │
    │    Output: MPEG-TS segments, 128kbps AAC, 4s chunks         │
    │    Local dir → Upload ALL to S3 (hls/{clip_id}/)            │
    │    Save: hls_playlist_url = "hls/{clip_id}/master.m3u8"     │
    │    Set status = 'ready'                                     │
    └─────────────────────────────────────────────────────────────┘
          │
          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │ 8. CLEANUP                                                  │
    │    Remove all local temp files                              │
    └─────────────────────────────────────────────────────────────┘
```

---

## Key Design Principles

### 1. Single Authoritative Decode
```python
# tasks.py:210-224
normalized_path = normalize_to_wav(input_file_path)
# ALL downstream steps (librosa, Whisper, HLS) use normalized_path
```
**Why:** Prevents format-specific bugs, ensures consistent input to all models.

### 2. Scratch Space Only
```python
# All temp files in local container filesystem
local_hls_dir = tempfile.mkdtemp(prefix=f'hls-{clip_id}-')
# Cleaned up in finally block
```
**No shared filesystem** — works in distributed deployment.

### 3. S3 for Durable Storage
```python
# Upload to S3 (uploads/ prefix = private, hls/ prefix = public)
storage_prefix = f"hls/{clip.id}"
for file in os.walk(local_hls_dir):
    default_storage.save(storage_key, file_handle)
```

### 4. Fail-Fast with Cleanup
```python
try:
    # ... each stage ...
except Exception as e:
    clip.status = 'failed'
    clip.save()
    return  # Celery retry handles transient
finally:
    # ALWAYS cleanup
    shutil.rmtree(local_hls_dir, ignore_errors=True)
```

---

## Status Transitions

```
created (API)          processing (task start)    ready (success)    failed (error)
    │                        │                         │                   │
    │   transaction.on_commit    librosa/whisper/       All vectors       Any exception
    ▼   → task enqueued        HLS complete            + HLS uploaded    → status='failed'
AudioClip                                              clip.status='ready'
```

---

## Scraper Integration

Same pipeline for scraped clips:
```
scrape_audio command / scrape_and_import task
       │
       ▼
fetch_audio() → download → normalize → save_clip()
       │
       ▼
AudioClip(imported_via_scraper=True, provenance fields)
       │
       ▼
process_audio_to_hls.delay(clip_id)  ← SAME TASK
```

---

## Performance Profile

| Stage | Time (30s clip) | Memory | Parallelizable |
|-------|-----------------|--------|----------------|
| S3 Download | 1-3s | Low | No |
| FFmpeg Normalize | 1-2s | Low | No |
| librosa Features | 0.5-2s | Medium | No |
| Whisper Transcribe | 5-15s | **High (384MB)** | No |
| Embedding Encode | 0.1s | Medium | No |
| KeyBERT Tags | 0.5-1s | Medium | No |
| FFmpeg HLS | 3-10s | Low | No |
| S3 Upload | 1-5s | Low | No |
| **Total** | **15-50s** | **~500MB peak** | **Sequential** |

---

## Failure Modes

| Stage | Failure | Result |
|-------|---------|--------|
| S3 Download | Network/404 | Retry (Celery) |
| FFmpeg Normalize | Corrupt file | status='failed' |
| librosa | Unsupported format | status='failed' |
| Whisper | OOM / crash | Retry (Celery) |
| Embedding | Model error | status='failed' |
| KeyBERT | Model error | status='failed' |
| FFmpeg HLS | Encoding error | status='failed' |
| S3 Upload | Network/quota | Retry (Celery) |

**All stages:** `clip.status = 'failed'` on exception, full cleanup in `finally`.

---

*Source: `backend/app/tasks.py:184-335`, `backend/app/views.py:95-112`, `backend/app/scrapers/`*