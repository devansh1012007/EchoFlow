from django.apps import AppConfig


class App1Config(AppConfig):
    name = 'backend.app'

    def ready(self):
        # N9 fix: import signals so the post_delete handler is
        # registered. The import has to live in ready() (not at the
        # top of the module) so Django apps are fully loaded before
        # the model class is referenced.
        from . import signals  # noqa: F401
