"""Per-request correlation_id storage.

A tiny thread/greenlet-safe key-value store for the request correlation
id. Used by CorrelationIdMiddleware to publish the id, and by the
logging filter (LOGGING in settings.py) to inject it into every log
record emitted during the request.

Uses contextvars (Python 3.7+) so the id is correctly scoped per
request even under async or gunicorn sync workers, where thread-local
storage would leak between requests handled by the same thread.
"""
import contextvars

_correlation_id_var = contextvars.ContextVar('correlation_id', default='')


def get_correlation_id() -> str:
    return _correlation_id_var.get()


def set_correlation_id(value: str) -> None:
    _correlation_id_var.set(value)


def clear_correlation_id() -> None:
    _correlation_id_var.set('')
