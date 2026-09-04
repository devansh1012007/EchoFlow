# Recommendation Engine

## Overview

EchoFlow uses a **hybrid recommendation system** combining:
1. **Vector similarity** (semantic + acoustic) — content-based
2. **Engagement metrics** (completion rate, velocity) — collaborative signals
3. **Social graph** (follow wedge) — social signals
4. **Exploration** (20% random high-velocity) — serendipity

---

## Composite Scoring Formula

**Computed in PostgreSQL** via `refill_user_feed` task (`tasks.py:539-551`):

```sql
-- Vector similarity (cosine distance → similarity)
sem_dist = CosineDistance(semantic_vector, user_sem_query)  -- [0, 2]
ac_dist = CosineDistance(acoustic_vector, user_ac_query)    -- [0, 2]
vector_similarity = 1.0 - (sem_dist + ac_dist) / 4.0       -- [0, 1]

-- Composite score
composite_score = 0.45 * vector_similarity
                + 0.30 * avg_completion_rate
                + 0.25 * engagement_velocity
```

### Weight Breakdown

| Component | Weight | Source | Range |
|-----------|--------|--------|-------|
| Vector similarity | 45% | User preference vectors vs clip vectors | [0, 1] |
| Avg completion rate | 30% | `AudioClip.avg_completion_rate` (updated by beat) | [0, 1] |
| Engagement velocity | 25% | `AudioClip.engagement_velocity` (updated by beat) | [0, 1] |

**Total:** 100% — normalized to [0, 1]

---

## User Preference Vectors

### Short-term Context (`calculate_time_decayed_vectors`)

```python
def calculate_time_decayed_vectors(user, limit=50):
    recent_interactions = UserInteraction.objects.filter(user=user).select_related('clip').order_by('-created_at')[:limit]
    
    for interaction in recent_interactions:
        # 1. Time decay: 1 / (1 + log(hours))
        hours_ago = (now - interaction.created_at).total_seconds() / 3600.0
        time_weight = 1.0 / (1.0 + math.log1p(max(0, hours_ago)))
        
        # 2. Completion weight: actual completion rate
        comp_weight = interaction.completion_rate if interaction.completion_rate > 0 else 0.1
        
        # 3. Intent weight
        if interaction.interaction_type in ['like', 'share']:
            intent_weight = 1.5      # Strong positive
        elif interaction.interaction_type == 'skip' and interaction.completion_rate < 0.2:
            intent_weight = -0.5     # Negative signal
        else:
            intent_weight = 1.0      # Neutral (view)
            
        final_weight = time_weight * comp_weight * intent_weight
        # Accumulate weighted clip vectors...
    
    # Normalize weighted average
    # Blend: 70% context + 30% long-term baseline
    ALPHA = 0.7
    final_sem = (ALPHA * weighted_sem) + ((1 - ALPHA) * long_term_semantic)
    # Normalize result
```

### Weight Components

| Weight | Formula | Purpose |
|--------|---------|---------|
| Time decay | `1 / (1 + log(hours+1))` | Recent interactions matter more |
| Completion | `completion_rate` (or 0.1) | Full listens = stronger signal |
| Intent | like/share=1.5, skip<20%=-0.5, else 1.0 | Explicit actions > passive views |

### Long-term Baseline (User model)

- Updated hourly by `evolve_long_term_user_baselines` task
- Uses `limit=500` (broader history)
- Stored on User: `long_term_semantic`, `long_term_acoustic`
- **30% weight** in blended vector (`ALPHA=0.7`)

### Blended Query Vectors (`calculate_blended_query_vectors`)

**Alternative implementation** (used by `SuggestionViewSet`):

```python
def calculate_blended_query_vectors(user):
    # 7-day window
    cutoff = now - timedelta(days=7)
    recent_interactions = UserInteraction.objects.filter(
        user=user, interaction_type__in=['like', 'share', 'view'],
        is_active=True, updated_at__gte=cutoff
    ).select_related('clip')
    
    # Time decay: 1 / (1 + log(hours))
    # Completion boost: * (completion_rate + 0.5)
    # ALPHA = 0.75 (75% short-term, 25% long-term)
```

**Difference from `calculate_time_decayed_vectors`:**
- 7-day hard window vs no window (limit-based)
- Different ALPHA (0.75 vs 0.7)
- Different intent weighting (no negative for skips)
- **Used by different endpoints** — inconsistency

---

## Feed Mixing Strategy (80/20 + Follow Wedge)

**In `refill_user_feed` (`tasks.py:554-572`):**

```python
# 80% EXPLOIT: Highest composite_score
exploit_count = int(count * 0.8)
exploit_clips = composite_query[:exploit_count]

# FOLLOW WEDGE: 5 recent clips from followed creators (forced)
followed_creators = user.following.all()
network_clips = base_queryset.filter(creator__in=followed_creators).order_by('-created_at')[:5]

# 20% EXPLORE: High engagement_velocity outside vector neighborhood
explore_count = count - exploit_count
explore_clips = base_queryset.exclude(id__in=[c.id for c in exploit_clips]).order_by('-engagement_velocity')[:explore_count]

# Combine + shuffle
clip_ids_to_push = exploit_ids + network_ids + explore_ids
random.shuffle(clip_ids_to_push)
```

### Mix Breakdown (count=50)

| Source | Count | Percentage | Purpose |
|--------|-------|------------|---------|
| Exploit (vector) | 40 | 80% | Personalized relevance |
| Follow wedge | 5 | 10% | Social discovery |
| Explore (velocity) | 5 | 10% | Serendipity, new content |

**Note:** Follow wedge is **fixed 5 clips** regardless of count — not proportional.

---

## Category-Scoped Recommendations (`SuggestionViewSet`)

**Endpoint:** `GET /suggestions/?category=comedy`

```python
def get_queryset(self):
    category = self.request.query_params.get('category')
    queryset = AudioClip.objects.filter(status='ready', category=category)
    
    sem_query, ac_query = calculate_time_decayed_vectors(user)
    
    if sem_query and ac_query:
        queryset = queryset.annotate(
            combined_distance=(
                CosineDistance('semantic_vector', sem_query) + 
                CosineDistance('acoustic_vector', ac_query)
            )
        ).order_by('combined_distance')
    
    # No engagement_velocity, no explore wedge
    # Pure vector similarity within category
```

**Differences from main feed:**
- Category filter (exact match)
- No composite score (no completion/velocity)
- No follow wedge
- No explore — pure exploitation

---

## Cold Start (`/tags/initialize/`)

**New user onboarding:**

```python
def initialize_vectors(self, request):
    selected_tags = request.data.get('selected_tags', [])
    
    baseline_clips = AudioClip.objects.filter(
        tags__overlap=selected_tags,
        semantic_vector__isnull=False,
        acoustic_vector__isnull=False
    ).order_by('-likes')[:100]
    
    user.long_term_semantic = mean(sem_vectors)
    user.long_term_acoustic = mean(ac_vectors)
    user.save()
    
    refill_user_feed.delay(user.id, count=30)
```

**Mechanism:**
1. User picks tags on signup
2. Find popular clips with those tags
3. Average their vectors → user's long-term baseline
4. Immediate feed refill

**Limitations:**
- Requires tagged clips in catalog
- Popular clips bias (ordered by likes)
- Tag quality depends on KeyBERT accuracy

---

## Global Metrics (Updated by Celery Beat)

### `update_global_metrics` (every 5 min)

```sql
-- Engagement velocity (raw SQL for performance)
UPDATE audioclip 
SET engagement_velocity = LEAST(
    (likes + shares * 2) / POWER(EXTRACT(EPOCH FROM (NOW() - created_at))/3600.0 + 2.0, 1.5) / 100.0, 
    1.0
) WHERE status = 'ready';

-- Avg completion rate
UPDATE audioclip SET avg_completion_rate = COALESCE((
    SELECT AVG(completion_rate) FROM userinteraction
    WHERE clip_id = audioclip.id AND interaction_type = 'view'
), 0) WHERE status = 'ready';
```

### Formula Analysis

**Engagement Velocity:**
```
velocity = (likes + 2*shares) / (hours_since_created + 2)^1.5 / 100
```
- Shares weighted 2x likes (stronger signal)
- Time decay: `(hours + 2)^1.5` — strong decay
- Normalized by 100, capped at 1.0
- **Favors new viral content**

**Avg Completion Rate:**
- Mean of `completion_rate` from `view` interactions
- Updated every 5 min (not real-time)
- Used as 30% weight in composite

---

## Redis Feed Queues

### Structure
```
Key: user_feed:{user_id}
Type: Redis LIST (LPUSH/RPOP)
TTL: 24 hours (86400s)
Content: Clip UUIDs (strings)
```

### Refill Trigger
- `FastFeedViewSet.list()` — if LPOP returns empty
- Also triggers if queue < 15 (removed duplicate trigger)

### Refill Lock
```python
lock_key = f"feed_refill_lock:{user_id}"
acquired = redis_client.set(lock_key, "1", nx=True, ex=30)
if not acquired:
    return "Refill already in progress."
```
- SETNX with 30s expiry
- Prevents concurrent refills for same user
- Released in `finally` block

---

## Known Issues & Limitations

| Issue | Impact | Fix Needed |
|-------|--------|------------|
| Two vector blending functions | Inconsistent recommendations | Unify `calculate_time_decayed_vectors` and `calculate_blended_query_vectors` |
| Global metrics full-table UPDATE | Lock contention at scale | Batch updates with pagination |
| No fallback feed | Redis outage → DB collapse | Cached global trending feed |
| Fixed follow wedge (5) | Doesn't scale with feed size | Proportional or configurable |
| No A/B testing | Can't measure algorithm changes | Experiment framework |
| Hardcoded weights | Can't tune per user/segment | Configurable weights |
| No diversity | Filter bubbles possible | MMR or diversity penalty |

---

*Source: `backend/app/tasks.py:422-660`, `backend/app/views.py:693-716, 719-799`, `backend/EchoFlow/settings.py:229-238`*