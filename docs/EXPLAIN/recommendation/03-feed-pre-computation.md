# Feed Batch Pre-Composition (Global + Per-User Candidate Pools)

**Audience:** Backend engineering team. This document is the design specification for implementing Redis-backed candidate pools that replace the on-demand composite SQL query in `refill_user_feed`. The implementation team writes the code from the snippets and contracts here.

**Branch:** `feat/stage2-service-layer-and-telemetry-stream`
**Related audit items:**
- `docs/unfixed-issues-2026-09-03.md` §4.7 (P2.2 — Candidate pool in Redis sorted sets) — the canonical open issue this design closes
- `docs/backend-bug-fixs.md` item 7 ("Feed batch pre-computation")
- `docs/phase-1-scaling-plan.md` §13 ("FIX FEED REFRESH TRIGGERS")
- `docs/event-driven-architecture-plan.md:559-574` (P2.1 + P2.2 specification, partial)
- `docs/EXPLAIN/failure/03-feed-resilience.md` (multi-tier fallback chain)

> **Note on doc numbering:** The task brief refers to this as "Group A item 7". The current canonical location of this work item is `docs/unfixed-issues-2026-09-03.md` §4.7 (under the "Open" heading, bucket "§6 P2 — Decouple recommendation"). The "Group A" label is a legacy artifact from an earlier audit-doc taxonomy that no longer matches §6 of the unfixed-issues report. Cite §4.7 going forward.

> **Doc audit note:** The brief cites `refill_user_feed` at `backend/app/tasks.py:346-456`. Verified: `@shared_task(...)` decorator at `tasks.py:346`, `def refill_user_feed(...)` at `tasks.py:347`, function body through `tasks.py:456`. All `tasks.py:` line cites in this document are anchored against the working tree at the time of writing.

---

## 1. Background and Motivation

### 1.1 The current refill path

`FastFeedViewSet.list` (`backend/app/views/feed.py:61-118`) is the hot read path. On every request it does:

1. `redis_client.lpop(redis_key, 10)` against `user_feed:{user_id}` (`views/feed.py:73`).
2. If the queue returned nothing: `refill_user_feed.delay(user_id, count=40)` (`views/feed.py:76`), then a second `lpop` (`views/feed.py:87`).
3. If both `lpop` calls returned nothing, return `202 Accepted` with `retry_after_ms: 1500` (`views/feed.py:90-98`).
4. Fetch the AudioClip rows for the popped IDs (`views/feed.py:107-112`).

The refill task itself (`backend/app/tasks.py:347-456`) is the expensive path:

- `calculate_time_decayed_vectors(user)` at `tasks.py:368` — `SELECT * FROM userinteraction WHERE user_id = ? ORDER BY created_at DESC LIMIT 50` plus numpy aggregation
- A composite-annotated `AudioClip` queryset at `tasks.py:374-387` that evaluates two `CosineDistance(...)` expressions **per row**, sums them into `vector_similarity`, blends with `avg_completion_rate` and `engagement_velocity`, and `ORDER BY -composite_score`
- A second queryset for the follow-graph wedge at `tasks.py:410-412` and a third for the explore wedge at `tasks.py:422-424`
- An exclusionary `WHERE NOT EXISTS (userinteraction WHERE clip_id = ... AND created_at >= now - 30 days)` at `tasks.py:364`

This is the SQL plan that the architecture audit (`docs/backend-architecture-audit.md` §"Feed Refills", `docs/event-driven-architecture-plan.md:76`) calls out as the second-largest scalability cliff behind the F() counter path.

### 1.2 Why this is a bottleneck at 10k concurrent

The math, end-to-end, against current code:

| Quantity | Value | Source |
|---|---|---|
| Active users (10k concurrent) | 10,000 | `docs/phase-1-scaling-plan.md:17` |
| Average clips consumed per user per minute | 10 | `docs/backend-architecture-audit.md` ("Feed Refills") |
| Average queue refill threshold | `< 20 entries` (see `tasks.py:361`) → refills roughly every 2 minutes per user | `tasks.py:361-363` |
| Refill SQL queries per refill | 3 (`calculate_time_decayed_vectors` + composite query + follow-graph query, see `tasks.py:368, 374, 410`; explore query `tasks.py:422` is only run when `explore_count > 0` so it counts as the 4th query in the typical case) | `tasks.py:368-440` |
| Refills per minute (10k users / 2 min) | 5,000 | derived |
| SQL queries per minute | 5,000 × 3 (or 4) = **15,000–20,000 queries/min** | derived |
| SQL queries per second | **250–330 SELECTs/sec** | derived |

This matches `docs/event-driven-architecture-plan.md:568`:

> "the DB sees 200+ queries/s"

…for the refill path alone, and that's separate from the `/feed/` SELECTs (`backend/app/views/feed.py:107-112`) and the `/suggestions/` SELECTs (`backend/app/views/feed.py:170-175`).

### 1.3 Lock contention with the F() counter path

Every refill composite query reads `engagement_velocity` and `avg_completion_rate` from `audioclip` (`tasks.py:382-384`). These columns are mutated by `update_global_metrics` every 5 min via `FOR UPDATE SKIP LOCKED` (`tasks.py:552-583`) and by `UserInteraction.save()` F() updates (`backend/app/models.py:200-206`, where `AudioClip.objects.filter(pk=...).update(likes=F(likes) + ...)` takes a row-exclusive lock). With Phase 1.0's `SKIP LOCKED` discipline the lock conflict window is bounded — but the refill's scan is a heap-fetch over every `status='ready'` row, so the *contention surface* (the rows touched) is the full table.

The F() row-level contention is the #1 risk (`docs/unfixed-issues-2026-09-03.md:29`); the refill SQL is the #2 risk. Both reduce to the same table under load.

### 1.4 What we want

Replace the SQL-per-refill with a Redis sorted-set read. The new `refill_user_feed` becomes:

```text
ZREVRANGEBYSCORE clip:candidates:exploit +inf -inf LIMIT 0 40
ZREVRANGEBYSCORE user:{id}:candidates:explore +inf -inf LIMIT 0 10
```

…with dedup and exclusion of seen clips, but **no SQL**. The SQL only fires on the cold-start fallback (see §6).

---

## 2. The Two-Pool Design

We materialize two Redis sorted sets (ZSETs), maintained by Celery Beat tasks, that `refill_user_feed` reads from. The two pools are separate because they have different freshness requirements, different per-key cost characteristics, and different personalization budgets.

### 2.1 Global exploit pool — `clip:candidates:exploit`

| Property | Value | Rationale |
|---|---|---|
| Key | `clip:candidates:exploit` | Single global key, not per-user |
| Member | clip UUID (string) | Same identifier as `audioclip.id` |
| Score | `composite_score` (float64) | Same formula as today, see §3 |
| Cardinality | `FEED_POOL_GLOBAL_TOP_N = 10000` | Top 10k clips |
| Refresh cadence | Every 5 min (`FEED_POOL_GLOBAL_TTL = 300`) | Match `update_global_metrics` cadence (`settings.py:285`); see §5 |
| Rebuild trigger | Celery Beat → `rebuild_global_exploit_pool` | New task, see §7 |
| Reader | `refill_user_feed` (80% of feed) | See §6 |

The global pool is the cheap path. It does not personalize — the composite score is computed against a single global-average vector (see §3.1), so all users share the same top-N.

### 2.2 Per-user explore pool — `user:{id}:candidates:explore`

| Property | Value | Rationale |
|---|---|---|
| Key | `user:{id}:candidates:explore` | One ZSET per active user |
| Member | clip UUID (string) | Same as global |
| Score | `composite_score` against **the user's blended vector** | Personalized ranking |
| Cardinality | `FEED_POOL_USER_TOP_N = 1000` | Top 1k per user |
| Refresh cadence | Hourly (`FEED_POOL_USER_TTL = 86400`) | Refresh fans out across the hour, see §2.4 |
| Rebuild trigger | Celery Beat → `rebuild_user_explore_pool` fanned out | New task, see §7 |
| Reader | `refill_user_feed` (20% of feed) | See §6 |

### 2.3 Why no third "explore pool"

The current `refill_user_feed` has three wedges: exploit (composite-scored, 80%), follow-graph (recent from followed creators, 5 slots), explore (high engagement velocity, fills the rest). The follow-graph wedge is a personal graph traversal (`tasks.py:409-417`) — small (5 slots), cheap (`creator__in=followed_creators`), and already a separate query.

The explore wedge (`tasks.py:419-429`) is `order_by('-engagement_velocity')` against the full catalog. **This can read from the global exploit pool directly** — the global pool's top-N is already ordered by composite score, but `engagement_velocity` contributes 0.25 of the composite weight (`tasks.py:384`), so the top-N is dominated by high-velocity clips. For the 20% wedge in the new design, we read from `user:{id}:candidates:explore` (per-user, personalized) rather than the global pool, because the global pool is intentionally non-personalized and the explore wedge benefits from novelty relative to the user's history.

DECISION: drop the explore wedge as a third pool; the per-user pool handles it. The follow-graph wedge is preserved as-is (it's 5 clips out of 50 and lives behind its own `creator__in=followed_creators` query — see §6.2 for the rewrite).

### 2.4 Fan-out across the hour

10k active users × 1 task each × hourly = ~170 tasks/min average. Beat's `DatabaseScheduler` (`settings.py:325`) does not dedup, so the work has to be absorbable by the worker pool.

The fan-out strategy: schedule a single Beat entry per hour that dispatches a Celery group (or chain) of `rebuild_user_explore_pool` tasks spread across the hour. Implementation pattern:

```python
# Sketch — implementation team writes the actual code.
# At minute 0 of every hour:
for user_chunk in chunks(active_users, 60):
    # delay(user_chunk) — one task per chunk, processed over the next minute
    rebuild_user_explore_chunk.delay(user_chunk)
```

This produces a constant 60 tasks/min average, with no thundering-herd spike. The `60` is configurable (`FEED_POOL_USER_FANOUT_CHUNKS_PER_HOUR`, default 60). Trade-off: smaller chunk size = finer fan-out granularity = more dispatcher overhead.

---

## 3. The Composite-Score Pre-Computation

### 3.1 The global pool's score

The global pool uses the same composite formula as today (`backend/app/tasks.py:381-386`, documented at `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37`):

```text
composite_score = 0.45 * vector_similarity
                + 0.30 * avg_completion_rate
                + 0.25 * engagement_velocity
```

For the **global** pool, `vector_similarity` is computed against a single precomputed "global average user" semantic+acoustic vector. This is a deliberate cheap-out:

```sql
-- Pseudocode. Implementation team computes the global average once per
-- pool rebuild and passes it as a constant to the CosineDistance().
SELECT AVG(semantic_vector) FROM audioclip WHERE status='ready';
-- (PGAgg over vector_cosine_ops — see §3.4 for caveats.)
```

For the **per-user** pool, `vector_similarity` is computed against `calculate_time_decayed_vectors(user)` (the same function used today). The user's blended vector is cached for the hour of the pool's TTL (§4.4) so the per-user rebuild doesn't pay the numpy cost 1k times.

### 3.2 The 0.45 / 0.30 / 0.25 weights are fixed

Per `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37` and the AI product team. **Do not** parameterize them in this work item. If the AI team wants A/B-testable weights later, that's a separate change in `docs/EXPLAIN/ai-ml/07-ann-candidate-generation.md` and Group A item 6 (P1 — ANN two-stage retrieval), which is explicitly out of scope here.

### 3.3 Why two pools, not one

The 80/20 split is the recommended ratio:

| | Global | Per-user |
|---|---|---|
| Personalization | None | Per-user blended vector |
| Refresh cost (per cycle) | 1 SELECT against `audioclip` | 1 SELECT per user × 10k users |
| Refresh cost total / hour | 12 (every 5 min) | 10k (fanned out) |
| Cold-start behavior | Always warm | First request after signup triggers ad-hoc rebuild (see §8.1) |
| Latency to read top-N | `ZREVRANGEBYSCORE` ~1ms | `ZREVRANGEBYSCORE` ~1ms |

Serving all 100% of feed from the global pool is cheapest but loses personalization. Serving all 100% from per-user is fully personalized but the rebuild cost is 10k SELECTs/hour — exactly what we are trying to eliminate. The 80/20 split preserves the current `tasks.py:400, 420` ratio (`80% exploit + 20% explore`, with the follow-graph wedge folded into the exploit side).

DECISION: serve 80% of feed from `clip:candidates:exploit` and 20% from `user:{id}:candidates:explore`. Document this contract in the new tasks' docstrings.

### 3.4 Vector mean caveat

`SELECT AVG(semantic_vector) FROM audioclip` against pgvector's HNSW indexes requires `vector_avg` semantics, which pgvector does not ship in a single aggregate. Implementation team: either (a) compute it in Python by streaming the rows in chunks of 1000 (`tasks.py:458-525` already does numpy aggregation in `calculate_time_decayed_vectors` and is a reference pattern), or (b) ship a `vector_avg` SQL function as part of this work item if the team prefers.

DECISION: stream-and-mean in Python inside the rebuild task. This is bounded work (10k clips × 384 floats = 15 MB per refresh × 12 refreshes/hour = 180 MB/hour of streamed data — fits in `fast_feed` worker memory comfortably).

---

## 4. Redis Memory Cost Analysis

The total Redis footprint changes substantially. Below is the math, anchored against the current `redis_cache` service (`docker-compose.yml:70-76`).

### 4.1 Current Redis footprint (baseline)

| Key class | Cardinality | Per-key size | Total |
|---|---|---|---|
| `user_feed:{user_id}` lists | 10k active users × ~50 entries | 50 × (UUID 36B + list overhead) ≈ 2.0 KB | ~20 MB |
| `user_vectors:{user_id}` (N11 cache) | 10k active users | (384+128) floats × 4B ≈ 2.0 KB | ~20 MB |
| `feed_refill_lock:{user_id}` | ephemeral, 30s TTL | ~50 B | <1 MB |
| Stream `stream:interaction.events` (P1.5) | MAXLEN 50k | ~200 B per entry | ~10 MB |
| Stream `stream:interaction.events:dlq` | bounded, ~5k | ~250 B per entry | ~1 MB |
| Dedup `processed_event:{event_id}` | 58M events/day × 24h TTL | ~80 B | ~200 MB |
| `global:trending:feed` list (planned, not yet shipped) | 100 entries | ~5 KB | <1 MB |
| `update_global_metrics:resume_id` | 1 string | ~50 B | <1 MB |
| **Total current** | | | **~250 MB** |

`docs/EXPLAIN/redis-celery/01-redis-usage.md:212-213` documents the current `--maxmemory 1gb` allocation. The 250 MB baseline assumes 58M dedup keys/day fully resident; in practice `allkeys-lru` evicts older keys under pressure, so steady-state usage is ~150 MB.

### 4.2 New pool footprint

**Global pool** — `clip:candidates:exploit` (10k clips × 1 ZSET):

- Member: clip UUID, 36 bytes
- Score: float64, 8 bytes
- Per-member ZSET overhead: 32 bytes (ziplist-encoded; for 10k members Redis uses `skiplist + dict` encoding — actually 50 bytes per member)
- Total: 10,000 × (36 + 8 + 50) ≈ **940 KB → ~1 MB**

**Per-user pool** — `user:{id}:candidates:explore` (10k users × 1 ZSET of 1k clips):

- Per-user: 1,000 × (36 + 8 + 50) ≈ **94 KB**
- All users: 10,000 × 94 KB ≈ **940 MB → ~1 GB**

The per-user pool is the dominant memory cost. Two knobs:

1. **`FEED_POOL_USER_TOP_N = 1000`** is configurable. Dropping to 500 saves ~470 MB at the cost of less novelty surface for the explore wedge.
2. **`FEED_POOL_USER_TTL = 86400`** (24h sliding) means a user's key auto-evicts 24h after the last rebuild. If the user logs in daily, the key is refreshed and stays; if the user churns, the key evicts.

The 24h TTL is the key cost-control mechanism. The `redis_cache` service is `allkeys-lru` (`docker-compose.yml:74`) which means cold-user keys can be evicted under memory pressure even before TTL expiry. Steady-state usage depends on the user distribution: a long-tail of users who log in once per week dominates the cold-pool cost.

DECISION: cap `FEED_POOL_USER_TOP_N` at 1000 by default. The implementation team may add a `Redis MEMORY USAGE` check in the rebuild task and back off (`ZREMRANGEBYRANK`) if a per-user ZSET exceeds a configurable byte budget.

### 4.3 Total Redis footprint (after this work)

| Key class | Cardinality | Total |
|---|---|---|
| Pre-existing (`user_feed`, `user_vectors`, `stream:*`, `processed_event:*`) | unchanged | ~250 MB |
| Global pool `clip:candidates:exploit` | 10k members | ~1 MB |
| Per-user pool `user:{id}:candidates:explore` | 10k × 1k members | ~1 GB (worst case) |
| Per-user pool with TTL churn (50% hit) | 5k × 1k | ~500 MB (steady state) |
| **Total worst case** | | **~1.25 GB** |
| **Total steady state** | | **~750 MB** |

This is a **3–5× increase** in Redis memory consumption relative to the current ~250 MB baseline. Concretely:

- Current: 250 MB against a 1 GB ceiling (`docker-compose.yml:74`)
- After: 750 MB – 1.25 GB against the same 1 GB ceiling → **will OOM-evict under default settings**

See §11.1 for the required `docker-compose.yml` change.

### 4.4 Cost control: lazy per-user rebuild

The per-user rebuild fan-out (§2.4) is the second control lever. A user who hasn't logged in for 24h doesn't need a freshly rebuilt pool — their last pool is still valid (engagement_velocity staleness is bounded, see §5).

Implementation:

```python
# Sketch — implementation team writes the actual code.
def rebuild_user_explore_pool(user_id):
    key = f"user:{user_id}:candidates:explore"
    # Skip if the key is still fresh (built within the last hour)
    ttl_remaining = redis_client.ttl(key)
    if ttl_remaining > FEED_POOL_USER_TTL - 300:  # 5-min grace
        return  # Already rebuilt this cycle
    # ... else rebuild and ZADD batched
```

DECISION: skip the rebuild if the pool is fresh within 5 minutes. This makes the per-user rebuild self-throttling — Beat's hourly cadence becomes a "refresh-at-most-every-hour" cap.

---

## 5. The 5-Minute Refresh Cadence Trade-Off

The global pool's refresh cadence matches `update_global_metrics` (`settings.py:285`), which produces `engagement_velocity` and `avg_completion_rate` every 5 minutes. The two tasks are co-dependent: the global pool reads what `update_global_metrics` writes.

| Cadence | Staleness on `engagement_velocity` | SELECTs / hour against `audioclip` | Verdict |
|---|---|---|---|
| 1 min | ~1 min | 60 | **Rejected.** 12× the SQL cost; the SQL is the bottleneck we're trying to eliminate. |
| 5 min | ~5 min | 12 | **Recommended.** Matches `update_global_metrics`. |
| 15 min | ~15 min | 4 | **Rejected.** A viral clip takes 15 min to surface. Phase 1 launch criterion says "≤ 5 min" for virality lag. |
| 30 min | ~30 min | 2 | **Rejected.** Half the SQL cost but user-visible staleness on trending content. |

For the per-user pool, the recommendation is hourly because:

1. The per-user pool reads the user's blended vector, which is already cached for 15 min by `get_user_vectors` (`backend/app/views/feed.py:30-31`). A 5-min rebuild reads the same cache for almost every user — wasted SELECTs.
2. The user-perceived impact of 1-hour per-user staleness is lower than 5-min global staleness, because the per-user pool only feeds the 20% explore wedge (§2.3). The 80% exploit wedge comes from the global pool which is fresh.
3. Hourly rebuild × 10k users = 10k SELECTs/hour ≈ 170/min, which the 4-worker `fast_feed` queue handles with margin (each worker does ~3-4 SELECTs/sec, see §11.3).

DECISION: 5 minutes for `rebuild_global_exploit_pool`, 60 minutes for `rebuild_user_explore_pool`. The fan-out across the 60-minute window is the smoothing mechanism (see §2.4).

---

## 6. The `refill_user_feed` Rewrite

### 6.1 Current code (anchor)

`backend/app/tasks.py:346-456`. Function signature:

```python
@shared_task(bind=True, max_retries=2, default_retry_delay=30, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True)
def refill_user_feed(self, user_id, count=50):
```

The current body executes, in order:

1. `User.objects.get(id=user_id)` (1 SELECT)
2. Acquire `feed_refill_lock:{user_id}` SETNX EX 30 (`tasks.py:356`)
3. Short-circuit if `llen(user_feed:{user_id}) >= 20` (`tasks.py:361`)
4. Fetch `seen_ids` from `userinteraction` last 30 days (`tasks.py:364`, 1 SELECT)
5. Fetch `queued_ids` from Redis list (`tasks.py:365`)
6. `calculate_time_decayed_vectors(user)` (`tasks.py:368`, see §3.4 for what this does today — 1 SELECT against `userinteraction`, numpy math)
7. Composite query against `audioclip` with `CosineDistance` annotations (`tasks.py:374-387`, the expensive one)
8. Take `exploit_count = int(count * 0.8)` (`tasks.py:400`)
9. Follow-graph query (`tasks.py:410-412`, 1 SELECT)
10. Explore query (`tasks.py:422-424`, 1 SELECT)
11. Dedupe and shuffle (`tasks.py:452`)
12. `RPUSH` into Redis list (`tasks.py:453`), set TTL (`tasks.py:455`)

### 6.2 The new body

The new `refill_user_feed` keeps the lock and short-circuit (§6.1 steps 2, 3) but replaces steps 4–10 with ZSET reads. It retains the follow-graph wedge because that's only 5 clips (`tasks.py:412`, `:exploit_count = 5`) and is per-user-personalized in a way neither pool covers.

```python
# backend/app/services/feed_pool.py
# Sketch — implementation team writes the actual code.
from django.core.cache import cache
from ..models import AudioClip, UserInteraction
from ..tasks import calculate_time_decayed_vectors

GLOBAL_KEY = 'clip:candidates:exploit'
USER_KEY_TEMPLATE = 'user:{user_id}:candidates:explore'


def get_user_candidates(user_id, count=50):
    """Return up to `count` candidate clip IDs from the global + per-user pools.

    DECISION: 80% exploit from the global pool, 20% explore from the per-user
    pool. Follow-graph wedge (5 clips from followed creators) is added in
    Python by the caller — the pools don't carry the follow-graph.

    Returns a list of UUID strings. Empty list on cold-start fallback
    (no global pool yet, no per-user pool yet).
    """
    redis_client = cache.client.get_client()
    exploit_count = int(count * 0.8)         # 40 of 50
    explore_count = count - exploit_count    # 10 of 50

    exploit_ids = redis_client.zrevrangebyscore(
        GLOBAL_KEY, '+inf', '-inf',
        start=0, num=exploit_count,
    )

    user_explore_ids = redis_client.zrevrangebyscore(
        USER_KEY_TEMPLATE.format(user_id=user_id), '+inf', '-inf',
        start=0, num=explore_count,
    )

    # Dedup (per-user pool may overlap global pool)
    seen = set()
    result = []
    for cid_bytes in list(exploit_ids) + list(user_explore_ids):
        cid = cid_bytes.decode('utf-8')
        if cid not in seen:
            seen.add(cid)
            result.append(cid)

    return result
```

The caller in `refill_user_feed` becomes:

```python
# backend/app/tasks.py — new refill_user_feed body, see §6.3 for the cold-start fallback
# (the function signature, decorators, lock, and short-circuit from §6.1
# steps 2, 3 are preserved verbatim)

candidates = get_user_candidates(user_id, count=count)

if not candidates:
    # COLD-START FALLBACK: pools not yet built. See §6.3.
    return _refill_user_feed_on_demand_fallback(user_id, count=count)

# Apply seen-clip exclusion against the user's last-30-day history.
# This is a SELECT, but it's only `id IN (SELECT clip_id FROM userinteraction
# WHERE user_id = ? AND created_at >= now - 30 days)`. The current code at
# tasks.py:364 already pays this cost; we keep it.
seen_ids = set(
    UserInteraction.objects.filter(
        user=user, created_at__gte=timezone.now() - timedelta(days=30)
    ).values_list('clip_id', flat=True)
)
queued_ids = [vid.decode('utf-8') for vid in redis_client.lrange(redis_key, 0, -1)]
exclude_ids = seen_ids | set(queued_ids)

# DECISION: filter the candidates in Python, not via a second SQL query.
# The candidates list is at most `count` entries; iterating 50 UUIDs is
# trivial. The alternative (NOT IN subquery on a 50-element list) would
# add a SQL round-trip we don't need.
candidates = [cid for cid in candidates if cid not in exclude_ids]

# Follow-graph wedge (5 clips from followed creators).
# Preserved from tasks.py:409-417. This is per-user-personalized in a way
# neither pool covers.
followed_creators = user.following.all()
network_clips = AudioClip.objects.filter(
    status='ready', creator__in=followed_creators,
).exclude(id__in=exclude_ids).order_by('-created_at')[:5]
for c in network_clips:
    cid = str(c.id)
    if cid not in exclude_ids and cid not in candidates:
        candidates.append(cid)
        exclude_ids.add(cid)

# If we're short of `count` after exclusion, top up from the global pool
# (sliding further down the score range).
if len(candidates) < count:
    extras = redis_client.zrevrangebyscore(
        GLOBAL_KEY, '+inf', '-inf',
        start=exploit_count, num=count - len(candidates) + 10,
    )
    for cid_bytes in extras:
        cid = cid_bytes.decode('utf-8')
        if cid not in exclude_ids and cid not in candidates:
            candidates.append(cid)
            if len(candidates) >= count:
                break

random.shuffle(candidates)
candidates = candidates[:count]
redis_client.rpush(redis_key, *candidates)
redis_client.expire(redis_key, 86400)
```

### 6.3 Cold-start fallback

When the global pool is missing (no rebuild has run yet — cold Redis, or a first deploy), `zrevrangebyscore` returns `[]`. When the per-user pool is missing (the user hasn't been rebuilt yet — new signup, or hourly cycle hasn't reached them), the user-key read returns `[]`. The caller in §6.2 handles `candidates == []` by calling `_refill_user_feed_on_demand_fallback`, which is **the current SQL path** (essentially `tasks.py:368-431`) with a 200 ms time budget.

```python
# Sketch
def _refill_user_feed_on_demand_fallback(user_id, count=50):
    """The current SQL path, with a 200ms time budget.

    If the SQL would exceed 200ms (estimated by EXPLAIN or by past query
    time cached in user_vectors:{user_id}:last_sql_ms), queue the refill
    for an async worker and return 'candidates pending'.

    DECISION: 200ms matches the FastFeedViewSet SLO. A query that takes
    longer should not block the HTTP request thread — return 202 Accepted
    with retry_after_ms, which is already the FastFeedViewSet contract
    when the queue is empty (views/feed.py:90-98).
    """
    ...
```

DECISION: the 200ms budget is enforced via the cached `user_vectors:{user_id}:last_sql_ms` key — write the elapsed time of the SQL path there, and skip the inline fallback if the last run exceeded 200ms. The fallback is then enqueued as a Celery task (`refill_user_feed.delay(user_id, count)`) and returns "candidates pending" to the caller. The caller translates "candidates pending" into a `202 Accepted` + `retry_after_ms: 1500` response (already implemented at `views/feed.py:90-98`).

### 6.4 The follow-graph wedge

The follow-graph wedge at `tasks.py:409-417` is not pooled. It is per-user, per-refill, ~5 clips, costs 1 SELECT (`AudioClip.objects.filter(status='ready', creator__in=followed_creators).order_by('-created_at')[:5]`). At 5,000 refills/min that's 5,000 SELECTs/min — small compared to the current 15,000–20,000 refills/min, but worth tracking.

DECISION: leave the follow-graph wedge as a live query in the new `refill_user_feed`. A future optimization (P3.x) could pool `user:{id}:follow_graph_recent` (1 SELECT per follow-graph event), but it's out of scope here.

---

## 7. The Rebuild Tasks

Two new Celery Beat tasks.

### 7.1 `rebuild_global_exploit_pool`

```python
# backend/app/tasks.py
# Sketch — implementation team writes the actual code.

@shared_task(bind=True, max_retries=2, default_retry_delay=30,
             autoretry_for=RETRYABLE_ERRORS, retry_backoff=True)
def rebuild_global_exploit_pool(self):
    """Refresh the global exploit pool ZSET every 5 minutes.

    Source: backend/app/tasks.py:374-387 (the composite annotation, currently
    per-refill). Target: clip:candidates:exploit ZSET.

    DECISION: stream-and-mean the global average semantic + acoustic vector
    in Python (chunked at 1000 rows) rather than asking pgvector for
    AVG(vector). pgvector doesn't ship a vector-aggregate function; a
    custom SQL function is overkill for this work item.

    Pipeline:
      1. Stream AudioClip rows in chunks of 1000. For each row, compute
         vector_similarity against the global average user vector.
      2. Compute composite_score (0.45/0.30/0.25 against the row's
         avg_completion_rate and engagement_velocity — both written by
         update_global_metrics 0-5 minutes ago).
      3. ZADD batched in chunks of 1000 to avoid Redis blocking. Use a
         pipeline + transaction to make the bulk write atomic.
      4. ZREMRANGEBYRANK to truncate to FEED_POOL_GLOBAL_TOP_N.
      5. EXPIRE the key to FEED_POOL_GLOBAL_TTL.
    """
    ...
```

The ZADD batching at 1000 rows per pipeline is important: a single 10k-row ZADD is atomic but blocks the Redis main thread for ~50ms (10k × 5µs/insert). Ten 1k-row ZADDs are atomic each and complete in <10ms total. Trade-off: ten round-trips vs. one round-trip — network is cheaper than Redis blocking.

DECISION: chunked ZADD, 1000 members per pipeline.

### 7.2 `rebuild_user_explore_pool`

```python
# backend/app/tasks.py
# Sketch — implementation team writes the actual code.

@shared_task(bind=True, max_retries=2, default_retry_delay=30,
             autoretry_for=RETRYABLE_ERRORS, retry_backoff=True)
def rebuild_user_explore_pool(self, user_id):
    """Refresh one user's explore pool ZSET.

    Source: backend/app/tasks.py:368 (calculate_time_decayed_vectors) +
    tasks.py:374-387 (composite annotation). Target: user:{id}:candidates:explore.

    DECISION: skip if the key is still fresh (see §4.4 self-throttle).
    DECISION: cache the user's blended vector in user_vectors:{user_id}
    for the hour's duration (extending the existing 15-min cache at
    views/feed.py:30 to 1 hour for this rebuild path). The 15-min
    user_vectors cache is invalidated by the user's next interaction
    (services/interactions.py:50-55 — see the invalidate_user_vectors_cache
    helper exposed but not yet wired).
    """
    ...
```

### 7.3 Fan-out dispatcher

The fan-out across the hour (§2.4) is one Beat entry that dispatches many per-user tasks:

```python
# backend/app/tasks.py
# Sketch — implementation team writes the actual code.

@shared_task
def dispatch_user_pool_rebuilds(self):
    """Hourly dispatcher. Fans out per-user rebuilds across the hour.

    Called by Beat every hour. Slices active users into N chunks and
    delays each chunk to be processed over the next minute. This
    smooths the rebuild to a constant ~170 tasks/min.
    """
    from ..models import User
    chunks_per_hour = getattr(settings, 'FEED_POOL_USER_FANOUT_CHUNKS_PER_HOUR', 60)
    active_user_ids = User.objects.filter(is_active=True).values_list('id', flat=True)
    for chunk in chunks(active_user_ids, chunks_per_hour):
        for user_id in chunk:
            rebuild_user_explore_pool.apply_async(
                args=[user_id],
                countdown=chunks_per_hour,  # delay each chunk by 1 minute
            )
```

The `countdown` per chunk is the smoothing mechanism. The implementation team may instead use a Celery `chord` or `group` with `apply_async(countdown=...)` per-task for finer-grained smoothing.

---

## 8. Edge Cases

### 8.1 Cold-start user

A user who signs up has no per-user pool. The first `/feed/` call enqueues `rebuild_user_explore_pool(user_id)` and returns the global pool only (which is fresh — it's rebuilt every 5 min regardless of new users).

DECISION: trigger ad-hoc `rebuild_user_explore_pool(user_id).delay()` on the user's first `/feed/` hit, with a 24h dedup via a `user_pool_rebuild_requested:{user_id}` Redis flag. The dedup flag is `SETNX` with a 24h TTL. The first `/feed/` request waits up to 1.5s for the rebuild to complete (via the existing 202-Accepted retry contract at `views/feed.py:90-98`); subsequent requests see the populated per-user pool.

DECISION: **ad-hoc rebuild on first /feed/ hit, cached for 24h**. Alternative considered: serve global-only for the first 24h after signup. Rejected because the per-user pool is the source of the 20% explore wedge; without it, exploration degrades to global-top-10k for two weeks (the long-tail user cohort).

### 8.2 Empty catalog

If `audioclip` has no `status='ready'` rows, the rebuild query returns 0 rows, the ZSET ends up empty (or stays as the previous pool, depending on implementation choice — see below), and `refill_user_feed` returns `"No new clips to push."` (matching the current `tasks.py:450` behavior).

DECISION: on an empty rebuild, **leave the previous ZSET in place** (don't `DEL` before the rebuild). If the previous ZSET is also empty, the result is the same; if it had data, a transient catalog-empty state (a fluke Beat failure) doesn't evict the entire pool.

Implementation: the rebuild pipeline uses `ZADD ... GT` (only update if new score is greater) — see Redis docs. This means the rebuild is incremental and the ZSET survives an empty rebuild.

### 8.3 Redis outage during rebuild

The rebuild task calls `redis_client.zadd(...)` which raises on Redis connection error. The `autoretry_for=RETRYABLE_ERRORS` retry config retries up to 2 times with a 30s delay. If all retries fail, the previous ZSET stays in place (per §8.2). `refill_user_feed` reads the previous ZSET — possibly stale by up to one Beat cycle (5 min for global, 1 hour for per-user). The system is in a degraded-but-functional state.

If Redis goes down *between* the rebuild and the next refill: the refill's `zrevrangebyscore` raises `ConnectionError`. This is caught by `FastFeedViewSet.list`'s outer try/except (`views/feed.py:71`), which falls back to the trending-feed path (`views/feed.py:124-133`). Same graceful-degradation contract as today.

### 8.4 Pool stale after Redis restart

If Redis is restarted with AOF persistence (`docker-compose.yml:74` has `--appendonly yes`), the ZSETs survive. If Redis is restarted without AOF (e.g., `docker compose kill -9 redis_cache`), the ZSETs are gone. `refill_user_feed` then hits the empty-ZSET path → cold-start fallback (§6.3) → on-demand SQL path → returns 202 if SQL > 200ms.

DECISION: graceful-degradation contract is "5-min p99 latency hit at most, no 500s". The first refill after Redis restart pays the SQL cost (200ms-budget); the next global-pool Beat cycle (≤5 min) repopulates the pool and refills return to <5ms.

### 8.5 Pool corruption

If a ZSET member is not a valid UUID (e.g., a string was written by a buggy consumer), `AudioClip.objects.filter(id__in=...)` at `views/feed.py:108` returns a subset (Postgres UUID column rejects the bad value silently). The user sees fewer clips, no error.

DECISION: filter non-UUID members in `get_user_candidates` before returning. Implementation team:

```python
import re
UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)

def _is_uuid(s):
    return bool(UUID_RE.match(s))
```

Apply this filter in `get_user_candidates` and log a warning per non-UUID member (one log line per rebuild, not per refill — suppress by sampling).

### 8.6 Stale `engagement_velocity`

Between the rebuild and the refill, `engagement_velocity` may change for some clips. Specifically: the global pool is rebuilt every 5 min (`update_global_metrics` runs every 5 min at `settings.py:285`), so a clip whose `engagement_velocity` spikes just after a rebuild shows up in the pool ~5 min later. This is the **staleness contract** and is documented in §5.

For viral content, 5-min lag is acceptable. If the AI team wants < 1-min lag, that's a separate work item (real-time event-driven pool invalidation, currently in `docs/event-driven-architecture-plan.md` as P1.4 / "Transactional outbox for ClipPublished", explicitly out of scope here per §10).

### 8.7 User unfollows a creator

The per-user pool is rebuilt hourly. If a user unfollows creator X between two rebuilds, clips from X remain in the user's pool until the next rebuild (≤1 hour). The follow-graph wedge (`tasks.py:410-412`) is unaffected because it queries `user.following.all()` live, not from the pool.

DECISION: 1-hour follow-staleness is acceptable. The user sees at most one refill cycle of stale follow-creator content.

### 8.8 Pool size mismatch with catalog

If the catalog grows past 10k clips (the global pool's TOP_N), the pool's tail is the 10k-th ranked clip, not the 11k-th. A clip that just hit the top 10k takes ≤5 min to enter the pool (§5). A clip that has been in the catalog for hours but ranks 11k-th is correctly excluded.

For the per-user pool: `FEED_POOL_USER_TOP_N = 1000` clips ranked against the user's blended vector. Same staleness model as global.

---

## 9. Implementation Checklist

In order:

1. **Add `backend/app/services/feed_pool.py`** with three functions:
   - `rebuild_global_exploit_pool()` — stream-and-mean, ZADD batched
   - `rebuild_user_explore_pool(user_id)` — per-user, self-throttled
   - `get_user_candidates(user_id, count=50)` — the §6.2 helper
   - Module-level constants: `GLOBAL_KEY`, `USER_KEY_TEMPLATE`, `FOLLOW_GRAPH_SIZE = 5`

2. **Add two Celery Beat tasks** in `backend/app/tasks.py`:
   - `rebuild_global_exploit_pool` — schedule every 5 min (`FEED_POOL_GLOBAL_TTL = 300`)
   - `dispatch_user_pool_rebuilds` — schedule every hour; fans out `rebuild_user_explore_pool(user_id)` calls
   - `rebuild_user_explore_pool(user_id)` — invoked by the dispatcher

3. **Refactor `refill_user_feed`** at `tasks.py:347-456`:
   - Keep the lock, short-circuit, follow-graph wedge, dedup, shuffle, RPUSH, EXPIRE
   - Replace steps 4–10 (`tasks.py:364-429`) with calls to `get_user_candidates(user_id, count)` + the cold-start fallback in `_refill_user_feed_on_demand_fallback`
   - Extend the `user_vectors:{user_id}` cache TTL from 15 min to 1 hour *for the rebuild path only* — keep the 15-min TTL for the `/suggestions/` path. Or use a separate cache key (`user_vectors_pool:{user_id}` with 1h TTL). DECISION: separate cache key. Reason: the two paths have different freshness requirements and sharing a TTL would force them to one value.

4. **Add to `CELERY_BEAT_SCHEDULE`** at `settings.py:282`:
   ```python
   'rebuild-global-exploit-pool': {
       'task': 'backend.app.tasks.rebuild_global_exploit_pool',
       'schedule': 300.0,
   },
   'dispatch-user-pool-rebuilds': {
       'task': 'backend.app.tasks.dispatch_user_pool_rebuilds',
       'schedule': 3600.0,
   },
   ```

5. **Add config knobs** at `settings.py`:
   ```python
   FEED_POOL_GLOBAL_TOP_N = int(os.getenv('FEED_POOL_GLOBAL_TOP_N', '10000'))
   FEED_POOL_USER_TOP_N = int(os.getenv('FEED_POOL_USER_TOP_N', '1000'))
   FEED_POOL_GLOBAL_TTL = int(os.getenv('FEED_POOL_GLOBAL_TTL', '300'))
   FEED_POOL_USER_TTL = int(os.getenv('FEED_POOL_USER_TTL', '86400'))
   FEED_POOL_USER_FANOUT_CHUNKS_PER_HOUR = int(
       os.getenv('FEED_POOL_USER_FANOUT_CHUNKS_PER_HOUR', '60')
   )
   FEED_POOL_USER_COLD_START_BUDGET_MS = int(
       os.getenv('FEED_POOL_USER_COLD_START_BUDGET_MS', '200')
   )
   FEED_POOL_SQL_FALLBACK_ENABLED = os.getenv(
       'FEED_POOL_SQL_FALLBACK_ENABLED', 'true'
   ).lower() in ('1', 'true', 'on')
   ```

6. **Bump Redis memory limit** in `docker-compose.yml:74`:
   ```yaml
   command: redis-server --appendonly yes --maxmemory 3gb --maxmemory-policy allkeys-lru
   ```
   This is required (§11.1). Without it, the new pools will OOM-evict under default load.

7. **Test file** `backend/app/tests/test_feed_pool.py`:
   - `test_global_rebuild_writes_top_n_to_zset`
   - `test_user_rebuild_writes_top_n_to_zset`
   - `test_refill_uses_pool_when_present`
   - `test_refill_falls_back_to_sql_when_pool_empty` (cold-start path)
   - `test_refill_dedupes_pool_overlap`
   - `test_refill_excludes_seen_clips`
   - `test_refill_filters_non_uuid_members` (§8.5)
   - `test_refill_returns_202_when_sql_exceeds_budget` (§6.3)
   - `test_user_rebuild_skipped_when_fresh` (§4.4)
   - `test_follow_graph_wedge_unchanged` (regression)
   - `test_zset_size_within_budget` (memory budget check)
   - `test_global_rebuild_chunks_zadd_in_1000s`

8. **Update `docs/EXPLAIN/recommendation/`** with the actual code structure once implemented. The current `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37` documents the formula — leave that alone. This doc (`docs/EXPLAIN/recommendation/03-feed-pre-computation.md`) is the canonical reference for the pool design.

9. **Update `docs/unfixed-issues-2026-09-03.md` §4.7** from "OPEN" to "FIXED" with the anchor and a one-paragraph summary.

---

## 10. What is Out of Scope

Per the task brief and the existing audit trail:

- **ANN two-stage retrieval** (`docs/EXPLAIN/ai-ml/07-ann-candidate-generation.md`, Group A item 6 / P1 — AI team's work). This work item pre-computes the *candidate set*; the AI team's work item pre-computes the *candidate generation step* (HNSW two-stage retrieval at query time). They compose: this doc's pool is the input layer that the AI team's two-stage retrieval reads from. Order: ship this first, then the AI team's work reads from the pool's output.

- **F() counter architectural fix** (`docs/unfixed-issues-2026-09-03.md` §3.2 / §3.3, P1.1 counter pipeline — Redis INCRBY + flush_counters_to_pg). Deferred to P1.1.

- **Transactional outbox for `UserFollowed` / `ClipPublished` events** (`docs/unfixed-issues-2026-09-03.md` §4.5, P1.4). The outbox enables per-event pool invalidation (§8.6). Without the outbox, we accept 5-min staleness.

- **Real-time pool invalidation** (per-event pool rebuild). Overkill at this scale. The 5-min global pool staleness is the documented contract (§5, §8.6).

- **Follow-graph pool** (§6.4). Deferred to P3.x. The follow-graph wedge remains a live query.

- **`/suggestions/` rewrite**. Currently at `backend/app/views/feed.py:153-186` and uses `get_user_vectors` cache (15-min TTL). The candidate pool doesn't help `/suggestions/` because the endpoint filters by `category` (`views/feed.py:157`), which is a different slice than the global pool. Out of scope here.

- **A/B testable composite weights**. Hardcoded 0.45 / 0.30 / 0.25 per `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37`. Future AI team work.

---

## 11. Risks and Trade-Offs

### 11.1 Memory cost — the biggest concrete blocker

**The new pools cost ~1 GB of Redis memory** (§4.3). The current `redis_cache` allocation in `docker-compose.yml:74` is `--maxmemory 1gb`. **Without a bump to at least 3 GB, the new pools will OOM-evict live `user_feed:*` lists, triggering the self-feeding collapse scenario that `docs/event-driven-issues-2026-09-03.md` §4.2 / `docs/event-driven-architecture-plan.md:225` identifies as failure mode #6.**

REQUIRED change to `docker-compose.yml:74`:

```yaml
# Before
command: redis-server --appendonly yes --maxmemory 1gb --maxmemory-policy allkeys-lru

# After
command: redis-server --appendonly yes --maxmemory 3gb --maxmemory-policy allkeys-lru
```

Note: this overlaps with `docs/unfixed-issues-2026-09-03.md` §4.2 (P0.4 — Redis `volatile-lru` + 2 GB). That item bumps to 2 GB and switches to `volatile-lru`. **This doc's 3 GB + `allkeys-lru` is the alternative** — the implementation team must reconcile the two changes (they overlap on the same `docker-compose.yml:74` line). DECISION: this work item bumps to 3 GB + keeps `allkeys-lru`. Rationale: per-user pools must always survive (they're rebuilt hourly — if evicted, the rebuild happens but the refill QPS spike during rebuild is unacceptable). `volatile-lru` would evict them. `allkeys-lru` with 3 GB keeps everything in memory.

The team should also reconcile with `docs/EXPLAIN/decisions/02-discrepancies.md:55` which lists "Split Redis" as implemented — the `redis_cache` 1 GB allocation is the current shipped state, not the planned state.

### 11.2 Staleness contract

5 minutes for the global pool, 1 hour for the per-user pool. The user-visible impact:

| Event | Visible in global pool | Visible in per-user pool |
|---|---|---|
| New clip uploaded | ≤5 min (next rebuild) | ≤1 hour (next user-cycle rebuild) |
| Clip hits 1000 likes | ≤5 min (next `update_global_metrics` beat) | ≤1 hour |
| User likes clip X | n/a (doesn't change global) | ≤1 hour |
| Creator unfollows user | n/a | ≤1 hour (until next rebuild) |

The 80/20 split means most of the feed (the 80% exploit wedge) is global and benefits from the 5-min freshness. The 20% explore wedge is the per-user pool, which is acceptable at 1-hour staleness because it's the *exploration* slice (by design, the user expects to see new things they haven't engaged with — perfect freshness isn't required).

### 11.3 Pool rebuild is a thundering herd — manageable

170 tasks/min average (10k users / 60 min). The `celery_feed` worker pool has 4 workers (`docker-compose.yml:325-340`), each doing ~3-4 tasks/sec (a per-user rebuild is `calculate_time_decayed_vectors` + 1 composite SQL + ZADD ≈ 3-4 sec).

4 workers × 3.5 tasks/sec = 14 tasks/sec = **840 tasks/min**.

840 / 170 = ~5× headroom. Comfortable.

If the catalog grows to 100k active users: 1700 tasks/min × 1 task/sec (optimistic) vs. 840 tasks/min capacity → saturated. The implementation team should add a Prometheus counter for `rebuild_user_explore_pool` runtime and alert at p95 > 4 sec. At that point, increase `celery_feed` worker count to 8 or split per-user rebuild into a separate worker queue.

### 11.4 Lock contention with the F() counter path

The global pool's `SELECT` reads `engagement_velocity` and `avg_completion_rate` from `audioclip` — the same rows that `UserInteraction.save()` writes to via F() updates (`backend/app/models.py:200-206`). With `FOR UPDATE SKIP LOCKED` on the F() side (`models.py:193` and `tasks.py:567, 583` for `update_global_metrics`), the rebuild's read can skip locked rows.

BUT: the rebuild reads `SELECT ... FOR UPDATE SKIP LOCKED` against an id range; the F() `select_for_update` (`models.py:193`) takes a row-exclusive lock on the `userinteraction` row, **not** the `audioclip` row. The actual `audioclip` update at `models.py:205-206` takes a brief row-exclusive lock on the `audioclip` row during the F() write. The rebuild's SELECT (no lock, just an ordered scan) may briefly block on that F() write but will not deadlock — Postgres's MVCC handles this.

DECISION: the rebuild does NOT acquire `FOR UPDATE` — it reads via the default `ACCESS SHARE` lock mode, which doesn't conflict with the F()'s `ROW EXCLUSIVE` lock. The contention surface is the brief moment a single F() UPDATE holds its row lock, and that lock is held for microseconds (one `UPDATE audioclip SET likes = likes + 1`).

This is the *same* contention surface as the current `refill_user_feed`. The pool design **doesn't make this worse**; the F() architectural fix (P1.1) is what makes it disappear.

### 11.5 Pool becomes the new SPOF

If Redis goes down, refill falls back to SQL (§6.3, §8.4). The graceful-degradation contract is "5-min p99 latency hit at most, no 500s".

Concrete failure scenario:

1. Redis is up, pools are populated.
2. Redis crashes.
3. `FastFeedViewSet.list` catches the `ConnectionError` at `views/feed.py:71` and falls back to the trending-feed query (`views/feed.py:124-133`). No 500s.
4. The trending-feed query is a single `SELECT ... ORDER BY -engagement_velocity LIMIT 20` against `audioclip`. This is the existing graceful-degradation path (`docs/EXPLAIN/failure/03-feed-resilience.md` "Current Behavior").

What the new design **adds**: when Redis comes back up but the pools are empty (AOF replay takes time, or Redis was restarted without persistence), `refill_user_feed` falls back to SQL for up to 5 min (until the next global-pool Beat cycle). This is documented in §8.4.

What the new design **doesn't change**: the cascading-failure scenario where 5k concurrent users hit `/feed/` simultaneously during a Redis outage, each triggering `refill_user_feed` which all do composite SQL at once. The pool design helps here **only** if Redis stays up but the pools are stale — it doesn't help if Redis is down.

DECISION: the cascading-failure scenario is mitigated by the existing graceful-degradation (`views/feed.py:71-140`). This design does not regress that path.

---

## 12. Verification Plan

### 12.1 Unit tests

See §9 item 7. The test file `backend/app/tests/test_feed_pool.py` covers:

- Pool rebuild correctness (top-N selection)
- Refill reads from pool (mock the ZSET, assert the helper returns the expected clip IDs)
- Refill falls back to SQL on cold start (mock empty ZSET, assert SQL path runs)
- Refill dedupes (overlapping global + per-user pools → no duplicate UUIDs in result)
- Refill excludes seen clips (mock `userinteraction` rows, assert they're not in the result)
- Non-UUID filter (§8.5)
- 202 response when SQL exceeds budget (§6.3)
- User-rebuild self-throttle (§4.4)
- Follow-graph wedge regression (existing `tasks.py:410-412` logic preserved)
- ZADD chunking at 1000 (mock Redis pipeline, assert 10 pipeline calls for 10k members)
- ZSET size within budget (assert `redis_client.zcard(key) <= FEED_POOL_GLOBAL_TOP_N` after rebuild)

### 12.2 Integration / regression

```bash
# Inside the Docker web container, per AGENTS.md "Running Tests":
docker compose exec web pytest backend/app/tests/test_feed_pool.py -v
docker compose exec web pytest backend/app/tests/ --tb=short
```

The full pytest suite must remain green. The `test_services_*` and `test_security_and_validation` suites should be unaffected — they don't exercise `refill_user_feed`.

### 12.3 Load test

```bash
# 10k concurrent /feed/ for 30 minutes (k6 or locust).
# Per AGENTS.md, run inside the docker stack; verify:
#   - p50 < 10ms (Redis ZSET read)
#   - p95 < 50ms (Redis ZSET read + dedup + follow-graph SELECT)
#   - p99 < 200ms (Redis ZSET read + dedup + follow-graph SELECT + cold-start fallback path)
#   - Redis memory < 2.5GB (3GB ceiling with 500MB headroom)
#   - SQL queries/sec against audioclip < 5 (was 250-330 pre-change; target is ~95% reduction)
```

The SQL query count target is the verification metric: if pre-change is 250-330 SELECTs/sec at 10k concurrent refill rate, and post-change is the follow-graph wedge only (5,000 refills/min × 1 follow-graph SELECT = 5,000/min ≈ 83/sec) plus the cold-start fallback (≪ 83/sec), the expected reduction is ~95%.

### 12.4 Cold-start fallback verification

```bash
# Inside the Docker web container:
docker compose exec web celery -A backend.EchoFlow control cancel_consumer fast_feed
# (pauses the worker — pools won't be refreshed)
# Trigger /feed/ via curl with a valid JWT; observe:
#   - First call: 202 Accepted with retry_after_ms
#   - Subsequent calls after Redis warm: 200 OK with results from SQL path
docker compose exec web celery -A backend.EchoFlow control cancel_consumer fast_feed --resume
```

### 12.5 Redis memory dashboard

Add to the observability stack (`docs/unfixed-issues-2026-09-03.md` §3.4, P3.2 — currently 🟡 Partial):

```promql
# Memory utilization of redis_cache
redis_memory_used_bytes / redis_memory_max_bytes > 0.85
```

Alert at 85% memory utilization. The 3 GB ceiling (§11.1) gives a 15% headroom; alert at 85% (2.55 GB used) gives 450 MB buffer for transient spikes (e.g., the `stream:interaction.events` MAXLEN 50k chunk eviction under load).

### 12.6 Reconciliation with `docs/EXPLAIN/decisions/02-discrepancies.md`

After implementation, update:

- `docs/unfixed-issues-2026-09-03.md` §4.7 from "OPEN" to "FIXED" with anchor `backend/app/services/feed_pool.py` and the rebuild task names.
- `docs/EXPLAIN/redis-celery/01-redis-usage.md:212-213` to reflect the new 3 GB ceiling.
- `docs/EXPLAIN/docker/02-docker-compose.md:192` (the redis-cache resource row) similarly.
- `docs/EXPLAIN/architecture/02-deployment-topology.md:291` similarly.

---

## 13. Decision Log (Tag Reference)

Future developers should not "simplify" these decisions without understanding the constraints. Each entry below is annotated for code review:

```text
// DECISION (in get_user_candidates): 80% global + 20% per-user.
// Why: matches tasks.py:400, 420 ratio; per-user pool cost is 10x global
// per cycle, so 80/20 is the optimal cost-quality point. Tradeoff:
// per-user exploration is the cheaper half of the feed by ratio but
// the more personalized half by composition. Acceptable.

// DECISION (in rebuild_global_exploit_pool): chunked ZADD at 1000.
// Why: one 10k-member ZADD blocks Redis main thread for ~50ms; ten
// 1k-member ZADDs complete in <10ms total. Tradeoff: 10 round-trips
// vs. one; network is cheaper than Redis blocking.

// DECISION (in rebuild_user_explore_pool): skip if fresh within 5 min.
// Why: Beat's DatabaseScheduler doesn't dedup; the self-throttle makes
// the rebuild "at-most-every-hour" instead of "exactly-every-hour".
// Tradeoff: a user who happens to be in two Beat cycles in one hour
// only gets rebuilt once. Acceptable; we want freshness, not duplication.

// DECISION (in refill_user_feed cold-start fallback): 200ms SQL budget.
// Why: matches FastFeedViewSet SLO (views/feed.py:90-98 returns 202
// after 1500ms retry hint; a 200ms SQL is acceptable in-line, anything
// more must be async). Tradeoff: a SQL that exceeds 200ms causes a
// 1.5s perceived latency for the first refill after cold start.
// Acceptable; documented as the graceful-degradation contract.

// DECISION (in feed_pool.py module-level): USER_KEY_TEMPLATE uses
// "user:{user_id}:candidates:explore" naming.
// Why: consistent with the planned "user:{id}:context:*" namespace
// from docs/event-driven-architecture-plan.md:562. Tradeoff: the
// "user:context:" prefix is reserved for the P2.1 user-context vector
// work item (docs/unfixed-issues-2026-09-03.md §4.6, currently OPEN).
// Coordination needed: when P2.1 ships, the candidate pool key
// remains "user:{id}:candidates:*" and does NOT conflict.

// SECURITY (in get_user_candidates): filter non-UUID members.
// Why: a buggy upstream consumer could write non-UUID strings to the
// ZSET (e.g., test fixtures, debug scripts). Postgres UUID column
// silently filters them at query time, leading to user-visible
// underfilled feeds. Filtering in Python at read time catches this
// earlier and logs the bad member for debugging. Tradeoff: regex
// check per ZSET read; for 50 candidates it's <1ms. Acceptable.

// SECURITY (in rebuild_global_exploit_pool): the rebuild task is
// not user-aware — it reads from AudioClip.status='ready' which is
// the public catalog. No authorization check needed. The rebuild
// does not reveal anything that the public clip list endpoint
// doesn't already expose.

// HACK (in refill_user_feed follow-graph wedge): left as a live query
// instead of pooled. Reason: 5 clips per refill × 5k refills/min = 25k
// SELECTs/min, which is acceptable. A follow-graph pool is a future
// P3.x optimization (one SELECT per follow event vs. one SELECT per
// refill). Tradeoff: ~25k SELECTs/min vs. ~50 SELECTs/min with pooling.
// Acceptable for this work item; document for the next iteration.

// TODO (in services/feed_pool.py): replace stream-and-mean global
// vector aggregation with a pgvector vector_avg SQL function when
// pgvector ships one. The current stream-and-mean is O(N) Python work;
// the SQL function would be O(N) C work and 10x faster. Defer until
// pgvector ships the aggregate or the team implements a custom one.
```

---

## 14. Verification Note (Provenance)

This design doc was constructed by:

1. **Reading the source.** Every `file:line` cite was read from the working tree, not from a prior doc. Specifically: `backend/app/tasks.py:346-456` (`refill_user_feed`), `backend/app/tasks.py:368` (`calculate_time_decayed_vectors` call site), `backend/app/tasks.py:529-588` (`update_global_metrics` + `FOR UPDATE SKIP LOCKED`), `backend/app/views/feed.py:58-118` (`FastFeedViewSet.list`), `backend/app/services/interactions.py:48-75, 124-168` (telemetry stream path), `backend/app/models.py:200-206` (F() counter path), `backend/EchoFlow/settings.py:190-210, 282-325` (Redis + Celery Beat config), `docker-compose.yml:70-76` (`redis_cache` service).

2. **Cross-referencing the audit.** `docs/unfixed-issues-2026-09-03.md` §4.7 (P2.2) is the canonical open issue. The task brief's "Group A item 7" reference is a legacy label; §4.7 is the correct anchor. The math in §1.2 matches `docs/event-driven-architecture-plan.md:568` ("the DB sees 200+ queries/s").

3. **Following the EXPLAIN docs.** The composite formula at `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37` is the source of truth for the 0.45/0.30/0.25 weights. The Redis eviction policy discussion at `docs/EXPLAIN/failure/03-feed-resilience.md` is the source of truth for the graceful-degradation contract. The AI team's two-stage retrieval at `docs/EXPLAIN/ai-ml/07-ann-candidate-generation.md` is explicitly out of scope here (§10).

4. **Discrepancies found during verification:**
   - The task brief cites `refill_user_feed` at `tasks.py:346-456` — verified, correct.
   - The task brief says "Group A item 7" — verified, this maps to `docs/unfixed-issues-2026-09-03.md` §4.7 (P2.2), not "Group A". Doc should be updated to cite §4.7 going forward.
   - `docs/EXPLAIN/decisions/02-discrepancies.md:55` says "Split Redis implemented" — verified, `redis_cache` exists at `docker-compose.yml:70-76` with 1 GB. The 3 GB bump proposed in §11.1 supersedes the 1 GB allocation.
   - `docs/backend-audit.md:721` and `docs/scaling-analysis.md:619` claim `calculate_time_decayed_vectors` has a "weights never populated" bug — `docs/backend-bug-fixs.md` Row 9 says this is a false positive (`weights.append(final_weight)` IS called at `tasks.py:484` in the current code). The weights bug is resolved; this design assumes `calculate_time_decayed_vectors` works as documented at `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:43-89`.

---

*Source: `backend/app/tasks.py:346-456`, `backend/app/views/feed.py:58-118`, `backend/app/services/interactions.py:1-181`, `backend/app/models.py:170-207`, `backend/EchoFlow/settings.py:190-325`, `docker-compose.yml:70-76`, `docs/unfixed-issues-2026-09-03.md` §4.7, `docs/event-driven-architecture-plan.md:559-574`, `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-89`, `docs/EXPLAIN/redis-celery/01-redis-usage.md`, `docs/EXPLAIN/failure/03-feed-resilience.md`. All anchors verified against the working tree at the time of writing.*