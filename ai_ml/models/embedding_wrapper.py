"""Semantic embedding wrapper."""


def get_embedding_model():
    """Lazy-load embedding model.

    TODO: Migrate from backend.app.tasks.get_embedding_model()
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")


def encode(text: str) -> list[float]:
    """Encode text to semantic vector.

    TODO: Migrate from backend.app.tasks
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")
