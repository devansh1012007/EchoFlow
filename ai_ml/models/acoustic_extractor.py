"""Acoustic feature extraction wrapper (librosa) — STUB, deliberately DEFERRED.

Status: DEFERRED (2026-11-21 audit decision). Documentation-of-intent
only: the working acoustic extractor still lives in
``backend.app.tasks``, and no runtime code may import this module (the
guard ``backend/app/tests/test_ai_ml_boundary.py`` enforces the import
boundary and keeps this stub free of heavy imports such as librosa
itself).

What will live here eventually: ``extract_acoustic_vector()`` producing
the 128-dim acoustic vector via librosa, moved out of
``backend.app.tasks``. (``numpy`` stays importable here: it is a light,
already-required backend dependency, only used for annotations.)

See ai_ml/README.md ("Migration Status") for the deferral rationale.
"""
import numpy as np


def extract_acoustic_vector(y: np.ndarray, sr: int) -> list[float]:
    """Extract a 128-dim acoustic vector from audio.

    Deliberately deferred — the working implementation is
    ``backend.app.tasks.extract_acoustic_vector``.
    """
    raise NotImplementedError(
        "ai_ml.models.acoustic_extractor.extract_acoustic_vector is a "
        "deliberately deferred stub: the working implementation still lives "
        "in backend.app.tasks.extract_acoustic_vector (see ai_ml/README.md, "
        "'Migration Status')."
    )
