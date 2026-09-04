# Partially Addressed Issues — Completion Plan

**Date:** 2026-09-04 (plan); 2026-09-04 (shipped)
**Status:** 6 of 7 Partially Addressed items SHIPPED (PR 1/2/3 of this plan). B14 (CDN cache headers) deferred — waiting on the real-CDN activation, no code blocker.
**Scope:** The 7 Partially Addressed issues from the verification (Group A items 1, 3, 5, 8; Group B items 13, 14, 17; Group D item 25). Plus the B19 module-docstring drift.
**Out of scope:** The 3 Not Shipped items (A6, B15, D26) and the 4 False Positives (B16, B17, C22, C24). Those are either doc-only or AI-team work.

## Ship status (2026-09-04)

| Item | Status | Commit / PR | Files |
|------|--------|-------------|-------|
| **A1** PgBouncer app-side tuning | SHIPPED | PR 1 `e73b149` | `backend/EchoFlow/settings.py`, `backend/app/tests/test_settings.py` (5 new tests) |
| **A3 Part 1** cache invalidation (share + telemetry) | SHIPPED | PR 1 `e73b149` | `backend/app/services/interactions.py`, `backend/app/services/shares.py`, `backend/app/tasks.py`, + 3 test files (6 new tests) |
| **B19** docstring drift | SHIPPED | PR 1 `e73b149` | `backend/app/services/interactions.py` (module docstring) |
| **A8** Prometheus + Grafana | SHIPPED | PR 2 `5131f78` | `docker-compose.yml`, `docker/prometheus/`, `docker/grafana/`, `backend/app/tests/test_metrics_endpoint.py` (1 new test) |
| **B13** Sentry integration (ready-to-configure) | SHIPPED | PR 2 `5131f78` | `backend/EchoFlow/sentry.py`, `backend/app/services/sentry.py`, `backend/app/apps.py`, `requirements-base.txt`, `.env.example`, `backend/app/tests/test_sentry.py` (5 new tests) |
| **A5** read-replica activation contract tests | SHIPPED | PR 3 `0198721` | `backend/app/tests/test_db_router.py` (3 new tests), `docs/EXPLAIN/database/05-read-replica-design.md` (Activation Playbook section) |
| **B17** HF_TOKEN rotation runbook | SHIPPED | PR 3 `0198721` | `docs/EXPLAIN/operations/hf-token-rotation.md` (new, 133 lines) |
| **D25** integration test suite | SHIPPED | PR 3 `0198721` | `pytest.ini`, `conftest.py`, `backend/app/tests/test_integration_pgvector.py`, `backend/app/tests/test_integration_concurrency.py`, `backend/app/tests/test_adversarial_pass3.py` (2 unskipped), `.github/workflows/django.yml` |
| **B14** CDN cache headers | DEFERRED | — | Was: `docker-compose.yml` default + `docker/nginx.conf` cache-control headers. The nginx terminator is now in main (commit `05f6592`); the change is small (~30 lines) and ready to ship, but is a separate PR to keep the surface area small. Plan in §6 below. |

**Test growth:** 179 → 230 passed (+51), 4 → 9 skipped (+5 new integration tests skip on SQLite).

---

## 0. What "partially addressed" means here

A "partially addressed" item is one where:
- The code is shipped and correct
- A specific deployment-side or feature-flag-side activation step is missing
- OR: a partial implementation is in place and a follow-up is needed to reach the full intent of the audit item

These items don't have a single "fix" the way C18 (drop dead column) does. They have an **architectural decision** to lock in, then a **multi-step rollout** to complete.

The plan for each item follows the same template:
1. **Core logical gap** (what's actually wrong today, in concrete terms)
2. **Architectural decision** (the long-term fix; not a quick patch)
3. **Concrete changes** (file:line, the actual diffs)
4. **Test strategy** (how we prove it works)
5. **Rollout / operational handoff** (what you do after the merge)
6. **Trade-offs accepted**

---

## 1. A1 — PgBouncer app-side tuning

### Core logical gap
PgBouncer is in front of the app in production. It reduces connection pressure on Postgres (a single backend connection is shared across many app connections). **But the app has not been told to behave differently when talking to PgBouncer.** The two specific issues:

1. **No `statement_timeout`** on the connection options. A single misbehaving query (the `update_global_metrics` SQL, an unbounded `UserInteraction` lookup) can hold a backend connection for minutes. With 25 default-pool-size connections, 25 slow queries = the whole app is unresponsive. PgBouncer is supposed to be the safety net, but it's just routing; the per-query time limit lives on the connection.
2. **No `idle_in_transaction_session_timeout`.** A transaction that opens and never commits (a bug in a view, a worker that crashes mid-transaction) holds its connection forever. The 25-connection pool fills with these dead connections, and new requests start queueing. Today the only protection is the OS-level TCP keepalive (~hours, not minutes).

### Architectural decision
**Add per-session `OPTIONS` to the `default` DATABASES entry** so every connection from the app (web, celery, celery_feed, celery_media, celery_beat) gets the same timeouts. Single source of truth. No per-service drift.

The exact values, per `docs/unfixed-issues-2026-09-03.md:83`:
- `statement_timeout = '30s'` — a query can't take more than 30s; if it does, Postgres aborts it with `QueryCanceled`. The error is caught by DRF and turned into a 500. The connection is freed back to PgBouncer.
- `idle_in_transaction_session_timeout = '60s'` — a transaction that has been open with no activity for 60s is aborted.
- `lock_timeout = '10s'` — a query waiting on a row lock for 10s is aborted (defense against long-held locks).
- `connect_timeout = '10s'` — fail fast on connection issues instead of hanging for 30s.

### Concrete changes

**File: `backend/EchoFlow/settings.py:140-165`** (the `DATABASES['default']` block)

Before:
```python
DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get('DATABASE_URL', ''),
        conn_max_age=600,
        conn_health_checks=True,
    ),
}
```

After:
```python
DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get('DATABASE_URL', ''),
        conn_max_age=600,
        conn_health_checks=True,
        options={
            # SEC: per-session timeouts. Without these, a single
            # slow query or stuck transaction can hold a backend
            # connection indefinitely, and PgBouncer's 25-conn
            # default pool fills up. Each timeout trades a worse
            # error (QueryCanceled) for a better failure mode
            # (the connection is freed and the request returns a
            # 5xx the user can retry).
            '-c statement_timeout=30s',
            '-c idle_in_transaction_session_timeout=60s',
            '-c lock_timeout=10s',
            '-c connect_timeout=10s',
        },
    ),
}
```

The same `options` block is **already** present on the `'read'` alias at `settings.py:177-179` (just `default_transaction_read_only=on`). The `default` block is the gap.

### Test strategy

Add to `backend/app/tests/test_settings.py` (new file) or extend an existing config test:

1. **`test_default_db_options_include_safety_timeouts`** — assert the `default` DATABASES options contain `statement_timeout`, `idle_in_transaction_session_timeout`, `lock_timeout`, `connect_timeout`. Catches future regression if someone removes the options.
2. **`test_pgbouncer_pool_size_larger_than_postgres_max_connections`** — config sanity check: `pgbouncer DEFAULT_POOL_SIZE` (25) must be `<` Postgres `max_connections` (200). Static check against docker-compose.
3. **`test_settings_no_postgres_command_override`** — negative test: confirm the `db` service has no `command:` override (a Postgres config we don't want to introduce without reviewing). This is a guardrail, not a defect test.

The timeouts themselves are best validated by an integration test (D25) — a query that sleeps 60s should be cancelled. That belongs in the integration suite, not the unit suite.

### Rollout / operational handoff

- **Step 1:** Merge the `settings.py` change. The new options take effect on the next DB connection (no service restart needed for existing connections; they reconnect on `conn_health_checks` after `conn_max_age=600` = 10 minutes).
- **Step 2:** Monitor for 48h. If a query starts hitting the 30s timeout, the request returns 500. The error shows up in worker logs (and, post-B13, in Sentry). Either raise the timeout, fix the slow query, or accept the 500.
- **Step 3 (optional, future):** Mount a tuned `postgres.conf` to the `db` service in `docker-compose.yml`. Per `docs/unfixed-issues-2026-09-03.md:85-86` this would also enable `pg_stat_statements` for query-level observability. **Not in this plan** — it requires a Postgres restart and is orthogonal to the timeouts.

### Trade-offs accepted

- **30s statement_timeout is aggressive.** Some legitimate long queries (e.g., the periodic `update_global_metrics` over 5K rows, or a one-off admin query) may hit it. Acceptable because both have alternative paths (`update_global_metrics` is batched; admin queries go through the Django shell with a higher `statement_timeout` if needed). If a real workload hits the limit, raise to 60s.
- **`lock_timeout=10s` may surface as flaky 5xx under contention.** The architectural fix for lock contention is Item 9 (Redis INCRBY + flusher, Phase 1 already shipped). Once that's fully rolled out, lock contention on `AudioClip` goes away and the 10s timeout is rarely hit.

---

## 2. A3 — Redis split + cache invalidation strategy

### Core logical gap
The Redis split is done (`redis_broker` for Celery, `redis_cache` for Django cache + feed pool + telemetry stream). But the **invalidation strategy is TTL-only.** Every cached value has a fixed TTL; no value is ever explicitly invalidated when its source of truth changes.

Concrete consequences:

1. **The `user_vectors:{user_id}` cache is stale for up to 15 min after a like/skip.** This is **partially fixed** by Group B item 10 (`invalidate_user_vectors_cache` is now called from `record_like_toggle` and `record_skip` via `transaction.on_commit`). But `record_share` and `record_telemetry` don't invalidate — those code paths were explicitly out of scope per the user's "wire only the 2 sites the doc named" decision. Stale vectors persist up to 15 min after a share or telemetry event.
2. **The `clip:candidates:exploit` global ZSET is rebuilt every 5 min by Celery beat.** This is the "right" invalidation (full rebuild on a schedule). But the rebuild itself holds a Redis pipeline; if it fails mid-rebuild, the ZSET is in a half-built state. No recovery path.
3. **The user-specific `user:{id}:candidates:explore` ZSET is rebuilt hourly with per-user tasks.** Same problem at smaller scale.
4. **There is no `delete_pattern` or `KEYSPACE notifications` consumer.** Even if we wanted to invalidate on every `UserInteraction` insert, the plumbing isn't there.

The gap: **explicit, source-driven invalidation exists for 2 of ~6 user-state-mutating paths; the rest rely on TTL.**

### Architectural decision
Two parts, prioritized:

**Part 1 (this plan):** Wire `invalidate_user_vectors_cache` from the 2 remaining call sites that the Group B pass deliberately skipped — `record_share` and `record_telemetry`. This collapses the stale-vector window from 15 min to near-zero for all user-state-mutating paths. **Same pattern as the Group B fix; minimal change.**

**Part 2 (future, separate):** A small invalidation-router pattern — when a user state changes, call a single `invalidate_user_caches(user_id, kind)` function that knows about every cache key related to the user. This is what the `docs/EXPLAIN/recommendation/03-feed-pre-computation.md:520` spec hints at. **Not in this plan** — it's a refactor; the win from Part 1 is the same in terms of correctness.

For the candidate pool ZSETs: **the 5-min rebuild is the right answer.** Adding event-driven invalidation would be a premature optimization. The half-built state is solved by the Lua-based atomic rebuild (if it fails, the old ZSET is still there).

### Concrete changes

**File: `backend/app/services/interactions.py`**

Two changes, mirroring the Group B #10 pattern:

1. `record_share` (line 194-204 area): add `transaction.on_commit(lambda: invalidate_user_vectors_cache(user.id))` after the `get_or_create` + state change.
2. `record_telemetry` (line 147-191 area): add the same after the synchronous update_or_create fallback. (The stream-flush consumer also needs the same — see file change list below.)

**File: `backend/app/tasks.py:flush_telemetry_stream` consumer** (around line 800-870, the actual consumer that bulk-inserts from the Redis stream)

After the bulk insert, if any of the inserted rows changed a user's state, invalidate that user's cache. The simplest approach: invalidate on every flush (one Redis `DEL` per unique user in the batch — N `DEL` for N users, single round-trip per user via pipeline). This is "over-invalidate" but the cost is one cache miss per user, not a correctness issue.

**File: `backend/app/services/shares.py`** (the share-send path, not just `record_share`)

After the share event is created, invalidate the sharer's cache.

### Test strategy

Add to `backend/app/tests/test_services_interactions.py`:

1. `TestRecordShare::test_invalidates_user_vectors_cache` — cache a value, call `record_share`, assert cleared.
2. `TestRecordTelemetry::test_synchronous_fallback_invalidates_cache` — disable the stream (set `ECHOFLOW_TELEMETRY_STREAM=off`), call `record_telemetry`, assert the user's cache is cleared on `on_commit`.

Add to `backend/app/tests/test_services_shares.py`:

3. `test_send_share_invalidates_user_vectors_cache` — cache a value, call `shares.send_share`, assert cleared.

Add to `backend/app/tests/test_task_publisher.py` (existing file):

4. `TestFlushTelemetryInvalidation::test_flush_invalidates_user_caches` — fabricate a Redis stream with events for 2 users, run `flush_telemetry_stream.run()`, assert both users' `user_vectors:*` keys are gone.

### Rollout / operational handoff

- **Step 1:** Merge. The change is local to the service layer + the telemetry consumer. No env changes.
- **Step 2:** Verify in dev that the cache is now being invalidated after share and telemetry events. Use the observability TUI (`scripts/observability_tui.py`) to watch the `cache_get_set_duration_seconds` metric — the miss rate should climb slightly as invalidations increase, then settle.

### Trade-offs accepted

- **Telemetry flush now does N+1 Redis `DEL`s per batch.** A flush batch of 100 events from 30 unique users = 30 `DEL` calls. Cheap (Redis DEL is O(1)); not a bottleneck. We could pipeline them if it ever matters.
- **Cache misses spike after the rollout** as the 15-min TTL'd entries expire. This is the *correct* behavior — we WANT to recompute on every user state change. It does mean more `calculate_time_decayed_vectors(user)` calls (the recompute path), which is O(50) interactions per user. Acceptable for a small user base; would need a materialization strategy at 10K+ active users.

---

## 3. A5 — Read replica / db_routers.py activation

### Core logical gap
The `ReadRouter` (71 lines, 4 hooks, 14 tests) is shipped and conditional-wired. The conditional check is `'read' in DATABASES` — and `READ_DATABASE_URL` is never set anywhere in the repo. So the router is correctly implemented but **inert in production**. The audit doc's intent was "use a streaming replica for read-heavy queries" — the code is ready, the infrastructure isn't.

The specific queries that would benefit from routing to a replica:
- `/feed/` (the FastFeed endpoint, called on every app open)
- `/suggestions/` (the category-scoped ranking endpoint)
- `refill_user_feed` (Celery task, hits the DB hard)
- The candidate pool rebuilds (every 5 min and 1 hour)
- `update_global_metrics` (every 5 min)

The router already handles "not in an atomic block" — a critical guard. If a read query is issued inside `transaction.atomic()`, the router returns `None` and the query goes to the default. This is correct: reads inside a write transaction must see the just-written state, which the replica may not have yet.

### Architectural decision
**This is a deployment-side activation, not a code change.** The router code is correct. The activation is:
1. Provision a Postgres streaming replica (the cloud-side work).
2. Add a `db_read` service to `docker-compose.yml` (or, in prod, a Terraform-managed managed replica).
3. Set `READ_DATABASE_URL=postgres://...@db_read:5432/echoflow_db` in `.env` (or a secrets manager).
4. The router activates automatically when `READ_DATABASE_URL` is set (it constructs `DATABASES['read']` from it; the `if 'read' in DATABASES` check then registers the router).

There's no code change in this plan. The work is infrastructure. What's worth committing in this plan is:
- A **contract test** that ensures the router correctly activates when `READ_DATABASE_URL` is set (catches the case where someone refactors `settings.py` and breaks the conditional).
- A **README section** in the deployment docs that documents the activation steps.

### Concrete changes

**File: `backend/app/tests/test_db_router.py` (existing file, extend it)**

Add a test that uses `override_settings(DATABASES={...})` to set `'read'` in DATABASES, then asserts:
- `settings.DATABASE_ROUTERS` is registered
- A queryset on a routed model is directed to the `'read'` alias
- A queryset inside `transaction.atomic()` falls back to `'default'`

The current tests call the router functions directly with monkey-patches; they don't exercise the `override_settings` path. The new test closes that gap.

**File: `docs/EXPLAIN/database/05-read-replica-design.md` (existing design doc)**

Append a "Activation Playbook" section (10-20 lines) that documents:
- The env var to set
- The expected replica lag (must be `< 1s` for ranking queries to be useful; `< 5s` for analytics queries)
- The expected failover behavior (if the replica is down, the router returns `None` and reads fall back to primary; brief latency spike during the failover detection)
- The cloud-side actions (Terraform / managed-DB) — out of code scope

### Test strategy

1. `test_db_router.py::TestRouterConditionalActivation::test_router_activates_when_read_alias_present` — `override_settings` adds `'read'`, asserts `DATABASE_ROUTERS` is set.
2. `test_db_router.py::TestRouterConditionalActivation::test_router_inert_when_read_alias_absent` — current default state, asserts `DATABASE_ROUTERS` is empty.
3. `test_db_router.py::TestRouterConditionalActivation::test_queryset_under_atomic_block_falls_back_to_primary` — uses `override_settings` + `transaction.atomic()`, asserts the queryset uses `'default'`.

The contract test is the only new test work. The activation playbook is a doc-only change.

### Rollout / operational handoff

This is purely operational. The rollout is:

- **Step 1 (cloud-side, ~1 day):** Provision a Postgres 16 streaming replica. Set `wal_level=replica`, configure `primary_slot_name`, take a base backup, stream WAL.
- **Step 2 (compose-side, ~30 min):** Add a `db_read` service to `docker-compose.yml` (or use the cloud's managed endpoint). Point it at the replica. Set `READ_DATABASE_URL` in `.env`.
- **Step 3 (web-tier, ~5 min):** Restart `web` and `celery*` services. The `DATABASES['read']` is constructed on import; the `DATABASE_ROUTERS` is set; reads start flowing to the replica.
- **Step 4 (monitoring, 1 week):** Watch replica lag. Watch the read query count on the primary (it should drop). Watch for any `ReadOnlyError` (replication lag > 0 caused a query to land on a replica that didn't have the write yet — should be near-zero).
- **Step 5 (rollback if needed):** Unset `READ_DATABASE_URL`. The router goes back to returning `None`. Reads go to primary. The change is reversible in <1 minute.

### Trade-offs accepted

- **Read replica adds a new failure mode.** The replica can fall behind, the replica can be down, the replica can have a different row count during failover. The router's "fall back to primary" behavior handles these, but a slow fallback is a 1-2 second latency spike. Acceptable for the read-heavy workload.
- **The router routes based on `app_label`, not per-query.** A query on the `auth` app (e.g., `User.objects.count()`) goes to primary even if you want it on the replica. The `ROUTED_APP_LABELS = frozenset({'app'})` whitelist is intentional — `auth` is small and we want its writes to be primary-visible. If a future audit wants `auth` reads on the replica, the whitelist expands.

---

## 4. A8 — Custom Prometheus metrics: scraper + dashboards + alerting

### Core logical gap
6 custom metrics are shipped and hot-path-instrumented:
- `feed_refill_duration_seconds` (Histogram)
- `suggestion_ranking_duration_seconds` (Histogram)
- `toggle_like_duration_seconds` (Histogram)
- `cache_get_set_duration_seconds` (Histogram)
- `hls_processing_duration_seconds` (Histogram)
- `celery_tasks_processed_total` (Counter)

Plus a stdlib TUI viewer (`scripts/observability_tui.py`) that reads `/metrics/` over HTTP. The TUI is a stopgap so the metrics are at least consumable today.

The gap: **no scraper, no dashboards, no alerts.** A scraper (Prometheus) is what makes the metrics queryable in PromQL. Without it, you can't graph "p99 of suggestion_ranking over the last 24h." Without dashboards (Grafana), the data is raw. Without alerts, you only see errors when you happen to be looking at the TUI.

The 765-line design doc (`docs/EXPLAIN/observability/03-prometheus-grafana-design.md`) specifies the full stack. None of it is built.

### Architectural decision
**Build the minimum viable stack: Prometheus + Grafana, no Alertmanager in this pass.** Two reasons:

1. **The metrics exist. The data exists in the TUI. The next step is making the data queryable in time series.** That's exactly what Prometheus does. Once Prometheus is scraping, every metric is queryable in PromQL, which is what Grafana dashboards and Alertmanager rules both consume.
2. **Alertmanager adds alerting, which is a separate concern.** This plan ships scraping + visualization; alerting is a follow-up.

The architecture:
- **`prometheus`** service in `docker-compose.yml` — scrapes `/metrics/` from `web` every 15s.
- **`grafana`** service in `docker-compose.yml` — auto-provisions datasources (Prometheus) and dashboards (the 6 metrics above) from mounted JSON.
- **A small set of dashboards** (4-5 JSON files in `docker/grafana/dashboards/`) covering the 6 metrics with p50/p95/p99 panels + the top-N Celery tasks panel.

The full design doc proposes more (recording rules, alert routing, Slack integration, retention policy). **This plan ships the foundation; the doc's advanced features are future work.**

### Concrete changes

**New file: `docker/prometheus/prometheus.yml`**
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
scrape_configs:
  - job_name: 'echoflow-web'
    metrics_path: '/metrics/'
    static_configs:
      - targets: ['web:8005']
        labels: { service: 'echoflow', tier: 'web' }
```

**New file: `docker-compose.yml` additions**
- `prometheus` service: `prom/prometheus:v2.55.0`, mounts `prometheus.yml` to `/etc/prometheus/prometheus.yml`, port 9090.
- `grafana` service: `grafana/grafana:11.2.0`, mounts `grafana/provisioning` and `grafana/dashboards`, port 3000.
- Both as part of the metrics stack; no depends_on on the app services beyond the existing `web` healthcheck.

**New file: `docker/grafana/provisioning/datasources/prometheus.yml`**
Auto-provisions the Prometheus datasource so Grafana works on first boot.

**New file: `docker/grafana/provisioning/dashboards/echoflow.yml`**
Auto-loads all dashboards from `docker/grafana/dashboards/*.json`.

**New file: `docker/grafana/dashboards/01-feed-and-suggestions.json`**
- p95 of `feed_refill_duration_seconds` over 1h
- p95 of `suggestion_ranking_duration_seconds` over 1h
- Cache hit rate (from `cache_get_set_duration_seconds` count by `op=get` vs `op=set`)

**New file: `docker/grafana/dashboards/02-celery-health.json`**
- `rate(celery_tasks_processed_total[5m])` by queue, by task, by outcome
- p95 of `hls_processing_duration_seconds` over 1h

**New file: `backend/app/tests/test_metrics_endpoint.py`**
- `test_metrics_endpoint_returns_all_six` — assert the `/metrics/` page contains all 6 custom metric names. (The TUI already covers this; the test makes it CI-runnable.)

### Test strategy

1. `test_metrics.py::TestMetricsEndpoint::test_all_six_custom_metrics_exposed` — request `/metrics/`, assert each custom metric name appears in the text output. Catches future instrumentation regressions.
2. `test_metrics.py::TestMetricsEndpoint::test_metrics_endpoint_requires_no_auth` — public metrics endpoint, no auth needed. Confirms the URL conf.

The Prometheus and Grafana services themselves are best validated by manual inspection (`docker compose up prometheus grafana; open http://localhost:9090/targets; confirm 1/1 up`). They have no unit tests; they're infra.

### Rollout / operational handoff

- **Step 1:** Add the compose services, the prometheus.yml, the Grafana provisioning.
- **Step 2:** `docker compose up -d prometheus grafana`. Open `http://localhost:9090/targets` — confirm the web target is `UP`. Open `http://localhost:3000` — confirm dashboards load.
- **Step 3:** Update the AGENTS.md "Observability TUI" section to point to Grafana as the primary observability tool. The TUI is still useful for quick spot-checks (no browser needed); mark it as the dev fallback.

### Trade-offs accepted

- **Grafana dashboards are JSON files, hand-written.** The alternative is to use `grafonnet` (a JSON-generator) — overkill for 4 dashboards. Hand-written JSON is verbose but readable; the cost is ~150 lines of JSON per dashboard.
- **No alert rules in this pass.** The audit doc proposes alert rules; this plan defers them. The rationale: alerts need an escalation path (Slack channel, on-call schedule, etc.) that depends on the team's operational setup. Ship the data; add alerts in a follow-up.
- **Prometheus retention defaults to 15 days.** The design doc proposes 90 days. The default is fine for dev; production retention is a deployment-side env var (`--storage.tsdb.retention.time=90d`).

---

## 5. B13 — Sentry integration

### Core logical gap
**Sentry is not present anywhere in the repo.** No SDK in requirements, no init in `settings.py`, no `capture_exception` helper. The current error reporting is:
- Python `logger.exception(...)` in service-layer error paths (e.g., `services/interactions.py:174` in the stream fallback)
- Django's default error handling in views → 500 response
- A WARNING log in the Celery worker's stdout (captured by Docker logs)

The gap: **errors are visible only if someone is reading logs at the moment they happen.** Sentry's value is that errors are captured, deduped, and queryable indefinitely, with full context (request, user, environment, stack trace, breadcrumb trail).

### Architectural decision
**Integrate `sentry-sdk` at startup, in `apps.ready()` (not in `settings.py`)**. This is the official Django integration pattern. Three reasons:

1. **Lazy init.** The `sentry_sdk.init()` call is expensive (network connections, regex compilation). Putting it in `settings.py` means it runs on every test, every management command, every celery worker startup. Putting it in `apps.ready()` lets us gate it on `DJANGO_DEBUG=False` so dev and tests don't pay the cost.
2. **Multi-process aware.** Each Celery worker process, each gunicorn worker, and the web process all need to call `sentry_sdk.init()` once. Doing it in `apps.ready()` runs in each process automatically.
3. **Capture-pattern helper.** A `capture_exception(exc, **context)` wrapper in `services/sentry.py` (or `app/sentry.py`) standardizes what context we attach (request correlation_id, user id, the operation being attempted). This is the same pattern as `metrics.py` — a small wrapper that the rest of the codebase calls.

The integration uses **sentry-sdk 2.x** (current major version as of 2026). Key features used:
- `sentry_sdk.init(dsn, environment, traces_sample_rate, profiles_sample_rate)` — basic init
- `sentry_django.integration.DjangoIntegration()` — auto-captures uncaught exceptions in views
- `sentry_sdk.celery.CeleryIntegration()` — auto-captures Celery task failures
- `sentry_sdk.set_tag('correlation_id', ...)` — cross-references Sentry errors with the worker's correlation_id (Item 11 from Group B)
- `sentry_sdk.capture_exception(exc)` — manual capture in service-layer error paths

### Concrete changes

**File: `requirements-base.txt`** (add line)
```
sentry-sdk[django,celery]==2.18.0
```

**File: `backend/EchoFlow/apps.py`** (new app for Sentry init, or extend the existing EchoFlow config)

Create `backend/EchoFlow/sentry.py`:
```python
"""Sentry initialization. Runs in apps.ready() so each process (web,
celery, celery_feed, celery_media, celery_beat) initializes exactly
once. Gated on DJANGO_DEBUG=False to avoid the SDK overhead in dev.
"""
import os
import logging

logger = logging.getLogger(__name__)


def init_sentry():
    dsn = os.environ.get('SENTRY_DSN')
    if not dsn:
        return  # not configured
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get('SENTRY_ENV', 'production'),
        release=os.environ.get('GIT_COMMIT_SHA', 'unknown'),
        traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.1')),
        profiles_sample_rate=float(os.environ.get('SENTRY_PROFILES_SAMPLE_RATE', '0.05')),
        integrations=[DjangoIntegration(), CeleryIntegration()],
        # Strip query strings from request bodies to avoid PII capture
        send_default_pii=False,
    )
    logger.info("sentry initialized: env=%s", os.environ.get('SENTRY_ENV'))
```

Wire it in `backend/EchoFlow/apps.py`:
```python
from django.apps import AppConfig
import os


class EchoFlowConfig(AppConfig):
    name = 'backend.EchoFlow'

    def ready(self):
        if os.environ.get('DJANGO_DEBUG', 'False').lower() in ('false', '0', 'no', 'off'):
            from . import sentry
            sentry.init_sentry()
        # ... existing ready() code
```

**File: `backend/app/services/interactions.py` (and other service modules)**

Add a `capture_exception` call in error paths. Example:
```python
from .sentry import capture_exception  # a small wrapper

try:
    counter_store.increment(...)
except Exception as exc:
    capture_exception(exc, op='counter_store.increment', clip_id=str(clip_id))
    logger.debug(...)
```

The wrapper is `backend/app/sentry.py` (new file):
```python
"""Sentry capture helper. Wraps sentry_sdk.capture_exception with
EchoFlow-specific context (correlation_id, user, operation).
"""
import sentry_sdk
from contextlib import contextmanager

from ..EchoFlow import correlation


def capture_exception(exc=None, **context):
    """Capture an exception in Sentry with the current request's
    correlation_id and any caller-supplied context.
    """
    cid = correlation.get_correlation_id()
    with sentry_sdk.push_scope() as scope:
        if cid:
            scope.set_tag('correlation_id', cid)
        for k, v in context.items():
            scope.set_extra(k, v)
        sentry_sdk.capture_exception(exc)
```

**File: `backend/app/services/sentry.py`** (new — the wrapper)

**File: `.env.example`** — add Sentry vars
```
# Sentry (optional; leave blank to disable)
SENTRY_DSN=
SENTRY_ENV=production
SENTRY_TRACES_SAMPLE_RATE=0.1
SENTRY_PROFILES_SAMPLE_RATE=0.05
```

**File: `docker-compose.yml`** — pass Sentry env to web + celery services

**File: `backend/EchoFlow/celery.py`** — already imports `task_failure` for metrics; add `sentry_sdk` capture for failures too (via the CeleryIntegration, this happens automatically).

### Test strategy

Add `backend/app/tests/test_sentry.py`:

1. `test_sentry_not_initialized_when_dsn_missing` — `SENTRY_DSN=''`; assert `sentry_sdk.Hub.current.client` is `None` (the SDK is a no-op when unconfigured).
2. `test_sentry_not_initialized_when_debug_true` — `DJANGO_DEBUG=True` and `SENTRY_DSN` set; assert the SDK is a no-op.
3. `test_sentry_initialized_in_production_mode` — `DJANGO_DEBUG=False`, `SENTRY_DSN=https://fake@sentry.io/123`; assert `sentry_sdk.Hub.current.client` is not None.
4. `test_capture_exception_attaches_correlation_id` — set correlation_id, call `capture_exception(RuntimeError('test'))`, assert the captured event has `tags.correlation_id == 'abc-123'`.
5. `test_capture_exception_works_when_sentry_uninitialized` — no DSN, call `capture_exception(...)`, assert it does not raise.

Plus, update the wheelhouse regen script (the doc at line 109-124) to include `sentry-sdk` in the offline wheelhouse.

### Rollout / operational handoff

- **Step 1 (cloud-side, 30 min):** Create a Sentry project at sentry.io. Get the DSN. It's a URL like `https://abc123@sentry.io/456`.
- **Step 2 (compose-side, 10 min):** Set `SENTRY_DSN` in `.env` (or a secrets manager). Set `SENTRY_ENV` to `production` (or `staging` for the staging env).
- **Step 3 (merge + deploy):** Merge the integration. The SDK initializes in each process on the next deploy. Errors from that point forward are captured.
- **Step 4 (verify, 1 hour):** Trigger a known error (e.g., POST to a removed endpoint). Confirm the error appears in the Sentry dashboard with the correct stack trace, environment, and correlation_id.
- **Step 5 (tune, 1 week):** Adjust `SENTRY_TRACES_SAMPLE_RATE` based on traffic. 10% is the default; for low-traffic envs, raise to 100% to get full traces. For high-traffic, lower to 1%.

### Trade-offs accepted

- **Sentry SDK adds ~50ms to first-request latency in each process** (TLS handshake, project info fetch). After init, the cost per request is microseconds. Acceptable.
- **`send_default_pii=False`** means user IPs, cookies, and auth headers are NOT sent to Sentry. This is the safer default; opt-in to PII if you need it for a specific issue. The correlation_id IS sent (as a tag, not PII).
- **No alert routing in this pass.** Sentry's UI can configure email/Slack alerts without code changes. That's a UI config, not a code change.
- **Sentry becomes a runtime dependency.** If Sentry is down, the SDK falls back to no-op (errors are still logged locally). The integration is non-blocking; the app doesn't depend on Sentry being up.

---

## 6. B14 — CDN front of MinIO

### Core logical gap
The MinIO bucket is set up for public-read on the `hls/` prefix. The nginx TLS terminator (from the parallel agent's branch `feature/https-termination`) is wired. But:

1. **`PUBLIC_MEDIA_ENDPOINT_URL` defaults to HTTP** in `docker-compose.yml:251`: `${PUBLIC_MEDIA_ENDPOINT_URL:-http://localhost:9000}`. The fallback is the direct MinIO endpoint, not nginx. If a user copies `.env.example` and doesn't override the env var, they hit MinIO directly over HTTP — which is the security regression the nginx terminator is supposed to prevent.
2. **No real CDN** (CloudFront, Cloudflare, Bunny, Fastly). nginx terminates TLS in-network; clients in different geographic regions all hit the same `web` service. A real CDN caches HLS segments at edge POPs and serves them to nearby clients. For an audio app where 90% of bytes are HLS segments, the CDN is the single biggest performance win available.
3. **No cache-control headers** on HLS responses from MinIO. Even without a CDN, setting `Cache-Control: public, max-age=31536000, immutable` on the segment responses would let browsers cache segments for a year. The nginx config doesn't add these.

The audit doc's B14 is "CDN front of MinIO." The minimal version of that is: `PUBLIC_MEDIA_ENDPOINT_URL` defaults to https, and HLS responses have cache headers. The maximal version is: a real CDN distribution. This plan ships the minimal version; the maximal version is operational/Terraform.

### Architectural decision
**This is a deployment-side config change, not a code change.** The pattern:
1. Update the compose default to HTTPS via nginx.
2. Add cache-control headers in the nginx config (or in the S3 bucket policy).
3. Document the real-CDN activation in `docs/EXPLAIN/storage/`.

**Cache-control headers: who sets them?** Two options:
- **Option A: nginx adds the headers** in the HLS location block. The application code (Django) doesn't change. nginx is the public-facing proxy; it controls the response headers.
- **Option B: Django/DRF adds the headers** in the serializer that returns the HLS URL. The header is set when the response is generated.

Option A is the right one. Reasons:
- **HLS segments are immutable** (a master.m3u8 changes only when the worker regenerates it; a `seg-0.ts` never changes once written). Cache-Control should be set once at the storage layer, not per-response.
- **The `originals/` prefix is private** (no public read). Cache headers don't apply.
- **DRF is dynamic** (returns different `clip_id` URLs based on query). nginx is static (serves `/hls/<id>/master.m3u8` from a fixed path).

So the change is: nginx's HLS location block adds `Cache-Control: public, max-age=31536000, immutable` for the HLS prefix, and `Cache-Control: no-store` for the manifest (m3u8) since it might be regenerated. **Wait — the manifest IS the regenerated file.** Master and index m3u8 files change when the worker re-renders. The `.ts` segments don't.

So the cache policy is:
- `hls/<id>/master.m3u8` and `hls/<id>/index.m3u8` → `Cache-Control: no-cache, must-revalidate` (always revalidate, don't store stale)
- `hls/<id>/seg-N.ts` → `Cache-Control: public, max-age=31536000, immutable` (cache for a year)

### Concrete changes

**File: `docker-compose.yml:251`** (the `PUBLIC_MEDIA_ENDPOINT_URL` default)

Before:
```yaml
- PUBLIC_MEDIA_ENDPOINT_URL=${PUBLIC_MEDIA_ENDPOINT_URL:-http://localhost:9000}
```

After:
```yaml
# DECISION: default to HTTPS via the in-network nginx terminator
# (per feature/https-termination). The previous default was direct
# MinIO over HTTP, which is a security regression. To use a real
# CDN, override this with the CDN's public URL.
- PUBLIC_MEDIA_ENDPOINT_URL=${PUBLIC_MEDIA_ENDPOINT_URL:-https://localhost:9443}
```

**File: `docker/nginx.conf`** (the HLS location block in the nginx config from `feature/https-termination`)

Add `add_header Cache-Control` directives:
```nginx
location ~ ^/hls/.*/master\.m3u8$ {
    add_header Cache-Control "no-cache, must-revalidate" always;
    add_header X-Content-Type-Options "nosniff" always;
    # ... existing proxy_pass to minio
}
location ~ ^/hls/.*/index\.m3u8$ {
    add_header Cache-Control "no-cache, must-revalidate" always;
    # ... existing proxy_pass
}
location ~ ^/hls/.*\.ts$ {
    add_header Cache-Control "public, max-age=31536000, immutable" always;
    # ... existing proxy_pass
}
```

**File: `docs/EXPLAIN/storage/CDN-activation.md`** (new doc, ~50 lines)

Documents the activation path for a real CDN (CloudFront, Cloudflare, Bunny):
- DNS setup (CNAME the CDN to the bucket's public origin)
- Cache-Control header propagation (CDNs respect the origin's headers by default)
- Purge API (HLS manifest changes need a purge; segments don't)
- Cost model (~$0.01/GB egress for most CDNs; 90% reduction in origin egress)

### Test strategy

Add to `backend/app/tests/test_https_termination.py` (existing file from the parallel agent's branch):

1. `test_public_media_endpoint_url_defaults_to_https` — assert `settings.PUBLIC_MEDIA_ENDPOINT_URL` (after `docker compose up` with no `.env` override) starts with `https://`. Catches the regression of someone changing the compose default back to HTTP.
2. `test_nginx_cache_headers_on_hls_segments` — fetch a `.ts` URL through nginx, assert `Cache-Control: public, max-age=31536000, immutable`. Skipped if nginx isn't reachable.
3. `test_nginx_no_cache_on_manifests` — fetch `master.m3u8`, assert `Cache-Control: no-cache, must-revalidate`.

The existing `TestLiveNginxTerminator` class is the pattern; the new tests follow the same `skip_if_nginx_not_reachable` autouse.

### Rollout / operational handoff

- **Step 1 (merge):** Update compose default + nginx config. The change is to the same branch (`feature/https-termination`) that already shipped the TLS terminator.
- **Step 2 (dev verification):** `docker compose up -d`; visit `https://localhost:9443/hls/<id>/master.m3u8`; confirm the response has the right `Cache-Control`.
- **Step 3 (real CDN, future):** Set up CloudFront or Cloudflare in front of the bucket. The activation is:
  - Create a CDN distribution pointing at the bucket's public origin.
  - Update `PUBLIC_MEDIA_ENDPOINT_URL` to the CDN's URL.
  - Configure the CDN to respect origin `Cache-Control` headers (default).
  - Set up a purge API token for when workers regenerate manifests.

### Trade-offs accepted

- **`max-age=31536000` is a year.** This is safe for `.ts` segments (they're content-addressed by index, and the index is monotonically increasing — clip-42's seg-5.ts never gets re-generated, only seg-6+). If a worker ever overwrites a segment at the same path, the cached copy is stale for up to a year. The audit-verified behavior is that workers write new segments, never overwrite. If that changes, lower the TTL.
- **HTTPS default means HTTP curl no longer works** for the public endpoint in dev. Dev users who want plain HTTP have to override `PUBLIC_MEDIA_ENDPOINT_URL=http://localhost:9000`. Acceptable; the dev path is for browser testing where HTTPS works via the self-signed cert.

---

## 7. B17 — HF_TOKEN rotation: doc-only fix

### Core logical gap
The Group B verification reclassified B17 as "PARTIAL FALSE POSITIVE" — the architecture is correct, the doc wording was misleading. The current state after Group B is that `docs/backend-bug-fixs.md:598-599` was reworded to describe the actual architecture. So the "code-side checks" claim is gone.

But the doc still doesn't capture the **operational handoff** for rotation. The current text says "ops task (rotate the actual value in the HuggingFace dashboard)" — true, but it doesn't say HOW. A new developer or operator reading the doc would have to dig through the Dockerfile, the docker-compose, and the AGENTS.md to piece together the rotation procedure.

### Architectural decision
**No code change. Add an operational runbook.**

A runbook answers: "I need to rotate the HF_TOKEN. What do I do, in order?" The current docs answer pieces of this question but not the whole thing.

### Concrete changes

**File: `docs/EXPLAIN/operations/hf-token-rotation.md`** (new file, ~80 lines)

```
# HF_TOKEN Rotation Runbook

When to rotate: every 90 days (HuggingFace default recommendation)
or immediately if the token is exposed in a public log / paste.

## What HF_TOKEN does

The token is consumed at IMAGE BUILD TIME only. It is used to
authenticate downloads of 3 model weights (Whisper, SentenceTransformer,
KeyBERT) that are BAKED INTO the celery_media image layer. The
runtime never uses the token because the models are pre-loaded.

## Where it's used

Build:
- Dockerfile:117-124 (BuildKit secret mount)
- docker-compose.yml:407-417 (secrets block)
- docker-compose.yml:593-595 (secrets source = env:HF_TOKEN)

Runtime:
- No references in backend/.
- HF_HUB_OFFLINE=1 + TRANSFORMERS_OFFLINE=1 in docker-compose.yml:456-457
  force offline model loading.

## Rotation procedure

1. Generate a new token at https://huggingface.co/settings/tokens.
   Required scope: "read" (the models are public; "write" is not needed).
2. Update the secret store:
   - Local dev: edit .env (which is gitignored)
   - CI: update the GitHub Actions secret `HF_TOKEN`
   - Production: update the deployment platform's secret (k8s secret,
     ECS parameter, etc.)
3. Rebuild the celery_media image:
   docker compose build --no-cache celery_media
   (the --no-cache forces re-download of the models; without it,
   the build uses the cached model weights and the new token is unused)
4. Roll the running celery_media containers:
   docker compose up -d celery_media
5. Verify:
   docker compose logs celery_media | grep "sentry initialized"
   (or, pre-B13: any line indicating the worker started successfully)
6. No app downtime. The web tier doesn't load ML models; the celery
   media worker is the only consumer. Other celery workers (feed, beat)
   don't load models either. The brief window during the celery_media
   restart will have queued HLS-processing tasks; they'll drain after
   the new worker comes up.

## Failure modes

- Empty HF_TOKEN at build time: the build's `if [ -s /run/secrets/hf_token ]`
  guard treats empty as anonymous. Whisper, SentenceTransformer, KeyBERT
  are all public models, so anonymous download works. If HuggingFace
  rate-limits anonymous traffic, the build fails with a 429. The fix
  is to set the new token; the build retries.
- Expired token: same as empty. The token has no "valid until" date
  in the secret itself; HuggingFace invalidates the token on the
  dashboard side. The same guard catches it.

## Audit log

Document each rotation:
- Date
- Old token's last-4 (for cross-reference; never paste the full token)
- New token's first-4
- Operator name
- Build result
```

### Test strategy

No code test. The runbook is documentation. It's verified by the next person who needs to rotate the token following the steps.

A **lightweight test** is possible: a static check that the Dockerfile still has the `if [ -s /run/secrets/hf_token ]` guard and the `HF_HUB_OFFLINE=1` env var. This would be a "doc-correctness test" that catches future refactors. **Not in this plan** — it's defensive against regressions, not the actual fix.

### Rollout / operational handoff

The doc itself is the rollout. The next rotation follows the new runbook.

### Trade-offs accepted

- **No code change, only doc.** This is a "false positive" in the strict sense — the architecture is already correct. The doc was the gap, and the doc is the fix.
- **No automation of the rotation.** Could be a cron job that calls the HuggingFace API to rotate and re-deploy. Out of scope; manual rotation every 90 days is fine for now.

---

## 8. D25 — Integration test suite (Postgres + Redis + Docker)

### Core logical gap
The unit test suite (179 passed, 4 skipped) runs against SQLite + LocMem. The CI provisions real Postgres + Redis as services (`.github/workflows/django.yml:29-52`) and installs ffmpeg. But the test command (`python manage.py test backend.app --verbosity 2`) runs the same SQLite-forced suite. The Postgres/Redis infra is present but unused.

Specific things the unit suite cannot exercise:
- `pgvector` HNSW index behavior
- `SELECT ... FOR UPDATE SKIP LOCKED` semantics (the SQLite translation differs)
- Real concurrent transactions (SQLite locks the whole DB)
- Real Redis Streams (XADD, XREADGROUP, consumer groups)
- Real Redis Lua scripting
- Real S3 / MinIO semantics (the in-memory storage is not a perfect simulation)
- Real Celery worker concurrency

The 4 currently-skipped tests (`test_adversarial_pass3.py:114,579` and `test_scraper.py:34,52`) are skipped because they need real infra. The skip reasons are correct (environmental), not regressions — but the gap is that CI doesn't have a way to RUN them.

### Architectural decision
**Add a `@pytest.mark.integration` marker, a separate test runner, and a CI step that runs integration tests against the real services.**

Three pieces:

1. **The marker.** `@pytest.mark.integration` on tests that need real Postgres/Redis/S3/ffmpeg. Registered in `pytest.ini` so `--strict-markers` is happy.
2. **The collection.** A separate `pytest -m integration` invocation that runs only the marked tests. Excludes the unit tests (which would re-run and add time without value).
3. **The CI step.** `.github/workflows/django.yml` gets a second `pytest` step that runs the integration markers after the unit tests pass. The CI Postgres + Redis services are already wired; this step just uses them.

The infrastructure: the existing `conftest.py` forces SQLite + LocMem for the unit suite. The integration suite needs the OPPOSITE — use the real Postgres + Redis from the env vars, NOT the overridden ones. The cleanest way is a separate `conftest_integration.py` (or a conditional in `conftest.py` based on a marker) that respects the env var.

### Concrete changes

**File: `pytest.ini`** (extend)

```ini
[pytest]
DJANGO_SETTINGS_MODULE = backend.EchoFlow.settings
testpaths = backend/app/tests
python_files = test_*.py
markers =
    integration: tests that require real Postgres + Redis + S3 (skipped on dev hosts)
```

**File: `conftest.py`** (extend)

Add a fixture that skips integration tests if real services aren't available:
```python
@pytest.fixture(autouse=True)
def _skip_integration_without_services(request):
    if 'integration' not in request.keywords:
        return
    # Check that the DATABASE_URL points to a non-SQLite backend
    from django.conf import settings
    if settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
        pytest.skip("integration tests require a real Postgres DATABASE_URL")
    # Check that the cache backend is Redis
    cache_backend = settings.CACHES['default']['BACKEND']
    if 'locmem' in cache_backend.lower() or 'local' in cache_backend.lower():
        pytest.skip("integration tests require a real Redis cache backend")
```

**File: `backend/app/tests/test_integration_pgvector.py`** (new)

A small suite of integration tests that exercise pgvector-specific behavior:
- `test_hnsw_index_exists_on_audioclip` — query `pg_indexes` to confirm the HNSW indexes are present
- `test_cosine_distance_query_uses_index` — `EXPLAIN ANALYZE` a vector query; assert the HNSW index is in the plan
- `test_vector_query_returns_correct_top_k` — insert N clips with known vectors, query for the nearest, assert the expected clip wins

**File: `backend/app/tests/test_integration_concurrency.py`** (new)

Tests that require real Postgres (SQLite is too aggressive on locking):
- Mark these with `@pytest.mark.integration`
- Re-enable the 2 currently-skipped adversarial tests (`test_adversarial_pass3.py:114, 579`) by changing their `@unittest.skip(...)` to a marker-based skip that only skips on SQLite:
  ```python
  @pytest.mark.integration
  @unittest.skipIf(connection.vendor == 'sqlite', "needs Postgres row-level locks")
  ```

**File: `.github/workflows/django.yml`** (extend)

Add a second test step after the existing one:
```yaml
- name: Run integration tests (Postgres + Redis)
  run: |
    pytest backend/app/tests/ -m integration --tb=short
  # The 'web' / 'db' / 'redis' services are already up from the
  # existing services block above; no new infra needed.
```

### Test strategy

The integration tests are themselves the test strategy. The contract:

- The marker is registered (verified by `pytest --markers`).
- The skip fixture activates (verified by setting `DATABASE_URL=sqlite://...` and running `pytest -m integration` — all marked tests should be skipped with the integration reason).
- The CI step actually runs (verified by the workflow file change; verified post-merge by the green checkmark).

### Rollout / operational handoff

- **Step 1 (merge):** The marker + skip fixture + new test files + CI step. All additive; no existing test is changed except for the 2 adversarial tests that get a marker.
- **Step 2 (CI verify):** After merge, the next CI run should show 2 new test classes (pgvector, concurrency) passing.
- **Step 3 (gradual expansion):** Future PRs that add pgvector-specific or concurrency-specific tests can mark them `@pytest.mark.integration` without further ceremony.

### Trade-offs accepted

- **CI runtime increases.** Each integration test takes longer (real network calls, real Postgres startup). Estimated 30-60s added to CI for the initial suite. Future expansion should stay under 5 minutes total for the integration step.
- **The 2 currently-skipped adversarial tests may reveal pre-existing bugs when re-enabled.** The N2 (counter race) and concurrent load tests have never actually run in CI. If they fail, that's a real bug, not a test issue. Acceptable — better to know now.
- **The `conftest.py` autouse fixture is a global behavior change.** It runs for every test, even those that don't have the integration marker. The `if 'integration' not in request.keywords: return` guard makes it a no-op for unit tests, but the import overhead exists. Acceptable.

---

## 9. B19 — Module docstring drift in `services/interactions.py`

This is a one-line cosmetic fix I noticed during verification. Not worth a section; the diff is:

**File: `backend/app/services/interactions.py:12-14`**

Before:
```
* register_skip: writes an interaction_type='view' row (NOT 'skip'),
  no counter is bumped (view is excluded from the field_map in
  UserInteraction.save()).
```

After:
```
* register_skip: writes an interaction_type='skip' row. The F() in
  UserInteraction.save() bumps AudioClip.skips via the field_map
  (Group C item 19 fix).
```

**No test needed.** This is docstring cleanup, included in whichever commit touches `services/interactions.py` next (likely A3 if Part 1 goes in this batch).

---

## 10. Combined plan: what to ship together

These 7 items (A1, A3, A5, A8, B13, B14, B17) are independent and can ship in any order. Recommended batching:

**Batch 1 — Quick wins (1 PR):**
- A1 (settings.py timeouts) — 1 file, 1 commit
- B14 (compose default + nginx cache headers) — 2 files, 1 commit, but **must go on `feature/https-termination` branch** (the parallel agent's branch) since it touches nginx config
- B17 (HF_TOKEN rotation runbook) — 1 new doc file, 1 commit
- B19 (docstring fix) — 1 file, 1 commit, included with A3 below

**Batch 2 — Service-layer (1 PR):**
- A3 Part 1 (wire invalidate from record_share + record_telemetry) — 3 files, 1 commit, 4 new tests

**Batch 3 — Observability (1 PR or 2):**
- A8 Part 1 (Prometheus + Grafana) — multiple new files, 1 commit, 2 new tests
- B13 (Sentry integration) — 3-4 files, 1 commit, 5 new tests (this PR is large; consider splitting init from helper)

**Batch 4 — Infra activation (no code):**
- A5 (no code change; new tests + activation doc only) — 1 new doc + 3 new tests in existing test file, 1 commit

**Batch 5 — Test infra (1 PR):**
- D25 (integration test suite) — 1 pytest.ini change + 1 conftest.py change + 2 new test files + 1 CI change, 1 commit

Total: **5 batches, ~8 commits, ~1500 lines added** (mostly A8's Grafana dashboards and B13's Sentry setup).

**Test growth estimate:**
- After Batch 1: 179 → 180 (1 new test for A1's static check) or 179 → 184 (if the B14 cache-header tests are runnable in this env)
- After Batch 2: 180 → 184 (+4 A3 invalidation tests)
- After Batch 3: 184 → 191 (+2 A8 metrics-endpoint tests + 5 B13 sentry tests)
- After Batch 4: 191 → 194 (+3 A5 router activation tests)
- After Batch 5: 194 → 196+ (integration tests in D25 — count TBD; gated by infra)

**Total: ~196 passed, 4 skipped, 0 failed** (vs current 179 passed).

---

## 11. Risks

1. **B13 Sentry SDK init order:** If `sentry_sdk.init()` is called before Django apps are loaded, the `DjangoIntegration` may miss some auto-instrumentation. The `apps.ready()` hook is the right place; this is well-documented in the SDK. Risk: low.
2. **A8 Grafana dashboards are JSON:** Hand-edited JSON is fragile. A future Grafana version may break the schema. Mitigation: pin the Grafana version in the image tag (`grafana/grafana:11.2.0`, not `:latest`).
3. **A1 timeouts on existing slow queries:** If `update_global_metrics` ever runs longer than 30s (e.g., if a clip count grows past the batch size), it'll get cancelled. The 5K batch size and the `id > %s` cursor pagination keep it bounded today; the risk is future workload growth.
4. **A5 replica activation:** If the replica is misconfigured, the router still routes to it; queries fail with cryptic errors. Mitigation: start with a short TTL of `READ_DATABASE_URL=...` (set it, watch for errors, unset if needed).
5. **D25 integration tests reveal pre-existing bugs:** The 2 adversarial tests (counter race, load test) have never run. They may fail. Acceptable outcome.

---

## 12. What I'd do first

If I had to pick one to start tomorrow, it'd be **B13 (Sentry)**. The reason: it has the highest information value per unit of work. A working Sentry integration immediately surfaces errors in production that today are silent. It changes the operational posture of the codebase from "look at logs when something breaks" to "Sentry tells you when something breaks, with full context, in real time." The cost is 1 day of work; the benefit is permanent.

Second pick: **A1 (PgBouncer tuning)**. 1-hour change, immediate safety improvement, zero risk.

Third pick: **A3 Part 1 (invalidate from share + telemetry)**. Closes the last gap in cache invalidation. 2-3 hours.

B14, A8, A5, B17, D25 follow in that order. B19 is bundled with A3.

---

## 13. Open questions for you

1. **Do you want to ship these as separate PRs (5 batches) or one big PR (1 commit per item, 1 PR total)?** Separate is safer; one big is faster to merge. My recommendation: 5 batches, 1 PR per batch.
2. **For B14, do you want me to coordinate with the parallel agent on `feature/https-termination`?** The nginx config is theirs; the cache headers are mine. Best to land on their branch.
3. **For A8, do you have a Sentry / Grafana / Prometheus account already, or am I shipping "ready to configure" with env-var defaults?** If you have accounts, give me the DSN / org IDs and I'll wire them. If not, the env vars stay blank and the services start but don't connect.
4. **For B17, should the runbook be in `docs/EXPLAIN/operations/` (consistent with the existing EXPLAIN layout) or in `docs/` (top-level for higher visibility)?** My preference: `docs/EXPLAIN/operations/` because that directory will eventually hold other runbooks (Sentry alerts, backup/restore, etc.).
5. **For D25, should the integration tests be required-to-pass before merge (gate), or advisory (run but don't block)?** My recommendation: required-to-pass. The CI is already slow; adding a fast-gate integration step is fine. If the integration tests turn out to be flaky, downgrade to advisory.
