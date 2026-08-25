# AI/ML Pipeline

Production ML pipeline for EchoFlow. This directory contains model wrappers, pipeline orchestration, and evaluation scripts.

## Structure

```
ai-ml/
├── models/              # ML model wrappers
│   ├── whisper_wrapper.py      # Audio transcription (faster-whisper)
│   ├── embedding_wrapper.py    # Semantic embeddings (sentence-transformers)
│   ├── kw_wrapper.py           # Keyphrase extraction (keybert)
│   └── acoustic_extractor.py   # Audio features (librosa)
├── pipelines/           # Orchestration
│   ├── audio_ingest.py         # Transcribe → Embed → Tag → HLS
│   ├── recommendation.py       # Composite scoring, vector blending
│   └── cold_start.py           # Tag-based vector bootstrapping
├── eval/                # Evaluation
│   ├── vector_quality.py       # Embedding quality metrics
│   └── feed_metrics.py         # Recommendation relevance
└── notebooks/           # Experiments
```

## Future Migration

The ML logic currently lives in `backend/app/tasks.py`. This directory is a placeholder for the future migration where:

1. Model loading wrappers move here (`get_whisper_model()`, `get_embedding_model()`, `get_kw_model()`)
2. Pipeline orchestration moves here (`process_audio_to_hls` logic)
3. Recommendation algorithms move here (`calculate_time_decayed_vectors`, `refill_user_feed` scoring)

Until then, `backend/app/tasks.py` remains the source of truth.
