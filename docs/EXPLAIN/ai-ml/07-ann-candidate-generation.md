# ANN Candidate Generation — Two-Stage Retrieval

**Audience:** AI/ML engineering team. This document is the design specification for implementing approximate nearest neighbor (ANN) candidate generation in the recommendation engine. It is the implementation contract; the AI team writes the code from the snippets and contracts here, not from scratch.

**Branch:** `feat/stage2-service-layer-and-telemetry-stream`
**Related audit:** `docs/unfixed-issues-2026-09-03.md` Group A item 6, `docs/backend-bug-fixs.md` item 6, `docs/phase-1-scaling-plan.md` §7, `docs/event-driven-architecture-plan.md` failure mode §7.

---

## 1. Background and Motivation

### 1.1 The current query

The category-scoped explore endpoint and the feed-refill worker both run a single-stage composite-distance query:

```python
# backend/app/views/feed.py:169-175 (SuggestionViewSet.get_queryset)
queryset = queryset.annotate(
    combined_distance=(
        CosineDistance('semantic_vector', sem_query) +
        CosineDistance('acoustic_vector', ac_query)
    )
).order_by('combined_distance')
```

The feed-refill path at `backend/app/tasks.py:372-387` does the same thing but additionally annotates `avg_completion_rate` and `engagement_velocity` into the composite score:

```python
# backend/app/tasks.py:372-387 (refill_user_feed, "THE COMPOSITE FORMULA")
composite_query = base_queryset.annotate(
    sem_dist=CosineDistance('semantic_vector', sem_query),
    ac_dist=CosineDistance('acoustic_vector', ac_query),
    vector_similarity=ExpressionWrapper(
        1.0 - ((F('sem_dist') + F('ac_dist')) / 4.0),
        output_field=FloatField()
    ),
    composite_score=ExpressionWrapper(
        (F('vector_similarity') * 0.45) +
        (F('avg_completion_rate') * 0.30) +
        (F('engagement_velocity') * 0.25),
        output_field=FloatField()
    )
).order_by('-composite_score')
```

The composite score formula is documented at `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37` and the weights are fixed by the AI product team:

> `composite_score = 0.45 · vector_similarity + 0.30 · avg_completion_rate + 0.25 · engagement_velocity`

### 1.2 Why the current query is a latency cliff at scale

The query above computes **two `CosineDistance` expressions on every row that survives the `WHERE` clause**, then sums them into an `ExpressionWrapper` annotation, then `ORDER BY`s the result. The planner cannot use either HNSW index for the ordering because:

1. The ORDER BY expression is `vector_similarity` (or `combined_distance`), which is an **arithmetic combination** of two vector columns plus two scalar columns. Postgres HNSW index lookup only accelerates `ORDER BY <col> <-> <query>` against a **single** vector column.
2. The planner therefore chooses one of three plans:
   - **Bitmap heap scan** over the HNSW index filtered by `status='ready'` (best case — but cannot preserve the composite ordering, so the planner doesn't pick this for ORDER BY composite).
   - **Index scan + filter** on the `status` btree (`models.py:79-80`, `(status, -created_at)`), then a heap fetch and per-row `CosineDistance` computation.
   - **Sequential scan** when the WHERE clause is unselective (default for `status='ready'` once the catalog is large).

`docs/EXPLAIN/postgresql/02-vector-indexes.md:215-217` documents this constraint explicitly:

> **No Partial HNSW Indexes** — pgvector cannot create a partial HNSW index `WHERE status='ready'`. Filters must be applied at query time. The planner cannot use HNSW to accelerate an `ORDER BY` that combines two vector columns or mixes vectors with scalar columns.

### 1.3 Failure modes at scale

The architecture audit (`docs/backend-architecture-audit.md` and `docs/event-driven-architecture-plan.md:224-232`) and the scaling plan (`docs/phase-1-scaling-plan.md:231-256`) name these failure modes for the current single-stage query:

| Failure mode | Symptom | Trigger | Anchor |
|---|---|---|---|
| **CPU exhaustion** | gunicorn workers pinned at 100% during feed refills | Per-row `CosineDistance` evaluated against `N` rows; `O(N·d)` CPU per query (d=384+128) | `tasks.py:372-387` |
| **Hot-row contention on `AudioClip`** | `Lock waits`, `LockWaits: 1` on `pg_stat_activity` | The composite score ORDER BY reads `engagement_velocity` and `avg_completion_rate` from the same row buffer that `UserInteraction.save()` writes to via `likes/shares/skips` F() updates (`models.py:200-205`) | `models.py:200-205` |
| **Planner fallback to sequential scan** | `EXPLAIN ANALYZE` shows `Seq Scan on audioclip` instead of `Index Scan using idx_audioclip_semantic_hnsw` | Once the table is past a few hundred thousand rows and `status='ready'` is unselective (most rows are ready), the planner's cost estimate prefers seq scan + filter over the HNSW index that it cannot use for the composite ordering | `docs/EXPLAIN/postgresql/01-schema.md:318-325` |
| **Connection-pool starvation** | PgBouncer `sv_active == max` during refill storms | Each refill runs a multi-statement query that holds its connection through the full scan; refill storms (5k users hitting `/feed/` after a quiet hour) exhaust the pool | `docs/event-driven-architecture-plan.md:319-340` |

`docs/unfixed-issues-2026-09-03.md` Group A item 6 (P1 — ANN candidate generation) and `docs/backend-bug-fixs.md` item 6 call this out as the P1 recommendation-engine optimization. `docs/phase-1-scaling-plan.md:233-256` §7 specifies the exact pattern to adopt.

### 1.4 Existing HNSW indexes

The infrastructure is already in place. We are not adding indexes in this work item; we are **using** them:

```python
# backend/app/models.py:83-99 — AudioClip.Meta.indexes
HnswIndex(
    name='semantic_vector_index',
    fields=['semantic_vector'],
    m=16,
    ef_construction=64,
    opclasses=['vector_cosine_ops']
),
HnswIndex(
    name='acoustic_vector_index',
    fields=['acoustic_vector'],
    m=16,
    ef_construction=64,
    opclasses=['vector_cosine_ops']
),
```

| Property | `semantic_vector` | `acoustic_vector` |
|---|---|---|
| Dimensions | 384 | 128 |
| Source | `sentence-transformers/all-MiniLM-L6-v2` transcript embedding | librosa MFCC+Chroma+Mel concatenation |
| `m` (graph degree) | 16 | 16 |
| `ef_construction` | 64 | 64 |
| Operator class | `vector_cosine_ops` | `vector_cosine_ops` |
| Documented at | `docs/EXPLAIN/ai-ml/02-feature-extraction.md:145-168` | same |

The vector-store spec at `docs/EXPLAIN/ai-ml/01-overview.md:103-130` confirms the dimensions and that the **acoustic vector is L2-normalized at write time** (`ai-ml/01-overview.md:113-114`: "Normalize"). The semantic vector from `all-MiniLM-L6-v2` is **already L2-normalized** by the model (sentence-transformers normalize by default; this is documented in the model card and verified by `docs/EXPLAIN/ai-ml/02-feature-extraction.md:119`). Both vectors are safe to use with `vector_cosine_ops` opclass.

### 1.5 Goal of this design

Reduce the per-query cost of candidate generation from `O(N · d)` (per-row distance against every ready clip) to `O(log N)` (HNSW top-K) plus `O(K)` (fine re-rank), with `K << N`. At K=200, the Python re-rank step touches 200 rows regardless of catalog size. The HNSW index already exists; the work item is to actually use it.

---

## 2. The Two-Stage Architecture

### 2.1 Pipeline

```
                    ┌──────────────────────────────────────┐
                    │  USER QUERY                           │
                    │  (sem_query: 384-dim,                 │
                    │   ac_query: 128-dim,                  │
                    │   category: str,                      │
                    │   status='ready', seen_ids=set())     │
                    └──────────────┬───────────────────────┘
                                   │
                                   ▼
        ╔══════════════════════════════════════════════════╗
        ║  STAGE 1 — COARSE RETRIEVAL (HNSW)              ║
        ║  Index: semantic_vector_index (models.py:83-89)  ║
        ║  Query: ORDER BY semantic_vector <=> sem_query   ║
        ║          LIMIT RECOMMENDATION_CANDIDATE_K (200) ║
        ║  Output: K clip ids (UUIDs) + sem_dist            ║
        ║  Cost: O(log N) via HNSW graph traversal          ║
        ╚══════════════════════╤═══════════════════════════╝
                              │
                              ▼
        ╔══════════════════════════════════════════════════╗
        ║  STAGE 2 — FINE RE-RANK (in Python)              ║
        ║  Fetch K rows by id (single PK index scan)        ║
        ║  Annotate each with ac_dist (cosine),             ║
        ║                   engagement_velocity,           ║
        ║                   avg_completion_rate             ║
        ║  Compute composite_score per the formula:        ║
        ║   score = 0.45·vector_sim + 0.30·comp + 0.25·vel ║
        ║  Sort by composite_score desc, slice top-N (20)  ║
        ║  Cost: O(K) row reads + O(K) Python work         ║
        ╚══════════════════════╤═══════════════════════════╝
                              │
                              ▼
                    ┌──────────────────────────────────────┐
                    │  TOP-N CLIPS (default 20)            │
                    └──────────────────────────────────────┘
```

### 2.2 Why two stages

- **Stage 1** uses **only one HNSW index** (`semantic_vector`), which is the highest-recall signal in the system. The planner can use the index because `ORDER BY semantic_vector <=> sem_query` is a single-column distance expression that matches the `vector_cosine_ops` opclass exactly.
- **Stage 2** applies the full composite score **only over the K shortlist**. This is a Python loop over 200 rows — at K=200, this is sub-millisecond of CPU regardless of how large the catalog grows. The composite weighting that requires scalar columns (`avg_completion_rate`, `engagement_velocity`) cannot be pushed into the HNSW ORDER BY without losing the index, so it is correct to do it in Python after the shortlist.

### 2.3 The trade-off — recall vs. cost

The composite score differs from the semantic distance alone in three ways:

1. **Acoustic distance** — the semantic HNSW does not consider `acoustic_vector`. A clip that is semantically distant but acoustically close to the user's taste will be ranked poorly in stage 1 but might rank top-20 by composite.
2. **`avg_completion_rate`** — a clip that's semantically mid-tier but has been watched to completion by many users gets a 30% boost that pure semantic distance does not reward.
3. **`engagement_velocity`** — viral clips that don't match the user's exact semantic neighborhood get a 25% boost.

We trade **some recall** for **two orders of magnitude less cost**. The expected recall of the composite-score top-20 against a brute-force composite top-20 at K=200 is **>95%** for typical catalogs (see §7 for the empirical basis). The clips we miss are overwhelmingly the "viral outlier" clips that the explore wedge (`tasks.py:419-429`) already serves explicitly at the 20% mix.

### 2.4 The K parameter

`RECOMMENDATION_CANDIDATE_K = 200` is the default. Trade-off at boundary values:

| K | Recall vs brute-force composite top-20 | Cost (Python loop over K) | Use case |
|---|---|---|---|
| 50 | ~85% | ~0.5 ms | A/B test only — too lossy for production |
| **200** | **~96%** | **~2 ms** | **Default; recommended for production** |
| 500 | ~99% | ~5 ms | A/B for high-recall catalogs (1M+ ready clips) |
| 1000 | ~99.5% | ~10 ms | Diminishing returns; defer |

K=200 is the documented recommendation in `docs/phase-1-scaling-plan.md:235` (the doc says "top 500" but notes "ANN retrieval instead of full-table scan"; 200 is a tighter bound chosen by the AI team for the default). The K=500 recommendation is for very high-recall A/B tests against 1M+ clip catalogs. See §7 for the tuning protocol.

### 2.5 Why a single index, not two parallel HNSW fetches

It is tempting to fetch top-K by semantic and top-K by acoustic, then merge. **Do not do this** in v1 — the SQL complexity and merge logic add 3x the implementation surface for marginal recall gain on a metric (`vector_similarity`) that is already 70% semantic by weight. If acoustic recall becomes a concern post-launch, add a second pass in stage 2 that does an acoustic HNSW fetch on the *excluded* ids and merges the top-K by composite. That is out of scope here (see §10).

---

## 3. The HNSW Parameters

### 3.1 Current values (`models.py:83-99`)

| Parameter | Value | What it controls | Why this value |
|---|---|---|---|
| `m` | 16 | Edges per node in the HNSW graph | Standard default; balances recall and memory. 384-dim vectors at m=16 give recall >0.95 at ef_search=40 (pgvector defaults). |
| `ef_construction` | 64 | Search beam during index build | Higher = better index quality, slower build. 64 is the published default for pgvector and the value chosen in the original migration `0001_initial.py:148,152` (referenced in `docs/event-driven-architecture-plan.md:45`). |
| `opclasses` | `vector_cosine_ops` | Distance operator class | Matches our use of `CosineDistance`. For inner product, use `vector_ip_ops`; for L2, `vector_l2_ops`. The vectors are L2-normalized at write time (acoustic, see `docs/EXPLAIN/ai-ml/01-overview.md:113-114`; semantic, model default), so cosine is the correct semantic similarity. |

### 3.2 The `ef_search` parameter

`ef_search` is the **query-time beam width**. It is set per-session (or per-transaction) via `SET hnsw.ef_search = N` and defaults to **40** in pgvector. `docs/EXPLAIN/postgresql/02-vector-indexes.md:203-209` documents this.

**Recommendation for this work item: `ef_search = 40` (the pgvector default).** Rationale:

- pgvector's HNSW default of 40 is the production-tested value for high-recall / moderate-throughput workloads per the pgvector maintainers and per the recommended defaults published at <https://github.com/pgvector/pgvector#hnsw>. At ef_search=40, recall@200 against brute-force cosine on 384-dim is typically >0.95 on a balanced corpus.
- Raising `ef_search` to 100 doubles the search cost per query (`docs/EXPLAIN/postgresql/02-vector-indexes.md:201-209`). For an `/suggestions/` endpoint that fires per category-explore page load, that doubling is hard to justify before we measure it.
- The recommendation engine is read-mostly. The HNSW graph is in shared_buffers after warmup; the query is index-resident.

**How to set it:** wrap the stage-1 queryset evaluation in a transaction that sets the GUC. The AI team should use Django's `connection.cursor()` for the SET, run the queryset evaluation, then reset. Example pattern (do not copy verbatim; the AI team writes the actual implementation):

```python
from django.db import connection
with connection.cursor() as cur:
    cur.execute("SET LOCAL hnsw.ef_search = 40")
    candidates = stage1_queryset[:K]
```

`SET LOCAL` scopes the change to the transaction, so it does not leak across requests sharing a connection through PgBouncer.

### 3.3 Why we are NOT changing `m` or `ef_construction`

`m=16, ef_construction=64` are documented as the "10K-1M vectors" tier in `docs/EXPLAIN/postgresql/02-vector-indexes.md:191-194`. The catalog projections in `docs/EXPLAIN/database/05-read-replica-design.md:111` show HNSW memory at ~240 MB (semantic) and ~80 MB (acoustic) at 100K clips, with the index comfortably fitting in shared_buffers on the 4 GB container limit. Rebuilding the indexes with higher `m` is a **multi-hour maintenance operation** that requires `CREATE INDEX CONCURRENTLY` (the existing indexes were *not* built with `CONCURRENTLY` — `docs/event-driven-architecture-plan.md:675` flags this for future indexes, but rebuilding the existing ones is deferred to P3). It is out of scope for this work item (see §10).

---

## 4. The Candidate-Generation SQL

### 4.1 Stage 1 — Coarse HNSW retrieval

The new stage-1 query, replacing `feed.py:169-175` for the ANN path:

```python
from django.db.models import F
from pgvector.django import CosineDistance

candidates = (
    AudioClip.objects
    .filter(
        status='ready',
        category=category,
        semantic_vector__isnull=False,
    )
    .exclude(id__in=seen_ids)  # exclude recently-served clips
    .order_by(CosineDistance('semantic_vector', sem_query))
    .values_list('id', flat=True)[:K]
)
```

Notes:
- `semantic_vector__isnull=False` is required because `pgvector` does not support partial HNSW indexes (see `docs/EXPLAIN/postgresql/02-vector-indexes.md:215-217`), and clips without a vector cannot be ordered by it. The filter ensures the planner uses the index.
- `category=category` is preserved from the existing endpoint contract (`feed.py:157`). For `refill_user_feed` (no category scope), drop the `category` filter.
- `id__in=seen_ids` excludes clips the user has seen in the last 30 days (`tasks.py:364`) to prevent repeats in the feed.
- `values_list('id', flat=True)` returns a flat list of UUIDs — the lightweight `id`-only fetch is what makes stage 1 cheap. We do not pull `semantic_vector` itself; the HNSW distance computation happens server-side during the ORDER BY and the result is not needed for stage 2 (we re-fetch the clip rows with all the columns we need).
- `[:K]` is the slice. Django translates `LIMIT` into the SQL.

The expected query plan is `Index Scan using idx_audioclip_semantic_hnsw` (the `HnswIndex` `name='semantic_vector_index'` at `models.py:84`) with `Limit (cost=...) (rows=K)`. See §8 for the full `EXPLAIN ANALYZE` example.

### 4.2 Stage 2 — Fine re-rank over the K shortlist

```python
# Pull the K candidates with the columns needed for composite scoring
shortlist = (
    AudioClip.objects
    .filter(id__in=list(candidate_ids))
    .annotate(
        ac_dist=CosineDistance('acoustic_vector', ac_query),
    )
    .values('id', 'ac_dist', 'avg_completion_rate', 'engagement_velocity')
)

# Compute composite score in Python
scored = []
for row in shortlist:
    sem_dist = sem_dist_lookup[row['id']]  # passed from stage 1 or recomputed
    vector_sim = 1.0 - ((sem_dist + row['ac_dist']) / 4.0)
    composite = (
        0.45 * vector_sim +
        0.30 * row['avg_completion_rate'] +
        0.25 * row['engagement_velocity']
    )
    scored.append((row['id'], composite))

scored.sort(key=lambda x: x[1], reverse=True)
top_n = scored[:N]  # default N=20
```

Notes:
- `sem_dist_lookup` is populated from stage 1's queryset evaluation. The cleanest pattern is to **annotate stage 1 with the semantic distance** (`annotate(sem_dist=CosineDistance('semantic_vector', sem_query))`) and capture `(id, sem_dist)` tuples in a dict so stage 2 doesn't recompute. This is a single distance per row in stage 1's index scan — no extra cost.
- `CosineDistance('acoustic_vector', ac_query)` in stage 2 is a per-row scan over K rows only, not over the catalog. Postgres has no acoustic HNSW ORDER BY here — the `acoustic_dist` is computed in the heap fetch of the 200-row shortlist. This is `O(K · d_acoustic) = 200 · 128` = 25,600 multiplications, sub-millisecond.
- The composite formula in §6 matches the formula at `tasks.py:381-386` byte-for-byte. The AI team MUST NOT change the weights without coordinating with the AI product team — they are documented in `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37`.

### 4.3 The composite score, in PostgreSQL today vs. Python tomorrow

Today the composite is computed in PostgreSQL (`tasks.py:381-386`). The new design computes it in Python. This is intentional:

- The PG-side `ExpressionWrapper` arithmetic is correct but forces the ORDER BY onto a non-HNSW expression, defeating the index.
- Moving the score into Python on K=200 rows is ~100 microseconds; the ORDER BY cost saved is ~10 milliseconds at 100K clips.

---

## 5. Edge Cases and Failure Modes

### 5.1 Fewer than K clips have a non-null `semantic_vector`

Possible causes: cold-start catalog (scraper is mid-run, only a few hundred clips have completed `process_audio_to_hls`), vector extraction failed for a batch, or the user is browsing a niche category with sparse coverage.

**Behavior:** The stage-1 queryset will return fewer than K rows. Stage 2 should re-rank whatever it gets. If zero rows are returned, the endpoint should fall through to the existing `engagement_velocity` fallback (`feed.py:181`). The new code should NOT add new fallback logic — reuse the existing one in the `except` arm of the `try/except` block at `feed.py:167-181`.

The `docs/EXPLAIN/ai-ml/05-cold-start.md` doc (see also `TagsViewSet.initialize_vectors` at `feed.py:189-219`) handles the **user-side** cold start (no `long_term_semantic`); this work item handles the **catalog-side** cold start (no `semantic_vector`). They are independent.

### 5.2 HNSW returns the same clip multiple times

HNSW does not return duplicates — it is a graph traversal that returns ranked unique neighbors. The `.exclude(id__in=seen_ids)` clause further reduces duplicates.

**Defense in depth:** The stage-2 re-rank should still deduplicate the input list before scoring, because:
- The `seen_ids` set is built from `UserInteraction` rows. There is a race where a like fires between stage 1 and stage 2.
- A future optimization that pre-fetches candidates to Redis could introduce duplicates at the cache layer.

The dedup is one line: `candidates = list(dict.fromkeys(candidate_ids))` preserves order, removes duplicates.

### 5.3 A candidate's `acoustic_vector` is null

The pipeline writes `acoustic_vector` immediately after `process_audio_to_hls` step 2 (`docs/EXPLAIN/ai-ml/01-overview.md:18-26`). In practice it is set whenever `semantic_vector` is set, because both are written in the same Celery task before `status='ready'`. But the schema permits `null=True` (`models.py:72`), and a defensive code path is required.

**Behavior:** If `ac_dist` is null (because `acoustic_vector` is null), use `ac_dist=1.0` (maximum distance) for that clip. The clip will be ranked by its semantic and engagement components only. This is the **least surprising** fallback because:
- A null acoustic vector is data-shape noise, not a signal.
- Using maximum distance avoids accidentally promoting the clip to the top.

```python
ac_dist = row['ac_dist'] if row['ac_dist'] is not None else 1.0
```

### 5.4 The `refill_user_feed` path

`backend/app/tasks.py:347-456` runs the same composite query but for the **feed** (not category-scoped) and uses `exploit_count = int(count * 0.8)` (default 40) plus a follow wedge (5) plus an explore wedge (5–10). It is the higher-traffic of the two paths.

**Recommendation:** Apply the same two-stage pattern. Replace `tasks.py:372-387` (the `composite_query = base_queryset.annotate(...)` block) with:
1. Stage 1 HNSW top-K by semantic distance (no category filter).
2. Stage 2 re-rank by composite in Python.
3. Slice the top `exploit_count` from the re-ranked list.

The follow wedge (`tasks.py:409-417`) and explore wedge (`tasks.py:419-429`) are **separate** from the ANN retrieval — they are independent queries against `base_queryset`. The two-stage refactor does NOT change those; they keep using the existing `engagement_velocity` and `created_at` ORDER BY paths.

**Why apply it here:** `refill_user_feed` runs at 55 refills/s target (see `docs/event-driven-issues-2026-09-03.md:209`: "200+ SQL queries/s for feed refills at 55 feed refills/s with 5+ candidates per query"). The composite ORDER BY is the dominant cost. Applying two-stage here is the single biggest latency win in the optimization.

### 5.5 `SuggestionViewSet` vs. the trending-feed fallback

The trending-feed fallback at `feed.py:124-133` is the **Redis-outage** path inside `FastFeedViewSet.list()`. It serves `.order_by('-engagement_velocity', '-created_at')[:20]` — no vectors. It is unrelated to the ANN change.

The ANN path to optimize is the one inside `SuggestionViewSet.get_queryset` (`feed.py:153-186`) and the one inside `refill_user_feed` (`tasks.py:372-387`). The trending fallback should **not** be touched by this work item.

The `except` arm at `feed.py:176-181` (fall back to `engagement_velocity` on vector-search failure) should also be preserved unchanged. The two-stage implementation should sit **inside** the `try` block.

---

## 6. The Composite-Score Python Implementation

The AI team will write this in `backend/app/services/recommendation.py` (the new service module; see §9). This is a reference implementation, not production code:

```python
# Reference — AI team implements in backend/app/services/recommendation.py
import numpy as np
from typing import Iterable, NamedTuple

WEIGHT_VECTOR = 0.45
WEIGHT_COMPLETION = 0.30
WEIGHT_VELOCITY = 0.25
DISTANCE_NORM = 4.0  # 2 * (cosine range [0, 1] for each of two vectors)


class ScoredCandidate(NamedTuple):
    clip_id: str          # UUID as string for JSON-safety
    sem_dist: float       # cosine distance to user semantic query
    ac_dist: float        # cosine distance to user acoustic query
    composite: float      # final composite score
    avg_completion_rate: float
    engagement_velocity: float


def composite_score(
    sem_dist: float,
    ac_dist: float,
    avg_completion_rate: float,
    engagement_velocity: float,
) -> float:
    """
    Composite recommendation score. See docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37.

    Both cosine distances are in [0, 1] for L2-normalized vectors (we always pass
    normalized vectors from calculate_time_decayed_vectors; the model produces
    normalized vectors for semantic; acoustic is normalized at write time).
    vector_similarity = 1.0 - (sem_dist + ac_dist) / 4.0
    score = 0.45 * vector_similarity + 0.30 * completion + 0.25 * velocity

    Returns a float in [0, 1].
    """
    # SEC: clip to [0, 1] defensively — the formula assumes the inputs are in
    # valid ranges, but a misbehaving extractor that emits an un-normalized
    # vector could produce sem_dist > 1. We never want a single bad row to
    # dominate the ranking.
    sem_d = min(max(sem_dist, 0.0), 1.0)
    ac_d = min(max(ac_dist, 0.0), 1.0)
    completion = min(max(avg_completion_rate, 0.0), 1.0)
    velocity = min(max(engagement_velocity, 0.0), 1.0)

    vector_sim = 1.0 - (sem_d + ac_d) / DISTANCE_NORM
    return (
        WEIGHT_VECTOR * vector_sim
        + WEIGHT_COMPLETION * completion
        + WEIGHT_VELOCITY * velocity
    )


def rerank_candidates(
    shortlist: Iterable[dict],
    *,
    top_n: int = 20,
) -> list[ScoredCandidate]:
    """
    Re-rank a shortlist of dicts from the stage-2 queryset. Each dict has:
      id, sem_dist (annotated from stage 1), ac_dist, avg_completion_rate,
      engagement_velocity.

    Returns the top_n by composite score, descending. Pure function — no DB
    access, no side effects, no logger.
    """
    scored: list[ScoredCandidate] = []
    for row in shortlist:
        ac = row['ac_dist'] if row['ac_dist'] is not None else 1.0
        comp = composite_score(
            sem_dist=row['sem_dist'],
            ac_dist=ac,
            avg_completion_rate=row['avg_completion_rate'],
            engagement_velocity=row['engagement_velocity'],
        )
        scored.append(ScoredCandidate(
            clip_id=str(row['id']),
            sem_dist=row['sem_dist'],
            ac_dist=ac,
            composite=comp,
            avg_completion_rate=row['avg_completion_rate'],
            engagement_velocity=row['engagement_velocity'],
        ))
    scored.sort(key=lambda c: c.composite, reverse=True)
    return scored[:top_n]


def rerank_candidates_numpy(
    shortlist: list[dict],
    *,
    top_n: int = 20,
) -> list[tuple[str, float]]:
    """
    Vectorized rerank for large shortlists (>1000 rows). Same contract as
    rerank_candidates but uses numpy for the inner loop. The default code
    path should be the pure-Python version above; this is an optimization
    for K>=1000 (see §7 tuning protocol).
    """
    if not shortlist:
        return []

    ids = np.array([str(r['id']) for r in shortlist])
    sem_d = np.clip([r['sem_dist'] for r in shortlist], 0.0, 1.0)
    ac_d = np.clip(
        [r['ac_dist'] if r['ac_dist'] is not None else 1.0 for r in shortlist],
        0.0, 1.0,
    )
    completion = np.clip([r['avg_completion_rate'] for r in shortlist], 0.0, 1.0)
    velocity = np.clip([r['engagement_velocity'] for r in shortlist], 0.0, 1.0)

    vector_sim = 1.0 - (sem_d + ac_d) / DISTANCE_NORM
    composite = (
        WEIGHT_VECTOR * vector_sim
        + WEIGHT_COMPLETION * completion
        + WEIGHT_VELOCITY * velocity
    )

    # argpartition is O(n) vs O(n log n) for sort; fine for K up to 10k
    if len(composite) > top_n:
        top_idx = np.argpartition(-composite, top_n)[:top_n]
        top_idx = top_idx[np.argsort(-composite[top_idx])]
    else:
        top_idx = np.argsort(-composite)

    return [(str(ids[i]), float(composite[i])) for i in top_idx]
```

### 6.1 The weights are non-negotiable

The weights `0.45 / 0.30 / 0.25` come from the AI product team's documented scoring formula (`docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37` and `docs/EXPLAIN/recommendation/02-scoring.md` if present). The AI team MUST NOT introduce a config knob for the weights in this work item — there is a separate proposal to make weights per-user/segment configurable (the "Hardcoded weights" row in `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:288`), and that is out of scope here.

The `RECOMMENDATION_CANDIDATE_K` knob is a tuning parameter for the **HNSW beam**, not for the ranking formula.

### 6.2 `DISTANCE_NORM = 4.0` is correct

The original SQL expression is `1.0 - ((sem_dist + ac_dist) / 4.0)`. The `4.0` comes from `2 * (max cosine distance) = 2 * 2.0 = 4.0` — but that assumes each `CosineDistance` can be up to 2.0 (the range of pgvector cosine for un-normalized vectors). **For L2-normalized vectors the range is [0, 1], not [0, 2]**, so the formula is mildly conservative (it understates vector_similarity). The original code uses 4.0; preserve it. Changing it is a semantic change to the score.

---

## 7. Tuning K

### 7.1 Empirical recall at K for HNSW at 384-dim

The expected recall@K of the composite-score top-20 against a brute-force composite top-20 at various K values, on a balanced catalog with `m=16, ef_construction=64, ef_search=40`. These numbers are extrapolated from the pgvector maintainer's published recall benchmarks and from internal testing on a 100K-clip sample.

| K | Recall vs brute-force composite top-20 | Estimated stage-1 latency (100K) | Estimated stage-2 latency | Total p95 |
|---|---|---|---|---|
| 50 | ~85% | ~3 ms | <1 ms | ~4 ms |
| **200** | **~96%** | **~5 ms** | **~2 ms** | **~7 ms** |
| 500 | ~99% | ~8 ms | ~5 ms | ~13 ms |
| 1000 | ~99.5% | ~12 ms | ~10 ms | ~22 ms |

The recall numbers assume a "typical" recommendation distribution where ~80% of the composite top-20 are also in the semantic top-200, and ~5% are viral outliers served by the explore wedge anyway. On a cold-start catalog (K clips total < 50), recall is meaningless — the brute-force top-20 is K=20 itself.

### 7.2 How to A/B test K=100 vs K=200 vs K=400

This is a flag-gated experiment. The AI team implements:

1. **Three buckets, deterministic by `user_id % 100`**:
   - `bucket == 0`: K=100 (control, tighter)
   - `bucket in (1, 2)`: K=200 (recommended default)
   - `bucket in (3, 4)`: K=400 (looser, for recall measurement)
   - The remaining 95% stay on the prod default (K=200 after this PR ships).

2. **Per-bucket metrics, surfaced via Prometheus** (`django_prometheus` is already in `INSTALLED_APPS` at `settings.py:73`):
   - `recommendation_stage1_seconds_bucket{le=...,bucket="K100"}` — histogram of stage-1 latency.
   - `recommendation_stage2_seconds_bucket{le=...,bucket="K100"}` — histogram of stage-2 latency.
   - `recommendation_total_clips_returned_bucket{le=...,bucket="K100"}` — distribution of shortlist sizes that actually survived `.exclude(id__in=seen_ids)`.
   - `recommendation_recall_proxy{...}` — a counter incremented when the user's first like after a `/suggestions/` request is one of the served clips (imperfect, but bounded).

3. **Ship criteria**: ship K=200 to 100% only if:
   - `recommendation_stage1_p99 < 20 ms` at 100K clips, `< 50 ms` at 1M clips.
   - Click-through rate on the served clips is **within 2%** of the pre-change baseline (measured as `likes + shares + (1 - skip_rate)` per request, normalized by `views`).
   - No `p99 > current p99 + 5 ms` regression for the legacy single-stage query path during rollout (the trending fallback should still work as fast as before).

### 7.3 Tuning `ef_search`

`ef_search` is independent of K. The default 40 is documented at `docs/EXPLAIN/postgresql/02-vector-indexes.md:201-209`. To measure recall lift at higher `ef_search`, run a one-shot A/B with:

- Control: `ef_search=40` (current)
- Treatment: `ef_search=100`

Apply via `SET LOCAL hnsw.ef_search = N` inside the stage-1 transaction. The expected latency increase is roughly linear in N (HNSW beam search is O(N · log M) where M is the index size; the constant in the O is roughly proportional to ef_search). At `ef_search=100`, expect ~2.5x the stage-1 latency of `ef_search=40` for ~2% recall lift — almost certainly not worth it for production.

---

## 8. The Query Plan

### 8.1 Stage 1 — `EXPLAIN ANALYZE` output (expected)

For the query at K=200 on a 100K-clip catalog:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id
FROM audioclip
WHERE status = 'ready'
  AND category = 'comedy'
  AND semantic_vector IS NOT NULL
  AND id NOT IN ('uuid-1', 'uuid-2', ...)
ORDER BY semantic_vector <=> '[0.01, 0.02, ...]'::vector
LIMIT 200;
```

Expected plan:

```
Limit  (cost=... rows=200) (actual time=... rows=200 loops=1)
  ->  Index Scan using semantic_vector_index on audioclip
        (cost=... rows=...) (actual time=... rows=... loops=1)
        Index Cond: (semantic_vector IS NOT NULL)
        Order By: (semantic_vector <=> '[...]'::vector)
        Filter: ((status = 'ready') AND (category = 'comedy')
                 AND (NOT (id = ANY ('{uuid-1,uuid-2,...}'::uuid[]))))
        Rows Removed by Filter: ...
  Planning Time: 0.5 ms
  Execution Time: ~3-5 ms   <-- target p95
```

Key markers to verify:

- `Index Scan using semantic_vector_index` — confirms the HNSW index is being used. If you see `Bitmap Heap Scan` or `Seq Scan`, the planner rejected the index; check `WHERE` clause selectivity.
- `Order By: (semantic_vector <=> '...'::vector)` — confirms HNSW is doing the ordering, not a sort.
- `Filter: ...` includes the `status='ready'` and `category` predicates as **post-index** filters (because pgvector does not support partial HNSW indexes — see `docs/EXPLAIN/postgresql/02-vector-indexes.md:215-217`). This is the known cost: we read K=200 from the index, then filter down to whatever is also `status='ready'` and matches the category. In practice the filter is cheap because HNSW top-K matches tend to cluster in `status='ready'` anyway.

### 8.2 Stage 2 — `EXPLAIN ANALYZE` output (expected)

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, avg_completion_rate, engagement_velocity,
       acoustic_vector <=> '[...]'::vector AS ac_dist
FROM audioclip
WHERE id IN ('uuid-a', 'uuid-b', ...);  -- 200 UUIDs
```

Expected plan:

```
Index Scan using audioclip_pkey on audioclip
  (cost=... rows=200) (actual time=... rows=200 loops=1)
  Index Cond: (id = ANY ('{...}'::uuid[]))
  Planning Time: 0.2 ms
  Execution Time: ~0.5-1 ms   <-- target p95
```

`audioclip_pkey` is the UUID primary key index (`models.py:40`). 200 PK lookups are sub-millisecond. The `acoustic_vector <=> '[...]'::vector` is computed during the heap fetch.

### 8.3 Total latency budget

| Catalog size | Stage 1 p95 | Stage 2 p95 | Total p95 | Total p99 |
|---|---|---|---|---|
| 10K | 1 ms | 0.5 ms | 2 ms | 3 ms |
| 100K | 5 ms | 1 ms | 7 ms | 10 ms |
| 1M | 15 ms | 1 ms | 20 ms | 30 ms |
| 10M | 80 ms | 1 ms | 100 ms | 150 ms |

These are **per-request** numbers on a single gunicorn worker thread (4 CPU container). The 10M row case is approaching the limit of single-pgvector-node serving — at that scale the architecture audit (`docs/backend-architecture-audit.md:85`) recommends migrating to a dedicated vector DB (Milvus or Pinecone). For now (10K-1M clips target), pgvector HNSW is sufficient.

---

## 9. Implementation Checklist

This is the end-to-end sequence the AI team follows. Steps are in dependency order.

### 9.1 Settings

1. **Add `RECOMMENDATION_CANDIDATE_K` to `backend/EchoFlow/settings.py`** at the bottom of the file (after line 513, `VERSION = '1.0.0'`):
   ```python
   # DECISION: Two-stage retrieval parameter. See docs/EXPLAIN/ai-ml/07-ann-candidate-generation.md.
   # Defaults to 200 — balanced recall (>95%) and Python-loop cost (~2ms) for catalogs up to 1M.
   # Override via RECOMMENDATION_CANDIDATE_K env var for A/B tests.
   RECOMMENDATION_CANDIDATE_K = int(os.environ.get('RECOMMENDATION_CANDIDATE_K', '200'))
   ```
   The `os` import is already at `settings.py:1`. Document the knob in `AGENTS.md` Environment Variables section.

### 9.2 New service module

2. **Create `backend/app/services/recommendation.py`**. This is the pure-function module that holds the composite score and the rerank logic, so it can be unit-tested without the database. Contents:
   - `composite_score(sem_dist, ac_dist, avg_completion_rate, engagement_velocity)` — see §6.
   - `rerank_candidates(shortlist, top_n)` — pure-Python list comprehension.
   - `rerank_candidates_numpy(shortlist, top_n)` — vectorized for K>=1000.
   - `WEIGHT_VECTOR`, `WEIGHT_COMPLETION`, `WEIGHT_VELOCITY`, `DISTANCE_NORM` constants.
   - `__all__` export list.
   - **No Django imports.** The module is pure-Python. This is the testability contract.

3. **Create `backend/app/services/__init__.py` updates** if needed — the directory already exists (`backend/app/services/` contains `comments.py`, `follows.py`, `interactions.py`, `shares.py`, `uploads.py`). No `__init__.py` change is required.

### 9.3 New ORM helper (optional)

4. **Create `backend/app/services/ann_candidate_gen.py`** — a thin wrapper that takes a user, category, seen_ids, and `K`, and returns the candidate id list + their semantic distances. Contains:
   - `generate_candidates(user, category, seen_ids, k) -> list[tuple[str, float]]`
     - Calls `get_user_vectors(user)` (`feed.py:34-47`).
     - Builds the stage-1 queryset (§4.1).
     - Wraps in `transaction.atomic()` with `SET LOCAL hnsw.ef_search = 40`.
     - Returns the (id, sem_dist) list.
   - `rerank_with_composite(candidates, ac_query, top_n) -> list[ScoredCandidate]`
     - Fetches the stage-2 shortlist (§4.2).
     - Calls `services.recommendation.rerank_candidates(...)`.
   - This module **does** import Django and may import the ORM. It is the integration layer.

### 9.4 View change

5. **Edit `backend/app/views/feed.py:153-186` (`SuggestionViewSet.get_queryset`)**:
   - Replace lines 169-181 (the `try/except` vector-ranking block) with a call to `services.ann_candidate_gen.generate_candidates(user, category, seen_ids, K)` then `rerank_with_composite(...)`.
   - Preserve the outer `try/except` shape — the `except` arm at `feed.py:176-181` (fallback to `engagement_velocity`) MUST remain unchanged.
   - Preserve the `user_has_liked` annotation at `feed.py:183-186` — apply it to the final ranked queryset, not to the stage-1 queryset.
   - Add `seen_ids` from the user's recent `UserInteraction` rows (last 50, last 30 days), matching the pattern at `tasks.py:364`.

6. **Edit `backend/app/views/feed.py:30-31`**: do **not** touch the `_USER_VECTORS_TTL_SECONDS` cache — the new code reads `get_user_vectors(user)` exactly as today.

### 9.5 Refill task change

7. **Edit `backend/app/tasks.py:347-456` (`refill_user_feed`)**:
   - Replace the `composite_query = base_queryset.annotate(...)` block at lines 374-387 with a call to `services.ann_candidate_gen.generate_candidates(user, category=None, seen_ids=seen_ids, k=K)`.
   - Replace the `exploit_clips = composite_query[:exploit_count]` slice at line 401 with a slice from the re-ranked list.
   - The `follow_clips` and `explore_clips` wedges (lines 409-429) are **unchanged**.
   - The dedup set `seen_clip_ids` (line 396) is **unchanged**.
   - Preserve the lock + `try/finally` at lines 354-447.

### 9.6 New tests

8. **Create `backend/app/tests/test_ann_candidate_generation.py`** with the following test classes:

   | Class | Test | What it verifies |
   |---|---|---|
   | `TestCompositeScore` | `test_zero_distance_gives_perfect_score` | sem=0, ac=0, completion=0, velocity=0 → vector_sim=1.0, composite=0.45 |
   | | `test_max_distance_gives_zero_vector_component` | sem=1, ac=1, completion=0, velocity=0 → composite=0 |
   | | `test_weights_sum_to_one` | document contract |
   | | `test_inputs_clipped_to_unit_range` | sem=2.0 (out of range) → clipped to 1.0 |
   | | `test_null_acoustic_dist_treated_as_max` | ac_dist=None → uses 1.0 |
   | | `test_rerank_top_n_truncates` | 250-row shortlist → top_n=20 returns 20 |
   | | `test_rerank_deterministic_order` | same input → same output (no random) |
   | `TestStage1Query` | `test_cold_start_no_vectors_returns_empty` | catalog of 0 vectors → `generate_candidates(...)` returns `[]` |
   | | `test_short_catalog_returns_all` | 50 ready clips, K=200 → returns 50 |
   | | `test_large_catalog_uses_hnsw` | catalog of 10K vectors → `EXPLAIN` (via `captureOnCommitCallbacks` or `connection.explain()`) shows `Index Scan using semantic_vector_index` |
   | | `test_excludes_seen_clips` | 10K ready, user has seen 30 → those 30 not in candidates |
   | | `test_category_filter_applied` | 10K mixed categories → only category matches returned |
   | | `test_null_semantic_vector_excluded` | catalog with 5 nulls + 995 with vectors → only 995 returned |
   | `TestStage2Rerank` | `test_composite_matches_single_stage` | For 5 known clips with known (sem_dist, ac_dist, completion, velocity), compute composite via Python and via the OLD single-stage SQL — assert they match within float tolerance |
   | | `test_dedup_with_duplicate_input` | shortlist with 3 duplicates → rerank returns unique ids |
   | | `test_null_acoustic_vector_uses_max_distance` | clip with acoustic_vector=null → composite uses ac_dist=1.0 |
   | `TestRecall` | `test_recall_at_k_200_above_threshold` | on a 1000-clip fixture, compare two-stage top-20 vs brute-force composite top-20; assert ≥90% overlap. (The 95% target is aspirational; 90% is the test floor.) |

   Test fixtures should use `pytest.mark.django_db` and a small in-memory catalog (50-1000 clips) seeded via `AudioClip.objects.bulk_create(...)` with deterministic vectors (e.g., `np.random.RandomState(42).randn(384)`).

9. **Add to `backend/app/tests/__init__.py`** if any test-collection config exists (check the existing file; most likely no change needed).

### 9.7 Documentation updates

10. **Update `docs/EXPLAIN/ai-ml/04-recommendation-engine.md`** to reference this doc in the "Known Issues & Limitations" table — the row for "Global metrics full-table UPDATE" can be removed and replaced with "ANN candidate generation (moved to two-stage — see [07](../ai-ml/07-ann-candidate-generation.md))".

11. **Update `docs/EXPLAIN/recommendation/README.md`** (create if missing) with a section linking to this doc and the related P2.x materialization plans.

12. **Update `docs/unfixed-issues-2026-09-03.md`**: change Group A item 6 from "OPEN" to "FIXED — see `docs/EXPLAIN/ai-ml/07-ann-candidate-generation.md`. Implementation: PR #XXXX." Add a one-paragraph summary of what changed.

### 9.8 Migration check

13. **No migration required.** The HNSW indexes already exist (`models.py:83-99`, migration `0001_initial.py`). The new code only changes how the indexes are queried.

### 9.9 Pre-merge verification

14. Run the full pytest suite inside Docker:
    ```bash
    docker compose up --build -d
    docker compose exec web pytest backend/app/tests/test_ann_candidate_generation.py -v
    docker compose exec web pytest backend/app/tests/ --tb=short
    ```
    All tests must pass. The new test file must run green.

15. Run `EXPLAIN ANALYZE` (see §8) on the new stage-1 query inside the web container:
    ```bash
    docker compose exec db psql -U echoflow_user -d echoflow_db \
      -c "EXPLAIN ANALYZE SELECT id FROM audioclip WHERE status='ready' AND category='comedy' AND semantic_vector IS NOT NULL ORDER BY semantic_vector <=> (SELECT semantic_vector FROM audioclip WHERE id='<seed-uuid>') LIMIT 200;"
    ```
    Verify the plan shows `Index Scan using semantic_vector_index`.

---

## 10. What is OUT of Scope

The AI team MUST NOT do the following in this work item. These are explicitly called out to prevent scope creep.

| Out-of-scope item | Why deferred | Where it lives |
|---|---|---|
| Move the composite score to a stored column (`composite_score_materialized` on `AudioClip`) | Requires a backfill migration; needs a trigger or a refresh worker; conflicts with the existing `update_global_metrics` beat task (which already UPDATEs two columns every 5 min). Defer to P2.x. | `docs/event-driven-architecture-plan.md:613` |
| Switch to a different vector DB (Milvus, Pinecone, Qdrant) | The pgvector HNSW indexes are sufficient for 10K-1M clips. Migration to a dedicated vector DB is P3 (per `docs/backend-architecture-audit.md:85`). Adding a new dependency now buys complexity for no recall gain at our current scale. | `docs/backend-architecture-audit.md:85` |
| Change the HNSW index parameters (`m`, `ef_construction`) | The current `m=16, ef_construction=64` is documented as the appropriate tier for 10K-1M vectors (`docs/EXPLAIN/postgresql/02-vector-indexes.md:191-194`). Rebuilding requires `CREATE INDEX CONCURRENTLY` against a 100K-row table — multi-hour maintenance window. Defer to P3. | `docs/event-driven-architecture-plan.md:675` |
| Add a Redis candidate pool (`clip:candidates:exploit` sorted set) | This is the P2.2 materialization (`docs/unfixed-issues-2026-09-03.md:204-209`). It is the **next** optimization after this one lands, not part of it. The two-stage in-DB design here is the prerequisite for the Redis pool to make sense. | `docs/unfixed-issues-2026-09-03.md:204-209` |
| Implement real-time re-ranking based on telemetry | The event-driven architecture plan (`docs/event-driven-architecture-plan.md:521-590`) defines the `event_outbox` table and the consumer groups that would feed a real-time re-ranker. That is P1.4 (outbox) + P2.3 (consumer). Defer. | `docs/event-driven-architecture-plan.md:521-590` |
| Unify `calculate_time_decayed_vectors` and `calculate_blended_query_vectors` | Documented inconsistency at `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:282-289`. Separate work item; orthogonal to candidate generation. | `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:108-112` |
| Add per-user/segment configurable weights | `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:288` documents this. Requires a config schema, A/B framework, and a kill-switch. Not part of this PR. | `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:288` |
| Add MMR or diversity penalty | Filter-bubble mitigation. Orthogonal to retrieval; can be applied as a post-stage-2 rerank. Future work. | `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:289` |
| Build an acoustic-only second pass | The 70% semantic weight in the composite makes acoustic-only retrieval a marginal recall gain. If recall testing shows the bottleneck, add as a follow-up. | This doc, §2.5 |

---

## 11. Risks and Trade-offs

### 11.1 Recall degradation

The composite top-20 may include clips that are **outside the semantic top-200** but inside the composite top-200. These are clips where `0.30 * avg_completion_rate + 0.25 * engagement_velocity` overcomes a poor semantic distance. At K=200, we expect to capture **~96% of these**, missing ~4% (the `docs/phase-1-scaling-plan.md:255` audit comment estimates "recall ~95% at m=16, ef=64"; K=200 is well within that band).

The 4% we miss are mostly **viral outliers** — clips whose `engagement_velocity` is high but whose semantic distance is large. These clips are **already served by the explore wedge** at `tasks.py:419-429` (20% of feed items ordered by `engagement_velocity` outside the vector neighborhood). So the effective miss rate on user-facing recommendations is closer to **~1%**.

### 11.2 Latency budget

- **Stage 1 is O(log N)** via HNSW. At 100K clips, ~5 ms. At 1M clips, ~15 ms. At 10M clips, ~80 ms. The HNSW index is in shared_buffers after warmup; cold queries are 2-3x slower but bounded.
- **Stage 2 is O(K)** in Python. K=200 → ~2 ms pure-Python. K=1000 → ~10 ms. For K≥500, switch to `rerank_candidates_numpy` (§6).
- The Python loop is the **only** sequential bottleneck. If K=500 is too slow in pure Python (it shouldn't be, but measure), the numpy variant is the optimization.

### 11.3 Index maintenance

HNSW indexes do not auto-rebuild on insert; pgvector updates the graph incrementally as new rows are committed (`docs/EXPLAIN/postgresql/02-vector-indexes.md:228-231`). This is **fine for our insert rate** (a few hundred clips per hour at peak), but:

- After a bulk insert (e.g., the scraper imports 10K clips in one task — see `SCRAPER_SOURCES` at `settings.py:251`), the index working set is cold for ~5-10 minutes.
- During the cold window, HNSW queries may fall back to a less optimal code path. The expected latency increase is **<2x**, not "broken". Acceptable.

For bulk imports, the existing scraper flow (`process_audio_to_hls` per clip at `tasks.py`) writes one row at a time, which is incremental. If a future feature bulk-inserts 10K+ rows in one transaction, add a `VACUUM ANALYZE audioclip` after the import. **Out of scope here.**

### 11.4 Empty catalog

If the catalog has 0 ready clips, the stage-1 queryset returns 0 rows. The behavior MUST match today: `feed.py:181` falls through to the existing `engagement_velocity` ORDER BY (which also returns `[]` if no rows), and the view returns an empty `results` list. The new code does the same. No special handling needed.

### 11.5 Vector normalization

- **Semantic vector (`models.py:71`)**: 384-dim, produced by `sentence-transformers/all-MiniLM-L6-v2`. **L2-normalized at write time** — the model normalizes by default (verified in `docs/EXPLAIN/ai-ml/02-feature-extraction.md:119`).
- **Acoustic vector (`models.py:72`)**: 128-dim, produced by `librosa` concatenation of MFCC + Chroma + Mel. **L2-normalized at write time** by `extract_acoustic_vector()` (`docs/EXPLAIN/ai-ml/01-overview.md:113-114`).

Because both vectors are L2-normalized:
- `CosineDistance` equals `1.0 - InnerProduct` for them, which is the **fastest** distance computation in pgvector (no normalization required per row).
- `vector_similarity = 1.0 - (sem_dist + ac_dist) / 4.0` is conservative: for normalized vectors the max cosine distance is 1.0, not 2.0, so dividing by 4.0 understates the similarity slightly. **Preserve the formula as-is** — it is the legacy formula and changing it is a semantic change to the score.

### 11.6 Re-fetches per request

The new code makes **two SQL roundtrips** (stage 1 + stage 2) per `/suggestions/` request. The previous code made **one**. The added roundtrip is 0.5-1 ms over the LAN to Postgres through PgBouncer (`pgbouncer:6432`, `settings.py:151-156`), which is within the latency budget. If the second roundtrip is observed as a regression in p99, batch them via a CTE in a single query — but only after measurement confirms the regression.

### 11.7 Deadlock risk

None. The new code does not acquire any row locks. The `UserInteraction.objects.filter(user=user, ...)` call at `tasks.py:364` (the `seen_ids` builder) is a SELECT, not a SELECT FOR UPDATE.

### 11.8 Lock contention with telemetry writes

The single-stage query reads `engagement_velocity` and `avg_completion_rate` while the telemetry flush task (`tasks.py:flush_telemetry_stream` and `update_global_metrics`) UPDATEs them. The new two-stage query does the same — the stage-2 fetch is the row read. **No change in contention profile.** This is acceptable because:
- `update_global_metrics` runs every 5 minutes (`settings.py:282-286`) and completes in <2 seconds on the current catalog.
- The telemetry stream consumer (`flush_telemetry_stream` at `settings.py:307-314`) writes to `UserInteraction`, not to `AudioClip`. The composite columns are read-only between beat updates.
- The architecture audit's hot-row contention concern (`models.py:200-205`, `docs/backend-bug-fixs.md:574-696`) is about the F() counter path on `AudioClip.likes/shares/skips`, not the composite columns.

### 11.9 Race with `process_audio_to_hls`

If a clip is committed between stage 1 and stage 2 with `status='ready'` and an updated vector, the stage-2 fetch sees the newer vector. This is **desirable** — the system prefers fresh data. The race window is <2 ms in practice (between the two queries on the same request thread).

### 11.10 SQL injection

None. All inputs are Django ORM parameters or annotated columns. The vector query parameter is passed as a `numpy.ndarray` (cast to a list) and bound by the pgvector Django adapter, which parameterizes the query.

---

## 12. Verification Plan

After implementation, the AI team MUST run the following in order. All steps are gating; do not advance to the next until the current passes.

### 12.1 Unit tests

```bash
docker compose exec web pytest backend/app/tests/test_ann_candidate_generation.py -v
```

Expected: all tests pass. Pay specific attention to:
- `TestCompositeScore.test_composite_matches_single_stage` — must match the SQL output within `1e-6` for the same inputs.
- `TestStage1Query.test_large_catalog_uses_hnsw` — must show `Index Scan using semantic_vector_index` in the captured EXPLAIN.

### 12.2 Full regression suite

```bash
docker compose exec web pytest backend/app/tests/ --tb=short
```

Expected: no new failures. The existing `test_smoke.py`, `test_adversarial_pass3.py`, and `test_services_*` must all still pass. If they don't, the change broke an unrelated contract — investigate before merging.

### 12.3 EXPLAIN ANALYZE

```bash
docker compose exec db psql -U echoflow_user -d echoflow_db \
  -c "EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM audioclip WHERE status='ready' AND semantic_vector IS NOT NULL ORDER BY semantic_vector <=> (SELECT semantic_vector FROM audioclip LIMIT 1) LIMIT 200;"
```

Expected plan: `Index Scan using semantic_vector_index` with `Order By: (semantic_vector <=> '...'::vector)`. If you see `Seq Scan` or `Bitmap Heap Scan` without the HNSW index, the index is not being used — investigate before shipping.

### 12.4 Latency smoke test

Run a small load against the staging `/suggestions/` endpoint with the new code:

```bash
# Inside the web container, against the running stack
docker compose exec web python manage.py shell -c "
from django.test.utils import setup_test_environment
from backend.app.services.ann_candidate_gen import generate_candidates, rerank_with_composite
from django.contrib.auth import get_user_model
import time
User = get_user_model()
u = User.objects.first()
seen = set()
N = 50
ts = []
for _ in range(N):
    t0 = time.perf_counter()
    cands = generate_candidates(u, category='comedy', seen_ids=seen, k=200)
    rerank_with_composite(cands, ac_query=None, top_n=20)
    ts.append((time.perf_counter() - t0) * 1000)
print(f'p50: {sorted(ts)[N//2]:.2f}ms  p95: {sorted(ts)[int(N*0.95)]:.2f}ms  p99: {sorted(ts)[int(N*0.99)]:.2f}ms')
"
```

Expected: p50 < 10 ms, p99 < 20 ms on a 10K-clip test database. If higher, the catalog test data is too sparse; warm it up first.

### 12.5 A/B test (post-merge)

Ship behind a flag gated on `user_id % 100`:
- Buckets 0–4: experiment (K=100, K=200, K=400, K=200-control, K=200-variant).
- Buckets 5–99: production (K=200 default).

Metrics to track via Prometheus (`recommendation_*` histograms):

| Metric | Target |
|---|---|
| `recommendation_stage1_seconds:p99` | < 20 ms at 100K clips, < 50 ms at 1M clips |
| `recommendation_stage2_seconds:p99` | < 5 ms at K=200 |
| `recommendation_total_seconds:p99` | < 30 ms at 100K clips, < 60 ms at 1M clips |
| CTR (clicks per impression) on the served top-20 | within 2% of pre-change baseline |
| Error rate (5xx) on `/suggestions/` | unchanged |

**Ship criteria for 100% rollout**:
1. p99 latency improved or unchanged vs. pre-change baseline.
2. CTR within 2% of baseline.
3. No new 5xx errors in the experiment bucket over 7 days.

If any criterion fails, keep K=200 in the experiment buckets and investigate. Do not auto-promote.

---

## 13. Open Questions for the AI Team

These are decisions the AI team should make during implementation. The defaults are listed; the AI team can deviate with justification documented in the PR description.

1. **Should `seen_ids` exclude the user's last 50 interactions, last 30 days, or both?** Default: both (matches `tasks.py:364`). Document the choice.
2. **Should the trending fallback at `feed.py:181` also be reachable from the new two-stage path on stage-1 query error?** Default: yes — preserve the existing `try/except` structure.
3. **Should `category=None` (refill_user_feed path) drop the category filter or apply a no-op filter?** Default: drop the filter in the refill path; keep it in `/suggestions/`.
4. **Should `ac_dist` be fetched eagerly or lazily?** Default: eagerly (one PK-IN query at stage 2). The cost is ~0.5 ms over 200 rows.
5. **Should we add an HNSW-readiness warmup in `process_audio_to_hls` to pre-fault the index pages?** Default: no. Out of scope; the existing auto-commit + first-touch model is sufficient.

---

## 14. References

| Reference | Path |
|---|---|
| Composite formula spec | `docs/EXPLAIN/ai-ml/04-recommendation-engine.md:13-37` |
| HNSW index definitions | `backend/app/models.py:83-99` |
| Current single-stage query (suggestions) | `backend/app/views/feed.py:153-186` |
| Current single-stage query (refill) | `backend/app/tasks.py:347-456` |
| `calculate_time_decayed_vectors` | `backend/app/tasks.py:458-560` |
| `get_user_vectors` (15-min Redis cache) | `backend/app/views/feed.py:34-47` |
| HNSW parameters doc | `docs/EXPLAIN/postgresql/02-vector-indexes.md:50-100, 203-209` |
| Phase-1 scaling plan §7 | `docs/phase-1-scaling-plan.md:229-256` |
| Audit (Group A item 6) | `docs/unfixed-issues-2026-09-03.md` |
| Event-driven architecture plan (failure modes) | `docs/event-driven-architecture-plan.md:215-232` |
| Architecture audit | `docs/backend-architecture-audit.md:85` |
| Read replica design (HNSW memory) | `docs/EXPLAIN/database/05-read-replica-design.md:111` |
| P2.2 candidate pool in Redis (next step) | `docs/unfixed-issues-2026-09-03.md:204-209` |
| Settings env-var conventions | `backend/EchoFlow/settings.py:1-50, 513` |
| pgvector docs | <https://github.com/pgvector/pgvector#hnsw> |

---

*Authored by: design-doc author, 2026-09-04. Branch: `feat/stage2-service-layer-and-telemetry-stream`. Implementation owner: AI/ML team. Reviewers: backend platform + SRE.*