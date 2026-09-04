"""Keyphrase extraction wrapper."""


def get_kw_model():
    """Lazy-load KeyBERT model.

    TODO: Migrate from backend.app.tasks.get_kw_model()
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")


def extract_keywords(text: str, top_n: int = 3) -> list[tuple[str, float]]:
    """Extract keywords from text.

    TODO: Migrate from backend.app.tasks
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")
