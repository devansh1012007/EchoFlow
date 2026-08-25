"""Acoustic feature extraction wrapper."""
import numpy as np


def extract_acoustic_vector(y: np.ndarray, sr: int) -> list[float]:
    """Extract 128-dim acoustic vector from audio.

    TODO: Migrate from backend.app.tasks.extract_acoustic_vector()
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")
