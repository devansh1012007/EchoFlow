# Migration History

## Migration Files

| File | Description | Applied |
|------|-------------|---------|
| `0001_initial.py` | Full schema: User, AudioClip, Comment, ShareEvent, UserInteraction, pgvector extension, indexes, HNSW | Initial |
| `0002_audioclip_likes_non_negative_and_more.py` | CheckConstraints for non-negative counters | After 0001 |

---

## 0001_initial.py — Full Schema

### Key Operations

1. **pgvector Extension**
```python
migrations.RunSQL(
    sql="CREATE EXTENSION IF NOT EXISTS vector;",
    reverse_sql="DROP EXTENSION IF EXISTS vector;",
    elidable=False,
)
```
**Must be in initial migration** — swappable dependency anchors other apps.

2. **Custom User Model** (`app_user`)
- Extends `AbstractUser`
- Adds: `encrypted_email`, `long_term_semantic`, `long_term_acoustic`, `profile_picture`, `following` M2M

3. **AudioClip** (`app_audioclip`)
- UUID PK, FK to User
- File fields, provenance, metrics, vectors, status

4. **Comment** (`app_comment`)
- UUID PK, FK to AudioClip + User
- Self-referential FK for replies (`parent`)

5. **ShareEvent** (`app_shareevent`)
- BigAutoField PK, FKs to User (sender/receiver) + AudioClip

6. **UserInteraction** (`app_userinteraction`)
- BigAutoField PK, FKs to User + AudioClip
- Unique constraint: `(user_id, clip_id, interaction_type)`

7. **Indexes**
- Standard B-tree indexes on common query patterns
- HNSW indexes on vector fields (semantic + acoustic)

8. **Unique Constraints**
- `UserInteraction`: `(user, clip, interaction_type)`

---

## 0002_audioclip_likes_non_negative_and_more.py — CheckConstraints

### Operations
```python
operations = [
    migrations.AddConstraint(
        model_name='audioclip',
        constraint=models.CheckConstraint(
            condition=models.Q(('likes__gte', 0)), 
            name='likes_non_negative'
        ),
    ),
    migrations.AddConstraint(
        model_name='audioclip',
        constraint=models.CheckConstraint(
            condition=models.Q(('shares__gte', 0)), 
            name='shares_non_negative'
        ),
    ),
    migrations.AddConstraint(
        model_name='audioclip',
        constraint=models.CheckConstraint(
            condition=models.Q(('skips__gte', 0)), 
            name='skips_non_negative'
        ),
    ),
    migrations.AddConstraint(
        model_name='audioclip',
        constraint=models.CheckConstraint(
            condition=models.Q(('comment_count__gte', 0)), 
            name='comment_count_non_negative'
        ),
    ),
    migrations.AddConstraint(
        model_name='comment',
        constraint=models.CheckConstraint(
            condition=models.Q(('likes__gte', 0)), 
            name='comment_likes_non_negative'
        ),
    ),
]
```

### Purpose
- **Database-level** enforcement (not just ORM)
- Prevents negative counters from:
  - Raw SQL updates
  - ORM bulk updates (`update()`)
  - Race conditions
  - Bugs in application logic

### Generated SQL
```sql
ALTER TABLE app_audioclip ADD CONSTRAINT likes_non_negative CHECK (likes >= 0);
ALTER TABLE app_audioclip ADD CONSTRAINT shares_non_negative CHECK (shares >= 0);
ALTER TABLE app_audioclip ADD CONSTRAINT skips_non_negative CHECK (skips >= 0);
ALTER TABLE app_audioclip ADD CONSTRAINT comment_count_non_negative CHECK (comment_count >= 0);
ALTER TABLE app_comment ADD CONSTRAINT comment_likes_non_negative CHECK (likes >= 0);
```

---

## Migration Best Practices (EchoFlow Context)

### 1. pgvector Extension in Initial Migration
```python
# 0001_initial.py:28-32
migrations.RunSQL(
    sql="CREATE EXTENSION IF NOT EXISTS vector;",
    reverse_sql="DROP EXTENSION IF EXISTS vector;",
    elidable=False,
)
```
**Why:** `swappable_dependency(settings.AUTH_USER_MODEL)` anchors other apps to this app's `__first__` node. Splitting extension into 0000 breaks cross-app ordering.

### 2. Vector Fields in Initial Migration
```python
# 0001_initial.py:49-50, 87-88
long_term_semantic = VectorField(dimensions=384, ...)
semantic_vector = VectorField(dimensions=384, ...)
```
**Dimensions fixed at migration time** — changing requires new migration + data migration.

### 3. HNSW Indexes in Initial Migration
```python
# 0001_initial.py:148-153
migrations.AddIndex(
    model_name='audioclip',
    index=pgvector.django.indexes.HnswIndex(
        ef_construction=64, fields=['semantic_vector'], m=16, 
        name='semantic_vector_index', opclasses=['vector_cosine_ops']
    ),
),
```
**Parameters baked into migration** — changing `m` or `ef_construction` requires `REINDEX`.

### 4. CheckConstraints in Separate Migration
```python
# 0002_...:13-32
migrations.AddConstraint(...)
```
**Added after initial** — allows initial deploy without constraints, then enforce.

---

## Applying Migrations

### Development
```bash
python manage.py migrate
```

### Docker
```bash
docker compose exec web python manage.py migrate
```

### Production (Zero-Downtime)
```bash
# 1. Deploy new code (backward compatible)
# 2. Run migration
docker compose exec web python manage.py migrate --plan  # Preview
docker compose exec web python manage.py migrate

# 3. For risky migrations:
#    - Use expand/contract pattern
#    - Add column → backfill → switch → remove old
#    - Never lock tables > 5s
```

---

## Rollback Strategy

### Safe Rollbacks
```bash
# Rollback last migration
python manage.py migrate app 0001

# Rollback all
python manage.py migrate app zero
```

### Unsafe Operations (No Rollback)
| Operation | Reverse |
|-----------|---------|
| `RunSQL` with `DROP EXTENSION` | Data loss |
| `AddConstraint` with data violation | Constraint drop only |
| `HnswIndex` changes | Requires `REINDEX` |

---

## Future Migration Considerations

### Planned Schema Changes
| Change | Migration Strategy |
|--------|-------------------|
| Add `AudioClip.views` counter | Add field → backfill from interactions → index |
| Split `UserInteraction` by date | Partition table → pg_partman |
| Add `AudioClip.transcript` | Add TextField → populate from AI pipeline |
| Vector dimension change | New column → backfill → switch → drop old |

### Partitioning `UserInteraction` (Scale)
```sql
-- When > 10M rows
CREATE TABLE app_userinteraction_partitioned (
    LIKE app_userinteraction INCLUDING ALL
) PARTITION BY RANGE (created_at);

-- Monthly partitions
CREATE TABLE app_userinteraction_2024_01 PARTITION OF app_userinteraction_partitioned
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

---

## Testing Migrations

### Test Suite (Not Currently Implemented)
```python
# tests/test_migrations.py
from django.test import TestCase
from django.db import connection

class MigrationTestCase(TestCase):
    def test_0001_creates_pgvector_extension(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM pg_extension WHERE extname = 'vector'")
            self.assertTrue(cursor.fetchone())
    
    def test_0002_constraints_prevent_negative_likes(self):
        clip = AudioClip.objects.create(...)
        with self.assertRaises(IntegrityError):
            clip.likes = -1
            clip.save()
```

---

*Source: `backend/app/migrations/0001_initial.py`, `backend/app/migrations/0002_*.py`*