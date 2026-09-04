"""EchoFlow application-level metrics.

This module registers the 5 custom histograms + 1 counter the design
doc (`docs/EXPLAIN/observability/03-prometheus-grafana-design.md`)
specifies. They sit ON TOP of django-prometheus's auto-collected
view/request metrics, and capture application-level operations the
framework can't observe directly: a single refill, a single ranking,
a single toggle-like.

CARDINALITY DISCIPLINE
----------------------
Labels are bounded to small enums. NEVER add `user_id`, `clip_id`, or
any other high-cardinality field as a label. See the DECISION
comment below.

How to use:
  from backend.app import metrics

  with metrics.feed_refill_duration_seconds.labels(
      source='pool', outcome='success'
  ).time():
      # ... do the refill ...
      pass

Or as a decorator:
  @metrics.feed_refill_duration_seconds.labels(source='pool').time()
  def refill_user_feed(...):
      ...

The histograms are registered with the prometheus_client default
registry, which django_prometheus exports at /metrics/. No further
wiring is needed.

When a new metric is needed, add it here with a DECISION comment
explaining the cardinality budget and the alert implications.
"""
from __future__ import annotations

import os

from prometheus_client import Counter, Histogram


# DECISION: Label cardinality is bounded to small enums. Each label
# multiplies the number of time series by its cardinality. With
# these bounds the entire metrics module produces <100 series,
# which is safe to scrape every 15s and store for 7 days.
#
# Banned labels: user_id, clip_id, request_id, correlation_id,
#                any UUID, any timestamp.
#
# Allowed labels: source, outcome, op, result, category, queue.
# Each label is an enum of <= 5 values.


# ---------------------------------------------------------------------------
# Histogram 1: refill_user_feed execution time
# ---------------------------------------------------------------------------

FEED_REFILL_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

feed_refill_duration_seconds = Histogram(
    'echoflow_feed_refill_duration_seconds',
    'Duration of refill_user_feed (pool + SQL fallback).',
    labelnames=('source', 'outcome'),
    buckets=FEED_REFILL_BUCKETS,
)
#   source: pool (Redis ZSET hit) | sql (composite-distance fallback) | cold (no vector)
#   outcome: success | empty | error


# ---------------------------------------------------------------------------
# Histogram 2: /suggestions/ ranking query time
# ---------------------------------------------------------------------------

SUGGESTION_BUCKETS = (0.005, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)

suggestion_ranking_duration_seconds = Histogram(
    'echoflow_suggestion_ranking_duration_seconds',
    'Duration of /suggestions/ query (vector + composite rerank).',
    labelnames=('category', 'outcome'),
    buckets=SUGGESTION_BUCKETS,
)
#   category: the user-supplied ?category=X value, or 'all' if unset
#             (DECISION: free-form category strings are bounded by
#             the catalog's category enum, typically <= 20)
#   outcome: success | fallback (engagement_velocity) | error


# ---------------------------------------------------------------------------
# Histogram 3: toggle_like (F() counter hot path)
# ---------------------------------------------------------------------------

TOGGLE_LIKE_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5)

toggle_like_duration_seconds = Histogram(
    'echoflow_toggle_like_duration_seconds',
    'Duration of /interactions/{id}/toggle-like/. '
    'Critical: this is the F() counter hot path.',
    labelnames=('outcome',),
    buckets=TOGGLE_LIKE_BUCKETS,
)
#   outcome: success | race_lost (F() under contention) | error


# ---------------------------------------------------------------------------
# Histogram 4: cache get/set hit rate
# ---------------------------------------------------------------------------

CACHE_BUCKETS = (0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5)

# DECISION: Only the `op` label. We removed the planned `result` label
# (hit/miss) because the prometheus_client `.labels()` API requires
# ALL labels to be set at call time, but result (hit/miss/error) is
# only known after the operation completes. The hit rate is derivable
# from the underlying op rate if we add a separate `cache_get_total`
# counter later; for now, the duration distribution is the primary
# signal and the hit/miss ratio is observable in Redis stats directly.
cache_get_set_duration_seconds = Histogram(
    'echoflow_cache_get_set_duration_seconds',
    'Duration of Redis cache get/set operations.',
    labelnames=('op',),
    buckets=CACHE_BUCKETS,
)
#   op: get | set


# ---------------------------------------------------------------------------
# Histogram 5: HLS processing end-to-end
# ---------------------------------------------------------------------------

HLS_BUCKETS = (1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0)

hls_processing_duration_seconds = Histogram(
    'echoflow_hls_processing_duration_seconds',
    'End-to-end duration of process_audio_to_hls.',
    labelnames=('outcome',),
    buckets=HLS_BUCKETS,
)
#   outcome: success | transient_error (will retry) | terminal_error (won't retry)


# ---------------------------------------------------------------------------
# Counter 6: Celery tasks processed
# ---------------------------------------------------------------------------

# DECISION: counter (not histogram) because we want a rate, not a
# distribution. Labels are bounded to the queue names from
# CELERY_TASK_ROUTES — 3 values today (default, fast_feed,
# heavy_media), bounded at <= 5 even if a fourth is added.

celery_tasks_processed_total = Counter(
    'echoflow_celery_tasks_processed_total',
    'Total Celery tasks processed, by queue and outcome.',
    labelnames=('queue', 'task', 'outcome'),
)
#   queue: default | fast_feed | heavy_media
#   task: the task name (e.g. 'process_audio_to_hls'). Cardinality
#         is bounded by the number of registered tasks; we accept
#         this because the alternative — collapsing task to a
#         constant — would make the metric useless for diagnosing
#         specific slow tasks.
#   outcome: success | retry | failure


orphan_hls_cleaned_total = Counter(
    'echoflow_orphan_hls_cleaned_total',
    'HLS prefixes deleted by the cleanup_orphan_hls Celery task. '
    'A non-zero value indicates post_delete signal failures, '
    'DBA-force-deletes, or pipeline interruptions.',
)
# No labels: the task is single-purpose; the only meaningful signal
# is the running count. A non-zero delta is what an operator alerts on.


# ---------------------------------------------------------------------------
# Convenience decorators
# ---------------------------------------------------------------------------

def time_feed_refill(source: str):
    """Context manager / decorator factory for refill_user_feed.

    Usage:
        with time_feed_refill('pool') as timer:
            ... do refill ...
        timer.set_outcome('success')   # or 'empty' / 'error'
    """
    return _TimerAdapter(feed_refill_duration_seconds, source=source)


def time_suggestion_ranking(category: str):
    return _TimerAdapter(
        suggestion_ranking_duration_seconds, category=category
    )


def time_toggle_like():
    return _TimerAdapter(toggle_like_duration_seconds)


def time_cache(op: str):
    return _TimerAdapter(cache_get_set_duration_seconds, op=op)


def time_hls_processing():
    return _TimerAdapter(hls_processing_duration_seconds)


class _TimerAdapter:
    """Tiny helper that lets you set the outcome label after the work
    completes. Most Prometheus histogram helpers require the labels
    to be known up-front; this adapter defers the outcome.

    If the target histogram does NOT declare an 'outcome' label, the
    adapter silently drops the outcome (so we can use one helper for
    multiple histograms with different label sets).
    """

    def __init__(self, histogram: Histogram, **labels):
        self._histogram = histogram
        self._labels = dict(labels)
        self._has_outcome = 'outcome' in histogram._labelnames
        self._outcome = 'success'
        self._start = None

    def __enter__(self):
        self._start = __import__('time').monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._outcome = 'error'
        duration = max(0.0, __import__('time').monotonic() - self._start)
        labels = dict(self._labels)
        if self._has_outcome:
            labels['outcome'] = self._outcome
        self._histogram.labels(**labels).observe(duration)
        return False  # do not suppress exceptions

    def set_outcome(self, outcome: str) -> None:
        """Call before __exit__ to override the success default.
        No-op if the histogram doesn't have an outcome label."""
        if self._has_outcome:
            self._outcome = outcome


# ---------------------------------------------------------------------------
# Run-mode gate
# ---------------------------------------------------------------------------
#
# In tests, the prometheus_client default registry accumulates state
# across tests. This is fine for production, but in unit tests it
# causes cross-test contamination. The pytest fixture in
# tests/test_metrics.py resets the relevant metric values at the
# start of each test.
#
# There is no global "disable metrics" switch — Prometheus clients
# don't support it. The recommended pattern is to wrap metric
# observations in code that runs only outside the test environment,
# or to use a single shared registry and accept the contamination.
# We accept it because the metrics are observational, not
# correctness-bearing.
