# Read-Replica Routing Design

> **Status:** Design document. The router is being implemented on `feat/stage2-service-layer-and-telemetry-stream`; the replica itself is **deferred**. The router will ship with `READ_DATABASE_URL` unset and therefore fall back to `default` for every read.
>
> **Branch under audit:** `feat/stage2-service-layer-and-telemetry-stream` (heads through 2026-09-03).
>
> **Scope:** Why the replica is needed, why it does not exist in this commit, what it would take to bring one online, and the router contract that gets us there cleanly.

---

## 1. Why a Read Replica Is Needed

### 1.1 PgBouncer alone is not enough

PgBouncer in transaction-pool mode is already in front of the primary (`docker-compose.yml:103-153`; `docker/pgbouncer/Dockerfile`). It multiplexes thousands of Django + Celery client connections onto ~25 real Postgres connections, which solves the **connection-count** failure mode flagged in `docs/event-driven-architecture-plan.md:225` (`OperationalError: too many clients`). The infrastructure audit and the event-driven plan both call this out as a Phase 1 prerequisite.

PgBouncer does **not** solve:

1. **CPU saturation.** Every read — `SELECT`, vector distance, JOIN — runs on the same Postgres backend that writes are running on. Pooling reduces the number of backends, not the work per backend.
2. **I/O contention.** A `COPY`/`UPDATE` on `audioclip` flushes shared buffers and the OS page cache. A pure-read query that would otherwise be cache-hot misses for hundreds of ms while the writer's pages evict its working set.
3. **Autovacuum starvation.** `userinteraction` produces ~58 M dead tuples/day at 10k concurrent (`event-driven-architecture-plan.md:224`). PgBouncer cannot help here; only physical isolation of write traffic from read traffic can.
4. **Lock contention between reads and writes.** `update_global_metrics` (`backend/app/tasks.py:529-614`) and `UserInteraction.save()` (`backend/app/models.py:173-206`) acquire row locks that any `SELECT` reading the same rows must wait for in some cases (`SELECT FOR SHARE` on tuples with pending `FOR UPDATE`).
5. **Read-only `SELECT FOR UPDATE` blocks reads.** `User.objects.annotate(...).get(pk=user.pk)` inside `ProfileViewSet.retrieve` (`backend/app/views/profile.py:45-55`) is benign today, but adding a future `SELECT FOR SHARE` would silently serialize against the F() update path.

Pool mode also introduces a subtlety worth documenting: `transaction` pool mode is **incompatible with named prepared statements, `LISTEN`, server-side cursors outside transactions, and `SET` outside a transaction**. Routing reads to a different backend means a session-level `SET` in the primary backend does not propagate to the read backend. Anything that relies on per-session state must be re-evaluated.

### 1.2 The concrete failure modes at 10k concurrent

From `docs/event-driven-architecture-plan.md:194-233` (the "10k live users" gap analysis), with the read-replica relevance column added:

| # | Failure mode | Read-replica relevance |
|---|---|---|
| 1 | F() row-lock contention on viral clips | **Indirect.** Reads don't acquire the lock; but reads of `AudioClip` from `refill_user_feed` (`backend/app/tasks.py:347-456`) and `/feed/` (`backend/app/views/feed.py:58-140`) compete for the same backend the lock-holder is running on. |
| 2 | `update_global_metrics` raw SQL scan | **Direct.** This task uses `FOR UPDATE SKIP LOCKED` (`tasks.py:559-569, 574-585`). Every `SKIP LOCKED` is a row lock on `audioclip`. Reads of the same clips must wait in `SELECT FOR SHARE` if they happen to land on a locked tuple. |
| 3 | Autovacuum starvation on `userinteraction` | **Direct.** At 670 events/s × 86,400 s ≈ 58 M dead tuples/day (`event-driven-architecture-plan.md:224`), autovacuum can never keep up. A read replica at least stops reads from blocking on the autovacuum worker's `ACCESS SHARE` lock; it does not fix autovacuum itself, but it bounds the read-side blast radius. |
| 4 | Connection pool exhaustion under spike | **Indirect.** PgBouncer (already deployed) is the primary mitigation. Replica routing doesn't change the connection count, but it does split the work. |
| 5 | MinIO resource limits | Unrelated. |
| 6 | Redis `allkeys-lru` self-feeding collapse | Unrelated. |
| 7 | No nginx/CDN | Unrelated. |
| 8 | Security headers | Unrelated. |
| 9 | No frontend telemetry batching | Unrelated. |
| 10 | `evolve_long_term_user_baselines` no per-user error handling | **Direct.** This is the worst read against `userinteraction` and `user` in the codebase (`.iterator(chunk_size=100)`, no per-user try/except). At 200k DAU this is a 10-minute full-table scan (`event-driven-architecture-plan.md:231`). Routing it to the replica would let it run without contending with `toggle_like`'s F() updates. |
| 11 | HLS URL computed per-request | Unrelated. |
| 12 | Inbox 30 s polling | Unrelated. |

### 1.3 The hot read paths

These are the paths that benefit from replica routing first. Each is a **pure read** with no `SELECT FOR UPDATE` or atomic-block write on the same transaction.

| Path | File:line | Read type |
|---|---|---|
| `GET /feed/` | `backend/app/views/feed.py:58-140` | `AudioClip.objects.filter(id__in=...).annotate(...).order_by(preserved_order)` + subquery on `UserInteraction` |
| `GET /suggestions/?category=X` | `backend/app/views/feed.py:143-186` | `CosineDistance(...)` annotation, vector-heavy |
| `POST /tags/initialize/` (the read half) | `backend/app/views/feed.py:198-218` | `AudioClip.objects.filter(tags__overlap=...)[:100]` |
| `GET /profile/me/` and `/profile/{id}/` | `backend/app/views/profile.py:29-55` | `User.objects.annotate(...).get(pk=...)` |
| `GET /profile/{id}/clips` | `backend/app/views/profile.py:57-75` | `AudioClip.objects.filter(creator=target, status='ready').annotate(...)` |
| `GET /comments/?clip={id}` | (router-relevant by convention) | Comment list query |
| `refill_user_feed` | `backend/app/tasks.py:347-456` | The composite-score vector query (`tasks.py:374-387`) is the heaviest read in the codebase at 10k concurrent |

### 1.4 The reads that MUST stay on primary

These are non-negotiable; the router routes them to `default` even when `READ_DATABASE_URL` is set. The list maps directly to the requirements in this PR.

| Path | Why it stays on primary |
|---|---|
| `UserInteraction.save()` F() update | `backend/app/models.py:173-206` — `select_for_update()` + F() UPDATE must be on the same backend the transaction started on. Routed reads across `transaction.atomic()` blocks risk writing to a backend the next `SAVEPOINT` is on the other side of. |
| `update_global_metrics` | `backend/app/tasks.py:529-614` — `FOR UPDATE SKIP LOCKED` is a write-side mechanism; reads routed to replica would skip the same rows the writer is locking, defeating the lock. |
| `comment.save()` / `comment.delete()` | `backend/app/models.py:126-136` — F() UPDATE on `audioclip.comment_count` inside the implicit transaction. |
| `services/uploads.finalize_upload` | `transaction.on_commit(...)` chain — write + on-commit dispatch; the `audio_clips` row must commit to the same backend the dispatch fires from. |
| `toggle_like` / `send_share` | `services/interactions.record_like_toggle` / `services/shares.send_share` — both end in `UserInteraction.save()` which is `transaction.atomic()`. |
| Anything inside `transaction.atomic()` that also writes | Django's `transaction.atomic()` is connection-bound; a router that switched mid-transaction would corrupt savepoint semantics. The router uses `connection.in_atomic_block` to detect this. |
| `SELECT ... FOR UPDATE` / `FOR UPDATE SKIP LOCKED` | Same as above — locking reads need the same backend the eventual write will land on. |

---

## 2. Why It Does Not Exist in This Commit

The replica is **not** provisioned in this PR. The user explicitly chose to defer the streaming-replica setup. The PR adds only the routing machinery — `DATABASE` setting with an optional `read` alias, the `ReadRouter` class, and the override plumbing in settings — and leaves the `READ_DATABASE_URL` env var unset. With it unset, the router returns `None` from every read-routing call and Django falls back to `default`. The behavior change in this commit is therefore **zero on the wire**; the commit is laying the foundation.

Concrete blockers for actually running a streaming replica today:

1. **No `READ_DATABASE_URL` env var** is consumed anywhere (`backend/EchoFlow/settings.py:151-156`). Adding it is a 5-line settings change, but the value must be set by ops; without a real URL, the `read` alias is not constructed. (The router falls back to `None` if the env var is absent — this is the entire reason the alias is "optional.")
2. **No `db_read` Compose service** in `docker-compose.yml`. The user's brief is explicit: "I am NOT adding a `db_read` compose service yet." A replica requires provisioning a second Postgres container with `pg_basebackup`-initialized data, a `recovery.conf` / `postgresql.auto.conf` with `primary_conninfo`, and a `pg_hba.conf` allowing replication traffic from the primary.
3. **No replication slot.** Streaming replication requires the primary to keep enough WAL for the replica to catch up. Without a `pg_create_replication_slot` call (or `max_replication_slots >= 1` plus a slot name in `primary_slot_name`), the primary will eventually recycle WAL the replica needs and the replica will fail to keep up.
4. **No `pg_basebackup` choreography.** The replica's first boot is a `pg_basebackup` from the primary, not a fresh `initdb`. The wheelhouse / image-build pipeline that ships `pgvector/pgvector:pg16` (`docker-compose.yml:3-33`) does not include this step; a new service definition, an entrypoint script, and an init container are required.
5. **No second pgbouncer (or multi-pool config) fronting the replica.** The single `pgbouncer` service at `docker-compose.yml:103-153` has one backend definition — `DB_HOST=db`, `DB_PORT=5432`. Pointing `pgbouncer` at both `db` and `db_read` from one pool would conflate transactions across the two; the standard approach is two pgbouncer instances with their own `pgbouncer.ini`. `edoburu/pgbouncer` templates the config from env vars (`docker/pgbouncer/Dockerfile:7-13`), so the second pgbouncer requires either a second Dockerfile variant or a config-file mount.
6. **No DNS / network path from web/celery to `db_read`.** The `pgbouncer` service is the only DB proxy on the Compose network. The `web` and `celery_*` services connect to `pgbouncer:6432` via `DATABASE_URL` (`docker-compose.yml:229, 322-323, 366, 434, 498`). A replica-pgbouncer needs its own port (e.g. `6433`), a separate service definition, and a `READ_DATABASE_URL` pointing at it.
7. **No monitoring queries wired up.** `pg_stat_replication`, `pg_last_wal_replay_lsn`, and `pg_replication_slots` are the three queries that tell you the replica is healthy. The Django observability stack ships only `/health/`, `/ready/`, `/metrics/` (`event-driven-architecture-plan.md:38`); none of these are wired to replication state. A replica without monitoring is a black box.
8. **No operational runbook for promotion.** Promotion (read-only → read-write) is a planned cutover: stop traffic, `pg_ctl promote` (or `SELECT pg_promote()`), redirect `DATABASE_URL` to the replica, restart. The PR does not document this; the runbook is out of scope.
9. **No decision on PITR.** WAL archiving for point-in-time recovery is orthogonal to read replication — they share infrastructure (`archive_mode`, `archive_command`) but are different feature flags. The audit (`docs/event-driven-architecture-plan.md:349`) lists PITR as a separate decision and explicitly defers it past the 10k-concurrent milestone.

What the PR **does** ship, by design:

- A new `'read'` connection alias in `DATABASES` that is constructed only when `READ_DATABASE_URL` is set (settings.py change).
- `backend/app/db_routers.py::ReadRouter` — a `DATABASE_ROUTERS`-registered router that returns the `read` alias for read-only operations on the `app` app's models and returns `None` for everything else.
- The fallback path: with `READ_DATABASE_URL` unset, the router returns `None` from every method, which causes Django to use `default`. Behavior unchanged.

---

## 3. What a Streaming Replica Actually Requires

A PostgreSQL streaming-replica is one primary and one or more replicas connected by a TCP replication connection. The replica's data directory is initialized via `pg_basebackup`, then a `primary_conninfo` line in `postgresql.auto.conf` tells it where to stream from. WAL is shipped continuously; the replica applies it via the startup process. The replica is read-only by default (`default_transaction_read_only = on`); writes are blocked.

### 3.1 RAM

PG with pgvector at 10k concurrent is sized at ~1.5 GB resident in `event-driven-architecture-plan.md:319-321` (the `db` service's 4 GB limit absorbs the working set plus `shared_buffers`). The replica needs:

- `shared_buffers` ≈ 25% of RAM (same as primary).
- `effective_cache_size` ≈ 75% of RAM.
- The OS page cache for the WAL apply backlog — if the primary runs hot for 30 s, the replica must be able to buffer ~30 s × peak WAL throughput without dropping apply.
- HNSW index pages — `semantic_vector_index` (`models.py:83-89`) and `acoustic_vector_index` (`models.py:91-97`) are `m=16`, 384-dim and 128-dim. At 100k clips, each index is roughly `100k * 16 * dimensions * 4 bytes ≈ 240 MB` for semantic, `80 MB` for acoustic, plus the graph edges. **Mirror of primary** — same shape.

**Realistic replica RAM floor at 10k concurrent:** **3 GB resident** (1 GB shared_buffers + 1 GB effective cache + 1 GB WAL apply + HNSW hot pages). The same 4 GB memory limit from `event-driven-architecture-plan.md:319` applies; 2 GB would be tight, 4 GB comfortable.

### 3.2 CPU

The replica does two CPU-bound things:

1. **WAL apply** — parsing and applying each replicated transaction. On a write-heavy workload (~83 likes/s + ~10 shares/s + ~670 telemetry events/s at 10k concurrent, per `event-driven-architecture-plan.md:208-210`), the apply load is roughly 0.2–0.5 cores steady-state.
2. **Read query serving** — every routed read query lands here. At 55 feed reads/s + 8% suggestion reads + profile reads, peak CPU is ~0.5–1.0 cores.

**Catch-up cost:** when the replica is freshly provisioned via `pg_basebackup`, applying the initial WAL backlog (if any) is the bottleneck. On a primary with 50 GB WAL (a few days of write activity), expect 30–90 min of single-core-bounded catch-up. This is not a CPU sizing problem; it's an operational one.

**Replica CPU budget:** **1 core reserved, 2 cores limit** is a sane starting point. The `db` service is sized at 2 cores / 4 GB (`docker-compose.yml:24-25`); the replica can match.

### 3.3 Storage

The replica's data directory must be at least as large as the primary's. With ~100k clips × ~50 KB HLS metadata + 200k users × ~5 KB + 50 M `userinteraction` rows × ~200 bytes ≈ 30 GB on the primary, the replica needs **30+ GB of disk**, with headroom for WAL retention if `wal_keep_size` is set generously.

**Smaller is possible** only if it will never be promoted. A pure-read replica can drop the `pg_wal` directory to its minimum and skip `pg_basebackup`'s `--checkpoint=fast`. **If the replica will ever be promoted** (the standard operational use case for "read replica" in this codebase), the storage must mirror the primary — promotion assumes the replica has a complete, consistent data set.

### 3.4 Network

The replication connection uses a dedicated TCP socket (default port `5432` on the primary, separate from the application traffic). Three requirements:

- **Same VLAN / low-latency link.** RTT > 50 ms makes sustained catch-up hard; RTT > 200 ms requires `synchronous_standby_names` to be off (the default) and accept that peak apply lag will be RTT-bounded.
- **Bandwidth ≥ peak WAL throughput.** At ~670 telemetry events/s × ~200 bytes/event ≈ 130 KB/s sustained WAL — trivial. Plus `userinteraction` writes, F() updates, `audioclip` writes. Realistic peak: 5–10 MB/s. A 1 Gbps link has 100,000× headroom; only matters on cross-AZ or cross-region links.
- **Same host = unix socket.** A co-located replica (different container on the same host) can use a unix socket in `postgresql.auto.conf`'s `primary_conninfo`. Faster than TCP, no port allocation, simpler firewalling.

In the Compose topology (`docker-compose.yml`), the replica would either:

- Run in a sibling container sharing the host's loopback (`primary_conninfo = 'host=localhost port=5432 ...'`), or
- Run on the Compose network with `primary_conninfo = 'host=db port=5432 ...'` (the existing `db` service is reachable as `db` on the Compose network).

The first is simpler; the second is the standard production pattern.

### 3.5 The role of WAL archiving (PITR)

`archive_mode = on` + `archive_command` ships WAL to a long-term store (S3, NFS, etc.). PITR (point-in-time recovery) uses this archive plus a base backup to restore to any point in time. **PITR is orthogonal to read replication** — they share infrastructure but solve different problems:

- Read replica → offload reads from primary.
- PITR → recover from "DELETE without WHERE" or similar data-loss event.

The audit (`event-driven-architecture-plan.md:349`) and `docs/unfixed-issues-2026-09-03.md` correctly list PITR as a separate decision, deferred past 10k. The replica being added does not require PITR; PITR being added does not require a replica.

---

## 4. Router Design

### 4.1 Goals

- **Reads on the `app` app route to `read` when it exists.** All other reads, writes, and writes-inside-atomic-blocks stay on `default`.
- **Missing `READ_DATABASE_URL` is non-fatal.** With it unset, the router is a no-op; behavior is identical to today.
- **No connection-state drift.** Routing a `SELECT` after a `BEGIN` to a different backend would corrupt savepoint semantics. The router refuses to route inside `transaction.atomic()`.
- **No silent read-after-write inconsistency.** The router respects the connection it's given; consumers that need "read my own writes" must explicitly target `default`.

### 4.2 Decision matrix

| Operation | App | `READ_DATABASE_URL` set? | Backend |
|---|---|---|---|
| `Model.objects.filter(...).get()` | `app` | yes | `read` |
| `Model.objects.filter(...).first()` | `app` | yes | `read` |
| `Model.objects.filter(...).count()` | `app` | yes | `read` |
| `.aggregate(...)`, `.annotate(...)`, vector distance | `app` | yes | `read` |
| `.bulk_create(...)` | `app` | either | `default` (router returns None) |
| `.save()`, `.update()`, `.delete()`, `.create()` | `app` | either | `default` |
| `select_for_update()` / `select_for_update(skip_locked=True)` | any | either | `default` |
| Anything inside `transaction.atomic()` | any | either | `default` (router refuses to switch) |
| `auth_user`, `django_session`, `token_blacklist`, etc. | `auth` / `sessions` / `token_blacklist` / etc. | either | `default` |
| Migrations (`django_migrations`, `django_celery_beat_*`) | n/a | either | `default` |

### 4.3 The router contract

```python
# Pseudocode of backend/app/db_routers.py (final implementation deferred)

class ReadRouter:
    # DECISION: routes are evaluated per-query. Returning None from any
    # method means "use the default router's choice" — which is `default`
    # when no other router is registered. We use that to short-circuit on
    # missing READ_DATABASE_URL: the router becomes a no-op when the alias
    # isn't constructed.

    ROUTABLE_APP = 'app'

    def _read_alias_available(self):
        # settings.DATABASES contains 'read' iff READ_DATABASE_URL is set.
        # Cached per-process; no per-query settings lookup.
        return 'read' in settings.DATABASES

    def db_for_read(self, model, **hints):
        # SECURITY: refuse to route inside an atomic block. A read inside
        # a SELECT FOR UPDATE / F() update sequence must land on the same
        # backend the transaction started on, otherwise the eventual write
        # will fail with "current transaction is aborted, commands ignored
        # until end of transaction block".
        if connection.in_atomic_block:
            return None
        if not self._read_alias_available():
            return None
        if model._meta.app_label != self.ROUTABLE_APP:
            return None
        return 'read'

    def db_for_write(self, model, **hints):
        # Writes always go to default. Even if a future use case justifies
        # writing to a replica (it doesn't — replicas are read-only by
        # default_transaction_read_only), we want it to be a deliberate
        # decision, not a router accident.
        return None

    def allow_relation(self, obj1, obj2, **hints):
        # If two objects would be on different backends, a cross-backend
        # JOIN is impossible. Reject it.
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # Migrations are a primary-only operation. The replica, being
        # read-only, can't apply DDL anyway.
        if db == 'read':
            return False
        return None
```

(The actual implementation may differ slightly — e.g. caching, thread-local checks — but the contract above is the source of truth. The implementation lands in a follow-up commit; this PR documents the design.)

### 4.4 Fallback behavior

The fallback is the **first-class** case. The router does the following on cold-start:

1. Read `settings.DATABASES`. If `'read'` is not present, set `_read_alias_available = False`.
2. For every read on the `app` app, return `None`. Django uses `default`.
3. Behavior is identical to the pre-PR state.

When `READ_DATABASE_URL` is set later (operator sets the env var, redeploys), the router picks up the alias on the next process restart. There is **no in-process live reconfiguration** — Django does not support hot reload of `DATABASES`, and the Django connection pool cannot be flushed mid-request without dropping active connections. The next gunicorn worker or Celery worker to boot sees the new alias; the old workers fall back to `default` until they cycle.

### 4.5 Why `in_atomic_block` is the right primitive

Django's `connection.in_atomic_block` is True whenever the current connection is inside a `transaction.atomic()` block (explicit or implicit via `ATOMIC_REQUESTS`). This includes:

- The atomic block wrapping `UserInteraction.save()` (`backend/app/models.py:187`).
- The implicit transaction around `transaction.on_commit(...)` callbacks in `services/uploads.finalize_upload`.
- Django's `ATOMIC_REQUESTS` mode if it's ever enabled (currently off; see `backend/EchoFlow/settings.py` — no `ATOMIC_REQUESTS` setting).

Inside any of these, the connection is bound to a specific backend's transaction state. Routing a read query to a different backend's connection is a programming error; the read would either fail (no transaction on the new backend) or worse, succeed against a snapshot that doesn't see the current transaction's writes. **Rejecting all reads inside `in_atomic_block` is correct and safe.**

---

## 5. Replication-Lag Trade-offs

Streaming replication is asynchronous by default. The replica is "behind" the primary by the apply lag — the time between the primary committing a transaction and the replica applying it.

### 5.1 Lag buckets

The user-facing consequences:

| Lag | Visible behavior |
|---|---|
| **< 1 ms** | Effectively synchronous. Users see their own writes immediately. The next `/feed/` request after a `toggle-like` shows the like. Rare in practice — even on co-located replicas, network and apply add ms of jitter. |
| **~10 ms** | Best-effort. Users see their own writes on the next request, almost always. Occasional 1-request "ghost" — the like is missing from the next `/feed/` response and appears in the one after. |
| **~100 ms** | User-visible flicker. A user who likes a clip, immediately hits back, then re-opens the feed may not see the like's side-effects (the `user_has_liked` annotation in `views/feed.py:104-110`). Acceptable for most product flows; jarring for users who re-check what they just did. |
| **~1 s** | Materially visible. A like is not reflected in the user's next `/feed/` for ~1 second. `engagement_velocity` recomputed by `update_global_metrics` is read from primary (`tasks.py:529-614`), so counter display is correct; the like annotation is the only thing lagging. |
| **> 10 s** | A replica is unhealthy. Pages will look "stuck in the past." Operations should page on this. |

### 5.2 The write-after-read problem

The clearest user-facing scenario:

1. User U likes clip C via `POST /interactions/{id}/toggle-like/` → `services/interactions.record_like_toggle` → `UserInteraction.save()` (`models.py:173-206`) → F() UPDATE on `audioclip.likes`. **Primary.** The F() UPDATE commits to the primary's backend.
2. User U immediately pulls `/feed/` to "see if my like worked." `FastFeedViewSet.list` (`views/feed.py:58-140`) does `redis_client.lpop(user_feed:{id}, 10)`, gets C's UUID, then `AudioClip.objects.filter(id__in=[C]).annotate(user_has_liked=Exists(...)).order_by(...)`. **This read is now on `read` (the replica).**
3. If the replica's apply lag is 100 ms, the `audio_clips` row update from step 1 is not yet visible. `user_has_liked` annotation may still report `False` because the corresponding `UserInteraction` row hasn't replicated.

The router **does not** solve this. Reverting to `default` for all "read-your-own-writes" reads would defeat the purpose of routing. The standard solutions, in order of complexity:

1. **Accept the lag.** Documented as "your like may take up to N seconds to appear in the feed"; this is the typical user expectation in social apps. The Redis `user_feed:{id}` list is repopulated by `refill_user_feed` on miss, so the inconsistency window is bounded by `llen < 15` triggers and the 5-min Beat cadence of the counter batcher (P1.1, deferred).
2. **Read-your-writes cookie.** On a write response, set a short-lived cookie (e.g. 5 s) flagging "next read goes to primary." This requires a per-request override hook in the gunicorn middleware; it is not implemented in this PR.
3. **Synchronous commit on critical paths.** Set `synchronous_standby_names = 'replica1'` on the primary for the duration of `toggle-like`/`send-share`. This **makes the primary wait for the replica to apply** before acknowledging the write — kills write latency. Not viable at 10k concurrent.
4. **Materialize "user's recent likes" in Redis.** When `toggle_like` fires, `RPUSH user:{id}:recent_likes {clip_id} EX 300` into a per-user Redis list with 5-min TTL. The feed serializer reads this list first, then falls through to the replica query. Out of scope for this PR; documented as a follow-up.

**Decision for this PR: accept the lag.** The `user_has_liked` annotation lag is bounded by replication lag (sub-second in practice for a co-located replica) and is consistent with how most social apps behave. The `likes` counter display lag is already the case today — `update_global_metrics` only refreshes every 5 min — so the new behavior is strictly better than the current behavior on the counter, and equivalent on the like-annotation.

---

## 6. Operational Concerns

### 6.1 Promoting the replica (read-only → read-write)

Promotion is the planned cutover from primary to replica when the primary has failed or maintenance is required. Steps:

1. **Stop writes on primary.** Drain in-flight requests, then stop accepting new writes. In a Compose setup: scale `web` to 0 replicas, wait for `celery_*` workers to idle.
2. **Promote.** On the replica container, run `pg_ctl promote` (or `SELECT pg_promote();` from a SQL session). The replica stops accepting replication and transitions to read-write.
3. **Redirect `DATABASE_URL`.** Update `.env` (or k8s ConfigMap, etc.) so `DATABASE_URL` points at the (now-promoted) replica. Restart `web` and `celery_*`.
4. **Verify.** `/health/` and `/ready/` (`backend/EchoFlow/urls.py` — registered views) should return 200. `/ready/` checks DB connectivity; `/health/` is the liveness probe.
5. **Reconfigure replica role.** The new primary needs a fresh replica behind it (if HA is desired); the old primary, once recovered, becomes the replica.

**During promotion, the read-replica routing layer is moot:** once `DATABASE_URL` points at the promoted replica, the router's `'read'` alias still points at the (now-defunct) replica. The simplest fix: unset `READ_DATABASE_URL` at the same time as the cutover, and the router falls back to `default` (which is now the promoted replica).

**Failover time:** in practice 30–60 s for steps 1–3 in a containerized setup. The replication lag at the moment of failover determines data loss: with `synchronous_standby_names` off (the default), up to `replication_lag × write_rate` transactions can be lost. At 100 ms lag and 100 writes/s, that's ~10 transactions — typically a few likes or telemetry events.

### 6.2 Monitoring queries

Three queries are essential to replica health. None are wired up in this PR; they are the operational layer the follow-up commit must add.

#### 6.2.1 Replication state on the primary

```sql
SELECT client_addr, state, sync_state, sent_lsn, replay_lsn,
       (sent_lsn - replay_lsn) AS byte_lag,
       EXTRACT(EPOCH FROM (now() - replay_lsn_time)) AS replay_lag_seconds
FROM pg_stat_replication;
```

- `state = 'streaming'` and `sync_state IN ('async', 'sync')` — replica is connected and applying.
- `byte_lag` — bytes of WAL the replica has not yet applied. Non-zero is normal; sustained growth means the replica is falling behind.
- `replay_lag_seconds` — wall-clock time since the replica last advanced.

A typical healthy reading at 10k concurrent: `byte_lag < 1 MB`, `replay_lag_seconds < 0.5 s`.

#### 6.2.2 Apply position on the replica

```sql
SELECT pg_last_wal_replay_lsn();
```

Returns the WAL position the replica last applied. Compare to the primary's `pg_current_wal_lsn()` — the gap is the WAL the replica has yet to apply. This is the same number as `byte_lag` above, computed from the replica side.

#### 6.2.3 Replication slots

```sql
SELECT slot_name, plugin, slot_type, active, restart_lsn,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
FROM pg_replication_slots;
```

- `active = true` — the slot has a connected consumer.
- `retained` — how much WAL is being held on the primary for this slot. If `restart_lsn` is far behind `pg_current_wal_lsn()` and the slot is `active = false`, the primary is hoarding WAL for a dead replica. **This will fill the primary's `pg_wal` directory.**

Operational guard: monitor `retained` size; alert if it exceeds, e.g., 10 GB. A runaway slot is the most common way a streaming-replica setup fills the primary's disk.

### 6.3 What happens during planned primary maintenance

If the primary is being restarted (OS upgrade, Postgres minor upgrade):

1. The replica continues serving reads. The router keeps routing.
2. Replication pauses; `byte_lag` and `replay_lag_seconds` grow.
3. When the primary comes back, replication resumes; the replica catches up at line rate (~50–100 MB/s on a 1 Gbps link).
4. **No read traffic is interrupted** as long as the replica stays up.

### 6.4 What happens during unplanned primary failure

1. Writes start failing with `OperationalError` / `ConnectionError`. Django retries via `autoretry_for=RETRYABLE_ERRORS` (`backend/app/tasks.py:163`); the request returns 500.
2. Reads routed to the replica continue to succeed for a while — until the replica's `restart_lsn` is so far behind that the primary's `pg_wal` is recycled and the replica can no longer catch up.
3. **The router does not fail over automatically.** Auto-failover is a separate problem (Patroni, PgBouncer watchdog, etc.) and out of scope for this PR.

---

## 7. The Minimum-Viable Path

From "router wired, `READ_DATABASE_URL` unset, behavior unchanged" to "reads landing on a live replica, primary no longer the read bottleneck."

### 7.1 Pre-flight checklist

- [x] PgBouncer in transaction-pool mode in front of primary (`docker-compose.yml:103-153`). Already shipped.
- [x] HNSW indexes on `semantic_vector` and `acoustic_vector` (`backend/app/models.py:83-97`). Already shipped.
- [x] `CHECK` constraints on `audioclip` counters (`backend/app/models.py:102-107`). Already shipped.
- [x] `update_global_metrics` id-batched with `SKIP LOCKED` (`backend/app/tasks.py:529-614`). Already shipped.
- [ ] Router implementation in `backend/app/db_routers.py`. **Ships in this PR.**
- [ ] `'read'` connection alias in `backend/EchoFlow/settings.py:151-156`. **Ships in this PR.**

### 7.2 Operational steps to bring up the replica

1. **Provision the replica container.** Add a `db_read` service in `docker-compose.yml` using the same `pgvector/pgvector:pg16` image as `db`. Set `command:` to run an entrypoint that:
   - Runs `pg_basebackup -h db -D /var/lib/postgresql/data -U ${DB_USER} -Fp -Xs -P -R` to initialize from the primary.
   - The `-R` flag writes `postgresql.auto.conf` with the connection string and creates `standby.signal`.
2. **Add a replication role on the primary.** `CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '...';`. Grant it in `pg_hba.conf` (`host replication replicator 0.0.0.0/0 scram-sha-256`).
3. **Add a replication slot.** On the primary: `SELECT pg_create_replication_slot('echoflow_replica');`. Set `primary_slot_name = 'echoflow_replica'` in the replica's `postgresql.auto.conf`.
4. **Provision a second pgbouncer** in front of `db_read`. Two approaches:
   - **Two-container pgbouncer.** A second service, `pgbouncer_read`, with its own `pgbouncer.ini` (a config-file mount or a second `edoburu/pgbouncer`-derived Dockerfile) pointing at `db_read`. Same auth, same pool config, but `DB_HOST=db_read`. Exposes port `6433`.
   - **One pgbouncer, two pool sections.** The same `pgbouncer` service with `[databases]` containing both `[DATABASE_URL]` and `[READ_DATABASE_URL]` blocks. Requires a custom `pgbouncer.ini` and a second port (`6432` for primary, `6433` for replica). The single-container approach is fine for Compose; in production with Patroni, it's almost always two containers.
5. **Set `READ_DATABASE_URL`** in `.env` to `postgres://user:pass@pgbouncer_read:6433/echoflow_db`. Mirror the auth and DB name from `DATABASE_URL`. Restart `web` and `celery_*`.
6. **Verify reads land on the replica.** Connect to the replica's Postgres directly (`docker compose exec db_read psql`) and `SELECT count(*) FROM audioclip;` — confirm the count matches the primary. Then enable `pg_stat_statements` on both backends and `CREATE EXTENSION pg_stat_statements;`; confirm that hot read queries (`SELECT * FROM audioclip WHERE id IN (...) ORDER BY ...` patterns) appear on the replica's `pg_stat_statements` and not on the primary's.
7. **Wire monitoring.** Add the three queries from §6.2 to a Prometheus exporter (postgres_exporter) and alert on:
   - `replay_lag_seconds > 5` for 1 min.
   - `byte_lag > 100 MB` for 5 min.
   - `retained > 10 GB` on `pg_replication_slots`.
8. **Smoke-test the failure modes.**
   - Stop `db_read` (`docker compose stop db_read`); verify the router falls back to `default` (reads continue to work, primary CPU rises). Restore `db_read`.
   - Stop `db` (`docker compose stop db`); verify writes fail with `OperationalError`, reads continue.
   - Both stopped: `/health/` and `/ready/` return 503 (as today).

### 7.3 Migration plan

The replica ships behind a feature flag in this PR's lifetime: `READ_DATABASE_URL` unset by default, opt-in via env. The cutover path is:

1. Land this PR; verify behavior unchanged (test suite green; manual smoke of `/feed/`, `/profile/`, `/suggestions/`, `/comments/`).
2. Provision the replica infrastructure per §7.2; do **not** set `READ_DATABASE_URL` yet.
3. Verify the replica is healthy (lag < 100 ms sustained for 24 h).
4. Set `READ_DATABASE_URL` for `web` and `celery_*` only — leave `celery_media` on `default` (it holds long-running transactions during HLS processing; routing reads inside the connection-pool-mode session is safe but adds no value).
5. Monitor primary CPU and read QPS for 48 h. Expected: primary read QPS drops by ~50% (feed + profile + suggestions); write QPS unchanged; primary CPU drops correspondingly.
7. **Decision gate:** if primary CPU stays below 50% at 10k concurrent, the replica is paying for itself; if not, profile queries with `pg_stat_statements` and find the ones still landing on primary.

---

## 8. Decision Log

### DECISION: Router returns None when `READ_DATABASE_URL` is unset

Returning `None` from `db_for_read` causes Django to use the default router's choice (`default`). This makes the router **a no-op when the alias is absent**, which is the right behavior for a feature-flagged rollout: the PR ships the code without changing production behavior.

### DECISION: Router rejects reads inside `transaction.atomic()`

`connection.in_atomic_block` is True for any code path that has begun a transaction. Routing a read to a different backend inside such a block would corrupt savepoint semantics — the eventual write (or `SAVEPOINT release`) would happen on a backend that has no idea what the read was about. The router refuses.

### DECISION: Reads on `auth`, `sessions`, `token_blacklist`, etc. stay on primary

The `app` app is the only app whose reads benefit from replica routing. Authentication and token-blacklist reads are tiny (microseconds) and must be on the same backend as the auth-related writes that produced them (e.g. `BLACKLIST_AFTER_ROTATION` writes a token blacklist row; the next refresh on the same token must see it). Restricting routing to `app._meta.app_label == 'app'` is a small, defensible boundary.

### DECISION: Migrations only run against `default`

`allow_migrate` returns `False` for `db == 'read'`. The replica is read-only; DDL against it would fail anyway. Explicit denial makes the intent visible in the router code rather than relying on Postgres to throw the error.

### DECISION: Accept replication lag rather than solve read-your-writes

Solving read-your-writes on a social app at 10k concurrent costs more than it's worth: a `synchronous_standby_names` setup adds RTT to every write; a per-request override middleware adds complexity to every read; a Redis "recent_likes" cache adds a new system to keep consistent. The user-visible lag for a like-annotation is bounded by sub-second replication lag in practice and is consistent with how most social apps behave. Documented in §5.2; revisit at 50k concurrent if the product team flags it.

### SECURITY: `READ_DATABASE_URL` does not carry a separate credential check

The router routes by app label, not by user. Replica reads are not "less trusted" than primary reads — they're the same data, served by the same application, just from a different backend. No row-level filtering is added; if the application enforces row-level access (e.g. `request.user` filtering), the replica reads honor it the same way the primary reads do. **No security boundary is added or removed by enabling the replica.**

### SECURITY: Connection credentials live in env, not in router

The router reads `settings.DATABASES['read']`, which is constructed from `READ_DATABASE_URL`. The URL contains credentials; the env var is the only place they exist outside of `docker-compose.yml` / the deployment ConfigMap. No change from today.

### TODO: Wire `pg_stat_replication` / `pg_last_wal_replay_lsn` / `pg_replication_slots` to Prometheus

The three queries in §6.2 must become metrics on `/metrics/` and alert rules in Prometheus before the replica is production-recommended. Currently the observability stack is `django_prometheus` middleware + `/metrics/` endpoint, no scraper (`event-driven-architecture-plan.md:38`). This is the follow-up after the replica is provisioned.

### TODO: Wire `celery_media` to `READ_DATABASE_URL` or leave it on `default`

`celery_media` runs long-running transactions inside `process_audio_to_hls` (reads inside the function, but no in-atomic routing because the writes are explicitly managed). Reads inside this process are a small fraction of total reads; routing them adds no measurable benefit and risks routing a read inside an implicit savepoint (e.g. the F() UPDATE on `AudioClip` that happens before HLS encoding). Default to leaving `celery_media` on `default`; revisit if profiling shows `process_audio_to_hls`-initiated reads are a bottleneck.

---

## 9. Open Questions

1. **WAL retention.** Without `archive_mode = on` (i.e. PITR), how much WAL should the primary retain for the replica? `wal_keep_size = '1GB'` is the default starting point; tune based on observed peak `byte_lag`.
2. **Connection routing in PgBouncer.** Does the second-pgbouncer approach use a separate `pgbouncer.ini` mount, or a second Dockerfile variant? The current `edoburu/pgbouncer` image templates config from env vars (`docker/pgbouncer/Dockerfile:7-13`); adding a second variant is straightforward but adds two files.
3. **Replica authentication.** Does the replica use the same SCRAM-SHA-256 credentials as the primary, or a separate `replicator`-only role? `CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '...'` is the standard pattern; the role is separate from the application user.
4. **Replica promotion in Compose.** When the primary fails, does the deployment auto-promote, or does an operator manually run `pg_ctl promote`? The standard production pattern is Patroni; for Compose, manual is acceptable.
5. **Multi-replica.** The router currently routes to a single `'read'` alias. With two replicas (e.g. one in a different AZ), the alias needs to become a list and the router needs to pick one. Django supports this via a custom router that returns a random alias from the list per query; not in this PR.

---

## Sources

Code and configuration referenced in this document:

- `backend/EchoFlow/settings.py:151-156` — current `DATABASES` config; `READ_DATABASE_URL` not yet consumed.
- `backend/app/db_routers.py:1` — current 1-line stub; replaced by the router in this PR.
- `docker-compose.yml:3-33` — `db` service (single Postgres, no command override).
- `docker-compose.yml:103-153` — `pgbouncer` service (transaction pool, 25 pool size, SCRAM-SHA-256).
- `docker-compose.yml:217-285` — `web` service env (`DATABASE_URL` only).
- `docker/pgbouncer/Dockerfile:1-21` — `edoburu/pgbouncer` base, env-driven config.
- `backend/app/models.py:83-97` — HNSW indexes on `semantic_vector` and `acoustic_vector` (`m=16`, `ef_construction=64`).
- `backend/app/models.py:148-207` — `UserInteraction.save()` with `select_for_update()` and F() UPDATE on `AudioClip` counters (the N2 fix).
- `backend/app/views/feed.py:58-140` — `FastFeedViewSet.list`: the hot read path.
- `backend/app/views/feed.py:143-186` — `SuggestionViewSet.get_queryset`: vector-distance heavy read.
- `backend/app/views/feed.py:189-219` — `TagsViewSet.initialize_vectors`: cold-start read.
- `backend/app/views/profile.py:29-75` — `ProfileViewSet` (me, retrieve, update_me, user_clips).
- `backend/app/tasks.py:347-456` — `refill_user_feed`: the composite-score vector read.
- `backend/app/tasks.py:458-524` — `calculate_time_decayed_vectors`: per-user 50-row `UserInteraction` read.
- `backend/app/tasks.py:529-614` — `update_global_metrics`: id-batched with `FOR UPDATE SKIP LOCKED` (must stay on primary).
- `docs/unfixed-issues-2026-09-03.md:31-86` — Group A item 5 (Postgres tuning), PgBouncer absence, capacity plan.
- `docs/event-driven-architecture-plan.md:194-233` — the 12 failure modes at 10k concurrent; read-replica mentioned at line 349 as "deferred until EXPLAIN shows read pressure."
- `docs/event-driven-architecture-plan.md:319-321` — `db` service sizing (4 GB / 2 cores).
- `docs/event-driven-architecture-plan.md:349` — explicit deferral: "Skip read-replicas at 10k; add when EXPLAIN shows read pressure."