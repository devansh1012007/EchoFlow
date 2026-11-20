"""Semantic embedding wrapper (sentence-transformers) — STUB, deliberately DEFERRED.

Status: DEFERRED (2026-11-21 audit decision). Documentation-of-intent
only: the working embedding loader still lives in ``backend.app.tasks``,
and no runtime code may import this module (the guard
``backend/app/tests/test_ai_ml_boundary.py`` enforces the import
boundary and keeps this stub free of heavy imports such as
sentence-transformers itself).

What will live here eventually: a lazy ``get_embedding_model()`` plus an
``encode()`` helper wrapping sentence-transformers, moved out of
``backend.app.tasks``.

See ai_ml/README.md ("Migration Status") for the deferral rationale.
"""


def get_embedding_model():
    """Lazy-load the sentence-transformers embedding model.

    Deliberately deferred — the working implementation is
    ``backend.app.tasks.get_embedding_model``.
    """
    raise NotImplementedError(
        "ai_ml.models.embedding_wrapper.get_embedding_model is a deliberately "
        "deferred stub: the working implementation still lives in "
        "backend.app.tasks.get_embedding_model (see ai_ml/README.md, "
        "'Migration Status')."
    )


def encode(text: str) -> list[float]:
    """Encode text into a semantic vector.

    Deliberately deferred — the working encoding path runs inside
    ``backend.app.tasks.process_audio_to_hls``.
    """
    raise NotImplementedError(
        "ai_ml.models.embedding_wrapper.encode is a deliberately deferred "
        "stub: the working encoding still lives in backend.app.tasks "
        "(process_audio_to_hls; see ai_ml/README.md, 'Migration Status')."
    )
