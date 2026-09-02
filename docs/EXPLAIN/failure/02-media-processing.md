# Media Processing Failure Handling

## Overview

The media processing pipeline (`process_audio_to_hls` task) has multiple failure points. This document details each stage's failure modes, detection, and recovery.

---

## Pipeline Stages & Failure Points

```
process_audio_to_hls(clip_id)
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. DOWNLOAD FROM S3                                         │
│    - Network timeout → Retry                                │
│    - 404 Not Found → Failed                                 │
│    - Permission denied → Failed                             │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. FFMPEG NORMALIZE                                         │
│    - Invalid container → Failed                             │
│    - Corrupt data → Failed                                  │
│    - Unsupported codec → Failed                             │
│    - OOM → Worker crash → Retry                             │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. LIBROSA FEATURES                                         │
│    - Load error → Failed                                    │
│    - Memory error → Worker crash                            │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. WHISPER TRANSCRIPTION                                    │
│    - Model load OOM → Worker crash                          │
│    - Transcription error → Failed                           │
│    - Empty result → Instrumental fallback                   │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. SEMANTIC EMBEDDING                                       │
│    - Model load error → Failed                              │
│    - Encode error → Failed                                  │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. KEYBERT TAGGING                                          │
│    - Model load error → Failed                              │
│    - Extract error → Empty tags                             │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. FFMPEG HLS TRANSCODE                                     │
│    - Encode error → Failed                                  │
│    - Disk full → Failed                                     │
│    - OOM → Worker crash                                     │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 8. S3 UPLOAD                                                │
│    - Network error → Retry                                  │
│    - Permission denied → Failed                             │
│    - Quota exceeded → Failed                                │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 9. CLEANUP & STATUS UPDATE                                  │
│    - DB error → Retry (Celery)                              │
│    - Cleanup error → Logged, ignored                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Detailed Failure Analysis

### Stage 1: S3 Download

**Code** (`tasks.py:205-209`):
```python
fd, input_file_path = tempfile.mkstemp(suffix=ext)
with clip.original_file.open('rb') as remote_file:
    shutil.copyfileobj(remote_file, local_copy)
```

**Failure Modes:**
| Error | Cause | Handling |
|-------|-------|----------|
| `ConnectionError` | Network blip | Celery retry (3x, exponential backoff) |
| `ClientError: 404` | Object deleted | `clip.status='failed'`, no retry |
| `ClientError: 403` | Permissions/IAM | `clip.status='failed'`, alert |
| `ReadTimeout` | Slow network | Celery retry |
| `NoCredentialsError` | Missing AWS keys | Config error, alert |

**Recovery:** Celery retries 3x with 60s→120s→240s delays.

---

### Stage 2: FFmpeg Normalize

**Code** (`tasks.py:142-169`, `tasks.py:212-213`):
```python
def normalize_to_wav(input_file_path, sr=22050):
    command = ['ffmpeg', '-y', '-i', input_file_path, '-ac', '1', '-ar', str(sr), '-f', 'wav', wav_path]
    subprocess.run(command, check=True, ...)
    return wav_path
```

**Failure Modes:**
| Error | Cause | Handling |
|-------|-------|----------|
| `CalledProcessError` | Invalid input file | `clip.status='failed'`, log stderr |
| `FileNotFoundError` | FFmpeg not installed | Docker image issue, alert |
| `OSError: [Errno 12]` | Out of memory | Worker crash → Celery retry |
| `PermissionError` | No write access | Scratch dir issue, alert |

**Key Insight:** Normalization is the **authoritative decode** — all downstream uses this WAV.

---

### Stage 3: Librosa Features

**Code** (`tasks.py:236-248`):
```python
try:
    y, sr = librosa.load(normalized_path, sr=22050)
    clip.acoustic_vector = extract_acoustic_vector(y, sr)
    clip.duration_ms = int(librosa.get_duration(y=y, sr=sr) * 1000)
    clip.save(update_fields=['acoustic_vector', 'duration_ms'])
except Exception as e:
    clip.status = 'failed'
    clip.save()
    return
```

**Failure Modes:**
| Error | Cause | Handling |
|-------|-------|----------|
| `LibrosaError` | Corrupt WAV | `clip.status='failed'` |
| `MemoryError` | Large file/OOM | Worker crash → Celery retry |
| `ValueError` | Invalid sample rate | `clip.status='failed'` |

---

### Stage 4: Whisper Transcription

**Code** (`tasks.py:251-279`):
```python
model = get_whisper_model()
segments, info = model.transcribe(normalized_path, beam_size=5)
transcript_text = " ".join([segment.text for segment in segments]).strip()
```

**Failure Modes:**
| Error | Cause | Handling |
|-------|-------|----------|
| `MemoryError` (model load) | OOM on worker | Worker crash → Celery retry |
| `RuntimeError` (CUDA) | GPU error | CPU fallback (int8) |
| `ValueError` | Invalid audio | `clip.status='failed'` |
| Empty transcript | Silent/instrumental | Zero vector + "instrumental" tag |

**Model Loading (Thread-Safe):**
```python
def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        with _model_lock:
            if whisper_model is None:
                whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return whisper_model
```

---

### Stage 5: Semantic Embedding

**Code** (`tasks.py:258-261`):
```python
if transcript_text:
    embed_model = get_embedding_model()
    vector = embed_model.encode(transcript_text)
    clip.semantic_vector = vector.tolist()
```

**Failure Modes:**
| Error | Cause | Handling |
|-------|-------|----------|
| `MemoryError` (model load) | OOM | Worker crash |
| `RuntimeError` | Model corrupted | `clip.status='failed'` |
| `ValueError` | Empty transcript | Handled above (zero vector) |

---

### Stage 6: KeyBERT Tagging

**Code** (`tasks.py:262-270`):
```python
keywords = get_kw_model().extract_keywords(
    transcript_text, keyphrase_ngram_range=(1,1), stop_words='english', top_n=3
)
clip.tags = [kw[0] for kw in keywords]
```

**Failure Modes:**
| Error | Cause | Handling |
|-------|-------|----------|
| `MemoryError` (model load) | OOM | Worker crash |
| `ValueError` | No keywords found | `clip.tags = ["general"]` fallback |

---

### Stage 7: FFmpeg HLS Transcode

**Code** (`tasks.py:285-327`):
```python
command = [
    'ffmpeg', '-y', '-i', normalized_path,
    '-c:a', 'aac', '-ar', '44100', '-ac', '2', '-b:a', '128k',
    '-f', 'hls', '-hls_time', '4', '-hls_playlist_type', 'vod',
    '-hls_segment_type', 'mpegts',
    '-master_pl_name', 'master.m3u8',
    os.path.join(local_hls_dir, 'index.m3u8')
]
subprocess.run(command, check=True, ...)
```

**Failure Modes:**
| Error | Cause | Handling |
|-------|-------|----------|
| `CalledProcessError` | FFmpeg encode failed | `clip.status='failed'`, log stderr |
| `FileNotFoundError` | FFmpeg missing | Docker image issue |
| `OSError: [Errno 28]` | Disk full | `clip.status='failed'` |
| `MemoryError` | OOM during encode | Worker crash → Celery retry |

**MPEG-TS Critical:**
```python
'-hls_segment_type', 'mpegts'  # Chrome compatibility
# fMP4 causes: DECODER_ERROR_NOT_SUPPORTED
```

---

### Stage 8: S3 Upload

**Code** (`tasks.py:302-323`):
```python
storage_prefix = f"hls/{clip.id}"
for root, _dirs, files in os.walk(local_hls_dir):
    for fname in files:
        local_path = os.path.join(root, fname)
        rel_path = os.path.relpath(local_path, local_hls_dir)
        storage_key = f"{storage_prefix}/{rel_path}".replace(os.sep, '/')
        with open(local_path, 'rb') as fh:
            default_storage.save(storage_key, fh)

clip.hls_playlist_url = f"{storage_prefix}/master.m3u8"
clip.status = 'ready'
clip.save()
```

**Failure Modes:**
| Error | Cause | Handling |
|-------|-------|----------|
| `ClientError: Timeout` | Network | Celery retry |
| `ClientError: 403` | Permissions | `clip.status='failed'` |
| `ClientError: 413` | Object too large | `clip.status='failed'` |
| `ClientError: 503` | Service unavailable | Celery retry |
| `NoCredentialsError` | Config missing | Alert, fail |

---

### Stage 9: Cleanup & Status

**Code** (`tasks.py:328-334`):
```python
finally:
    try:
        os.remove(normalized_path)
    except OSError:
        pass
    shutil.rmtree(local_hls_dir, ignore_errors=True)
```

**Guaranteed Cleanup:** Even on exception, temp files removed.

---

## Retry Behavior Summary

| Stage | Retries | Delay | Backoff | Max Delay |
|-------|---------|-------|---------|-----------|
| S3 Download | 3 | 60s | ×2 | 600s |
| FFmpeg Normalize | 3 | 60s | ×2 | 600s |
| Librosa | 3 | 60s | ×2 | 600s |
| Whisper | 3 | 60s | ×2 | 600s |
| Embedding | 3 | 60s | ×2 | 600s |
| KeyBERT | 3 | 60s | ×2 | 600s |
| FFmpeg HLS | 3 | 60s | ×2 | 600s |
| S3 Upload | 3 | 60s | ×2 | 600s |

**All via Celery task decorator:**
```python
@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=False
)
```

---

## Status Transitions

```
CREATED (API) → PROCESSING (task start) → READY (success) → FAILED (any error)
                         │                      │
                         │ (retry)              │ (manual reprocess)
                         ▼                      ▼
                    PROCESSING ────────────────→ FAILED (max retries exceeded)
```

---

## Monitoring & Alerting

### Key Metrics (Not Implemented)
| Metric | Alert Threshold |
|--------|-----------------|
| Task failure rate | > 5% in 5min |
| Avg processing time | > 60s |
| FFmpeg failure rate | > 10% |
| Whisper OOM rate | > 1% |
| S3 upload failures | > 1% |

### Log Patterns for Alerting
```bash
# Failure detection
grep "clip.status='failed'" logs | wc -l
grep "FFmpeg Error" logs | tail -10
grep "MemoryError" logs | grep -c "process_audio_to_hls"
```

---

## Recovery Procedures

### Manual Reprocess
```bash
# Reset clip status and re-enqueue
docker compose exec web python manage.py shell -c "
from backend.app.models import AudioClip
clip = AudioClip.objects.get(id='uuid')
clip.status = 'processing'
clip.save()
"
# Then:
docker compose exec web python -c "
from backend.app.tasks import process_audio_to_hls
process_audio_to_hls.delay('uuid')
"
```

### Bulk Reprocess Failed Clips
```python
from backend.app.models import AudioClip
from backend.app.tasks import process_audio_to_hls

failed = AudioClip.objects.filter(status='failed', created_at__gte='2024-01-01')
for clip in failed:
    clip.status = 'processing'
    clip.save()
    process_audio_to_hls.delay(str(clip.id))
```

---

*Source: `backend/app/tasks.py:184-335`*