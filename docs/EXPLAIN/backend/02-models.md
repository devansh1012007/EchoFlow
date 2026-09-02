# Backend Models

## Overview

All models defined in `backend/app/models.py`. Uses custom User model (`AUTH_USER_MODEL = 'app.User'`).

## Model Definitions

### User (`backend.app.User`)

Extends `AbstractUser` with additional fields:

```python
class User(AbstractUser):
    encrypted_email = models.TextField(unique=True, null=True, blank=True)
    following = models.ManyToManyField('self', symmetrical=False, related_name='followers', blank=True)
    long_term_semantic = VectorField(dimensions=384, null=True, blank=True)
    long_term_acoustic = VectorField(dimensions=128, null=True, blank=True)
    profile_picture = models.ImageField(upload_to='avatars/', null=True, blank=True)
```

**Key behaviors:**
- `save()`: Encrypts `email` to `encrypted_email` using Fernet (fail-fast if key missing)
- `following`: Self-referential ManyToMany for social graph
- Vector fields: Long-term preference baselines for recommendations
- `profile_picture`: Stored via S3Storage (avatars/ prefix)

**Constraints:**
- `encrypted_email` unique (prevents duplicate accounts with same email)
- Fernet key required at startup (`FIELD_ENCRYPTION_KEY`)

---

### AudioClip

Core content model representing an audio clip:

```python
class AudioClip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='audio_clips')
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=50, blank=True)
    
    original_file = models.FileField(upload_to='uploads/%Y/%m/%d/', null=True)
    hls_playlist_url = models.CharField(max_length=500, blank=True, null=True)
    
    # Provenance (scraper imports)
    source_name = models.CharField(max_length=100, blank=True, null=True)
    source_url = models.CharField(max_length=500, blank=True, null=True)
    license = models.CharField(max_length=100, blank=True, null=True)
    attribution_text = models.CharField(max_length=500, blank=True, null=True)
    imported_via_scraper = models.BooleanField(default=False)
    original_source_id = models.CharField(max_length=255, blank=True, null=True)
    
    # Global metrics
    duration_ms = models.IntegerField(default=0)
    avg_completion_rate = models.FloatField(default=0.0)
    engagement_velocity = models.FloatField(default=0.0)
    
    # Denormalized counters
    likes = models.BigIntegerField(default=0)
    shares = models.BigIntegerField(default=0)
    skips = models.BigIntegerField(default=0)
    comment_count = models.BigIntegerField(default=0)
    
    # AI Intelligence
    tags = models.JSONField(default=list, blank=True)
    semantic_vector = VectorField(dimensions=384, null=True, blank=True)
    acoustic_vector = VectorField(dimensions=128, null=True, blank=True)
    
    status = models.CharField(max_length=20, default='processing')
    created_at = models.DateTimeField(auto_now_add=True)
```

**Indexes (Meta.indexes):**
```python
indexes = [
    models.Index(fields=['status', '-created_at']),
    models.Index(fields=['status', '-engagement_velocity']),
    models.Index(fields=['category', '-likes']),
    HnswIndex(name='semantic_vector_index', fields=['semantic_vector'], m=16, ef_construction=64, opclasses=['vector_cosine_ops']),
    HnswIndex(name='acoustic_vector_index', fields=['acoustic_vector'], m=16, ef_construction=64, opclasses=['vector_cosine_ops']),
]
```

**Constraints (Meta.constraints):**
```python
constraints = [
    models.CheckConstraint(check=models.Q(likes__gte=0), name='likes_non_negative'),
    models.CheckConstraint(check=models.Q(shares__gte=0), name='shares_non_negative'),
    models.CheckConstraint(check=models.Q(skips__gte=0), name='skips_non_negative'),
    models.CheckConstraint(check=models.Q(comment_count__gte=0), name='comment_count_non_negative'),
]
```

**Status values:** `'processing'`, `'ready'`, `'failed'`

**Key behaviors:**
- `hls_playlist_url` stores S3 object **key** (e.g., `hls/<uuid>/master.m3u8`), not full URL
- Vector dimensions: semantic=384 (sentence-transformers), acoustic=128 (librosa)
- Provenance fields populated only for scraper imports
- Denormalized counters updated via `F()` expressions from `UserInteraction.save()`

---

### Comment

Nested threaded comments on clips:

```python
class Comment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clip = models.ForeignKey('AudioClip', on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    text = models.CharField(max_length=500)
    likes = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
```

**Meta:**
```python
indexes = [models.Index(fields=['clip', '-created_at'])]
ordering = ['-created_at']
constraints = [models.CheckConstraint(check=models.Q(likes__gte=0), name='comment_likes_non_negative')]
```

**Key behaviors:**
- `save()`: Increments `AudioClip.comment_count` via `F()` **only for top-level comments** (`not self.parent_id`)
- `delete()`: Decrements `comment_count` for top-level comments
- Uses `_state.adding` (not `self.pk`) because UUID PK assigned at `__init__`
- Replies don't affect clip's `comment_count`

---

### ShareEvent

Peer-to-peer clip sharing (inbox model):

```python
class ShareEvent(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_shares', on_delete=models.CASCADE)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='received_shares', on_delete=models.CASCADE)
    clip = models.ForeignKey(AudioClip, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)
```

**Meta:**
```python
indexes = [models.Index(fields=['receiver', '-created_at', 'is_read'])]
```

**Usage:**
- Created by `ShareViewSet.send_share()` 
- Inbox: `ShareViewSet.inbox()` filters by receiver
- Unread count: `ShareViewSet.unread_count()`
- **Does not decrement** clip.shares on delete (share action preserved)

---

### UserInteraction

Unified interaction model for likes, shares, skips, views:

```python
class UserInteraction(models.Model):
    TYPES = [
        ('like', 'Like'),
        ('share', 'Share'),
        ('skip', 'Skip'),
        ('view', 'View'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    clip = models.ForeignKey(AudioClip, on_delete=models.CASCADE)
    interaction_type = models.CharField(max_length=10, choices=TYPES)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    watch_time_ms = models.IntegerField(default=0)
    completion_rate = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
```

**Meta:**
```python
unique_together = ('user', 'clip', 'interaction_type')
indexes = [models.Index(fields=['user', 'interaction_type'])]
```

**Key behaviors (`save()` method):**
- Tracks `is_active` state changes (toggle like/unlike)
- On state change: atomically increments/decrements `AudioClip` counter via `F()`
- Uses `select_for_update()` to prevent race conditions on toggle
- `completion_rate` calculated server-side from `watch_time_ms / clip.duration_ms`

**Interaction types & counter mapping:**
| interaction_type | AudioClip field |
|-----------------|-----------------|
| like | likes |
| share | shares |
| skip | skips |
| view | (no direct counter, feeds avg_completion_rate) |

---

## Vector Fields (pgvector)

Both `User` and `AudioClip` have vector fields:

| Model | Field | Dimensions | Source |
|-------|-------|------------|--------|
| AudioClip | semantic_vector | 384 | sentence-transformers (all-MiniLM-L6-v2) on transcript |
| AudioClip | acoustic_vector | 128 | librosa (MFCC 40 + chroma 12 + mel 76) |
| User | long_term_semantic | 384 | Blended from interactions (hourly Celery Beat) |
| User | long_term_acoustic | 128 | Blended from interactions (hourly Celery Beat) |

**HNSW Indexes (AudioClip only):**
- `semantic_vector_index`: m=16, ef_construction=64, cosine ops
- `acoustic_vector_index`: m=16, ef_construction=64, cosine ops

**Query pattern:**
```python
from pgvector.django import CosineDistance
AudioClip.objects.annotate(
    sem_dist=CosineDistance('semantic_vector', query_vector),
    ac_dist=CosineDistance('acoustic_vector', query_vector)
).order_by('sem_dist')  # or combined distance
```

---

## Database-Level Constraints

Migration `0002_audioclip_likes_non_negative_and_more.py` adds CheckConstraints:

```sql
-- AudioClip
ALTER TABLE app_audioclip ADD CONSTRAINT likes_non_negative CHECK (likes >= 0);
ALTER TABLE app_audioclip ADD CONSTRAINT shares_non_negative CHECK (shares >= 0);
ALTER TABLE app_audioclip ADD CONSTRAINT skips_non_negative CHECK (skips >= 0);
ALTER TABLE app_audioclip ADD CONSTRAINT comment_count_non_negative CHECK (comment_count >= 0);

-- Comment
ALTER TABLE app_comment ADD CONSTRAINT comment_likes_non_negative CHECK (likes >= 0);
```

**Purpose:** Prevent negative counters even via raw SQL or bulk ORM updates.

---

## Relationships Summary

```
User
├── audio_clips (FK from AudioClip.creator) — CASCADE delete
├── following (M2M self) — symmetrical=False
├── followers (reverse M2M)
├── comments (FK from Comment.author) — CASCADE delete
├── sent_shares (FK from ShareEvent.sender) — CASCADE delete
├── received_shares (FK from ShareEvent.receiver) — CASCADE delete
└── interactions (FK from UserInteraction.user) — CASCADE delete

AudioClip
├── creator (FK to User) — CASCADE delete
├── comments (FK from Comment.clip) — CASCADE delete
├── shares (FK from ShareEvent.clip) — CASCADE delete
└── interactions (FK from UserInteraction.clip) — CASCADE delete

Comment
├── clip (FK to AudioClip) — CASCADE delete
├── author (FK to User) — CASCADE delete
├── parent (FK to self) — CASCADE delete (replies)
└── replies (reverse FK)

ShareEvent
├── sender (FK to User) — CASCADE delete
├── receiver (FK to User) — CASCADE delete
└── clip (FK to AudioClip) — CASCADE delete

UserInteraction
├── user (FK to User) — CASCADE delete
└── clip (FK to AudioClip) — CASCADE delete
```

---

## Migration History

| Migration | Description |
|-----------|-------------|
| `0001_initial.py` | Creates all tables, pgvector extension, indexes, HNSW indexes |
| `0002_audioclip_likes_non_negative_and_more.py` | Adds CheckConstraints for non-negative counters |

**Note:** `0001_initial.py` includes `RunSQL` for `CREATE EXTENSION IF NOT EXISTS vector` — must be in initial migration due to swappable dependency anchoring.

---

*Source: `backend/app/models.py`, `backend/app/migrations/0001_initial.py`, `backend/app/migrations/0002_*.py`*