# EchoFlow Key Design Decisions

This document captures the major architectural and implementation decisions found in the codebase, with their rationale, trade-offs, and alternatives considered.

---

## 1. Dual `EchoFlow/` Package Structure

**Decision:** Two separate packages named `EchoFlow/`:
- `backend/EchoFlow/` — Django project package (settings, urls, celery, wsgi, health)
- `backend/app/` — Django application package (models, views, tasks, scrapers)

**Rationale:** Standard Django project layout. The project package contains cross-cutting configuration; the app package contains business logic.

**Trade-off:** Confusing for newcomers. Import paths differ (`backend.EchoFlow.settings` vs `backend.app.models`).

**Code references:**
- `backend/EchoFlow/__init__.py` exports Celery app
- `backend/EchoFlow/settings.py` sets `ROOT_URLCONF = 'backend.EchoFlow.urls'`
- `backend/app/apps.py` defines `AppConfig`

---

## 2. Custom User Model (`backend.app.User`)

**Decision:** Extend `AbstractUser` with:
- `encrypted_email` (Fernet-encrypted, unique)
- `following` (self-referential ManyToMany)
- `long_term_semantic` (VectorField 384-dim)
- `long_term_acoustic` (VectorField 128-dim)
- `profile_picture` (ImageField)

**Rationale:** 
- Email encryption for PII compliance
- Vector fields on User model enable long-term preference baselines
- Social graph on User model avoids separate Follow model

**Trade-off:** User model carries ML state (vectors), coupling auth with recommendations.

**Code references:**
- `backend/app/models.py:27-43`
- `backend/EchoFlow/settings.py:312` `AUTH_USER_MODEL = 'app.User'`

---

## 3. pgvector for Vector Similarity (HNSW Indexes)

**Decision:** Use PostgreSQL + pgvector extension with HNSW indexes on:
- `AudioClip.semantic_vector` (384-dim, `vector_cosine_ops`)
- `AudioClip.acoustic_vector` (128-dim, `vector_cosine_ops`)
- HNSW params: `m=16`, `ef_construction=64`

**Rationale:**
- Keeps vectors in primary DB — no separate vector DB needed at current scale
- HNSW provides ANN (approximate nearest neighbor) with good recall
- Native Django ORM integration via `pgvector.django.VectorField` and `CosineDistance`

**Trade-offs:**
- Index memory grows with row count (~1.5GB per 10M vectors at 384-dim)
- No multi-tenancy isolation
- Index rebuilds lock table
- At >10M clips, need dedicated vector DB (Qdrant/Milvus)

**Code references:**
- `backend/app/models.py:91-106` (indexes in Meta)
- `backend/app/migrations/0001_initial.py:148-152`
- `backend/app/tasks.py:539-541` (CosineDistance in queryset)

---

## 4. Redis Feed Queues (Per-User Lists)

**Decision:** Pre-compute personalized feeds into Redis lists:
- Key: `user_feed:{user_id}` (LPUSH/RPOP)
- Refill trigger: queue length < 15
- Refill count: 40 clips (80% exploit, 20% explore + follow wedge)
- TTL: 24 hours

**Rationale:**
- Sub-millisecond feed latency (Redis LPOP)
- Decouples recommendation computation from API request
- Enables "infinite scroll" feel without DB query per page

**Trade-offs:**
- Cache invalidation on clip delete (must scan all user feeds)
- Memory: 1M users × 50 clips × ~36 bytes = ~1.8GB Redis
- Stale recommendations until refill
- No fallback when Redis down (architecture audit P0)

**Code references:**
- `backend/app/views.py:118-159` (FastFeedViewSet)
- `backend/app/tasks.py:512-592` (refill_user_feed)

---

## 5. Composite Scoring Formula

**Decision:** Feed ranking uses weighted composite score computed in PostgreSQL:
```
vector_similarity = 1 - (cosine_dist_semantic + cosine_dist_acoustic) / 4
composite_score = 0.45 * vector_similarity 
                + 0.30 * avg_completion_rate 
                + 0.25 * engagement_velocity
```

**Rationale:**
- 45% content similarity (what user likes)
- 30% completion rate (quality signal)
- 25% engagement velocity (virality/trending)
- Computed natively in PostgreSQL via `ExpressionWrapper` — single query

**Trade-offs:**
- Weights hardcoded; no A/B testing framework
- `engagement_velocity` formula favors new clips (time decay in denominator)
- `avg_completion_rate` only from 'view' interactions

**Code references:**
- `backend/app/tasks.py:539-551` (composite_query annotation)
- `backend/app/tasks.py:676-678` (engagement_velocity formula in update_global_metrics)

---

## 6. Time-Decayed User Vectors

**Decision:** User preference vectors blend recent context (70%) with long-term baseline (30%):
- Recent interactions: last 7 days, time decay `1/(1+log(hours))`
- Weighted by completion_rate and intent (like/share=1.5x, skip<20%=-0.5x)
- Blended: `ALPHA=0.7 * context + 0.3 * long_term`

**Rationale:**
- Captures "current mood" while retaining long-term taste
- Logarithmic time decay prevents recency from dominating entirely
- Intent weighting differentiates passive views from active engagement

**Trade-offs:**
- 7-day window hardcoded; no per-user adaptation
- Negative weights for skips can produce inverted vectors
- Long-term baseline only updated hourly via Celery Beat

**Code references:**
- `backend/app/tasks.py:594-660` (calculate_time_decayed_vectors)
- `backend/app/tasks.py:456-509` (calculate_blended_query_vectors — similar but different ALPHA=0.75)

---

## 7. S3-Compatible Storage with Split ACL

**Decision:** 
- `uploads/` prefix: private, signed URLs (1hr expiry)
- `hls/` prefix: public-read (anonymous download enabled via MinIO policy)

**Rationale:** 
- HLS is multi-file protocol; signed URLs don't work for relative segment references
- RFC 3986: relative reference resolution drops base URL's query string
- Original uploads (user content) must remain private
- Derived HLS streams are safe to serve publicly

**Trade-offs:**
- HLS streams accessible to anyone with URL (no per-user access control)
- Cannot revoke access to specific HLS stream without bucket policy change
- CDN caching requires public objects

**Code references:**
- `backend/EchoFlow/settings.py:270-293` (STORAGES config)
- `backend/app/media_urls.py` (get_hls_playback_url vs get_signed_media_url)
- `docker-compose.yml:108` (minio-init anonymous set download)

---

## 8. FFmpeg MPEG-TS Segments (Not fMP4)

**Decision:** HLS segments use MPEG-TS container (`-hls_segment_type mpegts`)

**Rationale:**
- Chrome MSE decoder rejects fMP4 with certain AAC configurations
- `DECODER_ERROR_NOT_SUPPORTED` / `DecoderStatus::kUnsupportedConfig`
- MPEG-TS universally supported by hls.js and all browsers

**Trade-offs:**
- MPEG-TS has higher overhead (~10% larger segments)
- fMP4 is more modern and efficient
- Segment magic bytes verified: `47 40 11 11` (MPEG-TS sync)

**Code references:**
- `backend/app/tasks.py:290-294` (ffmpeg command with mpegts)
- `docs/minio-s3-architecture.md:34-39`
- `docs/hls-playback-fix.md`

---

## 9. ML Models Lazy-Loaded in Celery Workers

**Decision:** Models loaded on first task execution, not at import:
- `get_whisper_model()` → `faster_whisper.WhisperModel("base", cpu, int8)`
- `get_embedding_model()` → `sentence_transformers.SentenceTransformer("all-MiniLM-L6-v2")`
- `get_kw_model()` → `keybert.KeyBERT()`
- Thread-safe double-checked locking with global `_model_lock`

**Rationale:**
- Avoids loading ~1GB models in web/API processes (gunicorn workers)
- Prevents duplicate loads in prefork Celery workers
- Models baked into `media` Docker image at build time (HF_TOKEN secret)

**Trade-offs:**
- First task after worker start has latency spike (~5-10s)
- Models stay in memory for worker lifetime
- `--pool=solo` required for heavy_media (no forking with loaded models)

**Code references:**
- `backend/app/tasks.py:28-82` (model getters with locking)
- `Dockerfile:116-123` (bake step with BuildKit secret)

---

## 10. Celery Task Routing by Queue

**Decision:** Three dedicated queues with specialized workers:
| Queue | Worker | Concurrency | Tasks |
|-------|--------|-------------|-------|
| `celery` (default) | `celery` | prefork (CPU) | Scraping, general |
| `fast_feed` | `celery_feed` | 4 (threads) | `refill_user_feed` |
| `heavy_media` | `celery_media` | solo (1 process) | `process_audio_to_hls` |

**Rationale:**
- Isolate memory-heavy ML/HLS from feed computation
- `fast_feed` needs concurrency for parallel vector queries
- `heavy_media` must be solo (models not fork-safe)
- Default queue for everything else

**Trade-offs:**
- More operational complexity (4 worker processes + beat)
- Queue routing defined in `settings.py:158-161`
- No priority within queues

**Code references:**
- `backend/EchoFlow/settings.py:158-161` (CELERY_TASK_ROUTES)
- `docker-compose.yml` service definitions

---

## 11. Audio Normalization Before ML/FFmpeg

**Decision:** Single authoritative FFmpeg decode to mono 22050Hz WAV before any processing:
```python
normalize_to_wav(input_path) → ffmpeg -ac 1 -ar 22050 -f wav
```
Then librosa, Whisper, and HLS transcode all use this normalized WAV.

**Rationale:**
- librosa uses soundfile → audioread fallback (warnings, unreliable)
- Whisper and FFmpeg would each re-decode original independently
- One decode ensures consistent input to all downstream steps
- Removes audioread dependency (deprecated in librosa 1.0)

**Trade-offs:**
- Extra FFmpeg pass per clip
- Temp file management complexity
- 22050Hz may lose high-frequency content (but sufficient for speech/music classification)

**Code references:**
- `backend/app/tasks.py:142-169` (normalize_to_wav)
- `backend/app/tasks.py:210-224` (usage in process_audio_to_hls)

---

## 12. Raw SQL for Global Metrics Update

**Decision:** `update_global_metrics` uses raw SQL UPDATE on entire table:
```sql
UPDATE audioclip 
SET engagement_velocity = LEAST((likes + shares*2) / (hours+2)^1.5 / 100, 1.0)
WHERE status = 'ready';

UPDATE audioclip SET avg_completion_rate = COALESCE((
    SELECT AVG(completion_rate) FROM userinteraction 
    WHERE clip_id = audioclip.id AND interaction_type = 'view'
), 0) WHERE status = 'ready';
```

**Rationale:**
- ORM would load all objects → memory explosion
- Single UPDATE is faster than N individual saves
- Runs every 5 minutes via Celery Beat

**Trade-offs:**
- **Full table lock** on large tables (architecture audit: "guaranteed table-lock event")
- No batching, no pagination
- Bypasses ORM signals/validation
- `engagement_velocity` formula recomputed from scratch each run (not incremental)

**Code references:**
- `backend/app/tasks.py:666-690`

---

## 13. UserInteraction Denormalized Counters with F() Expressions

**Decision:** AudioClip counters (likes, shares, skips, comment_count) updated atomically via `F()`:
```python
AudioClip.objects.filter(pk=clip.pk).update(
    **{field_to_update: F(field_to_update) + increment_val}
)
```
Triggered from `UserInteraction.save()` when `is_active` toggles.

**Rationale:**
- Avoids race conditions on concurrent likes/shares
- Single UPDATE per interaction (no SELECT then UPDATE)
- DB-level constraints prevent negative values

**Trade-offs:**
- Denormalization: counters can drift if bugs in Interaction logic
- `Comment.save()/delete()` manually update `comment_count` (not via signals)
- No transaction wrapping interaction + counter update (separate queries)

**Code references:**
- `backend/app/models.py:181-205` (UserInteraction.save)
- `backend/app/models.py:134-144` (Comment.save/delete)
- `backend/app/migrations/0002...` (CheckConstraints)

---

## 14. Transaction.on_commit for Async Task Enqueue

**Decision:** `process_audio_to_hls` enqueued via `transaction.on_commit`:
```python
def create(self, request, *args, **kwargs):
    serializer = self.get_serializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    clip = serializer.save()
    transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))
```

**Rationale:**
- Guarantees clip row exists in DB before worker picks up task
- If transaction rolls back, task never enqueued
- Prevents "clip not found" errors in worker

**Trade-offs:**
- Slight delay before task enqueued (after commit)
- If commit succeeds but Celery broker down, task lost (no outbox pattern)

**Code references:**
- `backend/app/views.py:95-112` (AudioUploadViewSet.create)

---

## 15. Gunicorn preload_app + post_fork Connection Reset

**Decision:** `preload_app = True` with `post_fork` hook closing all Django DB and Celery Redis connections.

**Rationale:**
- `preload_app` loads Django app in master process before forking workers
- `EchoFlow/__init__.py` imports Celery app → creates Redis connections in master
- Without reset, forked workers inherit stale connections
- Django also creates DB connections at import time

**Trade-offs:**
- post_fork adds latency to worker startup
- Must remember to close ALL connection types (DB, Redis, etc.)
- If new connection created at import time, must be added to hook

**Code references:**
- `gunicorn.conf.py:22, 44-65`
- `backend/EchoFlow/__init__.py:2` (imports celery_app at module level)

---

## 16. BuildKit Secret for HF_TOKEN (Not Build Arg)

**Decision:** HF_TOKEN passed as BuildKit secret mount during `media` image build:
```dockerfile
RUN --mount=type=secret,id=hf_token \
    set -eu; \
    if [ -s /run/secrets/hf_token ]; then \
        export HF_TOKEN="$(cat /run/secrets/hf_token)"; \
    fi; \
    python -c "from faster_whisper import WhisperModel; ..."
```

**Rationale:**
- `--build-arg` persists token in layer history (readable via `docker history`)
- Secret mount exists only during RUN, never in image layers
- `docker-compose.yml:398-402` defines secret from environment

**Trade-offs:**
- Requires BuildKit (Docker 23.0+ / Compose V2)
- Slightly more complex CI/CD
- Falls back to anonymous download if secret not provided

**Code references:**
- `Dockerfile:116-123`
- `docker-compose.yml:397-402`

---

## 17. Offline Wheelhouse for Deterministic Builds

**Decision:** All pip installs use `--no-index --find-links=/wheelhouse` with pre-built wheels.

**Rationale:**
- Fully offline, deterministic builds
- No PyPI dependency at build time
- CPU-only torch build pinned via constraints + extra-index-url
- `librosa==0.11.0` pinned (1.x requires Python 3.12)
- `django==5.2.17` pinned (6.x requires Python 3.12)

**Trade-offs:**
- Wheelhouse must be regenerated when any dependency changes
- Regeneration script requires Python 3.11 container
- `dj-rest-auth` pre-built as wheel (sdist-only on PyPI)

**Code references:**
- `Dockerfile:76-81, 101-106`
- `AGENTS.md` wheelhouse regeneration script

---

## 18. Fernet Email Encryption (Fail-Fast on Missing Key)

**Decision:** `FIELD_ENCRYPTION_KEY` required at startup; app crashes if missing:
```python
FERNET_KEY = os.environ.get('FIELD_ENCRYPTION_KEY')
if not FERNET_KEY:
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is missing...")
cipher_suite = Fernet(FERNET_KEY.encode())
```

**Rationale:**
- Silent fallback to plaintext would leak PII
- Fail-fast ensures configuration error caught immediately
- Same pattern as `DJANGO_SECRET_KEY`

**Trade-offs:**
- Key rotation not implemented (architecture audit: "key rotation missing")
- All existing encrypted emails unrecoverable if key lost
- Single key for all users (no per-user keys)

**Code references:**
- `backend/app/models.py:16-25`
- `backend/EchoFlow/settings.py:13-21` (SECRET_KEY same pattern)

---

## 19. CORS Configuration for HLS Range Requests

**Decision:** Explicit CORS headers for HLS playback:
```python
CORS_ALLOW_HEADERS = [..., 'range']  # Critical for HLS partial content
CORS_EXPOSE_HEADERS = ['Content-Range', 'Accept-Ranges']
CORS_ALLOW_ALL_ORIGINS = False  # Explicit allowed origins
```

**Rationale:**
- HLS.js uses HTTP Range requests for segment seeking
- Browsers send `Range` header, need `Content-Range`/`Accept-Ranges` in response
- Explicit allowed origins more secure than wildcard

**Trade-offs:**
- Must maintain allowed origins list
- `CORS_ALLOW_ALL_ORIGINS` was True in earlier versions (fixed)

**Code references:**
- `backend/EchoFlow/settings.py:31-63`

---

## 20. JWT Token Lifetimes (15min Access, 7day Refresh)

**Decision:** Short access tokens, longer refresh tokens:
```python
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}
```

**Rationale:**
- Stolen access token usable for only 15 minutes
- Refresh token rotation not implemented (would need blacklist)
- 7-day refresh matches typical "remember me" duration

**Trade-offs:**
- Frequent token refresh requests
- No token blacklist → logout doesn't invalidate access token until expiry
- Refresh token stored in sessionStorage (XSS accessible)

**Code references:**
- `backend/EchoFlow/settings.py:334-339`
- `frontend/sample_frontend/src/api/client.ts:50-78` (auto-refresh logic)

---

## 21. Denormalized Comment Count on AudioClip

**Decision:** `AudioClip.comment_count` updated in `Comment.save()` and `Comment.delete()`:
```python
def save(self, *args, **kwargs):
    if self._state.adding and not self.parent_id:
        AudioClip.objects.filter(pk=self.clip_id).update(comment_count=F('comment_count') + 1)

def delete(self, *args, **kwargs):
    if not self.parent_id:
        AudioClip.objects.filter(pk=self.clip_id).update(comment_count=F('comment_count') - 1)
```

**Rationale:**
- Avoids COUNT(*) query on every feed serialization
- Only top-level comments count (replies excluded)
- Atomic via F() expression

**Trade-offs:**
- Not via signals — easy to miss if Comment created elsewhere
- Drift possible if direct DB manipulation
- Parent check uses `_state.adding` (UUID PK assigned at init)

**Code references:**
- `backend/app/models.py:134-144`

---

## 22. Scraper License Filtering (Allowlist)

**Decision:** Configurable allowed licenses via `SCRAPER_ALLOW_LICENSES`:
```python
SCRAPER_ALLOW_LICENSES = os.getenv('SCRAPER_ALLOW_LICENSES', 'CC0,CC-BY,CC-BY-SA,CC-BY-NC').split(',')
```
Clips with unknown/unallowed licenses skipped with warning.

**Rationale:**
- Legal compliance for redistribution
- CC0/CC-BY/CC-BY-SA/CC-BY-NC are permissive for commercial use
- Unknown license → warning but imported (manual review needed)

**Trade-offs:**
- Freesound previews only (not full quality) without OAuth
- Kaggle requires local file path (not API)
- License metadata from source APIs may be inaccurate

**Code references:**
- `backend/EchoFlow/settings.py:206`
- `backend/app/management/commands/scrape_audio.py:52-56`

---

## 23. Cold-Start Tag-Based Vector Bootstrapping

**Decision:** `/tags/initialize/` endpoint bootstraps new user vectors:
1. User selects tags on onboarding
2. Query top 100 clips by likes matching those tags
3. Average their semantic/acoustic vectors
4. Store as user's `long_term_semantic/acoustic`
5. Trigger immediate feed refill

**Rationale:**
- Solves cold-start without interaction history
- Uses existing clip vectors (generated during processing)
- Tag overlap query: `tags__overlap=selected_tags`

**Trade-offs:**
- Requires sufficient tagged clips in catalog
- Popular clips bias (ordered by -likes)
- Tags from KeyBERT may be noisy

**Code references:**
- `backend/app/views.py:719-799` (TagsViewSet.initialize_vectors)
- `ai-ml/pipelines/cold_start.py` (stub for future migration)

---

## Discrepancies: Documented vs Actual

| Area | Documentation Claims | Actual Implementation |
|------|---------------------|----------------------|
| DEBUG mode | "hardcoded True" (README:24) | Env-driven `DJANGO_DEBUG` default False |
| CORS | "hardcoded True" (README:25) | Explicit `CORS_ALLOW_ALL_ORIGINS = False` |
| Media storage | "local disk" (README:83) | S3-compatible (settings.py STORAGES) |
| Feed fallback | "FeedViewSet fallback exists" | Commented out in views.py:160-215 |
| Rate limiting | "No rate limiting" (audit) | DRF throttling configured (1000/hr user) |
| Recommendation weights | "Never populated" (audit) | `weights.append(final_weight)` at tasks.py:629 |
| OPENAI_API_KEY NameError | "NameError in tasks" | Guarded by `get_openai_client()` check |

---

*Source: Codebase analysis of `backend/`, `docker-compose.yml`, `Dockerfile`, `docs/*.md`*