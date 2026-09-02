# Periodic Tasks (Celery Beat)

## Overview

**Celery Beat** runs as a separate service (`celery_beat`) using `django_celery_beat` DatabaseScheduler.
Periodic tasks defined in `settings.py:CELERY_BEAT_SCHEDULE` and manageable via Django admin.

---

## Scheduled Tasks

### 1. `update_global_metrics` — Every 5 Minutes

**Task:** `backend.app.tasks.update_global_metrics`

**Purpose:** Recalculate engagement metrics for ALL ready clips.

**Schedule:**
```python
'update-global-metrics': {
    'task': 'backend.app.tasks.update_global_metrics',
    'schedule': 300.0,  # 5 minutes
},
```

**Implementation** (`tasks.py:666-690`):
```python
@shared_task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600)
def update_global_metrics(self):
    clip_table = AudioClip._meta.db_table
    interaction_table = UserInteraction._meta.db_table

    # Engagement velocity
    query = f"""
    UPDATE {clip_table} 
    SET engagement_velocity = 
        LEAST((likes + (shares * 2)) / POWER(EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 + 2.0, 1.5)/100.0, 1.0)
    WHERE status = 'ready';
    """

    # Avg completion rate
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

**Formulas:**

| Metric | Formula | Notes |
|--------|---------|-------|
| `engagement_velocity` | `(likes + 2*shares) / (hours_since_created + 2)^1.5 / 100` | Capped at 1.0, favors new viral clips |
| `avg_completion_rate` | `AVG(completion_rate) FROM UserInteraction WHERE type='view'` | Only 'view' interactions |

**⚠️ Critical Issues:**
1. **Full table scan** — `WHERE status = 'ready'` hits all clips
2. **No batching** — Single UPDATE locks entire table
3. **No pagination** — Scales poorly beyond 100K clips
4. **Raw SQL** — Bypasses ORM, no signals, hard to test

**At scale:** This will cause **table locks for minutes** blocking all writes.

---

### 2. `evolve_long_term_user_baselines` — Every Hour

**Task:** `backend.app.tasks.evolve_long_term_user_baselines`

**Purpose:** Update user preference vectors from recent interactions.

**Schedule:**
```python
'evolve-user-baselines': {
    'task': 'backend.app.tasks.evolve_long_term_user_baselines',
    'schedule': 3600.0,  # 1 hour
},
```

**Implementation** (`tasks.py:693-706`):
```python
@shared_task(bind=True, max_retries=3, default_retry_delay=60, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600)
def evolve_long_term_user_baselines(self):
    users_to_update = []
    for user in User.objects.filter(is_active=True).iterator(chunk_size=100):
        new_sem, new_ac = calculate_time_decayed_vectors(user, limit=500)
        if new_sem is not None:            
            user.long_term_semantic = new_sem 
            user.long_term_acoustic = new_ac
        users_to_update.append(user)
    User.objects.bulk_update(users_to_update, ['long_term_semantic', 'long_term_acoustic'], batch_size=100)
```

**Parameters:**
- `limit=500` — Broader history than feed refill (50)
- `chunk_size=100` — Memory-efficient iteration
- `bulk_update(batch_size=100)` — Efficient DB writes

**Flow per user:**
```
calculate_time_decayed_vectors(user, limit=500)
       │
       ├── Recent 500 interactions (all time, no window)
       ├── Time decay: 1/(1+log(hours))
       ├── Completion weight: completion_rate
       ├── Intent weight: like/share=1.5, skip<20%=-0.5
       ├── Weighted average of clip vectors
       ├── Normalize
       ├── Blend: 70% context + 30% current long-term
       └── Return (semantic, acoustic)
       │
       ▼
bulk_update users
```

**Note:** Uses `calculate_time_decayed_vectors` (not `calculate_blended_query_vectors`) — different ALPHA (0.7 vs 0.75).

---

## DatabaseScheduler (`django_celery_beat`)

### Why DatabaseScheduler?

```python
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
```

| Feature | Benefit |
|---------|---------|
| Persistent | Survives restarts, no schedule drift |
| Admin UI | Manage periods via Django admin |
| Dynamic | Add/remove tasks without code deploy |
| Timezone-aware | Uses Django `TIME_ZONE` |

### Models Created
- `PeriodicTask` — Task definition + schedule
- `IntervalSchedule` — Every N seconds/minutes/hours
- `CrontabSchedule` — Cron-like schedules
- `ClockedSchedule` — One-time at specific datetime
- `SolarSchedule` — Sunrise/sunset based

### Management
```bash
# View scheduled tasks
docker compose exec web python manage.py show_periodic_tasks

# Or via Django admin: /admin/django_celery_beat/periodictask/
```

---

## Beat Service Configuration

**Docker Compose:**
```yaml
celery_beat:
  build: { target: api }
  command: >
    sh -c "set -e && python wait_for_db.py &&
           celery -A backend.EchoFlow beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler"
  healthcheck:
    disable: true  # Image's HTTP probe would fail (no gunicorn)
```

**No healthcheck** — `celery beat` doesn't serve HTTP; image's baked probe would fail.

---

## Task Reliability

### Retry Configuration
```python
@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=RETRYABLE_ERRORS,
    retry_backoff=True,
    retry_backoff_max=600,
)
```

| Setting | Value | Effect |
|---------|-------|--------|
| `max_retries` | 3 | Max 3 retry attempts |
| `default_retry_delay` | 60s | Initial delay |
| `retry_backoff` | True | Exponential: 60s, 120s, 240s... |
| `retry_backoff_max` | 600s | Cap at 10 minutes |

### RETRYABLE_ERRORS
```python
RETRYABLE_ERRORS = (
    OperationalError,           # DB connection
    ConnectionError,            # Network
    subprocess.CalledProcessError,  # FFmpeg
    OSError,                    # File system
)
```

---

## Monitoring Beat

### Logs
```bash
docker compose logs -f celery_beat
```

### Expected Output
```
[2024-01-15 10:00:00] INFO: Scheduler: Sending due task update-global-metrics (backend.app.tasks.update_global_metrics)
[2024-01-15 10:00:05] INFO: Task backend.app.tasks.update_global_metrics[uuid] succeeded in 2.3s
[2024-01-15 11:00:00] INFO: Scheduler: Sending due task evolve-user-baselines (backend.app.tasks.evolve_long_term_user_baselines)
```

### Django Admin
```
/admin/django_celery_beat/periodictask/
```
- Enable/disable tasks
- Change schedule
- View last run, next run
- Manual trigger

---

## Known Issues & Fixes Needed

### 1. `update_global_metrics` Full Table Lock
**Fix:** Batch updates with pagination
```python
def update_global_metrics_batched(batch_size=10000):
    last_id = 0
    while True:
        clips = AudioClip.objects.filter(status='ready', id__gt=last_id).order_by('id')[:batch_size]
        if not clips:
            break
        clip_ids = [c.id for c in clips]
        last_id = clip_ids[-1]
        
        # Update only these clips
        with connection.cursor() as cursor:
            cursor.execute(f"""
                UPDATE audioclip SET engagement_velocity = ...
                WHERE id IN ({','.join(['%s']*len(clip_ids))})
            """, clip_ids)
```

### 2. No Task Priorities
**Fix:** Use priority queues (requires RabbitMQ)

### 3. No Dead Letter Queue
**Fix:** Configure Celery DLQ for failed tasks

### 4. Schedule Drift
**Fix:** `CELERY_BEAT_SYNC_EVERY = 10` (sync schedule every 10 tasks)

---

## Adding New Periodic Tasks

### 1. Define Task
```python
# tasks.py
@shared_task(bind=True, max_retries=3, ...)
def my_periodic_task(self):
    ...
```

### 2. Add to Schedule
```python
# settings.py
CELERY_BEAT_SCHEDULE = {
    ...
    'my-new-task': {
        'task': 'backend.app.tasks.my_periodic_task',
        'schedule': 3600.0,  # or crontab(hour=3, minute=0)
    },
}
```

### 3. Or via Admin
```
/admin/django_celery_beat/periodictask/add/
```

---

*Source: `backend/EchoFlow/settings.py:229-239`, `backend/app/tasks.py:666-706`, `docker-compose.yml:339-386`*