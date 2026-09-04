"""Sentry initialization for EchoFlow.

Runs once per process (web, celery, celery_feed, celery_media, celery_beat)
when the Django app config is ready. Gated on DJANGO_DEBUG=False and
SENTRY_DSN being set, so dev, tests, and unconfigured environments never
pay the SDK init cost (network handshakes, regex compilation).

DECISION: init in apps.ready(), NOT settings.py. settings.py is imported
by every management command, every test, every celery worker — running
sentry_sdk.init() there would block test collection and add 50ms to every
test invocation. apps.ready() runs once per process after Django is fully
loaded, which is the canonical pattern from the sentry-sdk docs.
"""
import logging
import os

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    # DECISION: gate is in init_sentry itself (not just in apps.ready())
    # so direct callers and tests get the same no-op behavior. Both
    # conditions must hold: SENTRY_DSN must be set AND DJANGO_DEBUG must
    # be False. Dev/test environments never pay the SDK cost.
    dsn = os.environ.get('SENTRY_DSN')
    debug = os.environ.get('DJANGO_DEBUG', 'False').lower() == 'true'
    if not dsn or debug:
        return
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get('SENTRY_ENV', 'production'),
        release=os.environ.get('GIT_COMMIT_SHA', 'unknown'),
        traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.1')),
        profiles_sample_rate=float(os.environ.get('SENTRY_PROFILES_SAMPLE_RATE', '0.05')),
        integrations=[DjangoIntegration(), CeleryIntegration()],
        # SECURITY: do not forward IPs, cookies, auth headers to Sentry
        # by default. correlation_id is set explicitly as a tag in
        # backend/app/services/sentry.py so it survives the PII filter.
        send_default_pii=False,
    )
    logger.info("sentry initialized: env=%s", os.environ.get('SENTRY_ENV', 'production'))
