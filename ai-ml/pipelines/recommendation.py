"""Recommendation pipeline orchestration.

TODO: Migrate composite scoring, vector blending from backend.app.tasks
"""


def calculate_time_decayed_vectors(user, limit: int = 50):
    """Calculate time-decayed user preference vectors.

    TODO: Migrate from backend.app.tasks.calculate_time_decayed_vectors()
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")


def calculate_blended_query_vectors(user):
    """Calculate blended short-term + long-term user vectors.

    TODO: Migrate from backend.app.tasks.calculate_blended_query_vectors()
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")


def refill_user_feed(user_id: str, count: int = 50):
    """Refill Redis feed queue with composite-ranked clips.

    TODO: Migrate from backend.app.tasks.refill_user_feed()
    """
    raise NotImplementedError("Migration pending from backend.app.tasks")
