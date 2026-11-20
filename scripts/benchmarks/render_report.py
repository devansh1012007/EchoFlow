#!/usr/bin/env python3
"""Render the EchoFlow baseline benchmark report from measured CSVs.

Inputs (all produced by run_baseline.sh; none of them may be hand-edited):
  --stats     locust --csv output  <prefix>_stats.csv     (per-endpoint + Aggregated)
  --failures  locust --csv output  <prefix>_failures.csv  (error breakdown)
  --metrics   collect_system_metrics.py CSV (Redis / Celery queues / Postgres)
  --out       markdown report path

HONESTY RULES (enforced by construction):
  * Every number below is computed at render time from the input CSVs.
  * A missing input file, an empty file, or an unparsable column produces a
    per-section "MISSING" note — never a zero presented as a measurement,
    never a fabricated placeholder.
  * The target line is printed as a TARGET. The verdict is computed ONLY
    from measured numbers: PASS requires p95 <= 300 ms AND aggregate RPS
    >= 50 AND 5xx rate < 1%; if the measured RPS is below 50 the verdict is
    INCONCLUSIVE because the load profile itself was not reached. Nothing
    here may be quoted as an achieved result unless a run produced it.

Stdlib only (csv, argparse, datetime, re, urllib.parse).
"""

import argparse
import csv
import datetime
import os
import re
import sys
from urllib.parse import urlsplit

TARGET_TEXT = "Target (NOT yet a result): p95 <= 300 ms @ 50 RPS, <1% 5xx"
TARGET_P95_MS = 300.0
TARGET_RPS = 50.0
TARGET_5XX_RATE = 0.01  # 1%

FEED_NAME = "GET /feed/ [auth]"
AGGREGATE_TYPE = "Aggregated"


def fnum(value):
    """Parse a CSV float cell; return None when absent/unparsable."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def human_bytes(n):
    if n is None:
        return "n/a"
    unit = ["B", "KiB", "MiB", "GiB", "TiB"]
    v = float(n)
    for u in unit:
        if abs(v) < 1024.0 or u == unit[-1]:
            return f"{v:,.1f} {u}"
        v /= 1024.0
    return f"{n} B"  # pragma: no cover


def pct(value):
    if value is None:
        return "n/a"
    return f"{value * 100:.3f}%"


def read_csv_rows(path):
    if not path or not os.path.exists(path):
        return None
    if os.path.getsize(path) == 0:
        return None
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def classify_error(error):
    """Return ('5xx'|'429'|'other') for a locust failures.csv Error cell.

    locust encodes HTTP failures like `HTTPError('503 Server Error: ...')`
    and our locustfile labels explicit failures as `5xx <code>` /
    `429 throttled` (CatchResponseError). Classify from what is actually
    present; anything unidentifiable is 'other', never guessed into 5xx.
    """
    s = str(error)
    low = s.lower()
    if "5xx" in low:
        return "5xx"
    m = re.search(r"HTTPError\('(\d{3})", s)
    if m and m.group(1).startswith("5"):
        return "5xx"
    if "429" in s or re.search(r"HTTPError\('429", s):
        return "429"
    return "other"


# ---------------------------------------------------------------------------
# Stats section
# ---------------------------------------------------------------------------
def render_stats_table(rows):
    lines = []
    if rows is None:
        lines.append("_MISSING: locust stats CSV absent or empty — no latency/RPS data exists for this run._")
        return lines
    by_name = {}
    aggregate = None
    for r in rows:
        if r.get("Type") == AGGREGATE_TYPE:
            aggregate = r
        else:
            by_name[r.get("Name", "?")] = r
    header = (
        "| Endpoint | Requests | Failures | p50 (ms) | p95 (ms) | p99 (ms) |"
        " Requests/s | Failures/s |"
    )
    lines += [header, "|---|---:|---:|---:|---:|---:|---:|---:|"]
    order = [FEED_NAME, "GET /profile/me/ [auth]", "GET /health/",
             "GET /ready/", "POST /media/playback-token/<uuid>/ [auth]",
             "POST /auth/login/ [setup]"]
    names = [n for n in order if n in by_name] + sorted(
        n for n in by_name if n not in order)
    for name in names:
        r = by_name[name]
        lines.append(
            f"| {name} | {r.get('Request Count', '?')} | {r.get('Failure Count', '?')} "
            f"| {r.get('Median Response Time', '?')} | {r.get('95%', '?')} "
            f"| {r.get('99%', '?')} | {r.get('Requests/s', '?')} "
            f"| {r.get('Failures/s', '?')} |"
        )
    if aggregate is None:
        lines.append("_MISSING: no 'Aggregated' row in stats CSV._")
    else:
        a = aggregate
        lines += [
            "",
            "**Aggregated:** requests "
            f"{a.get('Request Count', '?')}, failures {a.get('Failure Count', '?')}, "
            f"p50 {a.get('Median Response Time', '?')} ms, p95 {a.get('95%', '?')} ms, "
            f"p99 {a.get('99%', '?')} ms, RPS {a.get('Requests/s', '?')}, "
            f"failures/s {a.get('Failures/s', '?')}.",
        ]
    return lines


def render_failures_section(rows):
    """Return (markdown_lines, summary) where summary is None or a dict
    {'5xx': n, '429': n, 'other': n, 'total': n} of measured occurrences."""
    if rows is None:
        return (
            [
                "_MISSING: locust failures CSV absent or empty — if the stats CSV "
                "also shows zero failures, there was nothing to fail._"
            ],
            None,
        )
    counts = {"5xx": 0, "429": 0, "other": 0}
    examples = {"5xx": [], "429": [], "other": []}
    total_occ = 0
    for r in rows:
        try:
            occ = int(r.get("Occurrences", "0") or 0)
        except ValueError:
            occ = 0
        total_occ += occ
        kind = classify_error(r.get("Error", ""))
        counts[kind] += occ
        if len(examples[kind]) < 10:
            examples[kind].append(
                f"  - `{r.get('Method', '?')} {r.get('Name', '?')}` — "
                f"{occ}x — {str(r.get('Error', ''))[:160]}"
            )
    lines = [f"- Total failure occurrences: **{total_occ}**"]
    for kind in ("5xx", "429", "other"):
        lines.append(f"- {kind}-classified: **{counts[kind]}**")
        lines += examples[kind]
    summary = dict(counts)
    summary["total"] = total_occ
    return lines, summary


# ---------------------------------------------------------------------------
# System metrics section
# ---------------------------------------------------------------------------
def render_metrics_section(rows):
    lines = []
    if rows is None:
        lines.append(
            "_MISSING: system-metrics CSV absent or empty — Redis / Celery / "
            "Postgres side was NOT sampled (collector not started, or its env "
            "vars were unset). The report does not backfill this section._"
        )
        return lines
    cols = rows[0].keys()

    def series(col):
        return [v for v in (fnum(r.get(col)) for r in rows) if v is not None]

    def peak(col):
        vals = series(col)
        return max(vals) if vals else None

    def mean(col):
        vals = series(col)
        return sum(vals) / len(vals) if vals else None

    def counter_delta(col):
        vals = series(col)
        if not vals:
            return None
        delta = vals[-1] - vals[0]
        if delta < 0:
            return None  # counter reset mid-run; refusing to invent one
        return delta

    def last_raw(col):
        """Last non-empty raw cell of a column (for string values like policies)."""
        for r in reversed(rows):
            v = str(r.get(col, "")).strip()
            if v:
                return v
        return None

    def cnt(v):
        """Format an integral count without a bogus decimal."""
        return f"{v:,.0f}" if v is not None else "n/a"

    lines.append(f"- Samples: **{len(rows)}** (interval set by BENCH_SAMPLE_INTERVAL).")

    lines.append("**Redis cache** (REDIS_CACHE_URL)")
    for label, col, fmt in (
        ("peak used_memory", "redis_cache_used_memory_bytes", human_bytes),
        ("mean used_memory", "redis_cache_used_memory_bytes", human_bytes),
        ("maxmemory", "redis_cache_maxmemory_bytes", human_bytes),
        ("maxmemory_policy", "redis_cache_maxmemory_policy", lambda v: str(v or "n/a")),
        ("evicted_keys (delta over run)", "redis_cache_evicted_keys_total", lambda v: f"{v:,.0f}"),
    ):
        if col not in cols:
            lines.append(f"  - {label}: _column absent (collector did not sample cache)_")
            continue
        if label.startswith("peak"):
            v = peak(col)
        elif label.startswith("mean"):
            v = mean(col)
        elif "delta" in label:
            v = counter_delta(col)
        else:
            v = last_raw(col)  # string column (e.g. policy)
        suffix = "" if v is not None else " — _no parsable samples_"
        lines.append(f"  - {label}: {fmt(v) if v is not None else 'n/a'}{suffix}")
    ev_delta = counter_delta("redis_cache_evicted_keys_total") if "redis_cache_evicted_keys_total" in cols else None
    if ev_delta is None and "redis_cache_evicted_keys_total" in cols:
        lines.append("  - evicted_keys: _counter reset mid-run or absent; delta not reported_")
    keys_peak = peak("redis_cache_dbsize_keys")
    if keys_peak is not None:
        lines.append(f"  - peak dbsize (keys): {keys_peak:,.0f}")

    lines.append("**Redis broker** (REDIS_BROKER_URL)")
    if "redis_broker_used_memory_bytes" not in cols:
        lines.append("  - _column absent (collector did not sample broker)_")
    else:
        lines.append(
            f"  - peak used_memory: {human_bytes(peak('redis_broker_used_memory_bytes'))}, "
            f"mean: {human_bytes(mean('redis_broker_used_memory_bytes'))}"
        )

    lines.append("**Celery queue depth** (LLEN on broker Redis — DEPTH, NOT LAG)")
    queue_cols = sorted(c for c in cols if c.startswith("celery_queue_") and c.endswith("_depth"))
    if not queue_cols:
        lines.append("  - _no queue columns present (broker not sampled)_")
    else:
        for c in queue_cols:
            q = c[len("celery_queue_"):-len("_depth")]
            lines.append(f"  - `{q}` peak depth: {cnt(peak(c))}")
        ua = peak("celery_unacked_count")
        lines.append(f"  - unacked (in-flight) peak: {cnt(ua)}")
        oldest = peak("celery_unacked_oldest_age_s")
        if oldest is not None:
            lines.append(
                f"  - oldest-unacked age (APPROXIMATION via Kombu unacked_index "
                f"score minus visibility timeout): {oldest:.1f} s peak"
            )
        else:
            lines.append(
                "  - oldest-unacked age: _not derivable from samples (no "
                "unacked messages, or visibility_timeout mismatch made the "
                "value negative and it was omitted)_"
            )
        for c in ("telemetry_stream_depth", "telemetry_stream_dlq_depth", "telemetry_legacy_list_depth"):
            if c in cols and peak(c) is not None:
                lines.append(f"  - `{c}` peak: {cnt(peak(c))}")

    lines.append("**Postgres** (DATABASE_URL, read-only pg_stat_* queries)")
    pg_cols = [c for c in cols if c.startswith("pg_")]
    if not pg_cols:
        lines.append("  - _no postgres columns present (DB not sampled)_")
    else:
        lines.append(f"  - peak `pg_active`: {cnt(peak('pg_active'))}")
        lines.append(f"  - peak `pg_idle_in_transaction`: {cnt(peak('pg_idle_in_transaction'))}")
        tot = peak("pg_total_client_connections")
        mx = series("pg_max_connections")
        if tot is not None:
            mx_txt = f" / max_connections {mx[-1]:.0f}" if mx else ""
            lines.append(f"  - peak client connections: {cnt(tot)}{mx_txt}")
        dl = counter_delta("pg_deadlocks_total")
        if dl is not None and dl == 0:
            lines.append("  - deadlocks delta over run: 0 (cumulative counter unchanged)")
        elif dl is not None:
            lines.append(f"  - deadlocks delta over run: {cnt(dl)}")
        else:
            lines.append("  - deadlocks: _column absent/empty — not measured_")
    return lines


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
def compute_verdict(stats_rows, failures_summary):
    """PASS/FAIL/INCONCLUSIVE from measured numbers ONLY."""
    if stats_rows is None:
        return "NOT EVALUABLE", ["locust stats CSV missing — nothing to judge"]
    agg = next((r for r in stats_rows if r.get("Type") == AGGREGATE_TYPE), None)
    if agg is None:
        return "NOT EVALUABLE", ["no 'Aggregated' row in stats CSV"]
    feed = next((r for r in stats_rows if r.get("Name") == FEED_NAME), None)
    judge_row = feed or agg
    p95 = fnum(judge_row.get("95%"))
    rps = fnum(agg.get("Requests/s"))
    reasons = []
    if feed is None:
        reasons.append("no per-endpoint row for 'GET /feed/ [auth]'; verdict uses the Aggregated row")
    if p95 is None or rps is None:
        return "NOT EVALUABLE", ["p95 or Requests/s unparsable in stats CSV"]
    reasons.append(f"measured feed p95 = {p95:.0f} ms (target <= {TARGET_P95_MS:.0f} ms)")
    reasons.append(f"measured aggregate RPS = {rps:.1f} (target >= {TARGET_RPS:.0f})")
    failure_count = fnum(agg.get("Failure Count"))
    total_requests = fnum(agg.get("Request Count"))
    five_xx_rate = None
    if failures_summary is not None:
        # Denominator is TOTAL requests, not total failures — the 5xx rate
        # is 5xx occurrences / all requests in the run.
        five_xx_rate = (
            failures_summary["5xx"] / total_requests
            if total_requests
            else None
        )
        if five_xx_rate is not None:
            reasons.append(f"measured 5xx rate = {pct(five_xx_rate)} (target < {pct(TARGET_5XX_RATE)})")
        else:
            reasons.append("5xx rate not computable (no request count)")
    elif failure_count == 0:
        five_xx_rate = 0.0
        reasons.append("5xx rate: failures CSV missing, but stats CSV reports 0 failures — treated as 0")
    else:
        return "NOT EVALUABLE", reasons + [
            f"stats CSV reports {failure_count:.0f} failures but the failures "
            "CSV is missing, so the 5xx rate cannot be measured"
        ]
    if rps < TARGET_RPS:
        return "INCONCLUSIVE", reasons + [
            f"achieved RPS {rps:.1f} is below the {TARGET_RPS:.0f} RPS target profile, "
            "so latency/5xx at the target load was not demonstrated"
        ]
    if p95 <= TARGET_P95_MS and (five_xx_rate is not None and five_xx_rate < TARGET_5XX_RATE):
        return "PASS", reasons
    return "FAIL", reasons


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Render the baseline benchmark report")
    parser.add_argument("--stats", required=True)
    parser.add_argument("--failures", required=True)
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--base-url", default=os.getenv("BENCH_BASE_URL", ""))
    parser.add_argument("--users", default=os.getenv("BENCH_USERS", "?"))
    parser.add_argument("--ramp", default=os.getenv("BENCH_RAMP_RATE", "?"))
    parser.add_argument("--duration", default=os.getenv("BENCH_RUN_TIME", "?"))
    args = parser.parse_args()

    stats_rows = read_csv_rows(args.stats)
    failures_rows = read_csv_rows(args.failures)
    metrics_rows = read_csv_rows(args.metrics)

    host = urlsplit(args.base_url).netloc if args.base_url else "unknown (BENCH_BASE_URL not provided)"
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    out = []
    out.append("# EchoFlow — Baseline Benchmark Report")
    out.append("")
    out.append(f"_Generated {now_utc}. All figures below come from the CSVs produced by this run._")
    out.append("")
    out.append("## Run metadata")
    out.append("")
    out.append(f"- Date (report generated, UTC): **{now_utc}**")
    out.append(f"- Base URL (host only): `{host}`")
    out.append(f"- Profile: users **{args.users}**, ramp **{args.ramp}/s**, duration **{args.duration}**")
    out.append(
        "- Task mix defaults: feed 80% / health 6% / ready 6% / profile 8% "
        "(see locustfile.py; playback token excluded at weight 0)"
    )
    out.append("")
    out.append(f"**{TARGET_TEXT}**")
    out.append("")
    out.append("> This line states the *goal* for the declared test profile. It is not a claim.")
    out.append("")

    failures_lines, failures_summary = render_failures_section(failures_rows)

    verdict, reasons = compute_verdict(stats_rows, failures_summary)
    out.append(f"## Verdict: **{verdict}**")
    out.append("")
    for r in reasons:
        out.append(f"- {r}")
    out.append("")

    out.append("## API latency (locust stats)")
    out.append("")
    out += render_stats_table(stats_rows)

    out.append("")
    out.append("## Failures (locust failures)")
    out.append("")
    out += failures_lines

    out.append("")
    out.append("## System metrics (collector)")
    out.append("")
    out += render_metrics_section(metrics_rows)

    out.append("")
    out.append("## Metric definitions")
    out.append("")
    out.append(
        "- **p50/p95/p99**: locust response-time percentiles in milliseconds "
        "(`Median Response Time` for p50; `95%`/`99%` columns).\n"
        "- **RPS**: locust `Requests/s` (request count / wall time).\n"
        "- **5xx rate**: occurrences of 5xx-classified errors in the failures CSV "
        "divided by total aggregated requests. Unclassifiable errors are NOT "
        "counted as 5xx.\n"
        "- **Queue depth**: LLEN of each Celery queue on the broker Redis — a "
        "snapshot of message count, NOT a wait-time measurement.\n"
        "- **unacked**: messages delivered to workers but not yet acknowledged "
        "(HLEN `unacked`); its 'oldest age' is an approximation documented in "
        "collect_system_metrics.py.\n"
        "- **Redis evictions / PG deadlocks**: cumulative counters; the report "
        "shows the delta over the run window."
    )
    out.append("")
    out.append("## How to reproduce")
    out.append("")
    out.append("```bash")
    out.append("# 1. Deployed stack up (docker compose), clips seeded (see README.md)")
    out.append("# 2. Bench machine deps: pip install locust  (dev-only; NOT in requirements*.txt)")
    out.append("# 3. Env: BENCH_BASE_URL, BENCH_TOKEN (or BENCH_USERNAME/BENCH_PASSWORD),")
    out.append("#         REDIS_CACHE_URL, REDIS_BROKER_URL, DATABASE_URL")
    out.append("BENCH_BASE_URL=https://<host> \\")
    out.append("BENCH_TOKEN=<pre-minted access JWT> \\")
    out.append("REDIS_CACHE_URL=redis://<cache-host>:6379/0 \\")
    out.append("REDIS_BROKER_URL=redis://<broker-host>:6379/0 \\")
    out.append("DATABASE_URL=postgres://<user>:<pass>@<db-host>:5432/<db> \\")
    out.append("bash scripts/benchmarks/run_baseline.sh")
    out.append("```")
    out.append("")
    out.append(
        "_Publishing numbers is only legitimate from a measured run with this "
        "profile recorded. Nothing in this repo has been measured yet; any "
        "latency claim made without running this harness is unsupported._"
    )
    out.append("")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        fh.write("\n".join(out))
    print(f"[render_report] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
