"""Audio ingestion pipeline orchestration — STUB, deliberately DEFERRED.

Status: DEFERRED (2026-11-21 audit decision). This is
documentation-of-intent, not unfinished work someone forgot: the working
ingest pipeline still lives in ``backend.app.tasks`` by design, and no
runtime code may import this module — the guard
``backend/app/tests/test_ai_ml_boundary.py`` fails the suite if that
changes.

What will live here eventually: the transcribe -> embed -> tag -> HLS
orchestration composing the ``ai_ml.models`` wrappers into one ingest
pass for a freshly uploaded clip.

See ai_ml/README.md ("Migration Status") for the deferral rationale.
"""


def process_audio_to_hls(clip_id: str):
    """Orchestrate: acoustic extract -> transcribe -> embed -> tag -> HLS.

    Deliberately deferred — the working implementation is
    ``backend.app.tasks.process_audio_to_hls``.
    """
    raise NotImplementedError(
        "ai_ml.pipelines.audio_ingest.process_audio_to_hls is a deliberately "
        "deferred stub: the working implementation still lives in "
        "backend.app.tasks.process_audio_to_hls (see ai_ml/README.md, "
        "'Migration Status')."
    )
