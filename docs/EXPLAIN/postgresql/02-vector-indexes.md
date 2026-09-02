# Vector Indexes (HNSW)

## Overview

**pgvector** provides **HNSW (Hierarchical Navigable Small World)** indexes for Approximate Nearest Neighbor (ANN) search.

**Two indexes on `AudioClip`:**
1. `semantic_vector_index` — 384-dim semantic vectors
2. `acoustic_vector_index` — 128-dim acoustic vectors

---

## Index Definition

```python
# backend/app/models.py:91-106
indexes = [
    # ... other indexes ...
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
]
```

**Migration** (`0001_initial.py:148-152`):
```python
migrations.AddIndex(
    model_name='audioclip',
    index=pgvector.django.indexes.HnswIndex(
        ef_construction=64, 
        fields=['semantic_vector'], 
        m=16, 
        name='semantic_vector_index', 
        opclasses=['vector_cosine_ops']
    ),
),
```

---

## HNSW Parameters

### `m` (Max Connections per Node)
- **Value:** 16
- **Range:** 4-64 typical
- **Effect:** Higher = better recall, more memory, slower build
- **Memory:** ~`m * dimensions * 8 bytes` per vector

### `ef_construction` (Build-time Search Width)
- **Value:** 64
- **Range:** 50-200 typical
- **Effect:** Higher = better index quality, slower build
- **Trade-off:** Build time vs query accuracy

### `opclasses` (Distance Operator)
- **Value:** `vector_cosine_ops`
- **Options:** `vector_l2_ops` (Euclidean), `vector_ip_ops` (Inner Product)
- **Cosine** = `1 - (A·B)/(|A||B|)` for normalized vectors

---

## Index Characteristics

### Memory Usage Estimate

| Vectors | Dimensions | m=16 | m=32 | m=64 |
|---------|------------|------|------|------|
| 100K | 384 | 60 MB | 120 MB | 240 MB |
| 1M | 384 | 600 MB | 1.2 GB | 2.4 GB |
| 10M | 384 | 6 GB | 12 GB | 24 GB |
| 100K | 128 | 20 MB | 40 MB | 80 MB |
| 1M | 128 | 200 MB | 400 MB | 800 MB |

**Formula:** `memory ≈ vectors * (m * 8 + dimensions * 4) bytes`

### Build Time
| Vectors | 384-dim | 128-dim |
|---------|---------|---------|
| 100K | ~30s | ~10s |
| 1M | ~5 min | ~2 min |
| 10M | ~50 min | ~20 min |

### Query Performance
| Scale | p50 Latency | p99 Latency | Recall@10 |
|-------|-------------|-------------|-----------|
| 100K | 2ms | 10ms | 98% |
| 1M | 10ms | 50ms | 95% |
| 10M | 50ms | 200ms | 90% |

---

## Querying Vectors

### Django ORM (CosineDistance)

```python
from pgvector.django import CosineDistance

# Single vector query
AudioClip.objects.annotate(
    distance=CosineDistance('semantic_vector', query_vector)
).order_by('distance')[:10]

# Combined semantic + acoustic
AudioClip.objects.annotate(
    sem_dist=CosineDistance('semantic_vector', sem_query),
    ac_dist=CosineDistance('acoustic_vector', ac_query),
    combined_distance=F('sem_dist') + F('ac_dist')
).order_by('combined_distance')
```

### Raw SQL (for complex queries)

```sql
-- Nearest neighbors with cosine distance
SELECT id, title, 
       1 - (semantic_vector <=> $1) as similarity
FROM app_audioclip
WHERE status = 'ready'
ORDER BY semantic_vector <=> $1
LIMIT 10;

-- Hybrid: vector + metadata filter
SELECT id, title,
       1 - (semantic_vector <=> $1) as sem_sim,
       1 - (acoustic_vector <=> $2) as ac_sim
FROM app_audioclip
WHERE status = 'ready' AND category = $3
ORDER BY (semantic_vector <=> $1) + (acoustic_vector <=> $2)
LIMIT 20;
```

**Operators:**
- `<=>` — Cosine distance (requires `vector_cosine_ops`)
- `<->` — L2 distance (requires `vector_l2_ops`)
- `<#>` — Inner product (requires `vector_ip_ops`)

---

## Index Maintenance

### Rebuild (Required After Bulk Inserts)
```sql
-- Full rebuild (locks table)
REINDEX INDEX semantic_vector_index;

-- Concurrent rebuild (PostgreSQL 12+)
REINDEX INDEX CONCURRENTLY semantic_vector_index;
```

### Statistics Update
```sql
ANALYZE app_audioclip;
```

### Monitoring Index Health
```sql
-- Index size
SELECT pg_size_pretty(pg_relation_size('semantic_vector_index'));

-- Index usage
SELECT * FROM pg_stat_user_indexes 
WHERE indexrelname = 'semantic_vector_index';

-- Bloat estimation
SELECT pg_size_pretty(pg_relation_size('semantic_vector_index')) as size,
       pgstatclear() -- reset stats
```

---

## Tuning for Scale

### Current (MVP)
```python
m=16, ef_construction=64
```
**Good for:** < 1M vectors, CPU-only, balanced recall/speed

### Production (1M-10M)
```python
m=32, ef_construction=128
```
**Trade-off:** 2x memory, 2x build time, better recall

### High-Recall (10M+)
```python
m=64, ef_construction=256
```
**Trade-off:** 4x memory, 4x build time, near-exact recall

### Query-time `ef_search`
```python
# Not set in Django — uses pgvector default (40)
# Can override per-query:
SET hnsw.ef_search = 100;
SELECT ... ORDER BY embedding <=> $1 LIMIT 10;
```

---

## Limitations & Workarounds

### 1. No Partial HNSW Indexes
**Problem:** Can't create `WHERE status='ready'` HNSW index
**Workaround:** Filter in query (sequential scan on filtered rows)
```python
# Current: filters in query, uses HNSW for ordering
AudioClip.objects.filter(status='ready').annotate(
    dist=CosineDistance('semantic_vector', query)
).order_by('dist')
```

### 2. No Multi-tenancy Isolation
**Problem:** All users share same index
**Workout:** Namespace vectors by user (not applicable for content-based)

### 3. Index Rebuild Locks Table
**Problem:** `REINDEX` blocks writes
**Solution:** `REINDEX CONCURRENTLY` (PG12+), or schedule maintenance window

### 4. Dimension Fixed at Create
**Problem:** Can't change dimensions without rebuild
**Solution:** Plan dimensions upfront (384/128 fixed)

---

## Alternative: IVFFlat (For Comparison)

```python
# pgvector also supports IVFFlat
IvfFlatIndex(
    name='semantic_ivf_index',
    fields=['semantic_vector'],
    lists=100,  # Number of clusters
    opclasses=['vector_cosine_ops']
)
```

| Aspect | HNSW | IVFFlat |
|--------|------|---------|
| Build time | Slower | Faster |
| Query speed | Faster | Slower |
| Recall | Higher | Lower |
| Memory | Higher | Lower |
| Updates | Online (slow) | Requires rebuild |
| Best for | Real-time, high recall | Batch, lower recall |

**EchoFlow uses HNSW** — real-time feed requires low latency.

---

## Future: Dedicated Vector Database

### When to Migrate
| Threshold | Indicator |
|-----------|-----------|
| 10M+ vectors | pgvector HNSW > 50% RAM |
| 100M+ vectors | Query latency > 200ms p99 |
| Multi-region | Need geo-distributed search |

### Options
| Database | Pros | Cons |
|----------|------|------|
| **Qdrant** | Rust, fast, filtering, cloud | Newer ecosystem |
| **Milvus** | Mature, distributed, GPU | Complex ops |
| **Pinecone** | Managed, serverless | Cost, vendor lock-in |
| **Weaviate** | GraphQL, modules | Java-based, heavier |

### Migration Pattern
```python
# Dual-write during transition
def save_clip_vectors(clip):
    # 1. Save to PostgreSQL (current)
    clip.semantic_vector = vector
    clip.save()
    
    # 2. Async write to vector DB
    vector_db.upsert(clip.id, vector, metadata={...})
    
# Switch read path
def search_similar(query_vector):
    if USE_VECTOR_DB:
        return vector_db.search(query_vector, limit=100)
    else:
        return pgvector_search(query_vector)
```

---

*Source: `backend/app/models.py`, `backend/app/migrations/0001_initial.py`, `pgvector` documentation*