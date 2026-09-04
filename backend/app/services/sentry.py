"""Sentry capture helper.

Wraps sentry_sdk.capture_exception with EchoFlow-specific context
(correlation_id from the request-scoped contextvar). Every service-layer
catch block should call capture_exception(exc, **context) instead of
sentry_sdk.capture_exception directly so the correlation_id tag is
attached automatically.

DECISION: correlation_id is attached as a TAG (not PII). Tags are queryable
in the Sentry UI and survive the send_default_pii=False filter in
EchoFlow/sentry.py:init_sentry. Debugging per-request becomes
"search Sentry for tag=correlation_id abc-123" — one hop from a JSON log
line to the Sentry event.
"""
import sentry_sdk

from backend.EchoFlow import correlation


def capture_exception(exc=None, **context):
    """Capture an exception in Sentry with the current request's
    correlation_id and any caller-supplied context.

    No-op if sentry_sdk has not been initialized (e.g. dev mode or
    unconfigured env). Does not raise on SDK errors — failures to
    report to Sentry must never break the calling code path.
    """
    cid = correlation.get_correlation_id()
    try:
        with sentry_sdk.push_scope() as scope:
            if cid:
                scope.set_tag('correlation_id', cid)
            for key, value in context.items():
                scope.set_extra(key, value)
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass
