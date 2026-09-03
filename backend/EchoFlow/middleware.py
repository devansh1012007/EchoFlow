"""Correlation-ID middleware.

Reads/generates an X-Request-ID header for every HTTP request, attaches
it to the request, and echoes it in the response. A logging filter
(see settings.LOGGING) injects the same id into every log line
emitted during the request so a worker crash can be traced back to
the originating request.

Why: when Celery's process_audio_to_hls fails for clip UUID X in
production, the worker log line currently has no link to the
originating HTTP request. Without a correlation id, debugging
requires grep + guesswork across the request_id-less JSON logs.

Companion: backend.EchoFlow.correlation module (contextvar store).
"""
import uuid

from .correlation import set_correlation_id, clear_correlation_id


class CorrelationIdMiddleware:
    HEADER = 'HTTP_X_REQUEST_ID'
    RESPONSE_HEADER = 'X-Request-ID'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get(self.HEADER) or uuid.uuid4().hex
        # Bound to the request so views can read it.
        request.correlation_id = request_id
        # Make it available to the logging filter via contextvars.
        set_correlation_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            clear_correlation_id()
        response[self.RESPONSE_HEADER] = request_id
        return response
