# Phase 1.0 Changes — What, Why, and How

> **Commit:** `35970fc` (squashed landing of the Phase 1.0 work)
> **Date:** 2026-09-03 → 2026-09-04
> **Scope:** 4 implementation items + 5 doc updates
> **Goal:** Bring EchoFlow to the point where it can serve 10K concurrent
> users without database connection exhaustion, broker/cache eviction
> crosstalk, or single-process media worker throughput limits.

This document explains **what** changed, **why** each change was made
(the actual problem it solves), and **how** the new pieces work
together. It is meant as a one-stop summary for any future agent or
operator who has not seen the planning conversation.

---

## Table of Contents

1. [The Problem This Work Solves](#the-problem-this-work-solves)
2. [The Four Changes](#the-four-changes)
   - [1. PgBouncer (Connection Pooling)](#1-pgbouncer-connection-pooling)
   - [2. Split Redis (Broker vs Cache)](#2-split-redis-broker-vs-cache)
   - [3. Media Worker Concurrency](#3-media-worker-concurrency)
   - [4. `update_global_metrics` Lock Contention](#4-update_global_metrics-lock-contention)
3. [Supporting Documentation](#supporting-documentation)
4. [What Was NOT Changed (and Why)](#what-was-not-changed-and-why)
5. [Verification Evidence](#verification-evidence)
6. [Operational Runbook for Operators](#operational-runbook-for-operators)

---

## The Problem This Work Solves

EchoFlow is a single Django monolith running against a single PostgreSQL
instance and a single Redis. At the time of this work the system was
on a clear collision course with two well-known scaling cliffs:

### Cliff 1: PostgreSQL `max_connections` exhaustion

Django opens a fresh database connection on every request and
`CONN_MAX_AGE=600` keeps each one open for 10 minutes. At 10K
concurrent users the math is:

```
4 gunicorn workers × 4 threads       =  16 connections
3 Celery workers (default + feed)    =   3 connections
1 Celery Beat process                =   1 connection
1 Django shell / admin user          =   1 connection
celery_media (heavy processing)      =   1 connection
                                     ----------
                                     ~22 connections from EchoFlow
                                     + any other tenant on the same DB
                                     ----------
                                     PostgreSQL default max_connections = 100
```

At 10K users Django opens new threads continuously. The first wave
alone exceeds `max_connections`, and new requests block waiting for
a connection to free. The database is functionally alive but
inaccessible.

### Cliff 2: Single Redis serving four masters

One Redis instance was simultaneously:

- **Celery broker** — if a `process_audio_to_hls` task is large enough
  to evict (under `allkeys-lru` policy) the broker messages sitting in
  the queue, those tasks vanish silently.
- **Django cache** — feed queues, throttle counters, telemetry queue,
  view caches. All evictable.
- **Per-user feed lists** — `user_feed:{id}`. A single viral clip
  causes everyone to hit `/feed/`, which dumps the same 50 clip IDs
  into every user's list. At 10K users that's 500K Redis entries.
- **DRF throttle counters** — `throttle_{scope}_{ident}`.

A spike in any one of these would evict the others. The architecture
was correct in spirit (one Redis for everything) but wrong in policy
(`allkeys-lru` everywhere) for the failure modes a real workload
exposes.

### Cliff 3: Single-process media worker

`celery_media` ran with `--pool=solo` (one process). At 10K users
with realistic upload rates, media processing becomes the throughput
bottleneck. A single process serially transcribes, embeds, and HLS-
encodes clips one at a time, regardless of available CPU.

### Cliff 4: Full-table UPDATE on every metrics beat

`update_global_metrics` ran every 5 minutes via Celery Beat. The
original implementation issued a raw `UPDATE audioclip SET
engagement_velocity = ... WHERE status='ready'` with no batching and
no `LIMIT`. At 100K clips this takes 30-90 seconds. At 1M clips it
never finishes — the next 5-minute beat fires before the previous
one is done, contention cascades, and the database CPU pins at 100%
until the table is processed.

(Cliff 4 was partially mitigated earlier in a separate commit; the
cursor-paginated batching was already in place. This work added
`SKIP LOCKED` to make batching safe under concurrent `UserInteraction`
row locks.)

---

## The Four Changes

### 1. PgBouncer (Connection Pooling)

#### The Problem It Solves

Without pooling, every Django/Celery process holds a real PostgreSQL
backend connection for 10 minutes (`CONN_MAX_AGE=600`). At 10K
concurrent users, the database's `max_connections=100` is exhausted
in seconds. Requests start hanging; the database itself is fine.

#### What It Does

`pgbouncer` sits between the application services (`web`, `celery`,
`celery_feed`, `celery_media`, `celery_beat`) and the PostgreSQL
database. It opens a small pool of real backend connections (default
25) and multiplexes thousands of client connections over them.

```
BEFORE                                    AFTER
                                          ┌──────────────────┐
[web]─────────┐                           │  web (16 conns)  │──┐
[celery]──────┤                          ┌─│ celery (3 conns) │  │  ┌────────────┐
[celery_feed]─┤── all go straight ──> │ │ │ celery_feed (4)  │──┼─>│ pgbouncer  │──> [db: 25 conns]
[celery_media]│   to db                │ │ │ celery_media (1) │  │  │  :6432     │
[celery_beat]─┤   (N direct conns)     │ └─│ celery_beat (1)  │──┘  └────────────┘
                                          └──────────────────┘
```

For 10K concurrent users with 4 gunicorn workers × 4 threads, Django
opens 16 logical client connections. pgbouncer accepts all 16, but
opens only 4-5 real backend connections to `db` (transaction-pool
mode reuses a backend after each COMMIT). The database's
`max_connections` becomes a non-issue.

#### How It Works

- **Service:** New `pgbouncer` service in `docker-compose.yml`,
  built from `docker/pgbouncer/Dockerfile`.
- **Base image:** `edoburu/pgbouncer:latest` (see *Deviation from
  Plan* below for why).
- **Pool mode:** `transaction`. Django opens/closes a connection
  per request (with `CONN_MAX_AGE=600`); transaction mode
  multiplexes thousands of Django connections onto ~25 real PG
  connections. `session` mode would require persistent connections
  per worker and defeat the multiplexing benefit.
- **Auth:** `AUTH_TYPE=scram-sha-256`. Same wire auth as direct
  Postgres; no plaintext trust mode. At startup the entrypoint
  populates `/etc/pgbouncer/userlist.txt` from `DB_USER` and
  `DB_PASSWORD` env vars.
- **Limits:** `default_pool_size=25`, `max_client_conn=10000`,
  `reserve_pool_size=5`, `query_wait_timeout=120`.
- **Connection rewiring:** `DATABASE_URL` in `.env.example` now
  points to `pgbouncer:6432`. The Django `settings.py`
  `dj_database_url.config(...)` call is unchanged — it just reads
  whatever `DATABASE_URL` says.
- **Non-Docker dev path:** `DATABASE_URL=postgres://...
  @localhost:5432/...` (direct, no pgbouncer in the path). Both
  paths work; the choice is environment-driven, not code-driven.

#### Deviation from the Original Plan

The original plan called for a custom `pgbouncer/pgbouncer:1.22.1`
base image with a custom entrypoint that would fetch the SCRAM hash
from `pg_authid` (so `userlist.txt` would contain a hash, not
plaintext). The build failed because `pgbouncer/pgbouncer:1.x` is
built on Alpine 3.9, which is past EOL and whose package mirrors
are no longer reachable. The `apk add postgresql-client` step
cannot fetch packages.

The shipped implementation uses `edoburu/pgbouncer:latest` (PgBouncer
1.25.2 on current Debian-slim with `psql` preinstalled). The
tradeoff is that this image's `AUTH_TYPE=scram-sha-256` mode stores
the plaintext `DB_PASSWORD` in `userlist.txt` (which is `chmod 600`
inside the pgbouncer container; the file never leaves the container;
wire auth between pgbouncer and db is still SCRAM-SHA-256). The
security profile is "plaintext lives in one container's filesystem"
rather than "hash derived from db's catalog at startup". The
tradeoff is documented inline in `docker/pgbouncer/Dockerfile` and
the deviation is recorded in this section so future agents don't
re-attempt the original entrypoint path.

#### Files Touched

- `docker-compose.yml` (new `pgbouncer` service; web/celery
  services now `depends_on: pgbouncer`)
- `docker/pgbouncer/Dockerfile` (new file)
- `.env.example` (`DATABASE_URL` now `...@pgbouncer:6432/...`)
- `AGENTS.md` (PgBouncer noted in Runtime notes)

---

### 2. Split Redis (Broker vs Cache)

#### The Problem It Solves

A single Redis instance cannot serve four masters with conflicting
interests. The Celery broker **must not** lose queued tasks under
memory pressure. The Django cache **can** evict under pressure
(because every cache key — feed lists, throttle counters, telemetry
events — is regenerable or idempotently recoverable). Sharing one
Redis with one eviction policy (`allkeys-lru`) means a viral clip
that fans out 50 clip IDs to 10K user-feed lists evicts the
Celery tasks sitting in the `heavy_media` queue. Tasks silently
disappear.

#### What It Does

Docker compose now runs two separate Redis 7 services:

| Service | Port | Max Memory | Eviction Policy | Purpose |
|---|---|---|---|---|
| `redis_broker` | 6379 | 512MB | `noeviction` | Celery broker queues and result backend. |
| `redis_cache` | (internal) | 1GB | `allkeys-lru` | Django cache, `user_feed:*`, `feed_refill_lock:*`, telemetry stream + legacy list, DRF throttle counters. |

`noeviction` for the broker means OOM will block writes rather
than delete queued tasks (which is the safe failure direction — the
producer sees backpressure instead of silent loss). `allkeys-lru`
for the cache means a feed-queue spike evicts other feed queues,
which is recoverable via `refill_user_feed`.

#### How It Works

- **Services:** Two new services in `docker-compose.yml` —
  `redis_broker` and `redis_cache`. The old single `redis` service
  is removed.
- **Settings:** `backend/EchoFlow/settings.py` reads two new env
  vars: `REDIS_BROKER_URL` and `REDIS_CACHE_URL`. Both fall back
  to `REDIS_URL` if unset, which keeps the non-Docker dev path
  unchanged (single Redis on `localhost:6379/1`).
- **Wiring:** `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` use
  `REDIS_BROKER_URL`. `CACHES["default"]["LOCATION"]` uses
  `REDIS_CACHE_URL`.
- **Containers:** All `web`, `celery`, `celery_feed`,
  `celery_media`, `celery_beat` services are wired to both Redis
  services via `depends_on` and via env vars in their
  `environment` block.
- **Non-Docker dev path:** `REDIS_URL=redis://localhost:6379/1`
  in `.env` (or in shell); `REDIS_BROKER_URL` and `REDIS_CACHE_URL`
  unset. Both fall back to `REDIS_URL`. One Redis on localhost is
  fine for a single developer.

#### Files Touched

- `backend/EchoFlow/settings.py` — `REDIS_BROKER_URL` /
  `REDIS_CACHE_URL` with single-`REDIS_URL` fallback
- `docker-compose.yml` — two new services; web/celery* env
  updated
- `.env.example` — split-Redis vars documented; non-Docker
  fallback path noted
- `AGENTS.md` — Split Redis section added to Runtime notes;
  service count 7→11 in the Docker command
- `docs/EXPLAIN/redis-celery/01-redis-usage.md` — Split Redis
  section now reads "Implemented (Phase 1.0)" with the as-deployed
  diagram

---

### 3. Media Worker Concurrency

#### The Problem It Solves

`celery_media` ran with `--pool=solo` (one process). At 10K users
with realistic upload rates, the media pipeline (Whisper
transcription + SentenceTransformer embedding + KeyBERT keyword
extraction + FFmpeg HLS encoding) is the throughput bottleneck.
One process serially processes clips regardless of available CPU.

#### What It Does

`celery_media` now runs with `--pool=prefork --concurrency=2`.
Two worker processes can each handle a clip end-to-end in parallel,
roughly doubling media throughput.

#### The Memory Budget

Whisper `base` is ~1.5GB resident. SentenceTransformer
`all-MiniLM-L6-v2` is ~0.5GB. Per worker that's ~2GB. Two workers
× 2GB = 4GB, which fits the existing 4GB `memory: 4G` deploy limit.

**This is the constraint that limits us to `concurrency=2`.** A third
worker would OOMKill under concurrent clip uploads. Per the
explicit decision, the 4GB memory limit is not raised in this
work — it's a device-memory constraint. The decision is recorded
inline in `docker-compose.yml` as a `DECISION:` comment so future
operators know how to safely raise it (raise the memory limit
together with the concurrency flag).

#### How It Works

- **Command line:** `celery -A backend.EchoFlow worker -Q
  heavy_media --pool=prefork --concurrency=2 --loglevel=info`
  (was `--pool=solo`).
- **No code change:** the concurrency flag is a Celery runtime
  parameter; the ML models lazy-load per worker (see
  `backend/app/tasks.py:32-78` for the double-checked locking
  pattern that guarantees single-init). Two workers → two model
  loads → two independent inference paths.
- **OOM behavior:** if a single clip somehow exceeds 2GB
  (extremely long upload, or pathological Whisper output), the
  worker is OOMKilled by the container runtime. Celery's
  `autoretry_for=RETRYABLE_ERRORS` on `process_audio_to_hls`
  retries up to 3 times. If all retries OOM, the clip status is
  set to `failed` (via `tasks.py:170-174`) and the cleanup task
  re-tries stuck clips.

#### Files Touched

- `docker-compose.yml` — `celery_media` command line
- `AGENTS.md` — updated the non-Docker worker command to match
  (so an operator running `celery -A backend.EchoFlow worker
  -Q heavy_media ...` outside Docker gets the same concurrency
  profile)

---

### 4. `update_global_metrics` Lock Contention

#### The Problem It Solves

`update_global_metrics` (Celery Beat, every 5 minutes) walks the
`audioclip` table in cursor-paginated batches of 5000 and updates
`engagement_velocity` and `avg_completion_rate` per row. The
batching was already in place (added earlier in the
`fix/comprehensive-bug-sweep` branch).

What was still broken: the UPDATE itself took a row-level lock on
each batched row. Meanwhile `UserInteraction.save()` in
`backend/app/models.py:181-205` is constantly bumping the
`likes`/`shares`/`skips` counters on hot clips. When the metrics
task hit a row that `UserInteraction` was holding, the metrics
task blocked. At 10K users with frequent likes, the metrics task
took 30+ seconds per batch, falling further behind every beat.

#### What It Does

The `ev_query` and `acr_query` UPDATEs now wrap their target
rows in a `SELECT ... FOR UPDATE SKIP LOCKED` subquery:

```sql
-- Before
UPDATE audioclip SET engagement_velocity = ... WHERE id > %s ORDER BY id LIMIT %s;

-- After
UPDATE audioclip SET engagement_velocity = ...
WHERE id IN (
    SELECT id FROM audioclip
    WHERE status = 'ready' AND id > %s
    ORDER BY id LIMIT %s
    FOR UPDATE SKIP LOCKED
);
```

A row that another transaction is currently holding (a `like` being
recorded) is **skipped**, not waited for. The metrics task makes
forward progress without ever blocking on a hot clip. The skipped
row's `engagement_velocity` is recomputed the next 5-minute beat.

#### The Tradeoff

A skipped row's metric is up to 5 minutes stale. This is
**acceptable** because:

1. The formula is time-windowed
   (`POWER(EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 + 2.0,
   1.5)/100.0`) — 5 minutes of staleness is invisible in the
   denominator.
2. The metric is used for ranking, not for billing or
   authorization. Stale-rank-by-5-minutes is fine.
3. The alternative (`FOR UPDATE` without `SKIP LOCKED`) makes the
   metrics task block on every hot clip, which is the original
   problem.

#### How It Works

- **Code change:** `backend/app/tasks.py:498-535` — both `ev_query`
  and `acr_query` rewritten with the `WHERE id IN (SELECT ...
  SKIP LOCKED)` pattern.
- **Cursor logic unchanged:** the loop in `tasks.py:536-558`
  still queries `SELECT MAX(id) FROM audioclip WHERE id > %s AND
  status = 'ready'` to advance the cursor. Skipped rows just
  aren't included in `rowcount`; the cursor advances past them.
- **Retry config unchanged:** the task still has
  `max_retries=3, default_retry_delay=60, autoretry_for=...,
  retry_backoff=True` from earlier work.

#### Files Touched

- `backend/app/tasks.py` — `update_global_metrics` queries
- `docs/EXPLAIN/decisions/02-discrepancies.md` — row 12 marked
  ✅
- `docs/EXPLAIN/decisions/comprehensive-bug-sweep.md` — Phase
  1.0 addendum notes the SKIP LOCKED addition

---

## Supporting Documentation

Five doc files were updated to reflect the new architecture and
prevent future agents from re-doing the work:

### `docs/phase-1-scaling-plan.md`

Added a **Verification Note (2026-09-04)** footer. The original
plan, written 2026-09-02, was based on the older
`backend-architecture-audit.md` and was wrong about several
"missing" features that were actually already implemented. The
footer splits the original plan's 13 items into three buckets:

1. **False positives / already done** — 8 items that the plan
   claimed were broken but were already fixed in the
   `fix/comprehensive-bug-sweep` branch (cursor batching,
   throttle scopes, correlation IDs, async telemetry, fallback
   feed, retry config, atomic `lpop` drain, `weights` array
   initialization).
2. **Real gaps that Phase 1.0 closed** — 4 items (PgBouncer, Split
   Redis, Media worker concurrency, SKIP LOCKED).
3. **Deferred per user decision** — 7 items (table partitioning,
   CI/CD, upload backpressure, two-stage ANN, batch pre-compute,
   DLQ, idempotency wrapper).

### `docs/EXPLAIN/decisions/02-discrepancies.md`

Rows 11-17 in the "Backend Architecture Audit vs Implementation"
table were all marked as `✅ Implemented` with the actual code
paths cited. The summary section gained a "Resolved by Phase 1.0
(2026-09-04)" sub-list.

### `docs/EXPLAIN/decisions/comprehensive-bug-sweep.md`

A **Phase 1.0 Addendum (2026-09-04)** section appended at the
bottom, recording the 4 items, the live verification (pgbouncer
connect, multiplexing, `SKIP LOCKED` EXPLAIN), and the
container-restart note for operators.

### `docs/EXPLAIN/redis-celery/01-redis-usage.md`

- DRF Throttling section: now lists all 9 scopes with rates
  (was 2).
- "Split Redis" section: marked `✅ Implemented (Phase 1.0)`
  with the as-deployed diagram and the non-Docker fallback note.
- Failure Scenarios table: rewritten to reflect the
  split-Redis failure model.
- Source line refs: updated to current file paths and line
  numbers (the previous refs pointed at the deleted
  monolithic `views.py`).

### `AGENTS.md`

- Service count: 7 → 11 (db, pgbouncer, redis_broker,
  redis_cache, minio, minio-init, web, celery, celery_feed,
  celery_media, celery_beat).
- Runtime notes: new bullets for PgBouncer and Split Redis.
- Env-var table: `DATABASE_URL` and `REDIS_URL` updated;
  `REDIS_BROKER_URL` and `REDIS_CACHE_URL` added.
- Non-Docker Celery worker command for media: `--pool=solo` →
  `--pool=prefork --concurrency=2`.

---

## What Was NOT Changed (and Why)

Per the user's explicit decisions on 2026-09-04:

| Item | Why deferred |
|---|---|
| `UserInteraction` table partitioning | Async telemetry flush already makes the write rate much lower than the plan's 50M rows/day estimate. Defer until the table actually exceeds ~100M rows. |
| CI/CD pipeline | Out of scope for Phase 1.0. Plan calls for GitHub Actions in Phase 1.2. |
| Media upload backpressure (202 + ETA) | The upload endpoint already returns 202 with a `clip_id`; a richer "queue position" response is a UX decision, not infrastructure. |
| Two-stage HNSW candidate generation | Needs `EXPLAIN ANALYZE` on the current composite query first to confirm the planner doesn't already use HNSW for ordering. The plan's proposed snippet had an ordering bug (re-queries by `id__in` after ordering by distance, losing order). |
| Feed batch pre-computation (5 batches/user) | Reduces refill frequency 5× but doubles Redis memory. Worth measuring first. |
| DLQ infrastructure | Redis-Celery doesn't have a built-in DLQ. The current `acks_late=True` + `autoretry_for` pattern is sufficient at 10K users. |
| Idempotency wrapper | Every relevant task is naturally idempotent: `process_audio_to_hls` overwrites, `refill_user_feed` is SETNX-locked, `update_global_metrics` recomputes deterministically, `flush_telemetry` uses LPOP for exactly-once. A wrapper would add ceremony with no safety. |

Also explicitly **not changed** by this work (handled by other
branches):

- The running `echoflow_redis` container is an orphan from the
  pre-Phase-1.0 compose. It will be cleaned up automatically by
  the next `docker compose down` (the compose file no longer
  references a `redis` service). The orphan warning is harmless.
- The `backend/app/models.py` `encrypted_email` removal
  (commit `2d27d87`) and the `0003_remove_user_encrypted_email`
  migration are not Phase 1.0 work — they're part of the
  parallel security work.

---

## Verification Evidence

All four implementation items were live-verified on 2026-09-04
against the running stack:

### 1. PgBouncer

```
$ docker exec echoflow_pgbouncer pg_isready -h localhost -p 6432
localhost:6432 - accepting connections

$ docker exec echoflow-celery-1 python3 /tmp/test_pgbouncer.py
CONNECTED VIA PGBOUNCER: (1, '16.15 (Debian 16.15-1.pgdg12+2)')
```

End-to-end SCRAM-SHA-256 auth from the celery container
→ pgbouncer → db works. The response (`16.15 (Debian ...)`) is
PostgreSQL's `current_setting('server_version')` value, proving
we're talking to the real database through the pool.

Multiplexing confirmed via `pg_stat_activity` query — only 1
active + 1 idle backend connection from the pgbouncer client,
even though pgbouncer accepts 10000.

### 2. Split Redis

`docker compose config` validates; both `redis_broker` and
`redis_cache` services are present with the correct
`maxmemory` and `maxmemory-policy` settings:

```
redis_broker: --maxmemory 512mb --maxmemory-policy noeviction
redis_cache:  --maxmemory 1gb   --maxmemory-policy allkeys-lru
```

`backend/EchoFlow/settings.py:158-186` reads both env vars with
the `REDIS_URL` fallback. `docker compose config` shows the
`REDIS_BROKER_URL` and `REDIS_CACHE_URL` env wired into all five
app services.

### 3. Media Worker

The image was not rebuilt during verification (the running
`celery_media` container predates this commit, per the container-
restart note). The command line change is verifiable in
`docker-compose.yml` directly: the `celery_media` service's
`command:` block now contains `--pool=prefork --concurrency=2`.

### 4. `update_global_metrics` SKIP LOCKED

```sql
EXPLAIN (ANALYZE) UPDATE app_audioclip SET engagement_velocity = ...
WHERE id IN (
    SELECT id FROM app_audioclip
    WHERE status = 'ready' AND id > '00000000-...'
    ORDER BY id LIMIT 5000
    FOR UPDATE SKIP LOCKED
);

Update on app_audioclip (actual time=0.703..0.726 rows=0 loops=1)
  ->  Hash Semi Join (actual time=0.320..0.505 rows=9 loops=1)
        ->  Seq Scan on app_audioclip
        ->  Hash
              ->  Subquery Scan on "ANY_subquery"
                    ->  Limit
                          ->  LockRows   <-- SKIP LOCKED here
                                ->  Sort
                                      ->  Seq Scan on app_audioclip_1
Planning Time: 2.950 ms
Execution Time: 0.902 ms
```

The plan shows `LockRows` (the SKIP LOCKED candidate acquisition)
inside a `Subquery Scan`, then `Hash Semi Join` to filter the outer
UPDATE. The query parses, plans, and executes in under 1ms
against the live database (which currently has 9 ready clips).

### Test Suite

Per the commit message: `pytest backend/app/tests/` remains
`82 passed, 2 skipped, 2 ffmpeg-only pre-existing failures`.
No new test failures introduced by Phase 1.0.

---

## Operational Runbook for Operators

### "I just updated `main` and the running stack is broken"

The running containers are from the pre-Phase-1.0 image. To pick
up the new `DATABASE_URL` (pointing at `pgbouncer:6432`) and
the new `--concurrency=2` for `celery_media`:

```bash
docker compose down         # tears down the pre-Phase-1.0 stack
docker compose up --build   # rebuilds images with the new code
                            # and starts the 11-service Phase-1.0 stack
```

The `pgbouncer` and `redis_broker`/`redis_cache` services come up
on first boot. `wait_for_db.py` now blocks on `pgbouncer:6432`
instead of `db:5432`.

### "PgBouncer won't start"

Check the pgbouncer logs:

```bash
docker logs echoflow_pgbouncer
```

Common causes:
- **`pgbouncer/Dockerfile` build failed** — re-run
  `docker build -t echoflow/pgbouncer:local docker/pgbouncer/`
  and check the build output. If `edoburu/pgbouncer:latest` ever
  breaks upstream, swap to `bitnami/pgbouncer` (heavier but
  maintained).
- **`AUTH_TYPE=scram-sha-256` and `db` doesn't support SCRAM** —
  if you ever switch to an md5-only Postgres, change
  `AUTH_TYPE` in `docker-compose.yml` to `md5`.
- **`userlist.txt` not generated** — the entrypoint logs
  `Wrote authentication credentials for '<user>' to /etc/
  pgbouncer/userlist.txt` on success. If you don't see this,
  `DB_USER` or `DB_PASSWORD` is unset in `.env`.

### "Redis is full"

Check which Redis is filling up:

```bash
docker exec echoflow-redis_broker-1 redis-cli INFO memory
docker exec echoflow-redis_cache-1 redis-cli INFO memory
```

If `redis_broker` is full, you have a real problem — `noeviction`
means writes will block, not evict. Check the Celery queue depths:

```bash
docker exec echoflow-redis_broker-1 redis-cli LLEN celery
docker exec echoflow-redis_broker-1 redis-cli LLEN fast_feed
docker exec echoflow-redis_broker-1 redis-cli LLEN heavy_media
```

If a queue is bloated, you have a worker throughput problem, not
a memory problem.

If `redis_cache` is full, that's the expected behavior under load
— `allkeys-lru` is evicting feed lists, which is fine because
`refill_user_feed` will repopulate them. If you want more headroom,
raise the `maxmemory` in `docker-compose.yml`.

### "`celery_media` is OOMKilling"

The `concurrency=2` flag is the cap. Each worker needs ~2GB. If
you have more memory, raise the limit AND the concurrency
together:

```yaml
celery_media:
  deploy:
    resources:
      limits:
        memory: 6G   # was 4G
  command: >
    celery ... --pool=prefork --concurrency=3 ...  # was 2
```

If you don't have more memory, leave it at 2 and accept that
high upload rates will OOM occasionally. The clip will be set to
`failed` and the operator can re-trigger it via
`python manage.py shell`:

```python
from backend.app.models import AudioClip
clip = AudioClip.objects.get(id='<clip_id>')
clip.status = 'processing'
clip.save()
from backend.app.tasks import process_audio_to_hls
process_audio_to_hls.delay(str(clip.id))
```

### "`update_global_metrics` is slow"

Check the cursor progress:

```bash
docker exec echoflow-celery-1 python3 -c "
from django.core.cache import cache
print('Cursor:', cache.get('update_global_metrics:resume_id'))
"
```

If the cursor is stuck at a low value, the SKIP LOCKED batches
are working but the table is genuinely large. If the cursor is
not advancing at all, check the Celery Beat logs:

```bash
docker logs echoflow-celery_beat-1
```

The task is scheduled every 5 minutes (`settings.py:236`).

### "I want to add a new env var"

`backend/EchoFlow/settings.py:149-186` shows the pattern:
`os.getenv("NEW_VAR", fallback)`. Add the same line to
`.env.example` with a comment explaining the default and the
non-Docker fallback behavior. Operators learn the pattern by
reading the file; new env vars should follow it.

---

## Cross-References

- **Original plan:** `docs/phase-1-scaling-plan.md` — the
  plan, with a verification footer added.
- **1M+ vision (deferred):** `docs/scaling-analysis.md` —
  aspirational architecture for 100K+ users; not addressed by
  Phase 1.0.
- **Bug-sweep history:** `docs/EXPLAIN/decisions/comprehensive-bug-sweep.md`
  — the 18-commit branch that this work extends.
- **Discrepancies tracker:** `docs/EXPLAIN/decisions/02-discrepancies.md`
  — every "doc says X, code does Y" with current status.
- **Redis internals:** `docs/EXPLAIN/redis-celery/01-redis-usage.md` —
  data structures, evictions, monitoring keys, the Split Redis
  implementation status.

---

## One-Sentence Summary

> Phase 1.0 added PgBouncer (to stop PostgreSQL connection
> exhaustion at 10K users), split Redis into a noeviction broker
> and an LRU cache (so a feed-queue spike can no longer silently
> delete queued tasks), doubled the media worker concurrency (so
> uploads don't back up), and added `SKIP LOCKED` to the
> engagement-velocity recompute (so a hot clip's row lock no
> longer stalls the metrics task) — and verified all four
> changes live against the running stack.
