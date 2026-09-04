from django.apps import AppConfig


class App1Config(AppConfig):
    name = 'backend.app'

    def ready(self):
        # B13: Sentry init runs once per process after Django apps are
        # fully loaded. The DJANGO_DEBUG=False + SENTRY_DSN gate lives
        # inside init_sentry so dev/tests/unconfigured envs never pay
        # the SDK cost.
        from backend.EchoFlow.sentry import init_sentry
        init_sentry()

        # N9 fix: import signals so the post_delete handler is
        # registered. The import has to live in ready() (not at the
        # top of the module) so Django apps are fully loaded before
        # the model class is referenced.
        from . import signals  # noqa: F401
