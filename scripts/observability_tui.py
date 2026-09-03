#!/usr/bin/env python3
"""EchoFlow observability TUI.

Reads /metrics/ from a running EchoFlow web container and prints a
text-based dashboard of the 6 custom application metrics. Refreshes
every N seconds (default 5). Designed to work against the dev
docker-compose stack but is generic over the URL.

Usage:
  python scripts/observability_tui.py                 # default
  python scripts/observability_tui.py --once          # single refresh, exit
  python scripts/observability_tui.py --url http://localhost:8005/metrics/
  python scripts/observability_tui.py --interval 2    # refresh every 2s

The TUI is a stopgap. Once Grafana is wired (see
docs/EXPLAIN/observability/03-prometheus-grafana-design.md), this
script can be deleted.

Dependencies: only the Python stdlib. urllib, re, time, sys, argparse.
No pip install required.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from typing import Optional


# ---------------------------------------------------------------------------
# Prometheus text-format parser (minimal, just what we need)
# ---------------------------------------------------------------------------


class PrometheusSeries:
    """One labeled time series. {name{labels}} value [timestamp]"""
    __slots__ = ('name', 'labels', 'value', 'timestamp')

    def __init__(self, name, labels, value, timestamp=None):
        self.name = name
        self.labels = labels
        self.value = value
        self.timestamp = timestamp

    def label(self, key: str) -> Optional[str]:
        return self.labels.get(key)


def parse_prometheus(text: str) -> list[PrometheusSeries]:
    """Parse the Prometheus text exposition format.

    We don't need the full spec — just lines like:
        foo_total{a="1",b="2"} 42.0
        foo_total{a="1",b="2"} 42.0 1234567890123
    Lines starting with # are comments and skipped.
    """
    series: list[PrometheusSeries] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        # Split on the LAST whitespace
        match = re.match(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{([^}]*)\})?\s+(\S+)(?:\s+(\S+))?$', line)
        if not match:
            continue
        name = match.group(1)
        label_str = match.group(3) or ''
        value_str = match.group(4)
        ts_str = match.group(5)
        labels: dict[str, str] = {}
        if label_str:
            for kv in re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', label_str):
                k, v = kv
                labels[k] = v.replace('\\"', '"').replace('\\\\', '\\')
        try:
            value = float(value_str)
        except ValueError:
            continue
        ts = float(ts_str) if ts_str else None
        series.append(PrometheusSeries(name, labels, value, ts))
    return series


# ---------------------------------------------------------------------------
# Histogram statistics derived from the cumulative _sum, _count, _bucket
# ---------------------------------------------------------------------------


def estimate_quantile(series: list[PrometheusSeries], name: str, q: float,
                      label_filter: dict[str, str] | None = None) -> Optional[float]:
    """Estimate the q-th quantile (0 <= q <= 1) from a histogram.

    The Prometheus client convention is:
        <name>_bucket{le="X"} cumulative_count
        <name>_sum sum_of_observations
        <name>_count total_observations
    """
    label_filter = label_filter or {}
    buckets: list[tuple[float, float]] = []  # (le, cumulative_count)
    total_count: Optional[float] = None
    for s in series:
        if not s.name.startswith(name + '_bucket'):
            continue
        if not all(s.label(k) == v for k, v in label_filter.items()):
            continue
        le_str = s.label('le')
        if le_str is None or le_str == '+Inf':
            continue
        try:
            le = float(le_str)
        except ValueError:
            continue
        buckets.append((le, s.value))
    if not buckets:
        return None
    # Find total_count (from _count series)
    for s in series:
        if s.name == name + '_count' and all(s.label(k) == v for k, v in label_filter.items()):
            total_count = s.value
            break
    if total_count is None or total_count == 0:
        return None
    target = total_count * q
    buckets.sort(key=lambda b: b[0])
    prev_count = 0.0
    prev_le = 0.0
    for le, cum in buckets:
        if cum >= target:
            # Linear interpolation within this bucket
            if cum == prev_count:
                return le
            frac = (target - prev_count) / (cum - prev_count)
            return prev_le + frac * (le - prev_le)
        prev_count = cum
        prev_le = le
    # Past the last bucket — return the largest le
    return buckets[-1][0]


def series_count(series: list[PrometheusSeries], name: str,
                 label_filter: dict[str, str] | None = None) -> Optional[float]:
    label_filter = label_filter or {}
    for s in series:
        if s.name == name + '_count' and all(s.label(k) == v for k, v in label_filter.items()):
            return s.value
    return None


def series_sum(series: list[PrometheusSeries], name: str,
               label_filter: dict[str, str] | None = None) -> Optional[float]:
    label_filter = label_filter or {}
    for s in series:
        if s.name == name + '_sum' and all(s.label(k) == v for k, v in label_filter.items()):
            return s.value
    return None


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return '   -   '
    if seconds < 0.001:
        return f'{seconds*1_000_000:6.0f}µs'
    if seconds < 1.0:
        return f'{seconds*1000:6.1f}ms'
    return f'{seconds:6.2f}s '


def fmt_count(value: Optional[float]) -> str:
    if value is None:
        return '   -   '
    if value >= 1_000_000:
        return f'{value/1_000_000:6.1f}M '
    if value >= 1_000:
        return f'{value/1_000:6.1f}k '
    return f'{value:6.0f} '


def clear_screen() -> None:
    sys.stdout.write('\033[2J\033[H')
    sys.stdout.flush()


def render(series: list[PrometheusSeries], url: str, scrape_ts: float) -> str:
    """Render the full dashboard as a single string."""
    lines: list[str] = []
    width = 88
    lines.append('=' * width)
    lines.append(' EchoFlow Observability TUI '.center(width, '='))
    lines.append(f' Source: {url}'.ljust(width))
    lines.append(
        f' Scrape: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(scrape_ts))}'
        f'   Series: {len(series)}'.ljust(width)
    )
    lines.append('=' * width)
    lines.append('')

    # Histogram 1: feed_refill
    lines.append('┌─[1] feed_refill_duration_seconds (refill_user_feed)'.ljust(width) + '┐')
    for source in ('pool', 'sql', 'cold'):
        for outcome in ('success', 'error'):
            lf = {'source': source, 'outcome': outcome}
            count = series_count(series, 'echoflow_feed_refill_duration_seconds', lf)
            if count is None or count == 0:
                continue
            p50 = estimate_quantile(series, 'echoflow_feed_refill_duration_seconds', 0.5, lf)
            p95 = estimate_quantile(series, 'echoflow_feed_refill_duration_seconds', 0.95, lf)
            p99 = estimate_quantile(series, 'echoflow_feed_refill_duration_seconds', 0.99, lf)
            line = (
                f'│  source={source:<5} outcome={outcome:<7} '
                f'count={fmt_count(count)} '
                f'p50={fmt_duration(p50)} p95={fmt_duration(p95)} p99={fmt_duration(p99)}'
            )
            lines.append(line.ljust(width - 1) + '│')
    lines.append('└' + '─' * (width - 2) + '┘')
    lines.append('')

    # Histogram 2: suggestion_ranking — group by category
    lines.append('┌─[2] suggestion_ranking_duration_seconds (/suggestions/)'.ljust(width) + '┐')
    by_category: dict[str, dict[str, float]] = defaultdict(dict)
    for s in series:
        if s.name == 'echoflow_suggestion_ranking_duration_seconds_count':
            cat = s.label('category') or 'all'
            outcome = s.label('outcome') or 'success'
            by_category[cat][outcome] = s.value
    for cat in sorted(by_category.keys())[:6]:  # top 6 categories
        for outcome, count in by_category[cat].items():
            if count == 0:
                continue
            lf = {'category': cat, 'outcome': outcome}
            p50 = estimate_quantile(series, 'echoflow_suggestion_ranking_duration_seconds', 0.5, lf)
            p95 = estimate_quantile(series, 'echoflow_suggestion_ranking_duration_seconds', 0.95, lf)
            line = (
                f'│  category={cat:<14} outcome={outcome:<8} '
                f'count={fmt_count(count)} '
                f'p50={fmt_duration(p50)} p95={fmt_duration(p95)}'
            )
            lines.append(line.ljust(width - 1) + '│')
    if not by_category:
        lines.append('│  (no samples yet)'.ljust(width - 1) + '│')
    lines.append('└' + '─' * (width - 2) + '┘')
    lines.append('')

    # Histogram 3: toggle_like
    lines.append('┌─[3] toggle_like_duration_seconds (F() counter hot path)'.ljust(width) + '┐')
    for outcome in ('success', 'error', 'race_lost'):
        lf = {'outcome': outcome}
        count = series_count(series, 'echoflow_toggle_like_duration_seconds', lf)
        if count is None or count == 0:
            continue
        p50 = estimate_quantile(series, 'echoflow_toggle_like_duration_seconds', 0.5, lf)
        p99 = estimate_quantile(series, 'echoflow_toggle_like_duration_seconds', 0.99, lf)
        line = (
            f'│  outcome={outcome:<10} count={fmt_count(count)} '
            f'p50={fmt_duration(p50)} p99={fmt_duration(p99)}'
        )
        lines.append(line.ljust(width - 1) + '│')
    lines.append('└' + '─' * (width - 2) + '┘')
    lines.append('')

    # Histogram 4: cache_get_set
    lines.append('┌─[4] cache_get_set_duration_seconds (Redis)'.ljust(width) + '┐')
    for op in ('get', 'set'):
        lf = {'op': op}
        count = series_count(series, 'echoflow_cache_get_set_duration_seconds', lf)
        if count is None or count == 0:
            continue
        p50 = estimate_quantile(series, 'echoflow_cache_get_set_duration_seconds', 0.5, lf)
        p99 = estimate_quantile(series, 'echoflow_cache_get_set_duration_seconds', 0.99, lf)
        line = (
            f'│  op={op:<5} count={fmt_count(count)} '
            f'p50={fmt_duration(p50)} p99={fmt_duration(p99)}'
        )
        lines.append(line.ljust(width - 1) + '│')
    lines.append('└' + '─' * (width - 2) + '┘')
    lines.append('')

    # Histogram 5: hls_processing
    lines.append('┌─[5] hls_processing_duration_seconds (process_audio_to_hls)'.ljust(width) + '┐')
    for outcome in ('success', 'terminal_error', 'error'):
        lf = {'outcome': outcome}
        count = series_count(series, 'echoflow_hls_processing_duration_seconds', lf)
        if count is None or count == 0:
            continue
        p50 = estimate_quantile(series, 'echoflow_hls_processing_duration_seconds', 0.5, lf)
        p95 = estimate_quantile(series, 'echoflow_hls_processing_duration_seconds', 0.95, lf)
        line = (
            f'│  outcome={outcome:<14} count={fmt_count(count)} '
            f'p50={fmt_duration(p50)} p95={fmt_duration(p95)}'
        )
        lines.append(line.ljust(width - 1) + '│')
    lines.append('└' + '─' * (width - 2) + '┘')
    lines.append('')

    # Counter 6: celery_tasks_processed — top 8 by count
    lines.append('┌─[6] celery_tasks_processed_total (Celery)'.ljust(width) + '┐')
    task_counts: list[tuple[str, str, str, float]] = []
    for s in series:
        if s.name != 'echoflow_celery_tasks_processed_total':
            continue
        queue = s.label('queue') or '?'
        task = s.label('task') or '?'
        outcome = s.label('outcome') or '?'
        task_counts.append((queue, task, outcome, s.value))
    task_counts.sort(key=lambda x: -x[3])
    for queue, task, outcome, count in task_counts[:8]:
        line = f'│  queue={queue:<12} task={task:<26} outcome={outcome:<7} count={fmt_count(count)}'
        lines.append(line.ljust(width - 1) + '│')
    if not task_counts:
        lines.append('│  (no samples yet)'.ljust(width - 1) + '│')
    lines.append('└' + '─' * (width - 2) + '┘')
    lines.append('')
    lines.append(' (Ctrl-C to exit; refresh every Ns)'.center(width))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def fetch(url: str, timeout: float = 5.0) -> tuple[list[PrometheusSeries], float]:
    req = urllib.request.Request(url, headers={'User-Agent': 'echoflow-tui/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode('utf-8', errors='replace')
    return parse_prometheus(body), time.time()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--url',
        default=os.environ.get(
            'ECHOFLOW_METRICS_URL', 'http://localhost:8005/metrics/'
        ),
        help='URL of the /metrics/ endpoint (default: $ECHOFLOW_METRICS_URL or http://localhost:8005/metrics/)',
    )
    parser.add_argument(
        '--interval', type=float, default=5.0,
        help='Refresh interval in seconds (default 5)',
    )
    parser.add_argument(
        '--once', action='store_true',
        help='Render once and exit (useful for scripting)',
    )
    parser.add_argument(
        '--no-clear', action='store_true',
        help='Do not clear the screen between refreshes (for log capture)',
    )
    args = parser.parse_args()

    try:
        if args.once:
            series, ts = fetch(args.url)
            sys.stdout.write(render(series, args.url, ts) + '\n')
            return 0
        while True:
            try:
                series, ts = fetch(args.url)
                if not args.no_clear:
                    clear_screen()
                sys.stdout.write(render(series, args.url, ts) + '\n')
                sys.stdout.flush()
            except urllib.error.URLError as exc:
                sys.stderr.write(f'fetch failed: {exc}\n')
                sys.stderr.flush()
            except Exception as exc:
                sys.stderr.write(f'render failed: {type(exc).__name__}: {exc}\n')
                sys.stderr.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        sys.stderr.write('\ninterrupted; exiting\n')
        return 0


if __name__ == '__main__':
    sys.exit(main())
