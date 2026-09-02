# PostgreSQL Schema

## Overview

PostgreSQL 16 with **pgvector** extension for vector similarity search.

**Connection:** `dj_database_url` from `DATABASE_URL` env var
**Connection pooling:** `conn_max_age=600` (Django persistent connections)
**No PgBouncer** — direct connections (architecture audit gap)

---

## Tables

### 1. `app_user` (Custom User Model)

```sql
CREATE TABLE app_user (
    id BIGSERIAL PRIMARY KEY,
    password VARCHAR(128) NOT NULL,
    last_login TIMESTAMPTZ,
    is_superuser BOOLEAN DEFAULT FALSE,
    username VARCHAR(150) UNIQUE NOT NULL,
    first_name VARCHAR(150) DEFAULT '',
    last_name VARCHAR(150) DEFAULT '',
    email VARCHAR(254) DEFAULT '',
    is_staff BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    date_joined TIMESTAMPTZ DEFAULT NOW(),
    
    -- Custom fields
    encrypted_email TEXT UNIQUE,           -- Fernet-encrypted email
    long_term_semantic VECTOR(384),        -- pgvector: semantic preference
    long_term_acoustic VECTOR(128),        -- pgvector: acoustic preference
    profile_picture VARCHAR(100),          -- ImageField path
    
    -- ManyToMany: following (self-referential)
);
```

**Indexes:** Primary key on `id`, unique on `username`, `encrypted_email`

---

### 2. `app_user_following` (M2M: User ↔ User)

```sql
CREATE TABLE app_user_following (
    id BIGSERIAL PRIMARY KEY,
    from_user_id BIGINT REFERENCES app_user(id) ON DELETE CASCADE,
    to_user_id BIGINT REFERENCES app_user(id) ON DELETE CASCADE,
    UNIQUE (from_user_id, to_user_id)
);
```

**Note:** `symmetrical=False` → separate `following` (from_user) and `followers` (to_user)

---

### 3. `app_audioclip`

```sql
CREATE TABLE app_audioclip (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id BIGINT REFERENCES app_user(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    category VARCHAR(50) DEFAULT '',
    
    -- File storage (S3 keys)
    original_file VARCHAR(100),            -- uploads/... (private)
    hls_playlist_url VARCHAR(500),         -- hls/.../master.m3u8 (public)
    
    -- Provenance (scraper imports)
    source_name VARCHAR(100),
    source_url VARCHAR(500),
    license VARCHAR(100),
    attribution_text VARCHAR(500),
    imported_via_scraper BOOLEAN DEFAULT FALSE,
    original_source_id VARCHAR(255),
    
    -- Metrics
    duration_ms INTEGER DEFAULT 0,
    avg_completion_rate DOUBLE PRECISION DEFAULT 0.0,
    engagement_velocity DOUBLE PRECISION DEFAULT 0.0,
    
    -- Denormalized counters
    likes BIGINT DEFAULT 0,
    shares BIGINT DEFAULT 0,
    skips BIGINT DEFAULT 0,
    comment_count BIGINT DEFAULT 0,
    
    -- AI Intelligence
    tags JSONB DEFAULT '[]',
    semantic_vector VECTOR(384),           -- pgvector: from transcript
    acoustic_vector VECTOR(128),           -- pgvector: from librosa
    
    status VARCHAR(20) DEFAULT 'processing',  -- processing/ready/failed
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Indexes:**
```sql
-- Composite indexes for common queries
CREATE INDEX app_audioclip_status_created_idx ON app_audioclip (status, created_at DESC);
CREATE INDEX app_audioclip_status_velocity_idx ON app_audioclip (status, engagement_velocity DESC);
CREATE INDEX app_audioclip_category_likes_idx ON app_audioclip (category, likes DESC);

-- HNSW vector indexes (pgvector)
CREATE INDEX semantic_vector_index ON app_audioclip USING hnsw (semantic_vector vector_cosine_ops) WITH (m=16, ef_construction=64);
CREATE INDEX acoustic_vector_index ON app_audioclip USING hnsw (acoustic_vector vector_cosine_ops) WITH (m=16, ef_construction=64);
```

**Constraints (Migration 0002):**
```sql
ALTER TABLE app_audioclip ADD CONSTRAINT likes_non_negative CHECK (likes >= 0);
ALTER TABLE app_audioclip ADD CONSTRAINT shares_non_negative CHECK (shares >= 0);
ALTER TABLE app_audioclip ADD CONSTRAINT skips_non_negative CHECK (skips >= 0);
ALTER TABLE app_audioclip ADD CONSTRAINT comment_count_non_negative CHECK (comment_count >= 0);
```

---

### 4. `app_comment`

```sql
CREATE TABLE app_comment (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id UUID REFERENCES app_audioclip(id) ON DELETE CASCADE,
    author_id BIGINT REFERENCES app_user(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES app_comment(id) ON DELETE CASCADE,  -- Self-ref for replies
    text VARCHAR(500) NOT NULL,
    likes BIGINT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Indexes:**
```sql
CREATE INDEX app_comment_clip_created_idx ON app_comment (clip_id, created_at DESC);
```

**Constraints:**
```sql
ALTER TABLE app_comment ADD CONSTRAINT comment_likes_non_negative CHECK (likes >= 0);
```

**Ordering:** `ORDER BY created_at DESC` (Meta.ordering)

---

### 5. `app_shareevent`

```sql
CREATE TABLE app_shareevent (
    id BIGSERIAL PRIMARY KEY,
    sender_id BIGINT REFERENCES app_user(id) ON DELETE CASCADE,
    receiver_id BIGINT REFERENCES app_user(id) ON DELETE CASCADE,
    clip_id UUID REFERENCES app_audioclip(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_read BOOLEAN DEFAULT FALSE
);
```

**Indexes:**
```sql
CREATE INDEX app_shareevent_receiver_created_read_idx ON app_shareevent (receiver_id, created_at DESC, is_read);
```

---

### 6. `app_userinteraction`

```sql
CREATE TABLE app_userinteraction (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES app_user(id) ON DELETE CASCADE,
    clip_id UUID REFERENCES app_audioclip(id) ON DELETE CASCADE,
    interaction_type VARCHAR(10) NOT NULL,  -- like/share/skip/view
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMPTZ DEFAULT NOW(),   -- auto_now
    watch_time_ms INTEGER DEFAULT 0,
    completion_rate DOUBLE PRECISION DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW()    -- auto_now_add
);
```

**Constraints:**
```sql
ALTER TABLE app_userinteraction ADD CONSTRAINT unique_user_clip_type 
    UNIQUE (user_id, clip_id, interaction_type);
```

**Indexes:**
```sql
CREATE INDEX app_userinteraction_user_type_idx ON app_userinteraction (user_id, interaction_type);
```

---

## pgvector Extension

### Enablement (Migration 0001)
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```
**Must be in initial migration** due to swappable dependency anchoring.

### Vector Fields
```python
# Django model
semantic_vector = VectorField(dimensions=384, null=True, blank=True)
acoustic_vector = VectorField(dimensions=128, null=True, blank=True)
```

**Storage:** Binary format (PostgreSQL `vector` type)
**Dimensions enforced** at application level (not DB constraint)

---

## HNSW Indexes

### Configuration
```python
HnswIndex(
    name='semantic_vector_index',
    fields=['semantic_vector'],
    m=16,
    ef_construction=64,
    opclasses=['vector_cosine_ops']
)
```

### Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `m` | 16 | Max connections per layer (memory vs recall) |
| `ef_construction` | 64 | Build-time search width (quality vs speed) |
| `opclasses` | `vector_cosine_ops` | Cosine distance operator |

### Index Characteristics

| Metric | Semantic (384-dim) | Acoustic (128-dim) |
|--------|-------------------|-------------------|
| Index type | HNSW | HNSW |
| Memory/1M vectors | ~600MB | ~200MB |
| Build time | ~5 min | ~2 min |
| Query latency (p99) | 50-200ms | 20-100ms |
| Recall@10 | ~95% | ~90% |

### Querying
```python
from pgvector.django import CosineDistance

AudioClip.objects.annotate(
    sem_dist=CosineDistance('semantic_vector', query_vector),
    ac_dist=CosineDistance('acoustic_vector', query_vector)
).order_by('sem_dist')
```

**Returns:** Distance ∈ [0, 2] for normalized vectors
- 0 = identical direction
- 1 = orthogonal
- 2 = opposite

---

## Denormalized Counters

### Strategy
Counters on `AudioClip` updated via `F()` expressions from `UserInteraction.save()`:

```python
# UserInteraction.save()
AudioClip.objects.filter(pk=clip.pk).update(
    **{field_to_update: F(field_to_update) + increment_val}
)
```

### Counters
| Field | Source Interaction | Update Trigger |
|-------|-------------------|----------------|
| `likes` | `interaction_type='like'` | `is_active` toggle |
| `shares` | `interaction_type='share'` | `is_active` toggle |
| `skips` | `interaction_type='skip'` | `is_active` toggle |
| `comment_count` | `Comment.save()/delete()` | Top-level only |

### Consistency
- **Atomic** — `F()` expression = single UPDATE
- **Not transactional** — Interaction save + counter update separate
- **Drift possible** — Direct DB manipulation, bugs in Interaction logic
- **Constraints** — `CHECK (field >= 0)` prevents negative

---

## Migration History

| Migration | Description |
|-----------|-------------|
| `0001_initial.py` | All tables, pgvector extension, indexes, HNSW indexes |
| `0002_audioclip_likes_non_negative_and_more.py` | CheckConstraints for non-negative counters |

---

## Performance Considerations

### Current Scale Assumptions
| Table | Estimated Rows | Index Size |
|-------|----------------|------------|
| `app_audioclip` | 10K-1M | HNSW: 1-10GB |
| `app_userinteraction` | 100K-100M | ~5GB |
| `app_user` | 1K-100K | Minimal |

### Query Patterns
| Query | Index Used |
|-------|------------|
| Feed: `status='ready' ORDER BY -engagement_velocity` | `status_velocity_idx` |
| Feed: `status='ready' ORDER BY -created_at` | `status_created_idx` |
| Explore: `category=X ORDER BY vector_sim` | HNSW + filter |
| User clips: `creator=X, status='ready'` | FK index + status |
| Interactions: `user=X, type=like` | `user_type_idx` |

### Missing Indexes
- Partial index: `WHERE status='ready'` on vector columns (pgvector doesn't support partial HNSW)
- Composite: `(user_id, clip_id, interaction_type)` covered by unique constraint

---

## Scaling Limits (pgvector)

| Scale | Challenge | Solution |
|-------|-----------|----------|
| 1M vectors | HNSW memory ~1.5GB | OK on 16GB+ instance |
| 10M vectors | HNSW memory ~15GB | Need read replica for queries |
| 50M vectors | HNSW memory > RAM | Dedicated vector DB (Qdrant/Milvus) |
| 100M+ vectors | Index rebuild locks table | Partitioned indexes, online rebuild |

---

*Source: `backend/app/models.py`, `backend/app/migrations/0001_initial.py`, `backend/app/migrations/0002_*.py`*