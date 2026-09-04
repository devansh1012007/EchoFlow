"""Cold-start pipeline for new users.

TODO: Migrate tag-based vector bootstrapping from backend.app.tasks
"""


def initialize_user_vectors(user, selected_tags: list[str]):
    """Bootstrap user vectors from tag selection.

    TODO: Migrate from backend.app.views.TagsViewSet.initialize_vectors()
    """
    raise NotImplementedError("Migration pending from backend.app.views")
