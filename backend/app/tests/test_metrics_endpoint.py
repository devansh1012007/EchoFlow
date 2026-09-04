"""Tests for the /metrics/ Prometheus endpoint (A8).

Verifies the 6 custom EchoFlow application metrics are exposed at
/metrics/ in text exposition format (the format Prometheus scrapers
consume). The django_prometheus ExportToDjangoView handles the
request and renders both the auto-collected django_* metrics AND the
custom echoflow_* metrics registered in backend/app/metrics.py.

Single test: one endpoint contract — every documented metric name
appears in the text body. Regression guard against someone removing
the metric definitions from metrics.py or removing django_prometheus
from INSTALLED_APPS.

DECISION: prometheus_client only emits a metric family at /metrics/
once it has at least one observation. The histograms are registered
at import time but never observed in unit tests (they're observed in
the hot paths — refill_user_feed, record_like_toggle, etc.). We
observe each metric once at the top of the test so the exposition
includes them; otherwise the test would pass/fail based on whether
another test in the suite had already observed that metric.
"""
import pytest

from django.test import Client

from backend.app import metrics


pytestmark = pytest.mark.django_db


def _prime_metrics():
    """Touch each metric once so it appears in the /metrics/ exposition.

    The counter is incremented; the histograms are observed with a
    negligible duration. Without this priming, prometheus_client's
    text exposition omits metric families that have zero samples.
    """
    metrics.feed_refill_duration_seconds.labels(
        source='pool', outcome='success'
    ).observe(0.001)
    metrics.suggestion_ranking_duration_seconds.labels(
        category='all', outcome='success'
    ).observe(0.001)
    metrics.toggle_like_duration_seconds.labels(outcome='success').observe(0.001)
    metrics.cache_get_set_duration_seconds.labels(op='get').observe(0.001)
    metrics.hls_processing_duration_seconds.labels(outcome='success').observe(1.0)
    # The celery counter is incremented from backend/EchoFlow/celery.py
    # task signals; the metric name in the exposition strips the _total
    # suffix (prometheus_client convention).
    metrics.celery_tasks_processed_total.labels(
        queue='default', task='noop', outcome='success'
    ).inc()


class TestMetricsEndpoint:
    def test_all_six_custom_metrics_exposed(self):
        _prime_metrics()
        client = Client()
        response = client.get('/metrics/')
        assert response.status_code == 200
        body = response.content.decode('utf-8')
        for name in (
            'echoflow_feed_refill_duration_seconds',
            'echoflow_suggestion_ranking_duration_seconds',
            'echoflow_toggle_like_duration_seconds',
            'echoflow_cache_get_set_duration_seconds',
            'echoflow_hls_processing_duration_seconds',
            # The Counter is registered with name
            # `echoflow_celery_tasks_processed_total` but the
            # prometheus_client exposition strips the `_total`
            # suffix when rendering — see test_metrics.py:36-38
            # for the same observation in the registry test.
            'echoflow_celery_tasks_processed',
        ):
            assert name in body, (
                f"{name} missing from /metrics/ response. "
                f"Has the metric been removed from backend/app/metrics.py?"
            )
