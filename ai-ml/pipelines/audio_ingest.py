"""Audio ingestion pipeline orchestration.

TODO: Migrate process_audio_to_hls logic from backend.app.tasks
"""


def process_audio_to_hls(clip_id: str):
    """Orchestrate: acoustic extract → transcribe → embed → tag → HLS.

    TODO: Migrate from backend.app.tasks.process_audio_to_hls()
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")
