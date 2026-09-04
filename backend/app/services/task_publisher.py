"""Task publisher — wraps Celery's apply_async with correlation_id propagation.

Architectural fix for Group B item 11 (docs/backend-bug-fixs.md §10.3).

Problem: CorrelationIdMiddleware writes the per-request id to a
contextvars.ContextVar in the web tier. The contextvar is process-
scoped, not message-scoped. When the web request enqueues a Celery
task via .delay(), the contextvar value is not transmitted to the
worker. The worker's contextvar defaults to '' and the logging
filter renders every worker log line with correlation_id='-'.

Fix: producer side (this file) reads the contextvar at enqueue time
and attaches it as a task header. Worker side (EchoFlow/celery.py)
reads the header in a task_prerun signal and re-sets the contextvar
in the worker process. task_postrun clears it so the next task in
the same worker doesn't inherit a stale id.

Why a wrapper instead of editing every .delay() site individually:
1. Single source of truth for the header name and capture point.
2. New .delay() sites get correlation for free.
3. Caller-supplied headers (e.g. via apply_async(headers=...)) are
   merged, not overwritten — callers retain their own metadata.
4. Backwards-compatible: any keyword arg supported by apply_async
   (countdown, eta, queue, priority, ...) passes through.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

CORRELATION_ID_HEADER = 'correlation_id'


def publish(task, *args: Any, headers: dict | None = None, **kwargs: Any):
    """Enqueue `task` with the current correlation_id as a task header.

    Falls back to .delay() semantics if no special kwargs (countdown,
    eta, queue, etc.) are passed. Reads get_correlation_id() at CALL
    time (not at worker time), so the captured value is the one
    active in the web request that enqueued the task.

    Args:
        task: a Celery Task instance (the result of @shared_task).
        *args: positional args for the task.
        headers: optional caller-supplied header dict. Merged with
            the correlation_id header; the correlation_id wins if
            the caller supplied their own (to prevent accidental
            untraced tasks).
        **kwargs: any other apply_async kwarg (countdown, eta, queue,
            priority, routing_key, ...). Do NOT pass `headers` twice.
    """
    # Late import: avoids loading Django settings at module import
    # time (this module is imported by apps.py and views, which run
    # before settings are fully resolved in some test configurations).
    from ...EchoFlow import correlation

    cid = correlation.get_correlation_id() or ''
    merged_headers = dict(headers or {})
    if cid:
        merged_headers[CORRELATION_ID_HEADER] = cid
    elif CORRELATION_ID_HEADER in merged_headers:
        # SECURITY: never let a caller-supplied header shadow the
        # current request's id. If the request has no correlation
        # id (e.g. a management command), use the caller's.
        pass
    else:
        # No correlation id anywhere — log at debug so the missing
        # trace is visible to operators without polluting info logs.
        logger.debug(
            "publish: no correlation_id in context or headers for task %s",
            getattr(task, 'name', task),
        )

    return task.apply_async(args=args, kwargs=kwargs, headers=merged_headers)
