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
| Audio ingest orchestration (`audio_ingest.py`) | STUB — DEFERRED (2026-11-21 audit decision) | `pipelines/audio_ingest.py` | Still `NotImplementedError` by design: the working pipeline stays in `backend/app/tasks.py` (audit P2 "complete-or-clearly-defer"); no runtime code imports it — see Boundary enforcement |
| Cold-start (`cold_start.py`) | YES — implemented (supersedes the original STUB row) | `pipelines/cold_start.py` | `initialize_user_vectors` is real and consumed by the onboarding endpoint (`backend/app/views/feed.py`); exercised end-to-end by `test_tags_initialize_bounds.py`. Row corrected 2026-11-21 when the audit reconciled this table with the code |
| Model wrappers (`models/*.py`) | STUB — DEFERRED (2026-11-21 audit decision) | `models/*.py` | All `NotImplementedError` by design: the loaders stay in `backend/app/tasks.py` (audit P2 "complete-or-clearly-defer"); no runtime code imports them — see Boundary enforcement |
| Evaluation harnesses (`eval/*.py`) | `eval/feed_metrics.py` YES (audit P2 follow-up); `eval/vector_quality.py` still STUB — DEFERRED | `eval/feed_metrics.py` | Real offline evaluator: temporal holdout, Recall@K / NDCG@K (binary log2), catalog coverage, cold-start slice, chronological/popularity/live-recommender comparison. Run `python manage.py eval_feed --k 10 --holdout-days 14 --json-out report.json`. No Redis (the recommender runs the SQL fallback, `pool_first=False`, leak-free as-of simulation). Allow-listed in `test_ai_ml_boundary.py`. |

The `backend.app.tasks` module re-exports the 5 moved names
(`refill_user_feed`, `calculate_time_decayed_vectors`, and the three
pool tasks) via a thin shim so pre-migration callers and Celery
routing keep working unchanged. New code should import from
`ai_ml.pipelines.recommendation`, `ai_ml.pipelines.feed_tasks`, or
(for the onboarding cold-start) `ai_ml.pipelines.cold_start`
directly.

## Boundary enforcement

The deferral above is enforced, not just documented:
`backend/app/tests/test_ai_ml_boundary.py` (pure AST/`importlib`, no DB
marker) runs in the normal suite and guards three properties.

1. **Runtime imports are allow-listed.** The guard AST-scans every
   backend runtime file — `backend/app` recursively minus `tests/` and
   `migrations/`, plus `backend/EchoFlow/` and `backend/scripts/` — for
   absolute `import ai_ml...` / `from ai_ml...` statements and fails,
   naming the offending `file:line`, if anything imports an `ai_ml`
   module other than the three implemented ones:
   `pipelines/recommendation`, `pipelines/feed_tasks`,
   `pipelines/cold_start`. A stub that starts being imported at runtime
   turns the suite red instead of crashing a request path.
2. **Stubs stay import-light.** Each stub file is AST-checked against a
   blocklist of model runtimes (torch, transformers,
   sentence-transformers, faster-whisper, whisper, keybert, librosa,
   onnxruntime, ctranslate2, soundfile, av). Stubs are
   documentation-of-intent, not dead weight: importing one must stay
   cheap so cold imports, `django check`, and test collection stay fast.
3. **The stub API still says so.** Every public callable in a stub must
   raise `NotImplementedError` with a message pointing at the working
   implementation in `backend.app.tasks`, and each stub's docstring must
   mark the DEFERRED state and point back at this README.

Adding a module to the allow-list requires all three of: a real
implementation, tests, and an updated row in the table above.

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
2. `audio_ingest.py` stops being a `NotImplementedError` stub and
   becomes a real orchestrator that imports the new `recommendation`
   and `feed_tasks` modules. (`cold_start.py`, originally listed here
   too, was implemented separately — see its row in Migration Status.)
3. `eval/vector_quality.py` gets a real evaluation harness.
   (`eval/feed_metrics.py` is done — see its row in Migration Status;
   audit P2 follow-up, 2026-11-21.)

Until then, `backend/app/tasks.py` remains the source of truth for
the data processing pipeline.

