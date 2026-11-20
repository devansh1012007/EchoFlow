# EchoFlow — Backend Benchmark Harness (baseline)

Reproducible load + system-metrics harness for the deployed EchoFlow stack.
It exists because the portfolio audit finding P1 is real: **nothing in this
repo has been measured yet; any latency claim made without running this
harness is unsupported.**

> **The provisional target is a TARGET, not a result:**
> p95 feed API <= 300 ms at 50 RPS with <1% 5xx on the declared test profile.
> Do not present it as achieved until a run of this harness produces the
> numbers. Publishing numbers is only legitimate from a measured run, with
> the run's profile and date attached.

## Contents

| File | Role |
|---|---|
| `locustfile.py` | Load profile: JWT auth, weighted tasks (feed ~80% by default), paced to ~1 request/s/user so `-u 50` approximates 50 RPS. |
| `collect_system_metrics.py` | Samples Redis (cache + broker), Celery queue depths, Postgres activity/deadlocks into a CSV beside the load test. |
| `run_baseline.sh` | One-command baseline: health-check wait, collector in background, locust headless, report render. |
| `render_report.py` | Turns the run's CSVs into `results/baseline.md`. Missing inputs are reported per-section; nothing is fabricated. |
| `results/` | Output dir (created on first run). Check whether your `.gitignore` covers it before committing raw CSVs; keep exactly one measured run's outputs with its report. |

## Prerequisites

1. **A deployed stack.** Any of `docker-compose.yml` / `docker-compose.vps.yml`
   / `docker-compose.laptop.yml` with the `web` service reachable at the URL
   you will set in `BENCH_BASE_URL` (nginx TLS termination in front is fine —
   the bench hits the public entrypoint, which is the honest path).
2. **Seeded clips.** The feed needs real, `status='ready'` +
   `moderation_approved=True` clips, otherwise `/feed/` returns the designed
   202 "Preparing your feed..." forever and the measurement measures an empty
   system. Seed through the real upload path with the existing scripts:
   - `backend/scripts/seed_db.py` — drives `POST /clips/` etc. over HTTP
     (`SEED_AUTH_TOKEN` + `SEED_API_URL` env vars; see the script header),
   - `backend/scripts/seed_clips.py` / `seed_db2.py` — alternates.
   Wait for the media worker to finish (clips reach `ready`) before benching.
3. **Bench machine deps** — `locust` only:
   ```bash
   pip install locust
   ```
   locust is a **dev-only** dependency and is deliberately NOT added to any
   `requirements*.txt` file. `redis` and `psycopg2-binary` used by the
   collector are already pinned in `requirements-base.txt`
   (redis==8.1.0, psycopg2-binary==2.9.12).
4. **Network access from the bench machine to**:
   - the public URL (for locust),
   - the **cache Redis**, the **broker Redis**, and **Postgres** (for the
     collector). These are internal services; run the collector where they
     are reachable (host machine, VPN, `docker compose exec`, or an SSH
     tunnel) — or accept that those report sections will say MISSING.
     Postgres access must allow the read-only `pg_stat_*` queries below;
     note a non-superuser sees only the sessions Postgres permits it to see.

## Environment variables

### Required

| Var | Meaning |
|---|---|
| `BENCH_BASE_URL` | Public base URL, e.g. `https://echoflow.example`. |
| `BENCH_TOKEN` **or** `BENCH_USERNAME`+`BENCH_PASSWORD` | Credentials for the JWT flow. **`BENCH_TOKEN` (pre-minted access JWT) is strongly recommended** for the 50-user profile: `/auth/login/` is deliberately throttled to 10/min per IP (`backend/EchoFlow/settings.py:966`, `backend/app/urls.py:27-67`), so 50 ramping users logging in from one bench IP will eat 429s. Mint one token first: `curl -s -X POST $BENCH_BASE_URL/auth/login/ -H 'Content-Type: application/json' -d '{"username":"...","password":"..."}'` and use its `access` field. Access tokens live 15 min (`settings.py:1059`) — mint shortly before the run. |

### Collector (system metrics)

| Var | Meaning | Default |
|---|---|---|
| `REDIS_CACHE_URL` | Cache Redis (INFO memory/stats, evicted_keys). `docker-compose.yml:295`. | unset → columns empty, logged once |
| `REDIS_BROKER_URL` | Broker Redis (queue LLENs, unacked). `docker-compose.yml:294`. | unset → columns empty, logged once |
| `DATABASE_URL` | Postgres URL for read-only `pg_stat_*` queries. Compose builds it from `DB_USER/DB_PASSWORD/DB_HOST/DB_PORT/DB_NAME` (`docker-compose.yml:292`). | unset → columns empty, logged once |
| `BENCH_SAMPLE_INTERVAL` | Seconds between samples. | `5` |
| `BENCH_EXTRA_CELERY_QUEUES` | Comma-separated extra queue names beyond `celery,fast_feed,heavy_media`. | empty |
| `BENCH_REDIS_VISIBILITY_TIMEOUT` | Broker visibility timeout assumed for the oldest-unacked age approximation. | `3600` |

### Load profile

| Var | Meaning | Default |
|---|---|---|
| `BENCH_USERS` | locust `-u` | `50` |
| `BENCH_RAMP_RATE` | locust `-r` (users spawned per second) | `5` |
| `BENCH_RUN_TIME` | locust `-t` | `2m` |
| `BENCH_PACE_SECONDS` | per-user task-start interval; nominal RPS ≈ users / pace. 50 users @ 1.0s ≈ 50 RPS. | `1.0` |
| `BENCH_FEED_WEIGHT` | feed task weight | `80` |
| `BENCH_HEALTH_WEIGHT` | `GET /health/` weight | `6` |
| `BENCH_READY_WEIGHT` | `GET /ready/` weight (1 DB roundtrip per hit) | `6` |
| `BENCH_PROFILE_WEIGHT` | `GET /profile/me/` weight | `8` |
| `BENCH_PLAYBACK_WEIGHT` | `POST /media/playback-token/<uuid>/` weight (opt-in; needs clip uuids harvested from feed responses; endpoint is throttled 300/min) | `0` |

## Run

```bash
BENCH_BASE_URL=https://<host> \
BENCH_TOKEN=<pre-minted access JWT> \
REDIS_CACHE_URL=redis://<cache-host>:6379/0 \
REDIS_BROKER_URL=redis://<broker-host>:6379/0 \
DATABASE_URL=postgres://<user>:<pass>@<db-host>:5432/<db> \
bash scripts/benchmarks/run_baseline.sh
```

Outputs land in `scripts/benchmarks/results/`:
`baseline_stats.csv`, `baseline_failures.csv` (locust), `system_metrics.csv`
(collector), `baseline.md` (report). Ctrl-C stops both processes cleanly.

## What is measured — definitions

- **p50 / p95 / p99** — locust response-time percentiles in ms
  (`Median Response Time`, `95%`, `99%` columns of `*_stats.csv`).
- **RPS** — locust `Requests/s` (requests / wall clock). With
  `constant_pacing`, sustained latency above the pace window reduces
  achieved RPS (≈ users / mean response time); the report shows the
  achieved value, never the nominal one.
- **5xx rate** — 5xx-classified failure occurrences (failures CSV) / total
  aggregated requests. 429s are counted separately and reported; a 429 means
  the benchmark client saturated a throttle budget and must be visible.
- **Redis memory / evictions** — `used_memory`, `maxmemory`, and the delta
  of the cumulative `evicted_keys` counter over the run window (cache Redis;
  policy allkeys-lru per `docker-compose.yml:453`). Broker Redis runs
  noeviction — losing it would mean losing queued Celery tasks.
- **Celery queue depth** — LLEN of `celery`, `fast_feed`, `heavy_media`
  (queue names from `CELERY_TASK_ROUTES`, `backend/EchoFlow/settings.py:570-573`,
  plus the implicit default; workers run `-Q fast_feed` / `-Q heavy_media`,
  `docker-compose.yml:429,496`). **Depth is NOT lag** — it says how many
  messages are queued, not how long they waited. The optional
  `celery_unacked_oldest_age_s` is an approximation via Kombu's
  `unacked_index` zset and is labelled as such everywhere.
- **Telemetry** — depth of `stream:interaction.events` (+ DLQ) and the legacy
  `telemetry:queue` list (`backend/app/services/interactions.py:89`,
  `backend/app/tasks.py:658`). Depth only.
- **Postgres** — `pg_stat_activity` counts by state, `pg_settings.max_connections`,
  and the delta of cumulative `pg_stat_database.deadlocks` over the run.
  Read-only session; connect_timeout=3; a failed component is logged once and
  its columns stay empty for the rest of the run.
- **NOT covered by this harness (honest gaps):**
  - **Media-processing time** (upload → HLS ready). The codebase already
    instruments it (`echoflow_hls_processing_duration_seconds`,
    `backend/app/metrics.py:134`); extracting p95 from `/metrics/` or the
    Grafana dashboard is a documented follow-up, not part of this baseline.
  - **Per-user feed fan-out** — all locust users share one JWT identity, so
    the server's per-user Redis queue `user_feed:{id}` is drained by all of
    them concurrently. This stresses the refill path but is not a simulation
    of N distinct humans.

## Before you publish anything

1. Run the harness against a **deployed** stack with seeded clips — not
   against a laptop with an idle worker unless you say so in the report.
2. Keep the run metadata (users/ramp/duration, date, host) with the numbers.
3. State the target as a target: `p95 <= 300 ms @ 50 RPS, <1% 5xx` — pass/fail
   comes only from `render_report.py`, which refuses to judge a run whose
   achieved RPS is below 50 (verdict INCONCLUSIVE).
4. Save the report as **`docs/benchmarks/<date>-baseline.md`** (create the
   directory on the first measured run) together with the three CSVs, and
   link it from the audit fix. Do not edit the CSVs; regenerate instead.

## Follow-ups

- Media-processing p95 from the existing Prometheus histogram (see above).
- Multi-identity auth for realistic per-user feed queues (needs the login
  throttle lifted for the bench IP or a batch-minted token set).
- True Celery queue lag via `celery events` instead of depth + unacked
  approximation.
- Longer soak (30–60 min) once the 2-minute baseline is green, plus a
  spike profile (ramp to 100) for the degradation curve.
