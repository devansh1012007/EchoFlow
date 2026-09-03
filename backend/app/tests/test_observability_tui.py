"""Tests for the observability TUI.

The TUI is a stdlib-only script. We test the parser and the
quantile-estimation logic against synthetic Prometheus text.
End-to-end testing (against a real /metrics/ endpoint) is out of
scope for this fixture.

Companion: scripts/observability_tui.py
"""
import sys
from pathlib import Path

# Add scripts/ to sys.path so we can import the TUI module
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS = _PROJECT_ROOT / 'scripts'
sys.path.insert(0, str(_SCRIPTS))

from observability_tui import (  # noqa: E402
    parse_prometheus,
    estimate_quantile,
    series_count,
    series_sum,
    render,
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParsePrometheus:
    def test_parses_simple_counter(self):
        s = 'foo_total 42.0'
        series = parse_prometheus(s)
        assert len(series) == 1
        assert series[0].name == 'foo_total'
        assert series[0].value == 42.0
        assert series[0].labels == {}

    def test_parses_labeled_series(self):
        s = 'foo_total{a="1",b="two"} 7.5'
        series = parse_prometheus(s)
        assert series[0].labels == {'a': '1', 'b': 'two'}
        assert series[0].value == 7.5

    def test_parses_with_timestamp(self):
        s = 'foo_total 42.0 1234567890123'
        series = parse_prometheus(s)
        assert series[0].timestamp == 1234567890123.0

    def test_skips_comments(self):
        s = '# HELP foo description\n# TYPE foo counter\nfoo 1.0'
        series = parse_prometheus(s)
        assert len(series) == 1
        assert series[0].name == 'foo'

    def test_handles_escaped_quotes_in_labels(self):
        s = r'foo{a="he said \"hi\""} 1.0'
        series = parse_prometheus(s)
        assert series[0].labels['a'] == 'he said "hi"'

    def test_ignores_malformed_lines(self):
        s = 'good 1.0\nbad line with no value\ngood2 2.0'
        series = parse_prometheus(s)
        assert len(series) == 2


# ---------------------------------------------------------------------------
# Histogram quantile estimation
# ---------------------------------------------------------------------------


class TestEstimateQuantile:
    SAMPLE = '\n'.join([
        'echoflow_feed_refill_duration_seconds_bucket{source="pool",outcome="success",le="0.01"} 5',
        'echoflow_feed_refill_duration_seconds_bucket{source="pool",outcome="success",le="0.05"} 20',
        'echoflow_feed_refill_duration_seconds_bucket{source="pool",outcome="success",le="0.1"} 25',
        'echoflow_feed_refill_duration_seconds_bucket{source="pool",outcome="success",le="+Inf"} 30',
        'echoflow_feed_refill_duration_seconds_sum{source="pool",outcome="success"} 0.567',
        'echoflow_feed_refill_duration_seconds_count{source="pool",outcome="success"} 30',
    ])

    def test_estimate_p50(self):
        series = parse_prometheus(self.SAMPLE)
        lf = {'source': 'pool', 'outcome': 'success'}
        # 30 observations. p50 = 15th. The 5-bucket cumulative reaches
        # 20 at le=0.05, so p50 is between 0.01 (5) and 0.05 (20).
        # Linear interpolation: (15 - 5) / (20 - 5) = 0.667 of the
        # way from 0.01 to 0.05 = 0.0367.
        p50 = estimate_quantile(series, 'echoflow_feed_refill_duration_seconds', 0.5, lf)
        assert p50 is not None
        assert 0.03 < p50 < 0.04

    def test_estimate_p95(self):
        series = parse_prometheus(self.SAMPLE)
        lf = {'source': 'pool', 'outcome': 'success'}
        # p95 = 28.5th. The bucket at le=0.1 has 25; +Inf has 30.
        # p95 is in the last bucket (past 0.1).
        p95 = estimate_quantile(series, 'echoflow_feed_refill_duration_seconds', 0.95, lf)
        assert p95 is not None
        assert 0.1 <= p95 <= 0.11  # clamped to last finite le

    def test_returns_none_for_missing_label(self):
        series = parse_prometheus(self.SAMPLE)
        # Different label values → no series matches
        p50 = estimate_quantile(
            series, 'echoflow_feed_refill_duration_seconds', 0.5,
            {'source': 'pool', 'outcome': 'error'},
        )
        assert p50 is None

    def test_series_count_and_sum(self):
        series = parse_prometheus(self.SAMPLE)
        lf = {'source': 'pool', 'outcome': 'success'}
        assert series_count(series, 'echoflow_feed_refill_duration_seconds', lf) == 30
        assert series_sum(series, 'echoflow_feed_refill_duration_seconds', lf) == 0.567


# ---------------------------------------------------------------------------
# Render — smoke test that the dashboard string contains expected sections
# ---------------------------------------------------------------------------


class TestRender:
    SAMPLE = '\n'.join([
        '# TYPE echoflow_feed_refill_duration_seconds histogram',
        'echoflow_feed_refill_duration_seconds_bucket{source="pool",outcome="success",le="0.05"} 20',
        'echoflow_feed_refill_duration_seconds_bucket{source="pool",outcome="success",le="+Inf"} 30',
        'echoflow_feed_refill_duration_seconds_sum{source="pool",outcome="success"} 0.567',
        'echoflow_feed_refill_duration_seconds_count{source="pool",outcome="success"} 30',
        '# TYPE echoflow_celery_tasks_processed_total counter',
        'echoflow_celery_tasks_processed_total{queue="fast_feed",task="refill_user_feed",outcome="success"} 1500',
    ])

    def test_render_contains_all_six_sections(self):
        series = parse_prometheus(self.SAMPLE)
        out = render(series, 'http://localhost:8005/metrics/', 1234567890.0)
        # Each of the 6 metrics must appear as a section header
        for tag in ('[1] feed_refill', '[2] suggestion', '[3] toggle_like',
                    '[4] cache', '[5] hls', '[6] celery'):
            assert tag in out, f"missing section: {tag}"

    def test_render_handles_empty_payload(self):
        # No echoflow_* metrics at all — every section should
        # collapse gracefully (no crashes, no missing data)
        out = render([], 'http://localhost:8005/metrics/', 1234567890.0)
        assert '[1] feed_refill' in out
        assert '(no samples yet)' in out  # at least the celery section shows this
        assert 'Series: 0' in out
