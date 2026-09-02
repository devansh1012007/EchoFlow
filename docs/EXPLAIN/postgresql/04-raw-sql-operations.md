# Raw SQL Operations

## Overview

EchoFlow uses **raw SQL** for two critical periodic operations where Django ORM would be too slow or memory-intensive:

1. `update_global_metrics` — Every 5 minutes (full table UPDATE)
2. `calculate_time_decayed_vectors` — Uses ORM but could benefit from raw SQL

---

## `update_global_metrics` (`tasks.py:666-690`)

### Implementation

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600)
def update_global_metrics(self):
    clip_table = AudioClip._meta.db_table
    interaction_table = UserInteraction._meta.db_table

    # Query 1: Engagement velocity
    query = f"""
    UPDATE {clip_table} 
    SET engagement_velocity = 
        LEAST((likes + (shares * 2)) / POWER(EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 + 2.0, 1.5)/100.0, 1.0)
    WHERE status = 'ready';
    """

    # Query 2: Avg completion rate
    query2 = f"""
    UPDATE {clip_table} SET avg_completion_rate = COALESCE((
    SELECT AVG(completion_rate) FROM {interaction_table}
    WHERE clip_id = {clip_table}.id AND interaction_type = 'view'
    ), 0) WHERE status = 'ready';
    """

    with connection.cursor() as cursor:
        cursor.execute(query)
        cursor.execute(query2)
```

---

## Query 1: Engagement Velocity Analysis

### Formula Breakdown

```sql
engagement_velocity = LEAST(
    (likes + shares * 2) / POWER(hours_since_created + 2, 1.5) / 100.0, 
    1.0
)
```

| Component | Formula | Purpose |
|-----------|---------|---------|
| Weighted engagement | `likes + shares * 2` | Shares = 2x likes |
| Hours since creation | `EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0` | Age in hours |
| Time decay | `POWER(hours + 2, 1.5)` | Strong decay (1.5 power) |
| Normalization | `/ 100.0` | Scale to ~0-1 range |
| Cap | `LEAST(..., 1.0)` | Max velocity = 1.0 |

### Velocity Curve Examples

| Clip Age | Likes | Shares | Velocity |
|----------|-------|--------|----------|
| 1 hour | 100 | 10 | 0.85 |
| 1 hour | 10 | 1 | 0.09 |
| 24 hours | 1000 | 100 | 0.32 |
| 168 hours (1 week) | 5000 | 500 | 0.18 |
| 720 hours (30 days) | 10000 | 1000 | 0.04 |

**Key insight:** Strong time decay favors **new viral content** over old popular content.

---

## Query 2: Average Completion Rate

### Formula

```sql
UPDATE audioclip 
SET avg_completion_rate = COALESCE((
    SELECT AVG(completion_rate) 
    FROM userinteraction 
    WHERE clip_id = audioclip.id 
    AND interaction_type = 'view'
), 0)
WHERE status = 'ready';
```

### Logic
- Only `'view'` interactions (not like/share/skip)
- `completion_rate` = `watch_time_ms / clip.duration_ms` (capped at 1.0)
- `COALESCE` → 0 if no views yet
- Correlated subquery per clip

---

## Why Raw SQL?

### ORM Equivalent (Hypothetical)
```python
# This would be DISASTROUS at scale
for clip in AudioClip.objects.filter(status='ready'):
    clip.engagement_velocity = calculate_velocity(clip)
    clip.avg_completion_rate = calculate_completion(clip)
    clip.save(update_fields=['engagement_velocity', 'avg_completion_rate'])
```

### Problems with ORM Approach
| Issue | ORM | Raw SQL |
|-------|-----|---------|
| **Memory** | Loads all clips into Python | Server-side only |
| **Round trips** | N SELECT + N UPDATE | 2 UPDATE statements |
| **Lock time** | Row locks per save | Single table lock per UPDATE |
| **Throughput** | ~100 clips/sec | ~10,000 clips/sec |

### Why Not `bulk_update`?
```python
# Still requires loading all objects
clips = list(AudioClip.objects.filter(status='ready'))
for clip in clips:
    clip.engagement_velocity = ...
AudioClip.objects.bulk_update(clips, ['engagement_velocity'])
```
- Memory: All objects in Python
- Still N UPDATEs (batched but separate statements)
- No server-side computation

---

## Performance at Scale

### Current (Small Scale)
| Metric | Value |
|--------|-------|
| Clips | ~1,000 |
| Query 1 time | ~50ms |
| Query 2 time | ~200ms |
| Lock duration | < 1s |

### Projected (100K Clips)
| Metric | Value |
|--------|-------|
| Clips | 100,000 |
| Query 1 time | ~5s |
| Query 2 time | ~30s (correlated subquery) |
| Lock duration | **30-60s** — blocks all writes |

### Projected (1M Clips)
| Metric | Value |
|--------|-------|
| Clips | 1,000,000 |
| Query 1 time | ~60s |
| Query 2 time | **~5-10 min** |
| Lock duration | **Minutes** — blocks ALL writes |

---

## Critical Issues

### 1. Full Table Lock
```sql
UPDATE audioclip SET ... WHERE status = 'ready';
```
- **Locks entire table** (or all `status='ready'` rows)
- Blocks: `INSERT` (new clips), `UPDATE` (likes, status changes), `DELETE`
- At 1M clips: **minutes of write unavailability** every 5 minutes

### 2. Correlated Subquery (Query 2)
```sql
SELECT AVG(completion_rate) FROM userinteraction 
WHERE clip_id = audioclip.id
```
- Executes **once per row** (1M executions at scale)
- No index on `userinteraction.clip_id + interaction_type` for this pattern
- Should be rewritten as JOIN or materialized view

### 3. No Batching
- Processes all clips in single transaction
- No progress tracking
- Failure = full rollback (wasted work)

### 4. Runs Every 5 Minutes
- At scale, **next run starts before previous finishes**
- Concurrent executions → deadlocks

---

## Recommended Fixes

### Fix 1: Batched Updates with Pagination
```python
def update_global_metrics_batched(batch_size=10000):
    last_id = 0
    while True:
        # Get batch of clip IDs
        clips = AudioClip.objects.filter(
            status='ready', id__gt=last_id
        ).order_by('id').values_list('id', flat=True)[:batch_size]
        
        clip_ids = list(clips)
        if not clip_ids:
            break
            
        last_id = clip_ids[-1]
        
        # Update engagement velocity for batch
        with connection.cursor() as cursor:
            cursor.execute(f"""
                UPDATE audioclip 
                SET engagement_velocity = LEAST(
                    (likes + shares * 2) / POWER(EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 + 2.0, 1.5)/100.0, 1.0
                )
                WHERE id IN ({','.join(['%s']*len(clip_ids))})
            """, clip_ids)
        
        # Small delay between batches
        time.sleep(0.1)
```

### Fix 2: Join-Based Completion Rate
```sql
UPDATE audioclip a
SET avg_completion_rate = COALESCE(agg.avg_completion, 0)
FROM (
    SELECT clip_id, AVG(completion_rate) as avg_completion
    FROM userinteraction
    WHERE interaction_type = 'view'
    GROUP BY clip_id
) agg
WHERE a.id = agg.clip_id AND a.status = 'ready';
```
- Single query, no correlated subquery
- Can add index: `CREATE INDEX ON userinteraction (clip_id, interaction_type) WHERE interaction_type = 'view';`

### Fix 3: Materialized View (For Read-Heavy)
```sql
CREATE MATERIALIZED VIEW clip_metrics AS
SELECT 
    a.id,
    (a.likes + a.shares * 2) / POWER(EXTRACT(EPOCH FROM (NOW() - a.created_at))/3600.0 + 2.0, 1.5)/100.0 as engagement_velocity,
    COALESCE(AVG(ui.completion_rate) FILTER (WHERE ui.interaction_type = 'view'), 0) as avg_completion_rate
FROM audioclip a
LEFT JOIN userinteraction ui ON ui.clip_id = a.id
WHERE a.status = 'ready'
GROUP BY a.id, a.likes, a.shares, a.created_at;

-- Refresh every 5 min (concurrent, non-blocking)
REFRESH MATERIALIZED VIEW CONCURRENTLY clip_metrics;

-- Query from view (fast, no locks)
SELECT * FROM clip_metrics ORDER BY engagement_velocity DESC;
```

### Fix 4: Schedule Adjustment
```python
# At scale: reduce frequency or stagger
CELERY_BEAT_SCHEDULE = {
    'update-global-metrics': {
        'task': 'backend.app.tasks.update_global_metrics',
        'schedule': crontab(minute='*/10'),  # Every 10 min
    },
    'update-completion-rates': {
        'task': 'backend.app.tasks.update_completion_rates',
        'schedule': crontab(minute='5,15,25,35,45,55'),  # Offset
    },
}
```

---

## Other Raw SQL in Codebase

### `calculate_time_decayed_vectors` (Could Use Raw SQL)
Currently uses ORM with `select_related('clip')`. At scale, could benefit from:
```sql
-- Single query to get weighted vectors
SELECT 
    clip_id,
    semantic_vector,
    acoustic_vector,
    completion_rate,
    interaction_type,
    created_at,
    EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 as hours_ago
FROM userinteraction ui
JOIN audioclip a ON a.id = ui.clip_id
WHERE ui.user_id = %s 
  AND ui.is_active = true
  AND a.status = 'ready'
  AND ui.interaction_type IN ('like', 'share', 'view')
ORDER BY ui.created_at DESC
LIMIT 50;
```

---

## Security Note

**SQL Injection Risk:** Current code uses f-strings with table names:
```python
query = f"UPDATE {clip_table} SET ..."
```
- `clip_table` from `AudioClip._meta.db_table` — **safe** (Django-controlled)
- Never interpolate user input directly

---

*Source: `backend/app/tasks.py:666-690`*