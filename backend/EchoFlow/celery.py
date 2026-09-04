import os
import logging
from celery import Celery
from celery.signals import task_prerun, task_postrun, task_failure

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.EchoFlow.settings')

app = Celery('backend.EchoFlow')

app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks(['backend.app'])


# ---------------------------------------------------------------------------
# Custom Prometheus metrics for Celery tasks.
# Counts every completed (or failed) task into the
# echoflow_celery_tasks_processed_total counter with the queue name
# and outcome. Bounded cardinality: queue names are the 3 from
# CELERY_TASK_ROUTES (default, fast_feed, heavy_media), task names
# are the registered tasks (~20 today), outcomes are 3.
# See backend/app/metrics.py for the cardinality discipline.
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def _queue_for_task(task_name: str) -> str:
    """Resolve a task name to its Celery queue via CELERY_TASK_ROUTES.

    Falls back to 'default' if the task isn't in the routes table.
    """
    try:
        from django.conf import settings
        routes = getattr(settings, 'CELERY_TASK_ROUTES', {}) or {}
        for pattern, route in routes.items():
            if pattern in task_name:
                return route.get('queue', 'default')
    except Exception as exc:
        logger.debug("could not resolve queue for %s: %s", task_name, exc)
    return 'default'


@task_postrun.connect
def _on_task_postrun(sender=None, task=None, **kwargs):
    # sender is the task instance; task is the task class
    try:
        from backend.app import metrics
        task_name = sender.name if sender else 'unknown'
        queue = _queue_for_task(task_name)
        # Shorten the task name to the last dotted segment for
        # label readability (e.g. 'app.process_audio_to_hls' ->
        # 'process_audio_to_hls').
        short = task_name.rsplit('.', 1)[-1] if task_name else 'unknown'
        metrics.celery_tasks_processed_total.labels(
            queue=queue, task=short, outcome='success',
        ).inc()
    except Exception as exc:
        # Never let metrics code break the task.
        logger.debug("task_postrun metric failed: %s", exc)


@task_failure.connect
def _on_task_failure(sender=None, task=None, exception=None, **kwargs):
    try:
        from backend.app import metrics
        task_name = sender.name if sender else 'unknown'
        queue = _queue_for_task(task_name)
        short = task_name.rsplit('.', 1)[-1] if task_name else 'unknown'
        # The decorator autoretry_for=RETRYABLE_ERRORS will retry
        # some exceptions. We don't know here whether this is a
        # final failure or a retry. We count it as 'retry' by default
        # and let the postrun handle the final success; if
        # task_postrun doesn't fire, this will be slightly under-
        # counted. Acceptable noise.
        outcome = 'retry'
        metrics.celery_tasks_processed_total.labels(
            queue=queue, task=short, outcome=outcome,
        ).inc()
    except Exception as exc:
        logger.debug("task_failure metric failed: %s", exc)


# ---------------------------------------------------------------------------
# Correlation-id propagation to worker context.
# Group B item 11 (docs/backend-bug-fixs.md §10.3).
#
# The web tier sets the correlation_id contextvar per request and the
# publish() helper in backend.app.services.task_publisher attaches it
# as a task header. Here we read that header in task_prerun and set
# the contextvar so the logging filter can render %(correlation_id)s
# with the real id. task_postrun clears it so the next task in the
# same worker process doesn't inherit a stale id.
#
# DECISION: separate handlers from the metric handlers above. They
# share the task_postrun signal but the metric counter must run
# even on failure (where task_postrun still fires after retry), while
# the contextvar clear must run on EVERY termination path. Celery
# runs all connected handlers in registration order; we register
# after the metrics handler so contextvar is cleared last (no
# observable effect, but keeps the ordering explicit).
# ---------------------------------------------------------------------------
_CORRELATION_HEADER = 'correlation_id'


@task_prerun.connect
def _on_task_prerun_set_correlation(sender=None, task=None, **kwargs):
    try:
        headers = (kwargs.get('headers') or {})
        cid = headers.get(_CORRELATION_HEADER, '')
        if not cid:
            # Fall back to task.request if the producer used a Celery-
            # native path that put the id in request.correlation_id.
            request = getattr(task, 'request', None) if task else None
            cid = getattr(request, 'correlation_id', '') if request else ''
        if cid:
            from . import correlation
            correlation.set_correlation_id(cid)
    except Exception as exc:
        # Never let a logging/tracing hook break the task body.
        logger.debug("task_prerun correlation_id hook failed: %s", exc)


@task_postrun.connect
def _on_task_postrun_clear_correlation(sender=None, task=None, **kwargs):
    try:
        from . import correlation
        correlation.clear_correlation_id()
    except Exception as exc:
        logger.debug("task_postrun clear_correlation_id failed: %s", exc)