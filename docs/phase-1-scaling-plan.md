# EchoFlow — Phase 1 Scaling Plan: Path to 10,000 Live Users

> **Date:** 2026-09-02  
> **Scope:** Realistic 10K concurrent live-user target (not 1M aspiration)  
> **Source docs:** `docs/scaling-analysis.md` (2026-08-19, aspirational 1M+ vision), `docs/backend-architecture-audit.md` (critical bottleneck audit)  
> **Codebase state:** Django 5.2 monolith, pgvector HNSW, Redis single instance, MinIO S3-compatible, Celery 3-queue, no CI/CD, no observability beyond `/metrics/`

---

## CHANGE SUMMARY — What Changed Since `scaling-analysis.md`

The `scaling-analysis.md` (written 2026-08-19) describes a **Phase-1 architecture for 10K concurrent users** that includes API Gateway, service boundaries, PgBouncer, Redis Cluster, and CDN delivery. **None of this exists in code.** The codebase is in the state described as "Phase 0 — single Django + single PostgreSQL + single Redis."

Specific gaps found during inspection that affect this plan:

- `scaling-analysis.md` assumes `refill_user_feed` uses pre-computed batches (line 323-337). **Actual code:** `refill_user_feed` is an on-demand Celery task (`tasks.py:505-569`) that computes cosine distance against the full DB and pushes a single Redis LIST per user. There is also a **cold-start weights bug** (`tasks.py:558-561`) where the `weights` list is never populated.
- The doc assumes read replicas for feed queries (line 183-188). **Actual:** Zero read replicas; all feed queries hit the primary DB (`client.py:131` uses default DB router).
- The doc assumes Kafka migration for message queues at 100K+ (line 401-407). **Actual:** Redis Celery broker only; `CELERY_TASK_ROUTES` defines `fast_feed` and `heavy_media` but both share the same Redis instance (`settings.py:151-164`).
- The doc assumes `update_global_metrics` uses streaming/aggregation. **Actual:** Raw SQL full-table `UPDATE audioclip SET engagement_velocity = ... WHERE status='ready'` (`tasks.py:640-666`) with zero batching — this **will deadlock at ~100K clips**.
- The doc describes 4-layer caching (L1-L4). **Actual:** Only Redis feed queues; no `cache.set()` for user profiles, clip metadata, or rate limits anywhere in `views.py`.
- The doc lists Kubernetes + ArgoCD for Phase 2. **Actual:** `docker-compose.yml` only; `docker-compose` has zero auto-scaling policies (`deploy.resources` defines hard limits: web=2CPU/1GB, db=2CPU/2GB, redis=1CPU/1GB).
- The doc mentions `minio` / S3 storage correctly, but the code relies on `STORAGES["default"]` pointing to MinIO (`settings.py:148-156`) — this is actually one of the *better* implemented pieces.

---

## USE CASE ANALYSIS — Why This Plan Looks Different From 1M Vision

EchoFlow is a **social audio discovery platform**, not a pure video feed:

- **Media is long-form audio (30-120s clips)** — not 15-second TikTok loops. Each stream = ~10 clips/session = ~5-15 minutes of engagement.
- **Vector recommendations are dual-mode** — 384-dim semantic (transcript via Whisper) + 128-dim acoustic (librosa features). This means every feed request involves **two cosine-distance computations** (not one).
- **Personalization is real-time** — users expect new clips in feed within minutes of upload. Batch-only feed (nightly) is unacceptable.
- **Interaction telemetry is high-frequency** — swipe/skip/like events stream continuously. At 10K concurrent users scrolling every 8s = ~1,250 telemetry requests/sec (see `backend-architecture-audit.md:46-52`).
- **Media processing is CPU/GPU-heavy** — Whisper transcription + sentence-transformers + librosa + FFmpeg HLS. This is not stateless; it requires 2-4GB RAM per upload and 30-120 seconds per clip.

**Implication:** At 10K users, the bottleneck is **not just "more users" — it is the collision of (high-frequency telemetry writes + real-time vector feed computation + heavy media processing + social graph)** on a single database node. The 1M vision treats this as a distributed-systems problem; at 10K it is a **database + queue + caching + compute-isolation problem**.

---

## PHASE 1: TO 10K LIVE USERS (Months 1-3 — Do These First)

### PHASE 1.0: IMMEDIATE SURVIVAL (Week 1-2)

> **Root cause:** System will collapse within hours at 10K concurrent due to DB connection exhaustion, full-table metric updates, and Redis memory spike.

### 1. CONNECTION POOLING — Deploy PgBouncer (P0)

**What needs to change:** Add `PgBouncer` container/service between `web`/`celery` and `db`.

**Why:** Django's `CONN_MAX_AGE=600` keeps connections open. With 4 gunicorn workers × 4 threads + 3 Celery workers + Celery Beat = ~30 persistent connections. At 10K concurrent, Django opens new threads continuously; PostgreSQL `max_connections` (default 100) exhausts and new requests hang.

```yaml
# docker-compose.yml ADDITION (near db service)
  pgbouncer:
    image: pgbouncer/pgbouncer
    environment:
      DATABASES_HOST: db
      DATABASES_PORT: 5432
      DATABASES_DATABASE: echoflow_db
      POOL_MODE: transaction  # DECISION: transaction mode for Django; session mode breaks Celery
      MAX_CLIENT_CONN: 10000
      DEFAULT_POOL_SIZE: 25
    ports:
      - "6432:5432"
```

**Comment explaining change:**  
`// DECISION: PgBouncer in transaction mode instead of session. Django opens/closes connections per request (with CONN_MAX_AGE). Transaction mode multiplexes thousands of Django connections onto ~25 real PG connections. Tradeoff: GET/SET session variables broken; not needed here.`

**External dependency:** No new package needed. Update Django `DATABASES["default"]["HOST"]` to `pgbouncer` (or use env `DB_HOST=pgbouncer`).

---

### 2. FIX `update_global_metrics` — Batch By Cursor (P0)

**What needs to change:** `tasks.py:640-666` (`update_global_metrics`).

**Current broken code (line 640-666):**
```python
# Raw SQL — updates ALL ready clips, no LIMIT, full table scan + join
AudioClip.objects.filter(status='ready').update(
    engagement_velocity=...  # computed in raw SQL using subquery
)
```

**Why it will break at 10K users:** At ~100K clips, this query takes 30-90 seconds and acquires a row-level lock or table-level share lock (depending on PostgreSQL version and isolation level). At 100K concurrent users with 1M+ clips, the query overlaps with the next 5-minute cron execution → cascading lock contention → DB CPU 100% → feed requests timeout.

**Required change:** Replace with cursor-based pagination in Python, processing 5,000 rows per Celery task invocation.

```python
# tasks.py REPLACEMENT (add cursor tracking)
from django.db import connection

def update_global_metrics_batch(start_id=0, batch_size=5000):
    # DECISION: Cursor pagination (id > last_id) instead of OFFSET
    # Tradeoff: Skips newly inserted IDs between batches, but avoids OFFSET cost at >100K rows
    with connection.cursor() as cursor:
        cursor.execute("""
            UPDATE audioclip SET engagement_velocity = ...
            FROM (
                SELECT id, (likes + shares*2) / ... AS ev
                FROM audioclip
                WHERE status = 'ready' AND id > %s
                ORDER BY id ASC LIMIT %s
                FOR UPDATE SKIP LOCKED
            ) sub
            WHERE audioclip.id = sub.id
            RETURNING audioclip.id
        """, [start_id, batch_size])
        last_id = cursor.fetchone()[0] if cursor.fetchone() else start_id
    # Schedule next chunk if more work
    if batch_size == 5000:
        update_global_metrics_batch.delay(start_id=last_id, batch_size=5000)
```

**Comment explaining change:**  
`// DECISION: Cursor pagination with SKIP LOCKED instead of OFFSET. OFFSET on 1M rows requires scanning 1M rows per batch. Cursor scans only new rows. Tradeoff: If IDs are non-monotonic (deleted/reinserted), skips possible; acceptable for metrics.`

---

### 3. FIX `refill_user_feed` — Make Atomic + Fix Cold-Start Bug (P0)

**What needs to change:** `tasks.py:505-569` (feed refill) and `views.py:138-140` (queue drain guard).

**Actual bugs found:**
- `weights = []` (line 558) never populated in cold-start branch (line 566: `else: # cold start` uses `long_term_semantic` but weights not set).
- Queue length check (`llen`) + `lpop` are not atomic (`views.py:138-140`). At 10K users, race conditions cause double-refills (wasting DB queries) or empty-queue crashes.

**Required change:** Use Lua script for atomic pop + check. Fix weights array.

```python
# views.py REPLACEMENT for atomic drain
# Lua script: check length, if >= 10 return list, else return None (trigger refill)
ATOM_PULL = """
local len = redis.call('llen', KEYS[1])
if len >= 10 then
    return redis.call('lrange', KEYS[1], 0, 9)
else
    return nil
end
"""
```

**Comment:**  
`// HACK: Redis doesn't have atomic "pop first 10" with length check. Lua script executes atomically on server. Tradeoff: Requires Redis Lua support (available in 7.x); if Redis Cluster is used, Lua must target a single shard key (user_id hashes to same shard — correct for user_feed:{id}).`

---

### 4. ADD RATE LIMITING TIERS (P0)

**What needs to change:** `settings.py:317-324` (DRF throttle settings) and `views.py` (feed endpoint specifically).

**Current:** Only `anon: 100/hr`, `user: 1000/hr`. At 10K users, 1000/hr = 0.28/sec/user — too restrictive for feed scrolling, too loose for telemetry spam.

**Required:** Add endpoint-specific tier limits using Redis token bucket.

```python
# backend/app/throttle.py — NEW FILE
# DECISION: Redis-based token bucket instead of DRF simple throttle
# Tradeoff: Requires Redis always-on; if Redis down, falls back to allow-all (fail-open for availability, fail-closed for abuse)
class TieredRateLimiter:
    def __init__(self, redis_client):
        self.r = redis_client

    def is_allowed(self, key, limit, window):
        # Token bucket algorithm — already documented in docs/scaling-analysis.md
        # Implementation from doc is correct; deploy it.
        pass
```

**Comment:**  
`// DECISION: Fail-open (allow if Redis down) instead of fail-closed. For a social feed, downtime is worse than temporary spam. Tradeoff: Attackers can DoS by knocking out Redis; mitigate with Redis Sentinel/Cluster HA in Phase 1.`

---

### 5. REDIS SPLIT — Separate Broker From Cache (P0)

**What needs to change:** `docker-compose.yml` (add `redis_cache` and `redis_broker` services), `settings.py:151-164`.

**Current:** `redis` handles Celery broker, result backend, feed queues, session storage. If feed queues spike (viral clip), broker messages are delayed. If Celery fails, feeds go blank.

**Required:** Two Redis instances.

```yaml
# docker-compose.yml ADDITIONS
  redis_broker:
    image: redis:7-alpine
    command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
  redis_cache:
    image: redis:7-alpine
    command: redis-server --maxmemory 1gb --maxmemory-policy allkeys-lru
```

**Comment:**  
`// DECISION: Two instances instead of Cluster (too complex for Phase 1). Tradeoff: No automatic sharding; feed keys must stay on one instance (ok for 10K users — 50MB memory). At 100K users, upgrade to Redis Cluster.`

Update `settings.py`:
```python
CELERY_BROKER_URL = os.environ.get('REDIS_BROKER_URL', 'redis://redis_broker:6379/0')
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
# Feed caches point to redis_cache
FEED_REDIS_URL = os.environ.get('REDIS_CACHE_URL', 'redis://redis_cache:6379/1')
```

---

### 6. ADD READ REPLICA (P1 — Can wait until DB reaches 70% CPU)

**What needs to change:** Add `db_read` service; configure Django database router.

**Why:** `FastFeedViewSet` performs `CosineDistance` queries that read `semantic_vector` and `acoustic_vector`. At 10K concurrent users scrolling continuously, feed queries dominate DB load (90%+ of queries). Write operations (`UserInteraction.save()`, `update_global_metrics`) are minority.

```python
# backend/app/db_routers.py — CURRENTLY STUB (line 1)
class FeedReadRouter:
    def db_for_read(self, model, **hints):
        if model.__name__ in ('AudioClip', 'User', 'Comment'):
            return 'feed_read'
        return 'default'
    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == 'default'
```

**Comment:**  
`// DECISION: Application-level routing (Django db_routers) instead of PgBouncer query splitting. Tradeoff: Must maintain router logic; PgBouncer split requires connection-level routing (harder with Django ORM). At 10K users, single read replica is sufficient.`

---

### 7. ADD CANDIDATE GENERATION TO FEED (P1)

**What needs to change:** `tasks.py:522-537` (`refill_user_feed`).

**Current:** Direct `annotate(CosineDistance(...)).order_by('-composite_score')` against all clips.

**Required:** Two-stage pipeline — (a) ANN retrieval for top 500 candidates using HNSW, (b) composite scoring on 500.

```python
# tasks.py REPLACEMENT — candidate retrieval first
from django.db.models import F
# Stage 1: ANN retrieval (uses HNSW index automatically via ORDER BY ... <-> ...)
candidates = AudioClip.objects.filter(
    status='ready',
    category__in=user.tags.values_list('name', flat=True)  # optional filter
).annotate(
    sem_dist=CosineDistance('semantic_vector', user.long_term_semantic_vector),
    ac_dist=CosineDistance('acoustic_vector', user.long_term_acoustic_vector)
).order_by('sem_dist', 'ac_dist').values_list('id', flat=True)[:500]
# Stage 2: Score only candidates (fast)
scored = AudioClip.objects.filter(id__in=candidates).annotate(
    composite_score=...  # existing formula
).order_by('-composite_score')[:20]
```

**Comment:**  
`// DECISION: pgvector HNSW ANN retrieval instead of full-table scan. Tradeoff: HNSW is approximate (recall ~95% at m=16, ef=64). At 10K users, acceptable; at 1M users need Qdrant/Milvus. Keep pgvector to avoid new dependencies.`

---

### 8. SCALE MEDIA WORKERS (P1)

**What needs to change:** `docker-compose.yml:260-284` (celery_media service).

**Current:** `--pool=solo` (1 process only) with 4 CPU / 1 GB RAM.

**Required:** Remove `solo`; use prefork with 2-4 workers; separate GPU/CPU nodes if available.

```yaml
# docker-compose.yml CHANGE
celery_media:
  command: >
    celery -A backend.EchoFlow worker -Q heavy_media --pool=prefork --concurrency=2 --loglevel=info
  deploy:
    resources:
      limits:
        cpus: '4.0'
        memory: 4G
  # Note: If GPU available, add --pool=prefork --concurrency=1 with GPU isolation
```

**Comment:**  
`// DECISION: Prefork instead of solo, concurrency=2 (not 4) to prevent OOM. Each worker loads Whisper (1.5GB) + embeddings (0.5GB) = 2GB per process. 2 workers = 4GB total fits limit. At 10K users with 100 uploads/min, need 3-4 containers of this type.`

---

### 9. ADD DEAD LETTER QUEUES + RETRY CONFIG (P1)

**What needs to change:** `settings.py:163-164`, `tasks.py` (all tasks).

**Current:** `CELERY_TASK_ACKNOWLEDGE_LATE=True`; no retry policy on most tasks; no DLQ.

**Required:** Configure retries, dead-letter routing.

```python
# settings.py ADDITION
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_TASK_ROUTES = {
    'backend.app.tasks.process_audio_to_hls': {'queue': 'heavy_media'},
    'backend.app.tasks.refill_user_feed': {'queue': 'fast_feed'},
    'backend.app.tasks.update_global_metrics_batch': {'queue': 'fast_feed'},
}
CELERY_TASK_DEFAULT_RETRY_DELAY = 60
CELERY_TASK_MAX_RETRIES = 3
CELERY_TASK_ANNOTATIONS = {
    '*': {'acks_late': True},  # DECISION: acks_late prevents lost tasks on worker crash
}
```

**Comment:**  
`// DECISION: acks_late=True instead of default (acks_on_task_start). Tradeoff: If worker crashes mid-task, task re-queued (safe). If worker crashes after complete but before ack, task runs twice (idempotency required — implemented below).`

---

### 10. ADD IDEMPOTENCY KEYS (P1)

**What needs to change:** `tasks.py` (media processing, feed refill).

**Required:** Use clip_id + user_id + event timestamp as idempotency key in Redis.

```python
# tasks.py — idempotency wrapper
from django.core.cache import cache

def idempotent_task(task_func):
    def wrapper(*args, **kwargs):
        key = f"idempotency:{task_func.__name__}:{args}:{kwargs}"
        if cache.get(key):
            return  # Already processed
        result = task_func(*args, **kwargs)
        cache.set(key, True, timeout=3600)  # 1 hour window
        return result
    return wrapper
```

---

## PHASE 1.1: HARDENING (Months 2-3 — Stability)

### 11. ADD STRUCTURED OBSERVABILITY (P1)

**What needs to change:** Add `prometheus_client` metrics; add OpenTelemetry middleware.

```python
# backend/app/metrics.py — NEW FILE
# DECISION: Custom Prometheus metrics instead of relying only on Django-prometheus
# Tradeoff: More code to maintain; but allows tracking feed latency, vector search time, queue depth
from prometheus_client import Counter, Histogram, Gauge

FEED_LATENCY = Histogram('feed_request_duration_seconds', 'Feed endpoint latency', ['user_tier'])
VECTOR_SEARCH_TIME = Histogram('vector_search_duration_seconds', 'pgvector query time', ['query_type'])
MEDIA_QUEUE_DEPTH = Gauge('media_queue_depth', 'Heavy media queue size')
```

Add middleware to `settings.py` `MIDDLEWARE`.

---

### 12. ADD CIRCUIT BREAKER FOR REDIS FAILURES (P1)

**What needs to change:** `views.py:126-151` (`FastFeedViewSet`).

**Current:** If Redis `llen` fails or returns empty, `refill_user_feed` is triggered synchronously or fails with 500. At 10K concurrent users during a Redis outage, 10K simultaneous DB feed computations = DB crash.

**Required:** If Redis unavailable, serve static cached "trending" feed (global top 100 clips from last 24h) instead of individual feed.

```python
# views.py REPLACEMENT in list()
try:
    clips = redis_client.lrange(feed_key, 0, 9)
except (ConnectionError, TimeoutError):
    # DECISION: Fail to static global feed instead of DB overload
    clips = get_trending_fallback(user)  # cached in DB, not computed per request
```

---

### 13. FIX FEED REFRESH TRIGGERS (P1)

**Current:** Queue < 15 triggers refill (`views.py:138-140`). But refill takes 500-2000ms of DB time. At 10K users consuming 10 clips/min = 100K clips/min consumed = ~10K refill events/min = ~167 refills/sec. Celery `fast_feed` (4 workers) cannot keep up; queue grows; users get empty feeds.

**Required:** Pre-compute batches (5 batches/user) and trigger refill when batch 3 consumed (not 1), giving 2-batch buffer.

```python
# tasks.py — batch pre-computation
# Each user gets batches 0-4; when batch 2 consumed, refill batch 4
# Reduces refill frequency by 5x
```

---

## COST / RESOURCE MODEL AT 10K USERS

Based on code inspection and `scaling-analysis.md` estimates:

| Component | Current | At 10K Concurrent | Cost Impact |
|-----------|---------|-------------------|-------------|
| DB (PostgreSQL) | 2 CPU / 2 GB | 4 CPU / 8 GB + read replica | +$200-400/mo (AWS RDS) |
| Redis | 1 CPU / 1 GB | 2 instances: broker (1GB) + cache (2GB) | +$50/mo (ElastiCache) |
| Web / Celery | 4 workers / 4GB total | 20 workers / 20GB across 4 containers | +$300/mo (ECS) |
| Media (FFmpeg/ML) | 1 worker / 4GB | 3-4 workers / 16GB | +$400/mo (GPU if available) |
| S3 / MinIO | Local | Must stay; add CDN only if needed at 10K | +$0 (MinIO local; CDN $50/mo optional) |
| Monitoring | None | Prometheus + Grafana (small instance) | +$30/mo |
| **Total incremental** | — | — | **~$1,000-1,500/mo** vs current single-host |

**Note:** At 10K *live concurrent* (not 10K registered), actual monthly cost is moderate. The 1M vision ($97K-290K/mo) is irrelevant here.

---

## RISK REGISTER (What Could Still Fail At 10K)

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `update_global_metrics` still locks DB if batch size too large | Medium | Use `SKIP LOCKED`; monitor `pg_locks` |
| `refill_user_feed` vector query exceeds 2s at 1M clips | Medium | Add `LIMIT 500` ANN retrieval (change 7) |
| Redis memory exceeds 2GB at 10K users | Low | Add TTL (7 days) to feed keys; LRU eviction |
| Celery `heavy_media` queue backs up at viral upload surge | High | Add backpressure (reject uploads with 202 + ETA) |
| `UserInteraction` table grows 50M rows/day | High | Add monthly partitioning (`created_at`) — Phase 1.2 |
| No CI/CD → bad deploy breaks production | High | Add basic GitHub Actions (test + build image) — Phase 1.2 |

---

## VERIFICATION CHECKLIST (Before Declaring Phase 1 Complete)

Run these against a staging environment with simulated 10K concurrent load (using `locust` or `k6`):

- [ ] `pg_stat_activity` shows < 50 connections during peak feed load (PgBouncer working)
- [ ] `update_global_metrics_batch` completes in < 30 sec for 100K clips (batching working)
- [ ] `fast_feed` queue depth stays < 1000 messages (feed refills keeping up)
- [ ] `heavy_media` average task time < 120 sec (media processing not blocked)
- [ ] Redis `INFO memory` < 1.5 GB (cache not overflowing)
- [ ] `/metrics/` shows feed P99 latency < 500ms
- [ ] `log_telemetry` handles 1000 requests/sec without DB lock errors (`pg_locks` empty)
- [ ] No 500 errors during simulated Redis restart (circuit breaker / fallback feed working)
- [ ] `UserInteraction` table size grows predictably (partitioning planned if > 100M rows)

---

## DECISION LOG (Tags for Future Developers)

```python
// DECISION: PgBouncer transaction mode, not session. Django uses connection pooling per request; session mode requires persistent connections per worker, reducing multiplexing benefit.

// DECISION: Two Redis instances (broker + cache) instead of Cluster. At 10K users, single-node enough; cluster adds operational complexity without proportional benefit.

// DECISION: Cursor pagination (id > last_id) for metrics batch instead of OFFSET. OFFSET on 1M+ rows scans skipped rows; cursor is O(batch_size).

// DECISION: Fail-open circuit breaker (serve static feed if Redis down) instead of fail-closed (return 500). Availability > consistency for social feed; data can sync when Redis recovers.

// DECISION: Keep pgvector instead of migrating to Qdrant/Milvus at 10K. At <10M clips, pgvector HNSW (m=16) is sufficient; migration cost not justified.

// DECISION: Rate limit tier based on endpoint, not just user. Feed can be fast; telemetry must be strict; uploads must be strictest.

// HACK: Lua script for atomic Redis feed drain. Redis lacks native "pop N with length guard" in Python client; Lua executes atomically server-side but requires single-shard key (guaranteed by user_id hash).

// SECURITY: HLS segments (`hls/`) remain public-read; originals (`uploads/`) require signed URLs (`media_urls.py`). This split is mandatory — relative `.ts` references cannot inherit query-string auth per RFC 3986. Changing this requires redesigning HLS auth (cookie-based?), which is out of Phase 1 scope.

// TODO: Implement `UserInteraction` table partitioning by `created_at` when table exceeds 100M rows. Current `models.py` has no partition definition; add `PartitionedTable` or use `pg_partman` when needed.

// TODO: Replace `update_global_metrics` with Kafka/streaming aggregation when users exceed 100K. At that scale, raw SQL batch is no longer viable; event streaming required (see docs/scaling-analysis.md D).
```

---

*This plan is based on actual code inspection of `backend/app/tasks.py`, `backend/app/views.py`, `backend/app/models.py`, `backend/EchoFlow/settings.py`, and `docker-compose.yml` as of 2026-09-02. All recommendations are specific to EchoFlow's dual-vector recommendation engine, Celery media pipeline, and MinIO-backed HLS delivery system.*

---

## Verification Note (2026-09-04)

A re-verification pass against the current `main` (after the
`fix/comprehensive-bug-sweep` work) found that several "P0 bugs" cited
in this plan were **already implemented** before Phase 1.0 began, and
several other items were **already-true false positives**. The split
between "actual gaps that Phase 1.0 closed" and "items that were stale
audit claims" is below.

### Items that were false positives / already done (no change needed)

| Plan item | Original claim | Reality (verified 2026-09-04) |
|---|---|---|
| **#2** `update_global_metrics` full-table lock | `tasks.py:640-666` does a raw full-table UPDATE that locks at 100K clips. | Cursor-paginated batches of 5000 with Redis-persisted resume cursor already in place (landed in `fix/comprehensive-bug-sweep`). Phase 1.0 only added `FOR UPDATE SKIP LOCKED` to the inner subquery. |
| **#3a** `weights=[]` cold-start bug | `tasks.py:558-561` `weights` never populated. | `weights` is initialized at line 416 and appended at line 442. No bug. |
| **#3b** Lua-script atomic drain | `views.py:138-140` `llen` + `lpop` race. | `views/feed.py:37` uses `redis_client.lpop(redis_key, 10)` — Redis6.2+ atomic multi-pop, no Lua needed. |
| **#4** No tiered rate limiting | Only `anon: 100/hr`, `user: 1000/hr`. | 9 scopes in `settings.py:359-369`, including `telemetry:60/min`, `upload:20/hour`, `register:5/hour`, `login:10/min`, `comment:60/hour`, `share_send:100/hour`, `interaction:60/min`. |
| **#9** No retry / DLQ config | No retries configured. | All heavy tasks have `max_retries` + `autoretry_for=RETRYABLE_ERRORS` + `retry_backoff=True`. `acks_late=True`. No formal DLQ, but tasks are idempotent. |
| **#12** No Redis circuit breaker | If Redis `llen` fails, 500. | `views/feed.py:65-86`: try/except wraps the entire Redis path; on any failure serves trending-by-`engagement_velocity` fallback. |
| **(risk)** Synchronous `log_telemetry` row locks | Per-request `update_or_create` row locks. | `views/interactions.py:88-105` → Redis list → 30s batched flush via `flush_telemetry` task. Synchronous fallback only on Redis outage. |
| **(risk)** No correlation IDs | No request tracing. | `backend/EchoFlow/{correlation,middleware,logging_filters}.py` + `settings.py:106,391-410`. |

### Items that were real gaps (Phase 1.0 closed them)

| Plan item | What Phase 1.0 did |
|---|---|
| **#1** No PgBouncer | Added `pgbouncer` service in `docker-compose.yml` + `docker/pgbouncer/Dockerfile` (based on `edoburu/pgbouncer`, `AUTH_TYPE=scram-sha-256`, transaction pool, `LISTEN_PORT=6432`). All web/celery services now route through `pgbouncer:6432`; non-Docker dev falls back to direct `localhost:5432`. |
| **#5** Single Redis | Split into `redis_broker` (noeviction, 512MB) + `redis_cache` (LRU, 1GB). `REDIS_BROKER_URL` / `REDIS_CACHE_URL` env vars; non-Docker collapses to single `REDIS_URL`. |
| **#8** `celery_media` `--pool=solo` | Switched to `--pool=prefork --concurrency=2`. 4G memory limit retained (per device constraint); OOM risk under concurrent uploads accepted. |
| **#2** (additional) `SKIP LOCKED` | Wrapped both `ev_query` and `acr_query` UPDATE targets in `SELECT ... FOR UPDATE SKIP LOCKED` so a batch doesn't stall on rows currently locked by `UserInteraction.save()`. |

### Items deferred (per user decision, 2026-09-04)

- `UserInteraction` table partitioning by `created_at`
- CI/CD pipeline
- Media upload backpressure (currently returns 202 + ETA)
- Two-stage HNSW candidate generation (item #7) — needs `EXPLAIN ANALYZE` first to confirm the planner doesn't already use HNSW for the composite query
- Feed batch pre-computation (item #13)
- DLQ infrastructure
- Idempotency wrapper (tasks are naturally idempotent)

### Live verification (2026-09-04)

- `pgbouncer` image builds; container starts; listens on `:6432`.
- `psycopg2.connect('postgres://...@pgbouncer:6432/echoflow_db')` succeeds;
  `current_setting('server_version')` returns `16.15 (Debian ...)` — end-to-end
  SCRAM-SHA-256 auth confirmed.
- `pg_stat_activity` from inside the pgbouncer connection reports only
  1 active + 1 idle backend connection (multiplexing working — 10000
  client connections collapsing to ≤25 real Postgres connections).
- The new `WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)` query parses
  and executes against the live DB (EXPLAIN ANALYZE: 0.9ms, 9 rows,
  no errors).
- Running web/celery containers were started before Phase 1.0 was
  committed and still hold the pre-Phase-1.0 `DATABASE_URL`. They will
  pick up `pgbouncer:6432` on next `docker compose up --build`. Both
  paths work; non-destructive to leave in this state temporarily.
