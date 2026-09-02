# AI/ML Pipeline Overview

## Pipeline Stages

```
Audio Upload
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    process_audio_to_hls (Celery heavy_media)      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. DOWNLOAD & NORMALIZE                                        │
│     ├── Download from S3 (uploads/ prefix) to local temp       │
│     ├── FFmpeg: decode → mono 22050Hz WAV (authoritative)      │
│     └── Cleanup original temp file                              │
│                                                                 │
│  2. ACOUSTIC FEATURE EXTRACTION (librosa)                       │
│     ├── Load normalized WAV (librosa.load sr=22050)            │
│     ├── MFCC (40 dims) — timbre/voice texture                  │
│     ├── Chroma (12 dims) — harmonic/pitch content              │
│     ├── Mel Spectrogram (76 dims) — energy across frequencies  │
│     ├── Concatenate → 128-dim vector                           │
│     ├── Normalize for cosine similarity                         │
│     ├── Extract duration_ms (librosa.get_duration)             │
│     └── Save acoustic_vector + duration_ms to AudioClip        │
│                                                                 │
│  3. TRANSCRIPTION (faster-whisper)                              │
│     ├── Lazy-load WhisperModel("base", cpu, int8)              │
│     ├── Transcribe normalized WAV (beam_size=5)                │
│     └── Join segments → transcript_text                        │
│                                                                 │
│  4. SEMANTIC EMBEDDING (sentence-transformers)                  │
│     ├── Lazy-load SentenceTransformer("all-MiniLM-L6-v2")      │
│     ├── Encode transcript → 384-dim vector                     │
│     └── Save semantic_vector to AudioClip                      │
│                                                                 │
│  5. TAG EXTRACTION (KeyBERT)                                    │
│     ├── Lazy-load KeyBERT()                                    │
│     ├── Extract top 3 unigrams from transcript                 │
│     └── Save tags (JSON array) to AudioClip                    │
│                                                                 │
│  6. HLS TRANSCODING (FFmpeg)                                    │
│     ├── Create local temp dir for segments                     │
│     ├── FFmpeg: -c:a aac -ar 44100 -ac 2 -b:a 128k             │
│     │   -f hls -hls_time 4 -hls_playlist_type vod              │
│     │   -hls_segment_type mpegts (Chrome compatibility)        │
│     │   -master_pl_name master.m3u8                            │
│     ├── Upload ALL files to S3 (hls/{clip_id}/ prefix)         │
│     ├── Save hls_playlist_url = "hls/{clip_id}/master.m3u8"    │
│     ├── Set status = 'ready'                                   │
│     └── Cleanup local temp files                               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
AudioClip(status='ready') with vectors, tags, HLS URL
```

---

## Models Used

| Stage | Model | Implementation | Dimensions |
|-------|-------|----------------|------------|
| Acoustic | librosa (MFCC+Chroma+Mel) | `extract_acoustic_vector()` | 128 |
| Transcription | faster-whisper (base) | `WhisperModel("base", cpu, int8)` | Text |
| Semantic | sentence-transformers (all-MiniLM-L6-v2) | `SentenceTransformer.encode()` | 384 |
| Tagging | KeyBERT | `KeyBERT().extract_keywords()` | 3 tags |

---

## Model Loading Strategy

**Lazy loading with thread-safe double-checked locking** (`tasks.py:28-82`):

```python
whisper_model = None
embedding_model = None
kw_model = None
_model_lock = threading.Lock()

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        with _model_lock:
            if whisper_model is None:
                from faster_whisper import WhisperModel
                whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return whisper_model

# Similar for get_embedding_model(), get_kw_model()
```

**Why:**
- Avoids loading ~1GB models at import time
- Prevents duplicate loads in prefork Celery workers
- Models baked into Docker image at build time (HF_TOKEN secret)
- Runtime: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`

---

## Vector Details

### Acoustic Vector (128-dim)
```python
def extract_acoustic_vector(y, sr):
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=40).mean(axis=1)      # 40
    chroma = librosa.feature.chroma_stft(y=y, sr=sr).mean(axis=1)         # 12
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=76).mean(axis=1)  # 76
    acoustic_vector = np.concatenate((mfcc, chroma, mel))  # 128
    # Normalize
    norm = np.linalg.norm(acoustic_vector)
    if norm > 0: acoustic_vector = acoustic_vector / norm
    return acoustic_vector.tolist()
```

**Components:**
- MFCC (40): Mel-frequency cepstral coefficients — timbre, voice texture
- Chroma (12): Pitch class profile — harmonic content
- Mel (76): Energy distribution across mel frequency bands

### Semantic Vector (384-dim)
```python
embed_model = get_embedding_model()  # all-MiniLM-L6-v2
vector = embed_model.encode(transcript_text)  # 384-dim
clip.semantic_vector = vector.tolist()
```

**Model:** `sentence-transformers/all-MiniLM-L6-v2` — 384-dim, fast, good quality

### Tags (JSON Array)
```python
keywords = get_kw_model().extract_keywords(
    transcript_text,
    keyphrase_ngram_range=(1, 1),  # unigrams only
    stop_words='english',
    top_n=3,
)
clip.tags = [kw[0] for kw in keywords]  # e.g., ["comedy", "storytelling", "motivation"]
```

---

## HLS Transcoding Details

### FFmpeg Command
```bash
ffmpeg -y -i normalized.wav \
  -c:a aac -ar 44100 -ac 2 -b:a 128k \
  -f hls -hls_time 4 -hls_playlist_type vod \
  -hls_segment_type mpegts \
  -master_pl_name master.m3u8 \
  output/index.m3u8
```

**Parameters:**
| Parameter | Value | Purpose |
|-----------|-------|---------|
| `-c:a aac` | AAC codec | Universal browser support |
| `-ar 44100` | 44.1kHz sample rate | Standard audio |
| `-ac 2` | Stereo | Consistent output |
| `-b:a 128k` | 128kbps bitrate | Single quality (not ABR in current impl) |
| `-hls_time 4` | 4-second segments | Low latency, good seek |
| `-hls_playlist_type vod` | VOD playlist | Static, cacheable |
| `-hls_segment_type mpegts` | MPEG-TS containers | Chrome MSE compatibility |
| `-master_pl_name master.m3u8` | Master playlist name | Standard HLS |

**Output structure:**
```
hls/{clip_id}/
├── master.m3u8          # Master playlist (references variants)
├── index.m3u8           # Variant playlist (single quality currently)
├── segment_000.ts       # MPEG-TS segments
├── segment_001.ts
└── ...
```

**Note:** Current implementation produces **single quality** (128kbps). ABR (192/128/64kbps) commented out in tasks.py:337-417.

---

## Scraper Ingestion Pipeline

Same AI pipeline applied to scraped clips:

```
Scraper (management command or Celery task)
    │
    ├── fetch_audio() → [{url, title, page_url, license, id}]
    ├── download_audio() → local temp file (robots.txt, rate limit, size check)
    ├── normalize_and_trim() → pydub trim to max_seconds, stereo 44100Hz MP3
    ├── save_clip() → AudioClip(imported_via_scraper=True, provenance fields)
    └── process_audio_to_hls.delay(clip_id) → same AI pipeline
```

**Sources:** Wikimedia, Internet Archive, Freesound, Kaggle (local)

---

## Integration Points

| Component | Calls | Purpose |
|-----------|-------|---------|
| `AudioUploadViewSet.create()` | `process_audio_to_hls.delay()` | User uploads |
| `scrape_and_import` task | `process_audio_to_hls.delay()` | Scraper imports |
| `TagsViewSet.initialize_vectors()` | `refill_user_feed.delay()` | Cold start |
| `refill_user_feed` | `calculate_time_decayed_vectors()` | Feed ranking |
| `SuggestionViewSet` | `calculate_time_decayed_vectors()` | Category explore |
| `evolve_long_term_user_baselines` | `calculate_time_decayed_vectors(limit=500)` | Hourly vector update |

---

## Future Migration (ai-ml/ directory)

**Planned structure** (currently stubs):
```
ai-ml/
├── models/              # Model wrappers
│   ├── whisper_wrapper.py
│   ├── embedding_wrapper.py
│   ├── kw_wrapper.py
│   └── acoustic_extractor.py
├── pipelines/           # Orchestration
│   ├── audio_ingest.py
│   ├── recommendation.py
│   └── cold_start.py
└── eval/                # Evaluation
    ├── vector_quality.py
    └── feed_metrics.py
```

**Goal:** Move ML logic out of `backend/app/tasks.py` into dedicated package.

---

## Performance Characteristics

| Stage | Typical Time | Memory | Bottleneck |
|-------|-------------|--------|------------|
| FFmpeg normalize | 1-3s | Low | CPU |
| librosa features | 0.5-2s | Medium | CPU |
| Whisper transcribe | 5-30s | High (1GB) | CPU/GPU |
| Embedding encode | 0.1-0.5s | Medium | CPU |
| KeyBERT tags | 0.5-2s | Medium | CPU |
| FFmpeg HLS | 3-10s | Low | CPU/IO |
| S3 upload | 1-5s | Low | Network |

**Total:** ~15-50s per clip (depends on duration)

**Worker config:** `--pool=solo` (single process) to avoid model duplication

---

*Source: `backend/app/tasks.py`, `ai-ml/README.md`, `ai-ml/models/*.py`, `ai-ml/pipelines/*.py`*