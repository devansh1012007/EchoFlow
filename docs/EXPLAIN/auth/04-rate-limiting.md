# Rate Limiting

## Current Configuration (`settings.py:324-331`)

```python
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
    },
}
```

---

## Throttle Classes

### AnonRateThrottle
- **Scope:** `anon`
- **Limit:** 100 requests/hour
- **Identification:** Client IP address
- **Applies to:** Unauthenticated requests (login, register)

### UserRateThrottle
- **Scope:** `user`
- **Limit:** 1000 requests/hour
- **Identification:** Authenticated user ID
- **Applies to:** All authenticated endpoints

---

## Implementation Details

### Throttle Backend
- **Default:** Django cache (`django.core.cache.cache`)
- **Dev:** Local memory cache (per-process)
- **Prod:** Redis cache (shared)

### Cache Key Format
```
throttle_{scope}_{ident}
# e.g., throttle_user_123, throttle_anon_192.168.1.1
```

### Response Headers
```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1705315200
Retry-After: 3600  (on 429)
```

### 429 Response
```json
{
  "detail": "Request was throttled. Expected available in 3600 seconds."
}
```

---

## Current Limits Analysis

| Endpoint | Auth | Limit | Risk |
|----------|------|-------|------|
| `/auth/login/` | Anon | 100/hr | Low (brute force) |
| `/auth/register/` | Anon | 100/hr | Low |
| `/feed/` | User | 1000/hr | **High** (feed spam) |
| `/interactions/*/log-telemetry/` | User | 1000/hr | **Critical** (telemetry spam) |
| `/clips/` | User | 1000/hr | Medium (upload spam) |

---

## Critical Gaps (Architecture Audit)

### 1. No Per-Endpoint Overrides
```python
# Current: Global only
# Needed: Per-endpoint
class TelemetryThrottle(UserRateThrottle):
    scope = 'telemetry'
    rate = '60/minute'  # Stricter for telemetry

class FeedThrottle(UserRateThrottle):
    scope = 'feed'
    rate = '200/hour'  # Stricter for feed
```

### 2. No Redis-Backed Distributed Throttling
- **Dev:** Local memory → each worker has separate count
- **Prod:** Redis needed for accurate distributed limits

### 3. Telemetry Spam Risk
```
Attacker script:
  for i in range(1000):
      POST /interactions/clip_id/log-telemetry/
          {action_type: "view", watch_time_ms: 60000}
```
- 1000 requests → within 1000/hr limit
- Artificially inflates `completion_rate` → boosts clip ranking
- **No server-side validation** of `watch_time_ms` vs actual clip duration

### 4. No Burst Protection
- Sustained 1000/hr = ~17 req/min average
- Burst of 1000 in 1 minute → allowed
- Should have **burst + sustained** tiers

---

## Recommended Improvements

### 1. Per-Endpoint Throttles
```python
# views.py
class ClipInteractionViewSet(...):
    @action(..., throttle_classes=[TelemetryThrottle])
    def log_telemetry(self, ...):
        ...

class FastFeedViewSet(...):
    throttle_classes = [FeedThrottle]
```

### 2. Redis Token Bucket (Distributed)
```python
class DistributedTokenBucketThrottle:
    def __init__(self, redis_client):
        self.redis = redis_client
    
    def allow_request(self, key, rate_per_minute, burst_multiplier=2):
        bucket_key = f"throttle:{key}"
        
        # Token bucket algorithm
        now = time.time()
        bucket = self.redis.hgetall(bucket_key)
        
        if not bucket:
            # Initialize with burst capacity
            self.redis.hset(bucket_key, mapping={
                'tokens': str(rate_per_minute * burst_multiplier),
                'last_refill': str(now)
            })
            self.redis.expire(bucket_key, 3600)
            return True
        
        tokens = float(bucket['tokens'])
        last_refill = float(bucket['last_refill'])
        
        # Refill tokens
        elapsed = now - last_refill
        refill_rate = rate_per_minute / 60.0
        tokens = min(rate_per_minute * burst_multiplier, tokens + elapsed * refill_rate)
        
        if tokens >= 1:
            tokens -= 1
            self.redis.hset(bucket_key, mapping={
                'tokens': str(tokens),
                'last_refill': str(now)
            })
            return True
        
        return False
```

### 3. Server-Side Telemetry Validation
```python
def log_telemetry(self, request, pk=None):
    clip = self.get_object()
    watch_time_ms = serializer.validated_data['watch_time_ms']
    
    # VALIDATE: Can't watch more than clip duration
    max_watch = clip.duration_ms
    if watch_time_ms > max_watch * 1.1:  # 10% tolerance
        watch_time_ms = max_watch  # Cap it
    
    # VALIDATE: Minimum time between telemetry for same clip
    last_telemetry = UserInteraction.objects.filter(
        user=request.user, clip=clip, interaction_type='view'
    ).order_by('-updated_at').first()
    
    if last_telemetry:
        min_interval = 1000  # 1 second minimum
        if (timezone.now() - last_telemetry.updated_at).total_seconds() * 1000 < min_interval:
            return Response({'detail': 'Telemetry too frequent'}, status=429)
```

### 4. IP + User Composite Throttling
```python
class CompositeThrottle:
    def get_ident(self, request):
        # Combine IP + User for stricter limits
        user_id = request.user.id if request.user.is_authenticated else 'anon'
        ip = self.get_client_ip(request)
        return f"{user_id}:{ip}"
```

---

## Monitoring Throttle Metrics

### Prometheus Metrics (Not Implemented)
```python
THROTTLE_HITS = Counter('throttle_hits_total', 'Throttle hits', ['scope', 'result'])
THROTTLE_CURRENT = Gauge('throttle_current_usage', 'Current throttle usage', ['scope', 'user'])
```

### Logs
```python
# Log throttled requests
logger.warning("Rate limit exceeded", extra={
    'scope': scope,
    'user_id': user_id,
    'ip': ip,
    'endpoint': request.path
})
```

---

## Testing Throttles

```bash
# Test anon limit
for i in {1..105}; do curl -X POST http://localhost:8005/auth/login/ -d '{"username":"x","password":"y"}'; done

# Test user limit (with token)
for i in {1..1005}; do curl -H "Authorization: Bearer $TOKEN" http://localhost:8005/feed/; done
```

---

*Source: `backend/EchoFlow/settings.py:324-331`, `backend/app/views.py:308-377`*