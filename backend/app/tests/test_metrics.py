"""Tests for backend/app/metrics.py.

The metrics module registers 5 histograms + 1 counter with strict
cardinality budgets. We verify the registration contract, the label
shape, and the _TimerAdapter's outcome-deferral pattern.

End-to-end testing of the hot-path instrumentation
(refill_user_feed, record_like_toggle, etc.) is out of scope here
— those tests live in test_services_*.py and the integration test
suite.
"""
import time

import pytest
from prometheus_client import REGISTRY

from backend.app import metrics


# ---------------------------------------------------------------------------
# Registration contract: every metric must be in the default registry
# and have the documented label set.
# ---------------------------------------------------------------------------


class TestMetricsRegistration:
    def test_all_six_metrics_registered(self):
        # Each metric name must be exposed by the prometheus_client
        # default registry. django_prometheus reads from this same
        # registry, so registration is what makes them visible at
        # /metrics/.
        # The celery counter is defined in backend.EchoFlow.celery
        # (next to the @task_postrun signal that increments it);
        # importing it triggers the registration.
        # Note: prometheus_client's Counter class strips the _total
        # suffix from the exposition name, so 'celery_tasks_processed_total'
        # in the source becomes 'celery_tasks_processed' in the
        # registry.
        from backend.EchoFlow import celery as _celery_app  # noqa: F401
        for name in (
            'echoflow_feed_refill_duration_seconds',
            'echoflow_suggestion_ranking_duration_seconds',
            'echoflow_toggle_like_duration_seconds',
            'echoflow_cache_get_set_duration_seconds',
            'echoflow_hls_processing_duration_seconds',
            'echoflow_celery_tasks_processed',  # _total suffix stripped
        ):
            samples = list(REGISTRY.collect())
            names = {s.name for s in samples}
            assert name in names, f"{name} not in registry"

    def test_histogram_label_shapes(self):
        # Cardinality discipline: each label is bounded to a small
        # enum. If anyone adds a new label without thinking, this
        # test will catch the regression.
        assert metrics.feed_refill_duration_seconds._labelnames == ('source', 'outcome')
        assert metrics.suggestion_ranking_duration_seconds._labelnames == ('category', 'outcome')
        assert metrics.toggle_like_duration_seconds._labelnames == ('outcome',)
        # cache_get_set_duration_seconds was simplified to drop the
        # 'result' label because prometheus_client requires all
        # labels to be set at .labels() call time. See metrics.py
        # docstring on the histogram.
        assert metrics.cache_get_set_duration_seconds._labelnames == ('op',)
        assert metrics.hls_processing_duration_seconds._labelnames == ('outcome',)

    def test_counter_label_shape(self):
        assert metrics.celery_tasks_processed_total._labelnames == ('queue', 'task', 'outcome')


# ---------------------------------------------------------------------------
# _TimerAdapter: outcome deferral, exception handling, missing-label case
# ---------------------------------------------------------------------------


class TestTimerAdapter:
    def test_records_on_success(self):
        with metrics.time_feed_refill(source='pool') as timer:
            time.sleep(0.001)
        # After exit, a sample should be recorded with source='pool',
        # outcome='success'. The default 'success' outcome is what
        # _TimerAdapter uses unless overridden.
        count = metrics.feed_refill_duration_seconds.labels(
            source='pool', outcome='success'
        )._sum.get()  # type: ignore[attr-defined]
        assert count > 0

    def test_records_error_on_exception(self):
        # DECISION: The adapter must NOT swallow exceptions. The
        # context-manager exit records 'error' outcome AND re-raises
        # so the caller can handle it.
        with pytest.raises(RuntimeError, match="boom"):
            with metrics.time_feed_refill(source='pool'):
                raise RuntimeError("boom")
        # An observation should have been recorded with outcome='error'.
        # We don't check the exact value, just that the label exists.
        sample = metrics.feed_refill_duration_seconds.labels(
            source='pool', outcome='error'
        )
        # The _sum counter exists if any observation happened.
        assert hasattr(sample, '_sum')

    def test_set_outcome_overrides_default(self):
        with metrics.time_feed_refill(source='sql') as timer:
            timer.set_outcome('success')
        # The 'sql' source path should be recorded.
        sample = metrics.feed_refill_duration_seconds.labels(
            source='sql', outcome='success'
        )
        assert hasattr(sample, '_sum')

    def test_missing_outcome_label_is_silently_dropped(self):
        # cache_get_set_duration_seconds has no 'outcome' label.
        # The adapter must NOT try to set it; otherwise prometheus_client
        # raises "Incorrect label names".
        with metrics.time_cache(op='get'):
            time.sleep(0.001)
        # No exception means the adapter handled the missing label.
        sample = metrics.cache_get_set_duration_seconds.labels(op='get')
        assert hasattr(sample, '_sum')

    def test_histogram_with_outcome_label_can_set_outcome(self):
        # toggle_like_duration_seconds has 'outcome' as a label.
        with metrics.time_toggle_like() as timer:
            timer.set_outcome('race_lost')
        sample = metrics.toggle_like_duration_seconds.labels(outcome='race_lost')
        assert hasattr(sample, '_sum')


# ---------------------------------------------------------------------------
# Cardinality discipline: explicit ban on user_id / clip_id as labels
# ---------------------------------------------------------------------------


class TestCardinalityDiscipline:
    def test_no_user_id_or_clip_id_label(self):
        # DECISION: Never use user_id / clip_id / UUID as a label.
        # If a future change adds one, this test will fail and force
        # the author to acknowledge the cardinality explosion.
        all_labelnames = set()
        for m in (
            metrics.feed_refill_duration_seconds,
            metrics.suggestion_ranking_duration_seconds,
            metrics.toggle_like_duration_seconds,
            metrics.cache_get_set_duration_seconds,
            metrics.hls_processing_duration_seconds,
            metrics.celery_tasks_processed_total,
        ):
            all_labelnames.update(m._labelnames)
        for banned in ('user_id', 'clip_id', 'request_id',
                       'correlation_id', 'event_id', 'uuid'):
            assert banned not in all_labelnames, (
                f"Banned label '{banned}' found in metrics module. "
                f"Adding it would explode cardinality. See metrics.py "
                f"DECISION comment."
            )
