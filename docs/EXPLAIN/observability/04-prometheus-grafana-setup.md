# Prometheus + Grafana Activation

How to bring up the metrics stack shipped in PR #2 and verify it works.

## Prerequisites

The two services (`prometheus`, `grafana`) are part of the same Compose
file as the rest of the stack, so the activation is the same as for any
other service:

1. **Set `GRAFANA_ADMIN_PASSWORD`** in `.env`. There is no default
   fallback — Grafana refuses to start without it in v11.x (this is
   fail-loud, intentional). Pick any non-trivial value for dev:
   ```bash
   echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 24)" >> .env
   ```

2. **Bring up the stack**:
   ```bash
   docker compose up -d prometheus grafana
   ```
   The two services start in dependency order: `prometheus` waits for
   `web` to be `service_healthy` (the `/health/` probe succeeds); then
   `grafana` waits for `prometheus` to be `service_started`.

## Verify

1. **Prometheus targets up**:
   ```bash
   curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job:.labels.job, health:.health}'
   ```
   Expect one `echoflow-web` target with `health: up`.

2. **Grafana datasource provisioned**:
   ```bash
   curl -s -u admin:$GRAFANA_ADMIN_PASSWORD http://localhost:3000/api/datasources | jq '.[] | {name, type, url}'
   ```
   Expect one datasource named `Prometheus` pointing at
   `http://prometheus:9090`.

3. **Grafana dashboards loaded**:
   ```bash
   curl -s -u admin:$GRAFANA_ADMIN_PASSWORD http://localhost:3000/api/search?folderIds=0 | jq '.[].title'
   ```
   Expect two dashboards: `EchoFlow — Feed & Suggestions` and
   `EchoFlow — Celery Health`.

4. **Scrape actually populating**:
   ```bash
   curl -s 'http://localhost:9090/api/v1/query?query=echoflow_feed_refill_duration_seconds_count' | jq
   ```
   After ~15s the first scrape lands. The histogram appears with the
   labels that the hot path has populated (typically `source=pool` and
   `outcome=success` for the refill; `op=get` and `op=set` for the cache).

## Where to look when something is wrong

| Symptom | Where to look |
|---|---|
| `health: down` on the web target | `docker compose logs prometheus` — usually a `connection refused` to `web:8005`. Check the web container's healthcheck is passing. |
| Grafana datasource not provisioned | `docker compose logs grafana | grep -i datasource` — typically a YAML parse error in `docker/grafana/provisioning/datasources/prometheus.yml`. |
| Dashboards not loaded | `docker compose logs grafana | grep -i dashboard` — typically a JSON parse error in `docker/grafana/dashboards/*.json` or a path mismatch in `docker/grafana/provisioning/dashboards/echoflow.yml`. |
| Metrics endpoint returns 200 but body is empty | The custom histograms (`echoflow_*`) only appear after their first observation. Trigger one by hitting `/feed/` or `/interactions/{id}/toggle-like/` on the web container. |

## Customizing

- **Scrape interval**: `scrape_interval` in
  `docker/prometheus/prometheus.yml`. Lower = more disk I/O. 15s is the
  dev-tier default; production typically uses 30s.
- **Retention**: pass `--storage.tsdb.retention.time=30d` to the
  prometheus command in `docker-compose.yml`. Default is 15 days.
- **Adding a new dashboard**: drop a JSON file into
  `docker/grafana/dashboards/`. Grafana picks it up within
  `updateIntervalSeconds: 30` (set in
  `docker/grafana/provisioning/dashboards/echoflow.yml`).
- **Adding alert rules**: future work. The design doc
  (`docs/EXPLAIN/observability/03-prometheus-grafana-design.md:453`)
  proposes 6 rules; shipping them needs Alertmanager, which is out of
  scope for PR #2.

## CI / production notes

The Compose setup is dev-tier (single instance, default retention,
modest resource limits). For production:

1. Provision real persistent volumes for `prometheus_data` and
   `grafana_data` (not the local Docker volumes Compose creates).
2. Set `GF_SECURITY_ADMIN_PASSWORD` from a secrets manager. Rotate per
   your team's policy.
3. Override `--storage.tsdb.retention.time` to 30-90 days depending on
   disk budget.
4. Consider Prometheus federation or remote_write for HA — the dev
   single-instance is a single point of failure.

## Related

- `docs/EXPLAIN/observability/03-prometheus-grafana-design.md` — full
  design rationale, panel definitions, alert rules (proposed, not
  shipped).
- `scripts/observability_tui.py` — the stdlib TUI that reads `/metrics/`
  directly. Still useful for quick spot-checks; Grafana is now the
  primary interface.
- `backend/app/metrics.py` — the 6 application-level histograms and
  counters the dashboards query.
