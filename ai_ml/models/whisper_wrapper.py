"""Audio transcription wrapper."""


def get_whisper_model():
    """Lazy-load whisper model.

    TODO: Migrate from backend.app.tasks.get_whisper_model()
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")


def transcribe(audio_path: str) -> str:
    """Transcribe audio file to text.

    TODO: Migrate from backend.app.tasks
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")
