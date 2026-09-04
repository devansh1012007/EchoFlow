# ML Models Lazy Loading

## Overview

ML models (~1GB total) loaded **on first use**, not at import time. Critical for:
- Avoiding memory bloat in web/API processes (gunicorn workers)
- Preventing duplicate loads in prefork Celery workers
- Enabling Docker build-time baking

---

## Implementation (`tasks.py:28-82`)

### Global State
```python
whisper_model = None
embedding_model = None
kw_model = None
_model_lock = threading.Lock()
```

### Thread-Safe Double-Checked Locking

```python
def get_whisper_model():
    global whisper_model
    if whisper_model is None:                    # First check (fast path)
        with _model_lock:                        # Acquire lock
            if whisper_model is None:            # Second check (inside lock)
                from faster_whisper import WhisperModel
                try:
                    whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
                    logger.info("WhisperModel initialized successfully.")
                except Exception as e:
                    logger.exception("Failed to initialize WhisperModel: %s", e)
                    raise
    return whisper_model

# Identical pattern for get_embedding_model() and get_kw_model()
```

### Why Double-Checked Locking?

| Approach | Problem |
|----------|---------|
| Module-level load | Loads in ALL processes (web, beat, workers) — memory waste |
| Simple `if None:` | Race condition: multiple threads load simultaneously |
| `@lru_cache` | Not thread-safe for mutable globals |
| **Double-checked locking** | Fast path after init, single init guaranteed |

---

## Model Specifications

### Whisper (Transcription)
```python
WhisperModel("base", device="cpu", compute_type="int8")
```
| Property | Value |
|----------|-------|
| Size | 74M params |
| Memory (int8) | ~384MB |
| Speed | ~5-15s for 30s audio |
| Language | Multilingual (99) |

### Sentence-Transformers (Semantic Embedding)
```python
SentenceTransformer('all-MiniLM-L6-v2')
```
| Property | Value |
|----------|-------|
| Size | 22M params |
| Memory | ~100MB |
| Speed | ~0.1s per encode |
| Dimensions | 384 |

### KeyBERT (Tag Extraction)
```python
KeyBERT()  # Uses same sentence-transformer internally
```
| Property | Value |
|----------|-------|
| Memory | Shared with embedding model |
| Speed | ~0.5-1s per extract |
| Output | Top-N keywords |

---

## Build-Time Baking (Dockerfile)

**Media stage bakes models into image:**

```dockerfile
# Dockerfile:116-123
RUN --mount=type=secret,id=hf_token \
    set -eu; \
    if [ -s /run/secrets/hf_token ]; then \
        export HF_TOKEN="$(cat /run/secrets/hf_token)"; \
    fi; \
    python -c "from faster_whisper import WhisperModel; m = WhisperModel('base', device='cpu', compute_type='int8'); del m"; \
    python -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('all-MiniLM-L6-v2'); del m"; \
    python -c "from keybert import KeyBERT; m = KeyBERT(); del m"
```

**Runtime env vars (media stage):**
```dockerfile
ENV HF_HOME=/home/appuser/.cache/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
```

**Effect:**
- Models downloaded **once at build** (with HF_TOKEN for private models)
- Cached to `/home/appuser/.cache/huggingface`
- Runtime: **fully offline** — no network calls
- `HF_HUB_OFFLINE=1` prevents accidental downloads

---

## Worker Configuration

### Heavy Media Worker (`celery_media`)
```yaml
# docker-compose.yml
celery_media:
  command: celery -A backend.EchoFlow worker -Q heavy_media --pool=solo --loglevel=info
  environment:
    HF_HOME: /home/appuser/.cache/huggingface
    HF_HUB_OFFLINE: "1"
    TRANSFORMERS_OFFLINE: "1"
```

**`--pool=solo`** — Single process (no prefork)
- **Why:** Models loaded in memory; forking would duplicate memory
- **Trade-off:** No concurrency within worker (processes clips sequentially)

### Other Workers
```yaml
celery:           # Default queue — no ML models loaded
celery_feed:      # Fast feed — vector queries only (no ML)
celery_beat:      # Scheduler — no ML
web:              # Gunicorn — no ML (API only)
```

---

## Memory Profile

### Per Worker Process

| Component | Memory |
|-----------|--------|
| Python baseline | ~50MB |
| Whisper (int8) | ~384MB |
| SentenceTransformer | ~100MB |
| KeyBERT (shared) | ~0MB |
| **Total** | **~534MB** |

### Without Lazy Loading (Hypothetical)

| Process Type | Workers | Memory Each | Total |
|--------------|---------|-------------|-------|
| Gunicorn (web) | 4 | 534MB | 2.1GB |
| Celery default | 4 | 534MB | 2.1GB |
| Celery feed | 4 | 534MB | 2.1GB |
| **Total** | **12** | | **6.3GB+** |

### With Lazy Loading + Isolation

| Process Type | Workers | Memory Each | Total |
|--------------|---------|-------------|-------|
| Gunicorn (web) | 4 | 50MB | 200MB |
| Celery default | 4 | 100MB | 400MB |
| Celery feed | 4 | 200MB | 800MB |
| Celery media | 1 | 534MB | 534MB |
| **Total** | **13** | | **~1.9GB** |

**Savings: ~4.4GB (70% reduction)**

---

## Failure Handling

### Initialization Failure
```python
try:
    whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
except Exception as e:
    logger.exception("Failed to initialize WhisperModel: %s", e)
    raise  # Propagates to task → task fails → Celery retry
```

### Runtime Failure (OOM, etc.)
- Task fails → Celery retry (max_retries=3, exponential backoff)
- Worker process may be killed by OOM killer
- `--pool=solo` limits blast radius to 1 clip at a time

---

## Alternative: Model Server (Future)

**Current:** Models in-process (Celery worker)
**Future:** Dedicated inference service

```
┌─────────────────┐     gRPC/REST      ┌──────────────────┐
│  Celery Worker  │ ─────────────────► │  Inference Svc   │
│  (lightweight)  │ ◄───────────────── │  (GPU, batched)  │
└─────────────────┘   vectors, text    └──────────────────┘
```

**Benefits:**
- GPU acceleration (Whisper 10x faster)
- Shared model across workers (memory efficient)
- Independent scaling
- Model versioning without redeploy

**Implementation options:**
- Triton Inference Server
- FastAPI + ONNX Runtime
- vLLM / TGI for LLMs

---

## Debugging Tips

### Check Model Loading
```bash
# In celery_media container
docker compose exec celery_media python -c "
from backend.app.tasks import get_whisper_model, get_embedding_model, get_kw_model
print('Whisper:', get_whisper_model() is not None)
print('Embedding:', get_embedding_model() is not None)
print('KeyBERT:', get_kw_model() is not None)
"
```

### Memory Usage
```bash
docker stats celery_media --no-stream
```

### Model Cache Location
```bash
docker compose exec celery_media ls -la /home/appuser/.cache/huggingface/
```

---

*Source: `backend/app/tasks.py:28-82`, `Dockerfile:116-123, 155-178`, `docker-compose.yml:286-337`*