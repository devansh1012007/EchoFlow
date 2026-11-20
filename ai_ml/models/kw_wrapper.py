"""Keyphrase extraction wrapper (keybert) — STUB, deliberately DEFERRED.

Status: DEFERRED (2026-11-21 audit decision). Documentation-of-intent
only: the working keyphrase-extraction model loader still lives in
``backend.app.tasks``, and no runtime code may import this module (the
guard ``backend/app/tests/test_ai_ml_boundary.py`` enforces the import
boundary and keeps this stub free of heavy imports such as keybert
itself).

What will live here eventually: a lazy ``get_kw_model()`` plus an
``extract_keywords()`` helper wrapping keybert, moved out of
``backend.app.tasks``.

See ai_ml/README.md ("Migration Status") for the deferral rationale.
"""


def get_kw_model():
    """Lazy-load the KeyBERT model.

    Deliberately deferred — the working implementation is
    ``backend.app.tasks.get_kw_model``.
    """
    raise NotImplementedError(
        "ai_ml.models.kw_wrapper.get_kw_model is a deliberately deferred "
        "stub: the working implementation still lives in "
        "backend.app.tasks.get_kw_model (see ai_ml/README.md, "
        "'Migration Status')."
    )


def extract_keywords(text: str, top_n: int = 3) -> list[tuple[str, float]]:
    """Extract the top keyphrases from text.

    Deliberately deferred — the working tagging path runs inside
    ``backend.app.tasks.process_audio_to_hls``.
    """
    raise NotImplementedError(
        "ai_ml.models.kw_wrapper.extract_keywords is a deliberately deferred "
        "stub: the working tagging still lives in backend.app.tasks "
        "(process_audio_to_hls; see ai_ml/README.md, 'Migration Status')."
    )
