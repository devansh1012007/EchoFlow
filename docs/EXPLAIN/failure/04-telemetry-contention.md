# Telemetry Contention & PostgreSQL Locking

## The Problem

**`log_telemetry` endpoint** creates massive PostgreSQL contention under load.

### Current Implementation (`views.py:351-377`)

```python
@action(detail=True, methods=['post'], url_path='log-telemetry')
def log_telemetry(self, request, pk=None):
    clip = self.get_object()
    user = request.user
    
    watch_time_ms = serializer.validated_data['watch_time_ms']
    action_type = serializer.validated_data['action_type']
    
    # Server-side completion rate calculation
    clip_duration = max(clip.duration_ms, 1)
    completion_rate = min(watch_time_ms / clip_duration, 1.0)

    # UPSERT - Creates row lock contention
    interaction, created = UserInteraction.objects.update_or_create(
        user=user,
        clip=clip,
        interaction_type=action_type,
        defaults={
            'watch_time_ms': watch_time_ms,
            'completion_rate': completion_rate,
            'is_active': True 
        }
    )
    return Response({"status": "telemetry logged"})
```

---

## Contention Analysis

### Lock Contention Chain

```
Client POST /interactions/{clip_id}/log-telemetry/
       │
       ▼
UserInteraction.objects.update_or_create(
    user=user, clip=clip, interaction_type=action_type,
    defaults={...}
)
       │
       ├── SELECT ... FOR UPDATE (row lock on UserInteraction)
       │
       ├── If created: INSERT + commit
       │
       └── If exists: UPDATE + commit
              │
              ▼
       AudioClip.objects.filter(pk=clip.pk).update(
           **{field: F(field) + increment}
       )
              │
              ├── UPDATE AudioClip SET likes = likes + 1
              │   (Row lock on AudioClip)
              │
              └── Commit (releases both locks)
```

### Contention Points

| Resource | Lock Type | Duration | Contention Under Load |
|----------|-----------|----------|----------------------|
| `UserInteraction` row | `FOR UPDATE` (SELECT) | ~5-50ms | High — unique per user/clip/type |
| `AudioClip` row | `UPDATE` | ~1-10ms | **Extreme** — shared by ALL interactions |

### Projected Load

| Scale | Telemetry RPS | Lock Contention |
|-------|---------------|-----------------|
| 1K users | 10 | Low |
| 10K users | 100 | Moderate |
| 100K users | 1,000 | **Severe** |
| 1M users | 10,000 | **DB collapse** |

---

## Architecture Audit Finding

> "The single most important insight: You cannot incrementally scale this monolith... `log_telemetry` performs an `update_or_create` on `UserInteraction` for every swipe. This creates row-level locks and massive WAL bloat... If 1M users scroll every 8 seconds, the API receives 125,000 RPS just for telemetry. PostgreSQL cannot handle 125,000 UPDATE operations per second on a single primary node."

---

## Solutions

### 1. Redis Buffer + Async Flush (Recommended)

```python
# views.py - Modified log_telemetry
def log_telemetry(self, request, pk=None):
    clip = self.get_object()
    user = request.user
    
    watch_time_ms = serializer.validated_data['watch_time_ms']
    action_type = serializer.validated_data['action_type']
    completion_rate = min(watch_time_ms / max(clip.duration_ms, 1), 1.0)
    
    # Buffer in Redis (no DB lock)
    telemetry_data = {
        'user_id': user.id,
        'clip_id': str(clip.id),
        'action_type': action_type,
        'watch_time_ms': watch_time_ms,
        'completion_rate': completion_rate,
        'timestamp': time.time(),
    }
    
    # LPUSH to user's telemetry buffer
    redis_client = cache.client.get_client()
    buffer_key = f"telemetry_buffer:{user.id}"
    redis_client.lpush(buffer_key, json.dumps(telemetry_data))
    redis_client.ltrim(buffer_key, 0, 999)  # Keep last 1000
    redis_client.expire(buffer_key, 3600)  # 1 hour TTL
    
    return Response({"status": "telemetry buffered"})
```

### 2. Background Flush Task

```python
# tasks.py
@shared_task(bind=True, max_retries=3)
def flush_telemetry_buffers(self):
    """Run every 30 seconds via Celery Beat."""
    redis_client = cache.client.get_client()
    
    # Scan all user buffers
    for key in redis_client.scan_iter("telemetry_buffer:*"):
        user_id = int(key.split(":")[1])
        buffer = redis_client.lrange(key, 0, -1)
        
        if not buffer:
            continue
        
        # Process in batch
        interactions_to_create = []
        interactions_to_update = []
        clip_updates = defaultdict(lambda: defaultdict(int))
        
        for item_json in buffer:
            data = json.loads(item_json)
            # Aggregate per (user, clip, type)
            key = (data['user_id'], data['clip_id'], data['action_type'])
            # ... aggregation logic ...
        
        # Bulk DB operations
        with transaction.atomic():
            UserInteraction.objects.bulk_create(interactions_to_create, ignore_conflicts=True)
            UserInteraction.objects.bulk_update(interactions_to_update, ['watch_time_ms', 'completion_rate', 'is_active', 'updated_at'])
            
            # Bulk update AudioClip counters
            for clip_id, updates in clip_updates.items():
                AudioClip.objects.filter(pk=clip_id).update(**{
                    f"{field}": F(field) + delta for field, delta in updates.items()
                })
        
        # Clear buffer
        redis_client.delete(key)
```

### 3. Celery Beat Schedule

```python
# settings.py
CELERY_BEAT_SCHEDULE = {
    ...
    'flush-telemetry-buffers': {
        'task': 'backend.app.tasks.flush_telemetry_buffers',
        'schedule': 30.0,  # Every 30 seconds
    },
}
```

---

## Alternative: Kafka Event Stream

### Architecture
```
Client → API (buffer in Redis) → Flush Task → Kafka → Consumer → PostgreSQL
```

### Producer (Flush Task)
```python
from kafka import KafkaProducer

producer = KafkaProducer(
    bootstrap_servers='kafka:9092',
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)

def flush_to_kafka(batch):
    for record in batch:
        producer.send('user.interaction', value=record)
    producer.flush()
```

### Consumer (Separate Service)
```python
from kafka import KafkaConsumer

consumer = KafkaConsumer(
    'user.interaction',
    bootstrap_servers='kafka:9092',
    group_id='interaction-processor',
    value_deserializer=lambda m: json.loads(m.decode('utf-8'))
)

for message in consumer:
    batch = [message.value]
    # Collect batch
    if len(batch) >= 1000:
        process_batch(batch)
        batch = []
```

---

## Alternative: ClickHouse for Analytics

### Why ClickHouse?
- Columnar storage — perfect for analytics
- Handles billions of rows
- Fast aggregations
- Decouples analytics from transactional DB

### Schema
```sql
CREATE TABLE user_interactions (
    user_id UInt64,
    clip_id UUID,
    action_type LowCardinality(String),
    watch_time_ms UInt32,
    completion_rate Float32,
    timestamp DateTime64(3),
    created_at DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (user_id, clip_id, timestamp)
TTL timestamp + INTERVAL 90 DAY DELETE;
```

### Ingestion
```python
# Buffer → ClickHouse (async)
client = clickhouse_driver.Client(host='clickhouse')

def flush_to_clickhouse(batch):
    data = [(r['user_id'], r['clip_id'], r['action_type'], 
             r['watch_time_ms'], r['completion_rate'], r['timestamp']) for r in batch]
    client.execute(
        'INSERT INTO user_interactions (user_id, clip_id, action_type, watch_time_ms, completion_rate, timestamp) VALUES',
        data
    )
```

---

## Comparison

| Approach | Pros | Cons | Effort |
|----------|------|------|--------|
| Redis buffer + async flush | Simple, keeps PostgreSQL | Still uses Redis, eventual consistency | Low |
| Kafka + Consumer | Scalable, durable, replayable | New infra, complexity | High |
| ClickHouse | Best for analytics, fast queries | New infra, separate query path | Medium |
| Direct PostgreSQL (current) | Simple, consistent | **Doesn't scale** | None |

---

## Migration Path

### Phase 1: Redis Buffer (Week 1)
1. Modify `log_telemetry` to buffer in Redis
2. Create `flush_telemetry_buffers` task
3. Add to Celery Beat (30s)
4. Monitor buffer sizes, flush latency

### Phase 2: Kafka (Month 1)
1. Deploy Kafka cluster
2. Modify flush task to produce to Kafka
3. Build consumer service
4. Dual-write during transition

### Phase 3: ClickHouse (Month 2)
1. Deploy ClickHouse
2. Create schema
3. Point consumer to ClickHouse
4. Build analytics dashboards

---

## Immediate Mitigations (Before Full Fix)

### 1. Stricter Rate Limiting
```python
# views.py
from rest_framework.throttling import UserRateThrottle

class TelemetryThrottle(UserRateThrottle):
    scope = 'telemetry'
    rate = '60/minute'  # Stricter than global 1000/hr

class ClipInteractionViewSet(...):
    @action(..., throttle_classes=[TelemetryThrottle])
    def log_telemetry(self, ...):
        ...
```

### 2. Client-Side Batching
```typescript
// Frontend: batch telemetry, send every 10 swipes
const telemetryBuffer = [];
function logTelemetry(data) {
    telemetryBuffer.push(data);
    if (telemetryBuffer.length >= 10) {
        sendBatch(telemetryBuffer.splice(0));
    }
}
```

### 3. Server-Side Validation
```python
# Prevent telemetry spam
def log_telemetry(self, request, pk=None):
    clip = self.get_object()
    
    # Minimum time between telemetry for same clip
    last = UserInteraction.objects.filter(
        user=request.user, clip=clip, interaction_type='view'
    ).order_by('-updated_at').first()
    
    if last and (timezone.now() - last.updated_at).total_seconds() < 1:
        return Response({'detail': 'Telemetry too frequent'}, status=429)
    
    # Cap watch_time_ms at clip duration
    watch_time_ms = min(serializer.validated_data['watch_time_ms'], clip.duration_ms)
    ...
```

---

## Monitoring

### Key Metrics
| Metric | Alert Threshold |
|--------|-----------------|
| Telemetry buffer size (per user) | > 500 |
| Flush task duration | > 10s |
| DB lock wait time | > 100ms |
| Telemetry 429 rate | > 5% |

---

*Source: `backend/app/views.py:308-377`, `backend/app/tasks.py`, `docs/backend-architecture-audit.md`*