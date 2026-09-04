# AI/ML Pipeline

Production ML pipeline for EchoFlow. This directory contains model wrappers, pipeline orchestration, and evaluation scripts.

## Structure

```
ai_ml/
├── models/              # ML model wrappers
│   ├── whisper_wrapper.py      # Audio transcription (faster-whisper)
│   ├── embedding_wrapper.py    # Semantic embeddings (sentence-transformers)
│   ├── kw_wrapper.py           # Keyphrase extraction (keybert)
│   └── acoustic_extractor.py   # Audio features (librosa)
├── pipelines/           # Orchestration
│   ├── audio_ingest.py         # Transcribe → Embed → Tag → HLS
│   ├── recommendation.py       # Composite scoring, vector blending
│   ├── feed_tasks.py           # Celery task wiring (refill_user_feed + pool rebuilds)
│   └── cold_start.py           # Tag-based vector bootstrapping
├── eval/                # Evaluation
│   ├── vector_quality.py       # Embedding quality metrics
│   └── feed_metrics.py         # Recommendation relevance
└── notebooks/           # Experiments
```

## Migration Status

The ML logic originally lived in `backend/app/tasks.py`. Migration progress
(2026-09, branch `feat/feed-engine-to-ai-ml`):

| Subsystem | Lives in `ai-ml`? | Module | Notes |
|---|---|---|---|
| Feed ranking logic (`calculate_time_decayed_vectors`, `build_feed_candidates`, `build_global_exploit_pool`, `build_user_explore_pool`) | YES | `pipelines/recommendation.py` | Pure-Python; no Celery |
| Feed Celery tasks (`refill_user_feed`, `rebuild_global_exploit_pool`, `dispatch_user_pool_rebuilds`, `rebuild_user_explore_pool`) | YES | `pipelines/feed_tasks.py` | Task names pinned to `backend.app.tasks.*` for routing compat |
| Model loaders (`get_whisper_model`, `get_embedding_model`, `get_kw_model`, `extract_acoustic_vector`) | NO | `backend/app/tasks.py` | Still used by `process_audio_to_hls` (data processing pipeline) |
| `process_audio_to_hls` / `normalize_to_wav` (data processing) | NO | `backend/app/tasks.py` | Per "don't move the data processing pipeline" directive |
| Audio ingest orchestration (`audio_ingest.py`) | STUB | `pipelines/audio_ingest.py` | Still `NotImplementedError`; future work |
| Cold-start (`cold_start.py`) | STUB | `pipelines/cold_start.py` | Still `NotImplementedError`; future work |
| Model wrappers (`models/*.py`) | STUB | `pipelines/models/*.py` | All `NotImplementedError`; future work |

The `backend.app.tasks` module re-exports the 5 moved names
(`refill_user_feed`, `calculate_time_decayed_vectors`, and the three
pool tasks) via a thin shim so pre-migration callers and Celery
routing keep working unchanged. New code should import from
`ai_ml.pipelines.recommendation` / `ai_ml.pipelines.feed_tasks`
directly.

The on-disk directory is `/ai_ml/` (importable as the Python package
`ai_ml`). The repo root is on `sys.path` at runtime — pytest via
`conftest.py`, and the Celery workers / gunicorn via the `cwd=/app`
convention. Both `import backend.app...` and `import ai_ml.pipelines...`
resolve.

The directory was previously `/ai_ml/` (hyphen); renamed to `/ai_ml/`
(underscore) in the feed-engine separation pass because Python
identifiers cannot contain a hyphen, and the prior "alias via
`__init__.py` self-import" workaround could not survive Celery's
auto-discovery / submodule loading without surprising edge cases.

## Future Migration

Remaining items for a follow-up PR:

1. Model loading wrappers move here from `backend.app.tasks` (the four
   `get_*_model` functions and `extract_acoustic_vector`). Doing this
   last is intentional — the data processing pipeline
   (`process_audio_to_hls`) is the only consumer and we want to keep
   it untouched per the original directive.
2. `audio_ingest.py` / `cold_start.py` stop being `NotImplementedError`
   stubs and become real orchestrators that import the new
   `recommendation` and `feed_tasks` modules.
3. `eval/vector_quality.py` and `eval/feed_metrics.py` get real
   evaluation harnesses.

Until then, `backend/app/tasks.py` remains the source of truth for
the data processing pipeline.

