"""Audio transcription wrapper (faster-whisper) — STUB, deliberately DEFERRED.

Status: DEFERRED (2026-11-21 audit decision). Documentation-of-intent
only: the working whisper loader/transcription still lives in
``backend.app.tasks``, and no runtime code may import this module (the
guard ``backend/app/tests/test_ai_ml_boundary.py`` enforces the import
boundary and keeps this stub free of heavy imports such as
faster-whisper itself).

What will live here eventually: a lazy ``get_whisper_model()`` plus a
``transcribe()`` helper wrapping faster-whisper, moved out of
``backend.app.tasks``.

See ai_ml/README.md ("Migration Status") for the deferral rationale.
"""


def get_whisper_model():
    """Lazy-load the faster-whisper model.

    Deliberately deferred — the working implementation is
    ``backend.app.tasks.get_whisper_model``.
    """
    raise NotImplementedError(
        "ai_ml.models.whisper_wrapper.get_whisper_model is a deliberately "
        "deferred stub: the working implementation still lives in "
        "backend.app.tasks.get_whisper_model (see ai_ml/README.md, "
        "'Migration Status')."
    )


def transcribe(audio_path: str) -> str:
    """Transcribe an audio file to text.

    Deliberately deferred — the working transcription path runs inside
    ``backend.app.tasks.process_audio_to_hls``.
    """
    raise NotImplementedError(
        "ai_ml.models.whisper_wrapper.transcribe is a deliberately deferred "
        "stub: the working transcription still lives in backend.app.tasks "
        "(process_audio_to_hls; see ai_ml/README.md, 'Migration Status')."
    )
