# Cold Start: Tag-Based Vector Bootstrapping

## Problem

New users have **no interaction history** → no preference vectors → feed cannot personalize.

## Solution: `/tags/initialize/`

Onboarding flow where user selects interest tags → system bootstraps vectors from existing clips with those tags.

---

## Endpoint

**POST `/tags/initialize/`**
```json
{
  "selected_tags": ["comedy", "tech", "motivation"]
}
```

**Response:**
```json
{
  "status": "Algorithm initialized. Feed is ready."
}
```

---

## Implementation (`views.py:772-799`)

```python
@action(detail=False, methods=['post'], url_path='initialize')
def initialize_vectors(self, request):
    user = request.user
    selected_tags = request.data.get('selected_tags', [])
    
    # Find top 100 most liked clips matching selected tags
    baseline_clips = AudioClip.objects.filter(
        tags__overlap=selected_tags,              # JSONB overlap operator
        semantic_vector__isnull=False,
        acoustic_vector__isnull=False
    ).order_by('-likes')[:100]
    
    if not baseline_clips:
        return Response({"error": "Not enough data to build baseline."}, status=400)
        
    # Extract vectors
    sem_vectors = [np.array(clip.semantic_vector) for clip in baseline_clips]
    ac_vectors = [np.array(clip.acoustic_vector) for clip in baseline_clips]
    
    # Average vectors → user's long-term baseline
    user.long_term_semantic = np.mean(sem_vectors, axis=0).tolist()
    user.long_term_acoustic = np.mean(ac_vectors, axis=0).tolist()
    user.save()
    
    # Trigger immediate feed refill
    refill_user_feed.delay(user.id, count=30)
    
    return Response({"status": "Algorithm initialized. Feed is ready."})
```

---

## Tag Overlap Query

```python
tags__overlap=selected_tags
```

**PostgreSQL JSONB operator:** Returns rows where `tags` array shares **any** element with `selected_tags`.

**Example:**
- Clip tags: `["comedy", "storytelling", "funny"]`
- Selected tags: `["comedy", "tech"]`
- Match: **YES** (shares "comedy")

---

## Vector Bootstrapping Algorithm

```
User selects tags: ["comedy", "tech", "motivation"]
         │
         ▼
Query: AudioClip.tags overlaps selected_tags
         │
         ▼
Filter: semantic_vector IS NOT NULL AND acoustic_vector IS NOT NULL
         │
         ▼
Order by: -likes (most popular first)
         │
         ▼
Limit: 100 clips
         │
         ▼
For each clip: extract semantic_vector (384) + acoustic_vector (128)
         │
         ▼
Average across all 100 clips (axis=0)
         │
         ▼
Result: user.long_term_semantic (384), user.long_term_acoustic (128)
         │
         ▼
Normalize? No — mean of normalized vectors ≈ normalized
         │
         ▼
Save to User model
         │
         ▼
Trigger refill_user_feed(30 clips)
```

---

## Frontend Integration

**OnboardingModal** (`components/feed/OnboardingModal.tsx`):

```typescript
// Shows tag selection grid
const TAGS = [
  'comedy', 'music', 'education', 'tech', 'news',
  'storytelling', 'motivation', 'meditation', 'gaming', 'sports'
];

// User selects 3-5 tags
// On submit: tagsAPI.initialize(selected_tags)
// On success: sessionStorage.ef_new_user removed, modal closes
```

**Flow:**
```
New user registers
       │
       ▼
sessionStorage.ef_new_user = '1'
       │
       ▼
AppShell detects flag → shows OnboardingModal
       │
       ▼
User selects tags → POST /tags/initialize/
       │
       ▼
Vectors saved → refill_user_feed triggered
       │
       ▼
Modal closes → FeedPage loads personalized feed
```

---

## Limitations & Edge Cases

| Issue | Impact | Mitigation |
|-------|--------|------------|
| No clips with selected tags | Returns 400 error | Show popular tags with clip counts |
| Few clips (<10) | Poor vector estimate | Lower threshold, add popularity fallback |
| Tag quality poor | KeyBERT tags noisy | Manual tag curation, taxonomy |
| Popular clips bias | Viral content dominates | Weight by recency, not just likes |
| Cold user selects obscure tags | No matches | Suggest popular tags, show counts |

---

## Comparison: Cold Start Strategies

| Strategy | Pros | Cons | EchoFlow Choice |
|----------|------|------|-----------------|
| **Tag-based bootstrapping** | Immediate personalization, explainable | Needs tagged catalog | ✅ Primary |
| Popularity-based | Works with zero data | Not personalized | Fallback |
| Random exploration | Discovers niche content | Poor initial experience | Explore wedge |
| Demographic | No interaction needed | Privacy, bias | Not used |
| Social (friends) | Strong signal | Needs social graph | Follow wedge |

---

## Future Improvements

1. **Tag popularity API** — `/tags/popular/` returns tags with clip counts
2. **Multi-step onboarding** — broad categories → specific tags
3. **Implicit signals** — dwell time on tag selection
4. **Vector interpolation** — blend tag vectors with global average
5. **A/B test** — tag-based vs popularity vs hybrid cold start

---

*Source: `backend/app/views.py:772-799`, `frontend/sample_frontend/src/components/feed/OnboardingModal.tsx`, `ai_ml/pipelines/cold_start.py`*