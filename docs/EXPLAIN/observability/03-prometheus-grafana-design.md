# Prometheus + Grafana Design

## Why

Observability is the difference between "we're degrading" and "we already failed."
At 10k concurrent users, partial observability equals blind — see
`docs/unfixed-issues-2026-09-03.md:106-115` (§3.4 P3.2 — "Observability … scraping
/ dashboards / alerting open") and `docs/event-driven-architecture-plan.md:618-622`
(P3.2 — "Add Prometheus + Grafana + alertmanager — ⏳ PARTIAL").

The foundation is already in place: `django_prometheus` is wired in
`backend/EchoFlow/settings.py:73` (INSTALLED_APPS) and `backend/EchoFlow/settings.py:110, 125`
(MIDDLEWARE), `/metrics/` is exported in `backend/EchoFlow/urls.py:13`, and structured
JSON logs with `correlation_id` are configured in `backend/EchoFlow/settings.py:477-523`
together with `backend/EchoFlow/middleware.py` and `backend/EchoFlow/logging_filters.py`.
What is missing is the **scraper** (Prometheus), the **dashboards** (Grafana),
the **custom application-level histograms** on the hot paths (the auto-collected
django-prometheus metrics cover request latency and DB query counts but not the
application operations: a refill, a ranking, a toggle), the **alert rules**, and
the **CI probe** that asserts the `/health/`, `/ready/`, and `/metrics/` endpoints
return 200 against a freshly-started stack.

This document is the design for closing that gap. It does not introduce
distributed tracing, log aggregation, anomaly detection, or per-user
histograms — those are explicitly out of scope (§7).

## Current state and the gap

### What exists today

| Capability | Where | Status |
|---|---|---|
| `django_prometheus` registered | `backend/EchoFlow/settings.py:73` | ✅ |
| `PrometheusBeforeMiddleware` + `PrometheusAfterMiddleware` | `backend/EchoFlow/settings.py:110, 125` | ✅ — first and last in MIDDLEWARE list so the histogram wraps everything else |
| `/metrics/` exporter | `backend/EchoFlow/urls.py:13` (`ExportToDjangoView`) | ✅ |
| `/health/` (liveness) | `backend/EchoFlow/health.py:9-17`; wired in `urls.py:9` | ✅ |
| `/ready/` (readiness, DB ping) | `backend/EchoFlow/health.py:20-41`; wired in `urls.py:10` | ✅ |
| JSON structured logs | `backend/EchoFlow/settings.py:477-523` (`python-json-logger` + `CorrelationIdFilter`) | ✅ |
| Per-request correlation_id | `backend/EchoFlow/middleware.py:21-39` + `backend/EchoFlow/correlation.py:14-26` (`contextvars`) | ✅ |

### What is missing

1. **No scraper.** Nothing reads `/metrics/`. There is no `prometheus` service in
   `docker-compose.yml` (the file has 11 services today: `db`, `pgbouncer`,
   `redis_broker`, `redis_cache`, `minio`, `minio-init`, `web`, `celery`,
   `celery_feed`, `celery_media`, `celery_beat` — confirmed by
   `docker-compose.yml:1-541`).
2. **No dashboards.** No `grafana` service; no `grafana/provisioning/` directory.
3. **No custom histograms on the hot path.** django-prometheus auto-collects
   `django_http_requests_total_by_view`, `django_http_requests_latency_seconds_by_view`,
   `django_db_query_duration_seconds`, and `django_http_responses_total_by_status_view`.
   What it cannot see is the *application-level* operation: a single
   `refill_user_feed` task may take 80 ms total but include 5 ms inside the SQL
   fallback and 3 ms inside Redis SETNX — the auto histogram only sees the
   request latency, and there is no request for the worker task at all.
4. **No alert rules.** No `prometheus/alerts.yml`; no Alertmanager.
5. **No CI probe.** `.github/workflows/django.yml:54-97` runs migrations +
   `manage.py test backend.app` + `collectstatic --dry-run`, but never boots
   Compose and never curls `/health/`, `/ready/`, or `/metrics/`.

## The minimum-viable observability stack

### Compose services

Two new services land in `docker-compose.yml` (no existing services change):

| Service | Image | Port | Purpose | Resource limits |
|---|---|---|---|---|
| `prometheus` | `prom/prometheus:v2.51.0` | 9090 | scrapes `web:8000/metrics/` every 15s | 0.25 CPU, 256M RAM |
| `grafana` | `grafana/grafana:10.4.0` | 3000 | reads Prometheus via auto-provisioned datasource | 0.25 CPU, 256M RAM |

Resource limits are deliberately modest — this is a dev/single-instance stack,
not a multi-replica HA deployment. The `event-driven-architecture-plan.md:328`
sizing for "prometheus + grafana (NEW) 0.5 / 0.5 CPU, 512 MB / 512 MB" is the
**production** target (Phase 2); what we ship now is the dev-tier half.

### `prometheus/prometheus.yml` (scrape config)

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    monitor: "echoflow-dev"

scrape_configs:
  - job_name: "echoflow-web"
    metrics_path: "/metrics/"
    static_configs:
      - targets: ["web:8000"]
        labels:
          service: "web"

  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
```

The single `web:8000` target is correct: `gunicorn.conf.py` listens on
`:8000` inside the container, and Compose networking gives every service
DNS for every other service. The 15s scrape interval is the smallest value
that doesn't dominate disk I/O at 7-day retention — see §8.

### `grafana/provisioning/datasources/prometheus.yml` (auto-provisioned datasource)

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

`editable: false` so a developer does not accidentally repoint Grafana at
a dev Prometheus when the real one is right there. The provisioning model
means the datasource comes up at container start with no manual UI steps.

### `grafana/provisioning/dashboards/echoflow.yml` (dashboard provider)

```yaml
apiVersion: 1
providers:
  - name: "EchoFlow"
    orgId: 1
    folder: "EchoFlow"
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
```

This points Grafana at the `grafana_dashboards/` directory mounted into the
container, so dropping a new `.json` file there auto-loads it without a
manual import step.

### Admin password from env var

`GRAFANA_ADMIN_PASSWORD` is read by `docker-compose.yml`'s `grafana` service
and injected as `GF_SECURITY_ADMIN_PASSWORD`. There is **no** default
fallback — Grafana itself has a default `admin/admin` and that is precisely
what we do not want to ship, since `3000:3000` is published to the host.
A `.env.example` line `GRAFANA_ADMIN_PASSWORD=change-me-in-prod` is added
so a fresh clone fails closed if the env var is unset.

## The 8 dashboard panels

These are what ships in `grafana_dashboards/echoflow_overview.json`. Each
panel is described by what it shows, the PromQL it will run, and the
**why** — the rendering is Grafana's job, not this doc's.

### Panel 1 — request latency by endpoint (p50 / p95 / p99)

Endpoints of interest (from the AGENTS.md API table):
`/feed/`, `/suggestions/`, `/interactions/{id}/toggle-like/`,
`/comments/`, `/share/{id}/send-share/`. django-prometheus already exports
`django_http_requests_latency_seconds_by_view{view="..."}` (histogram) and
its companion `_bucket` / `_count` / `_sum` series. PromQL:

```
histogram_quantile(0.99,
  sum by (le, view) (
    rate(django_http_requests_latency_seconds_by_view_bucket{view=~"/feed/|/suggestions/|/interactions/.+/toggle-like/|/comments/|/share/.+/send-share/"}[5m])
  )
)
```

Three series, one per quantile (0.50, 0.95, 0.99), faceted by endpoint via
the `view` label. p99 is the panel that pages on; p50/p95 are for capacity
planning.

### Panel 2 — request rate per endpoint (req/s)

```
sum by (view) (rate(django_http_requests_total_by_view{view=~"..."}[1m]))
```

Faceted `by (view)` so a spike in `/log-telemetry/` (the architecture
audit's #1 abuse vector — `settings.py:444-451`) is visible alongside the
intentional traffic.

### Panel 3 — 5xx error rate per endpoint

```
sum by (view) (rate(django_http_responses_total_by_status_view{status=~"5..",view=~"..."}[5m]))
/
sum by (view) (rate(django_http_responses_total_by_status_view{view=~"..."}[5m]))
```

As a percentage. The denominator is non-zero in any real environment, but
the PromQL is safe under `/0` (returns no series, panel stays empty —
better than NaN).

### Panel 4 — active Celery workers per queue

`celery_exporter` (or a small custom gauge in `backend/app/tasks.py` — see
§4) emits `echoflow_celery_workers{queue="..."}`. PromQL:

```
sum by (queue) (echoflow_celery_workers)
```

The four queues we care about: `celery` (default), `fast_feed`,
`heavy_media`. The 4th (`celery_beat`) does not have a worker pool in the
traditional sense and is excluded.

### Panel 5 — Postgres connection pool utilization

`pg_stat_activity` count vs `max_connections`. Requires `postgres_exporter`
sidecar (out of scope for this design — see §7) OR a small custom Django
view that runs the query and exports a gauge. For now, the panel ships
**disabled** with a `// TODO:` marker in the dashboard JSON explaining it
lights up once `postgres_exporter` is added. Threshold aligned with
`event-driven-architecture-plan.md:372` (> 80% of `max_connections` = 200).

### Panel 6 — Redis memory usage + eviction count

Two series from `redis_exporter`:

```
redis_memory_used_bytes{instance="redis_cache"} / 1024 / 1024  # MB
rate(redis_evicted_keys_total{instance="redis_cache"}[5m])
```

Two services, two Redis instances: `redis_cache` (the LRU one with the
3 GB cap — `docker-compose.yml:81`) and `redis_broker` (the noeviction
one — `docker-compose.yml:45`). Threshold per
`event-driven-architecture-plan.md:373` (> 80% of 2 GB; we use the actual
`--maxmemory` flag value to compute the percentage).

### Panel 7 — background task success/failure rate

Celery does not natively export Prometheus metrics. Two options:

1. **celery-exporter** (community): runs as a sidecar, scrapes
   `celery inspect stats` over the broker. Out of scope for this design.
2. **Custom Counter pair** in `backend/app/tasks.py`: a `Counter` per
   task name with `outcome={success,retry,failure}` label. Incremented at
   the end of every task in a `try/finally`. This is what we ship.

PromQL:

```
sum by (task, outcome) (rate(echoflow_celery_task_outcomes_total[5m]))
```

Stacked area chart; the relative height of `failure` is the signal.

### Panel 8 — HNSW index size + cache hit ratio

Two queries against `pg_stat_user_indexes`:

```
pg_stat_user_indexes_size_bytes{indexrelname="audioclip_semantic_hnsw"} / 1024 / 1024
pg_stat_user_indexes_idx_scan{indexrelname="audioclip_semantic_hnsw"}
```

Index size grows linearly with row count; cache hit ratio is
`idx_scan / (idx_scan + seq_scan_on_audioclip)`. Same caveat as Panel 5:
requires `postgres_exporter` or a Django view. The panel ships disabled
until `postgres_exporter` is added.

## The custom histograms to add to the hot path

### Why these and not others

django-prometheus already exposes per-view latency. The histograms below
are for **application-level operations** that the framework cannot see:

| Histogram | What it measures | Why django-prometheus can't see it |
|---|---|---|
| `echoflow_feed_refill_duration_seconds` | end-to-end `refill_user_feed` task | runs in a Celery worker, not inside an HTTP request |
| `echoflow_suggestion_ranking_duration_seconds` | inside-`get_queryset` ranking math | django-prometheus sees the request latency, not the subset of time spent in CosineDistance |
| `echoflow_toggle_like_duration_seconds` | `record_like_toggle` row-lock time | the row-lock is the failure mode — it can dominate the request latency, but a histogram of `django_db_query_duration_seconds` aggregates it across all callers |
| `echoflow_cache_get_set_duration_seconds` | Redis cache hit/miss latency | cache.get/set are not a separate DB call — they show up under "cache" in django-prometheus, but only as a counter, not a latency histogram |
| `echoflow_hls_processing_duration_seconds` | end-to-end `process_audio_to_hls` | runs in `celery_media`, never touches an HTTP request |

These five ship in a new module `backend/app/metrics.py`. Each one
documents its labels in a `DECISION` comment so a future developer does
not add `user_id` or `clip_id` labels and blow up Prometheus memory
(see §8).

### The code

```python
# DECISION: Cardinality discipline. Histogram labels are bounded to small
# enum-like values (category strings, outcome enums, op enums). NEVER add
# `user_id`, `clip_id`, `request_id`, or any other high-cardinality label
# here — Prometheus stores one time series per (metric, label_set) tuple,
# so 1M users × 5 outcomes = 5M series per histogram. Each series costs
# ~3 KB; 5M series = 15 GB of RAM just for this metric. Use the
# correlation_id in the JSON logs to debug per-request issues; do not
# promote it to a label.
#
# All five histograms below are ON TOP OF the django-prometheus
# auto-collected per-view histograms. django-prometheus observes request
# latency, response codes, and DB query counts automatically; these
# observe the application-level operations inside the request/worker.
# Do not duplicate them — django-prometheus's `django_http_requests_*`
# families are NOT redefined here.

from prometheus_client import Histogram

_FILL_REFILL_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

echoflow_feed_refill_duration_seconds = Histogram(
    'echoflow_feed_refill_duration_seconds',
    'Duration of refill_user_feed task end-to-end',
    labelnames=('source', 'outcome'),
    buckets=_FILL_REFILL_BUCKETS,
)
# DECISION: `source` values are exactly: pool (precomputed ZSET path,
# fast ~2 ms), sql (composite-score query fallback, 20-200 ms), cold
# (no user vectors, engagement_velocity order, 50-500 ms). `outcome`
# values are exactly: success (clips pushed), empty (nothing to push),
# error (raised). Three sources × three outcomes = nine series.

echoflow_suggestion_ranking_duration_seconds = Histogram(
    'echoflow_suggestion_ranking_duration_seconds',
    'Duration of SuggestionViewSet.get_queryset ranking math',
    labelnames=('category', 'outcome'),
    buckets=(0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
# DECISION: `category` is whatever the request query string sent (a
# free-form user-supplied label in principle). In practice the frontend
# constrains it to the eight known AudioClip.category enum values, so
# series count is bounded. If a future PR adds user-supplied categories,
# add an allowlist here. `outcome` values: success (vector ranking),
# fallback (engagement_velocity order, used when get_user_vectors raised),
# error (unhandled exception inside get_queryset).

echoflow_toggle_like_duration_seconds = Histogram(
    'echoflow_toggle_like_duration_seconds',
    'Duration of record_like_toggle row-lock path',
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)
# DECISION: No labels. The F() counter side-effect in UserInteraction.save()
# is the load-bearing hot row; the histogram bucket resolution is finer
# here (1 ms to 1 s) because the failure mode is a 50-250 ms tail under
# 50 likes/sec on the same clip (event-driven-architecture-plan.md:222).
# Adding `clip_id` would be the exact cardinality bomb §8 warns against.

echoflow_cache_get_set_duration_seconds = Histogram(
    'echoflow_cache_get_set_duration_seconds',
    'Duration of Django cache get/set operations',
    labelnames=('op', 'result'),
    buckets=(0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5),
)
# DECISION: `op` is get|set; `result` is hit|miss|error. For `set`,
# `result` is always `hit` (no concept of miss on write) — Grafana
# queries that filter `op="set"` should not filter on `result`.

echoflow_hls_processing_duration_seconds = Histogram(
    'echoflow_hls_processing_duration_seconds',
    'Duration of process_audio_to_hls end-to-end',
    labelnames=('outcome',),
    buckets=(5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
)
# DECISION: `outcome` values are exactly: success (clip.status = ready),
# transient_error (re-raised for autoretry_for), terminal_error
# (clip.status = failed, no retry). Buckets are tuned to the seconds-to-
# tens-of-minutes range this task actually runs in — Whisper base on
# a 30 s clip is ~10 s; a 5 min clip is ~60-90 s.
```

The module also exports the Celery task counter pair referenced in
Panel 7 (Counter, not Histogram — same label discipline applies):

```python
from prometheus_client import Counter

echoflow_celery_task_outcomes_total = Counter(
    'echoflow_celery_task_outcomes_total',
    'Celery task outcomes by task name and result',
    labelnames=('task', 'outcome'),
)
# DECISION: Increment with outcome in {success, retry, failure} at the
# bottom of every task wrapped in this histogram. `retry` is distinct
# from `failure` so we can tell "transient error that will be retried"
# (alert: ignored) from "permanent failure" (alert: page). See §6 for
# the wrapping pattern.
```

### Wiring into the hot path

The histograms are wrapped as a `try/finally` block around each existing
operation. The pattern (for `record_like_toggle` in
`backend/app/services/interactions.py:84-95`):

```python
import time
from ..metrics import echoflow_toggle_like_duration_seconds

def record_like_toggle(user, clip):
    start = time.perf_counter()
    try:
        interaction, created = UserInteraction.objects.get_or_create(...)
        if not created:
            interaction.is_active = not interaction.is_active
            interaction.save()
        return interaction, created
    finally:
        echoflow_toggle_like_duration_seconds.observe(
            time.perf_counter() - start
        )
```

The same pattern applies at the entry point of:

| Hot path | Function | File:line |
|---|---|---|
| Feed refill | `refill_user_feed` | `backend/app/tasks.py:347` |
| Suggestion ranking | `SuggestionViewSet.get_queryset` | `backend/app/views/feed.py:153` |
| Toggle like | `record_like_toggle` | `backend/app/services/interactions.py:84` |
| Telemetry | `record_telemetry` | `backend/app/services/interactions.py:124` |
| HLS processing | `process_audio_to_hls` | `backend/app/tasks.py:164` |

For `refill_user_feed`, the `source` label is set inside the function
where the path decision is made (pool vs SQL vs cold — `tasks.py:382-457`).
For `process_audio_to_hls`, the `outcome` label is set in each
return-early branch (`tasks.py:174, 199, 231, 274, 304`) plus the success
path at `tasks.py:329`.

For the Celery counter, every wrapped task gets a `finally:` that
increments with the right `outcome`:

```python
@shared_task(bind=True, max_retries=3, ...)
def process_audio_to_hls(self, clip_id):
    try:
        ...existing body...
        echoflow_celery_task_outcomes_total.labels(
            task='process_audio_to_hls', outcome='success'
        ).inc()
    except RETRYABLE_ERRORS:
        echoflow_celery_task_outcomes_total.labels(
            task='process_audio_to_hls', outcome='retry'
        ).inc()
        raise
    except Exception:
        echoflow_celery_task_outcomes_total.labels(
            task='process_audio_to_hls', outcome='failure'
        ).inc()
        raise
```

The wrapping convention is "increment first, then re-raise" so a Celery
worker crash after the increment still records the outcome.

## The alert rules

Ship as `prometheus/alerts.yml`. Six rules, in priority order. Thresholds
are anchored against `event-driven-architecture-plan.md:367-381` (§5.4
Observability at 10k — minimum bar).

```yaml
groups:
  - name: echoflow-slo
    rules:
      - alert: HighFeedLatencyP99
        # /feed/ p99 > 1.5s for 5 min. Anchored against
        # event-driven-architecture-plan.md:222 ("50-250 ms of accumulated
        # lock time, plus 50 connections pinned") and the refill target
        # of < 100 ms at p99 (event-driven-architecture-plan.md:564).
        # 1.5s gives 15x headroom over the refill target and accounts for
        # the request-latency envelope around it.
        expr: |
          histogram_quantile(0.99,
            sum by (le) (
              rate(
                django_http_requests_latency_seconds_by_view_bucket{
                  view="/feed/"
                }[5m]
              )
            )
          ) > 1.5
        for: 5m
        labels:
          severity: page
          service: web
        annotations:
          summary: "/feed/ p99 latency > 1.5s for 5 min"

      - alert: HighFivexxRate
        # > 1% sustained 5 min. Matches the §5.4 table exactly.
        expr: |
          (
            sum by (view) (
              rate(django_http_responses_total_by_status_view{status=~"5.."}[5m])
            )
            /
            sum by (view) (
              rate(django_http_responses_total_by_status_view[5m])
            )
          ) > 0.01
        for: 5m
        labels:
          severity: page
          service: web
        annotations:
          summary: "5xx error rate > 1% on {{ $labels.view }}"

      - alert: PostgresConnectionsNearMax
        # > 80% of max_connections=200 (event-driven-architecture-plan.md:336).
        # Implemented as a custom gauge from a Django view that runs
        # `SELECT count(*) FROM pg_stat_activity`. (postgres_exporter
        # would expose this directly but is out of scope — §7.)
        expr: echoflow_pg_connections_active / 200 > 0.80
        for: 5m
        labels:
          severity: page
          service: db
        annotations:
          summary: "Postgres at {{ $value | humanizePercentage }} of max_connections"

      - alert: RedisCacheMemoryHigh
        # > 80% of --maxmemory 3gb (docker-compose.yml:81).
        # redis_exporter is also out of scope (§7); this expression is a
        # placeholder for when redis_exporter is added. The alert will
        # simply not fire until then, which is correct (better than a
        # broken PromQL that pages on stale data).
        expr: echoflow_redis_cache_memory_used_bytes / echoflow_redis_cache_maxmemory_bytes > 0.80
        for: 5m
        labels:
          severity: page
          service: redis_cache
        annotations:
          summary: "redis_cache at > 80% of maxmemory"

      - alert: CeleryQueueBacklog
        # Per §5.4: fast_feed > 100 sustained 5 min; heavy_media > 20
        # sustained 10 min. We coalesce both into one rule with two
        # thresholds via the queue label, warning severity (not page).
        expr: echoflow_celery_queue_length{queue="fast_feed"} > 100
        for: 10m
        labels:
          severity: warn
          service: celery_feed
        annotations:
          summary: "Celery queue {{ $labels.queue }} depth {{ $value }}"

      - alert: HighBackgroundTaskFailureRate
        # > 5% failures over 15 min. Paged because a stuck processing
        # pipeline (Whisper OOM, FFmpeg crash loop) needs intervention.
        expr: |
          (
            sum by (task) (
              rate(echoflow_celery_task_outcomes_total{outcome="failure"}[15m])
            )
            /
            sum by (task) (
              rate(echoflow_celery_task_outcomes_total[15m])
            )
          ) > 0.05
        for: 15m
        labels:
          severity: page
          service: celery
        annotations:
          summary: "Background task {{ $labels.task }} failing > 5%"
```

The `severity: page` label is what Alertmanager routes to PagerDuty /
SMS; `severity: warn` routes to Slack. There is no Alertmanager service
in this design (single-receiver Slack for now); wiring Alertmanager in
is a follow-up PR once the rules have proven stable for one operational
cycle.

## Implementation checklist

1. **`docker-compose.yml`** — add `prometheus` and `grafana` services with
   resource limits (CPU 0.25, RAM 256M each — see §2). Mount
   `prometheus/prometheus.yml` and `prometheus/alerts.yml` as volumes into
   the `prometheus` service. Mount `grafana/provisioning/` and
   `grafana/dashboards/` into the `grafana` service. Add a top-level
   `grafana_data:` volume.
2. **`prometheus/prometheus.yml`** — the scrape config from §2.
3. **`prometheus/alerts.yml`** — the six rules from §5.
4. **`grafana/provisioning/datasources/prometheus.yml`** — datasource from §2.
5. **`grafana/provisioning/dashboards/echoflow.yml`** — dashboard provider
   from §2.
6. **`grafana/dashboards/echoflow_overview.json`** — the 8 panels from §3.
   Panels 5 and 8 ship in **disabled** state with `description` fields
   noting "lights up when `postgres_exporter` is added."
7. **`backend/app/metrics.py`** (new file) — the 5 histograms + 1 counter
   from §4, with `DECISION:` comments on cardinality.
8. **Wire the histograms** into the hot-path code:
   - `backend/app/tasks.py:347` — `refill_user_feed`
   - `backend/app/views/feed.py:153` — `SuggestionViewSet.get_queryset`
   - `backend/app/services/interactions.py:84` — `record_like_toggle`
   - `backend/app/services/interactions.py:124` — `record_telemetry`
   - `backend/app/tasks.py:164` — `process_audio_to_hls`
   - Plus the `echoflow_celery_task_outcomes_total` counter wrapped around
     each task that the custom histograms wrap.
9. **CI probe** — add a step to `.github/workflows/django.yml` after the
   `Run tests` step (`django.yml:90-92`) that boots Compose (`docker compose
   up -d`), waits for `web` to be healthy (retry loop, see §8), then
   asserts:
   - `curl -fsS http://localhost:8005/health/` → `200`
   - `curl -fsS http://localhost:8005/ready/` → `200`
   - `curl -fsS http://localhost:8005/metrics/` → `200` AND response
     contains `django_http_requests_latency_seconds_by_view_bucket`
   - `curl -fsS http://localhost:9090/api/v1/query?query=up` → at least one
     target is `up`
   - `docker compose down -v` (teardown).
10. **Documentation update** — `docs/EXPLAIN/observability/01-current-state.md`
    (new) covers the existing pieces; this document covers the new pieces.

## What is OUT of scope

- **Distributed tracing (OpenTelemetry / Jaeger).** Too heavy for a dev
  stack; deferred until the JSON logs prove insufficient for debugging
  (estimated ~50k DAU threshold per `event-driven-architecture-plan.md:383`).
- **Sentry error tracking.** Separate work item. JSON logs with
  `correlation_id` are the bridge; Sentry comes after.
- **Per-user latency histograms.** Privacy + cardinality. A single
  histogram with `user_id` label at 1M users = 1M series = ~3 GB RAM.
  Debug per-request via `correlation_id` in the JSON logs.
- **Log aggregation (Loki / ELK).** Defer until the JSON logs are in
  production and operators confirm grep is too slow.
- **Anomaly detection.** Defer until we have ≥ 1 month of baseline data
  to compare against. Static thresholds (the §5 rules above) are the
  floor.
- **Multi-cluster Prometheus.** Single instance is fine for the dev /
  single-region target. Federation / Thanos is a Phase 2 problem.
- **`postgres_exporter` / `redis_exporter` sidecars.** Mentioned in
  `event-driven-architecture-plan.md:383` as part of the "minimum infra";
  deferred to a follow-up PR. Panels 5, 6, and 8 ship **disabled** until
  then (with `// TODO:` markers in the dashboard JSON).

## Risks and trade-offs

### Prometheus storage

15s scrape interval × 7 days retention × ~150 active series = ~50 GB on
disk in the worst case (every histogram at every label combination, one
sample per 15s). For the dev tier: `--storage.tsdb.retention.time=7d`
default + 1 GB volume limit is fine. The volume `prometheus_data:` is
capped by `deploy.resources.limits.memory: 256M`; if the TSDB outgrows
that the container OOM-kills and loses history. **Mitigation:** the
compose volume is unbounded by default; a `TODO` in the PR description
notes that production should add `--storage.tsdb.retention.size=10GB`
and `--storage.tsdb.retention.time=30d` and provision a larger volume.

### Grafana admin password

`GRAFANA_ADMIN_PASSWORD` is the single env var. There is no default
fallback in `docker-compose.yml` — if the env var is unset, the
`grafana` container refuses to start (Grafana itself rejects a missing
admin password in v10.x). This is fail-loud, not silent. The
`.env.example` ships a `change-me-in-prod` placeholder so a fresh clone
fails closed.

### Cardinality

Histograms with `user_id`, `clip_id`, `request_id`, or any other
high-cardinality label would explode memory. §4's `DECISION:` comment
in `backend/app/metrics.py` is the explicit constraint: labels are
bounded to enum-like values (`source`, `outcome`, `op`, `result`,
`category`). A code review that adds a new label must justify the
cardinality. The convention is: if you find yourself wanting
`user_id` as a label, use the `correlation_id` in the JSON logs
instead.

### Auto-collected django-prometheus vs custom histograms

django-prometheus already gives request latency, response codes, DB
query counts. The custom histograms are for **application-level
operations** that the framework cannot observe: a refill (runs in a
Celery worker, not a request), a ranking (subset of `get_queryset`
time, not the whole request), a row-lock (subset of DB query time,
not the whole request). Don't duplicate — `django_http_requests_*`
families are not redefined in `metrics.py`. The `DECISION:` comment
at the top of `metrics.py` (§4) restates this.

### CI probe flakiness

`/metrics/` on a freshly-started `web` container may not be ready for
5-10 seconds (gunicorn worker spawn, Django app load, model imports —
the existing `healthcheck.start_period: 90s` in `docker-compose.yml:277`
acknowledges this). The CI step wraps each curl in a retry loop:

```bash
for i in {1..30}; do
  if curl -fsS http://localhost:8005/health/ >/dev/null; then break; fi
  sleep 2
done
```

30 attempts × 2s = 60s worst case, which fits inside GitHub Actions'
default 10-min job timeout. The same loop is used for `/ready/` and
`/metrics/`. The probe fails (not flakes) if any of the three does
not respond within the budget — see §9.

## Verification plan

### Local verification

After implementation:

1. `docker compose up --build -d` — all 13 services (11 existing + 2 new)
   reach `healthy`.
2. `curl -fsS http://localhost:9090/api/v1/targets` — `echoflow-web`
   target is `up`.
3. `curl -fsS http://localhost:3000` — Grafana login page renders with
   the datasource pre-provisioned (no manual "Add datasource" step).
4. `curl -fsS -u admin:$GRAFANA_ADMIN_PASSWORD http://localhost:3000/api/dashboards/uid/echoflow-overview`
   — dashboard JSON returns 200 with all 8 panels.
5. Trigger an alert manually: edit `prometheus/alerts.yml` to set
   `HighFeedLatencyP99`'s threshold to `0.05` (50 ms), reload Prometheus
   (`curl -X POST http://localhost:9090/-/reload`), then run a tight
   loop:
   ```bash
   while true; do
     curl -fsS -H "Authorization: Bearer $JWT" http://localhost:8005/feed/
   done
   ```
   Within 5 minutes Prometheus fires the alert; verify with
   `curl -fsS http://localhost:9090/api/v1/alerts` → state `firing`.
6. Revert the threshold to `1.5`.

### CI verification

The new CI step in `.github/workflows/django.yml` (item 9 of §6) must
pass on a fresh `docker compose up`. The probe asserts HTTP 200 from
`/health/`, `/ready/`, `/metrics/`, and that `/metrics/` contains
`django_http_requests_latency_seconds_by_view_bucket` (a positive
signal that the auto-collection actually ran).

### Test verification

`pytest backend/app/tests/ --tb=short` continues to pass — no new
tests are required. The histograms are imported in `backend/app/metrics.py`
which is loaded once at process start; importing it under `manage.py test`
is a no-op (no observation, no side effect beyond registration). The
pytest suite does not exercise `/metrics/` end-to-end because that
requires a running gunicorn; the CI probe in §6 is the substitute.

For module-level testing of `metrics.py` in isolation, a developer can:

```bash
docker compose exec web python -c "
from backend.app import metrics
metrics.echoflow_toggle_like_duration_seconds.labels().observe(0.012)
print(metrics.echoflow_toggle_like_duration_seconds._metrics)
"
```

The `_metrics` private dict exposes the registered collectors for
assertion in a future PR — adding a `test_metrics.py` with
`prometheus_client.parser.text_string_to_metric_families` parsing is a
follow-up item, **not** required for this design.

### Open verifications

- **Panel 5, 6, 8** cannot be verified end-to-end until
  `postgres_exporter` and `redis_exporter` are added (out of scope).
  The dashboard JSON ships with `panels[*].id=5,6,8` having
  `"transparent": true` and a description that explains the dependency.
- **Alert routing** to PagerDuty/Slack requires Alertmanager, which is
  out of scope. The `severity: page` label is correct and routes once
  Alertmanager is added in a follow-up PR.
