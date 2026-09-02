# Feed Resilience & Redis Outage Handling

## Current Feed Architecture

```
FastFeedViewSet.list()
       │
       ▼
Redis LPOP user_feed:{user_id} (10 clips)
       │
       ├── If empty → refill_user_feed.delay(user_id, 40)
       │
       ▼
Preserve order → AudioClip.objects.filter(id__in=clip_ids)
       │
       ▼
FeedClipSerializer → Response
```

---

## Redis Outage Scenario

### Current Behavior (Broken)

```python
# FastFeedViewSet.list() - views.py:121-159
redis_client = cache.client.get_client()
clip_ids_bytes = redis_client.lpop(redis_key, 10)

if not clip_ids_bytes:
    refill_user_feed.delay(user_id, count=40)  # ALSO NEEDS REDIS!
    clip_ids_bytes = redis_client.lpop(redis_key, 10)
    
    if not clip_ids_bytes:
        return Response({"results": [], "message": "You've caught up!"})
```

**Problems:**
1. **Redis down** → `redis_client.lpop()` raises `ConnectionError`
2. **Refill task** → Also needs Redis (lock, queue) → fails
3. **Cascading failure** → All feed requests hit DB directly
4. **DB overload** → Vector similarity queries on all users simultaneously

### Architecture Audit Finding
> "If Redis crashes, FastFeedViewSet triggers refill_user_feed synchronously for 10 items. If 5,000 users do this simultaneously during a Redis outage, they will launch 5,000 heavy vector DB queries, instantly crashing PostgreSQL."

---

## Required: Fallback Feed

### Design: Multi-Tier Fallback

```
GET /feed/
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ TRY: Redis LPOP (fast path)                                 │
└─────────────────────────────────────────────────────────────┘
       │
       ├── Success → Return clips
       │
       ▼ (Redis error / empty)
┌─────────────────────────────────────────────────────────────┐
│ TRY: Recompute on-demand (slow path)                        │
│   - calculate_time_decayed_vectors()                        │
│   - Composite query with LIMIT 10                           │
│   - Cache result in Redis (if available)                    │
└─────────────────────────────────────────────────────────────┘
       │
       ├── Success → Return clips
       │
       ▼ (Vector query fails / timeout)
┌─────────────────────────────────────────────────────────────┐
│ FALLBACK: Cached Global Trending (static path)              │
│   - Pre-computed: Top 100 clips by engagement_velocity      │
│   - Stored in: Redis key `global:trending:feed`             │
│   - TTL: 1 hour                                               │
│   - Updated by: Celery Beat (every 5 min)                   │
└─────────────────────────────────────────────────────────────┘
       │
       ├── Success → Return trending clips
       │
       ▼ (Complete failure)
┌─────────────────────────────────────────────────────────────┐
│ LAST RESORT: Static Seed Clips                              │
│   - Hardcoded clip IDs in settings                          │
│   - 10 diverse clips                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation

### 1. Circuit Breaker for Redis

```python
# utils/circuit_breaker.py
import time
import threading

class CircuitBreaker:
    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure = None
        self.state = 'closed'  # closed, open, half-open
        self._lock = threading.Lock()
    
    def call(self, func, *args, **kwargs):
        with self._lock:
            if self.state == 'open':
                if time.time() - self.last_failure > self.timeout:
                    self.state = 'half-open'
                else:
                    raise CircuitOpenError("Circuit breaker open")
        
        try:
            result = func(*args, **kwargs)
            with self._lock:
                self.on_success()
            return result
        except Exception as e:
            with self._lock:
                self.on_failure()
            raise
    
    def on_success(self):
        self.failures = 0
        self.state = 'closed'
    
    def on_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.failure_threshold:
            self.state = 'open'

# Global instance
redis_circuit_breaker = CircuitBreaker(failure_threshold=5, timeout=60)
```

### 2. Fallback Feed Service

```python
# services/feed_service.py
from django.core.cache import cache
from backend.app.tasks import calculate_time_decayed_vectors
from backend.app.models import AudioClip
from pgvector.django import CosineDistance
from django.db.models import F, ExpressionWrapper, FloatField

class FeedService:
    def __init__(self):
        self.redis_breaker = redis_circuit_breaker
    
    def get_feed(self, user, count=10):
        """Main entry point with full fallback chain."""
        # Tier 1: Redis feed queue (fast)
        try:
            return self._get_from_redis(user, count)
        except RedisError:
            pass
        
        # Tier 2: On-demand computation (slow)
        try:
            return self._compute_on_demand(user, count)
        except Exception:
            pass
        
        # Tier 3: Global trending (cached)
        try:
            return self._get_global_trending(count)
        except RedisError:
            pass
        
        # Tier 4: Static seed (last resort)
        return self._get_static_seed(count)
    
    def _get_from_redis(self, user, count):
        redis_client = cache.client.get_client()
        clip_ids_bytes = redis_client.lpop(f"user_feed:{user.id}", count)
        
        if not clip_ids_bytes:
            # Trigger async refill
            from backend.app.tasks import refill_user_feed
            refill_user_feed.delay(user.id, count=40)
            return []
        
        clip_ids = [vid.decode('utf-8') for vid in clip_ids_bytes]
        return self._fetch_clips_preserving_order(clip_ids)
    
    def _compute_on_demand(self, user, count):
        sem_query, ac_query = calculate_time_decayed_vectors(user)
        
        if not sem_query or not ac_query:
            # Cold start: global trending
            return self._get_global_trending(count)
        
        queryset = AudioClip.objects.filter(status='ready')
        
        # Exclude recently seen (simplified)
        seen = list(UserInteraction.objects.filter(user=user).values_list('clip_id', flat=True))
        if seen:
            queryset = queryset.exclude(id__in=seen)
        
        queryset = queryset.annotate(
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
        ).order_by('-composite_score')[:count]
        
        return list(queryset)
    
    def _get_global_trending(self, count):
        """Get cached global trending feed."""
        cache_key = "global:trending:feed"
        cached = cache.get(cache_key)
        
        if cached:
            return cached[:count]
        
        # Compute and cache
        clips = AudioClip.objects.filter(status='ready').order_by('-engagement_velocity')[:100]
        clip_ids = [str(c.id) for c in clips]
        cache.set(cache_key, clip_ids, timeout=3600)  # 1 hour
        return clip_ids[:count]
    
    def _get_static_seed(self, count):
        """Hardcoded fallback - diverse popular clips."""
        from django.conf import settings
        seed_ids = getattr(settings, 'FEED_STATIC_SEED_IDS', [])
        return seed_ids[:count]
    
    def _fetch_clips_preserving_order(self, clip_ids):
        from django.db.models import Case, When
        preserved_order = Case(*[When(pk=pk, then=pos) for pos, pk in enumerate(clip_ids)])
        return AudioClip.objects.filter(id__in=clip_ids).order_by(preserved_order)
```

### 3. Updated FastFeedViewSet

```python
# views.py
class FastFeedViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.feed_service = FeedService()
    
    def list(self, request):
        user = request.user
        try:
            clips = self.feed_service.get_feed(user, count=10)
        except Exception as e:
            logger.exception("Feed generation failed completely")
            return Response({"results": [], "message": "Service temporarily unavailable"}, status=503)
        
        if not clips:
            return Response({"results": [], "message": "You've caught up!"})
        
        serializer = FeedClipSerializer(clips, many=True, context={'request': request})
        return Response({
            "next": "auto_trigger",
            "queue_health": 0,  # Unknown in fallback
            "results": serializer.data
        })
```

---

## Global Trending Cache (Celery Beat)

```python
# tasks.py - New periodic task
@shared_task
def update_global_trending_cache():
    """Run every 5 minutes to update global trending cache."""
    from django.core.cache import cache
    from backend.app.models import AudioClip
    
    clips = AudioClip.objects.filter(status='ready').order_by('-engagement_velocity')[:100]
    clip_ids = [str(c.id) for c in clips]
    cache.set("global:trending:feed", clip_ids, timeout=3600)  # 1 hour

# Add to CELERY_BEAT_SCHEDULE
CELERY_BEAT_SCHEDULE = {
    ...
    'update-global-trending': {
        'task': 'backend.app.tasks.update_global_trending_cache',
        'schedule': 300.0,  # Every 5 minutes
    },
}
```

---

## Static Seed Configuration

```python
# settings.py
FEED_STATIC_SEED_IDS = [
    "uuid-clip-1",  # Comedy
    "uuid-clip-2",  # Music
    "uuid-clip-3",  # Education
    "uuid-clip-4",  # News
    "uuid-clip-5",  # Motivation
    # ... 5 more diverse clips
]
```

**Selection Criteria:**
- Different categories
- High engagement_velocity
- Ready status
- Diverse creators

---

## Monitoring Fallback Usage

### Metrics to Track
```python
# In FeedService methods
FEED_SOURCE = Counter('echoflow_feed_source_total', 'Feed data source', ['source'])
# Sources: 'redis', 'computed', 'global_trending', 'static_seed', 'error'

def _get_from_redis(self, ...):
    FEED_SOURCE.labels(source='redis').inc()
    ...

def _compute_on_demand(self, ...):
    FEED_SOURCE.labels(source='computed').inc()
    ...

def _get_global_trending(self, ...):
    FEED_SOURCE.labels(source='global_trending').inc()
    ...

def _get_static_seed(self, ...):
    FEED_SOURCE.labels(source='static_seed').inc()
    ...
```

### Alerting
```yaml
# Alert if > 10% requests use fallback
- alert: HighFallbackUsage
  expr: rate(echoflow_feed_source_total{source=~"global_trending|static_seed"}[5m]) 
        / rate(echoflow_feed_source_total[5m]) > 0.1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High fallback feed usage - Redis likely down"
```

---

## Testing Fallback Chain

### Unit Tests
```python
# tests/test_feed_resilience.py
class FeedResilienceTests(TestCase):
    def test_redis_fallback_to_computed(self):
        with patch('django.core.cache.cache.client.get_client') as mock_redis:
            mock_redis.side_effect = RedisError()
            clips = FeedService().get_feed(user, count=10)
            self.assertTrue(len(clips) > 0)
    
    def test_full_fallback_to_static(self):
        with patch('django.core.cache.cache.client.get_client') as mock_redis:
            mock_redis.side_effect = RedisError()
            with patch('backend.app.services.feed_service.calculate_time_decayed_vectors') as mock_calc:
                mock_calc.side_effect = Exception("DB down")
                clips = FeedService().get_feed(user, count=10)
                self.assertEqual(clips, settings.FEED_STATIC_SEED_IDS[:10])
```

### Chaos Engineering
```bash
# Simulate Redis outage
docker compose pause redis
# Test feed endpoint
curl -H "Authorization: Bearer $TOKEN" http://localhost:8005/feed/
docker compose unpause redis
```

---

## Capacity Planning for Fallback

### On-Demand Computation Load
| Users | Concurrent Requests | DB Load | Mitigation |
|-------|---------------------|---------|------------|
| 1K | 100 | Moderate | OK |
| 10K | 1K | High | Circuit breaker + cache |
| 100K | 10K | **Crash** | Must prevent |

### Prevention
1. **Rate limit** `/feed/` endpoint (stricter than global)
2. **Circuit breaker** opens after 5 Redis failures
3. **Global trending cache** updated every 5 min by Beat
4. **Static seed** as absolute last resort

---

*Source: `backend/app/views.py:118-159`, `backend/app/tasks.py:512-592`, `docs/backend-architecture-audit.md`*