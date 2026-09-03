"""Logging filter that injects correlation_id into every log record.

Used by LOGGING in settings.py. Reads the current correlation_id from
the contextvars store (set by CorrelationIdMiddleware) and attaches it
to every log record emitted during the request scope.
"""
import logging

from .correlation import get_correlation_id


class CorrelationIdFilter(logging.Filter):
    def filter(self, record):
        record.correlation_id = get_correlation_id() or '-'
        return True
