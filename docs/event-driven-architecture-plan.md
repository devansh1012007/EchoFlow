# docs/event-driven-architecture-plan.md

> **Companion to:** `docs/relational-to-event-driven-architecture.md` (read first for foundational concepts).
> **Date:** 2026-09-02 · **Target:** 10,000 live (concurrently connected) users on the current stack.
> **Scope:** Updates the prior doc with what is actually in the codebase today, then lays out the detailed, sequenced engineering plan to reach and safely serve 10k concurrent users.

---

## 0. Read This First

The original `relational-to-event-driven-architecture.md` was written against an **aspirational** model of the codebase (referenced entities, e.g. "row-level `F()` updates on `UserInteraction.save()`", were partially described and partially assumed). Most of its architectural reasoning — *why* EchoFlow needs event-driven evolution, *what* stays relational, *how* idempotency / outbox / DLQ work — is **still correct and remains the foundation** of this plan. This document is the **delta**: what the codebase actually looks like in 2026-09, what changed since the prior doc, and the concrete, ordered work to reach 10k concurrent users without rewriting the system.

If you have time for only one document, read **§3 (the gap analysis)** and **§6 (the phased plan)**.

---

## 1. What the Codebase Actually Looks Like (2026-09-02)

A factual snapshot, established by reading every relevant file. Every line reference below is real and current.

### 1.1 Stack inventory

| Layer | What is deployed | Source |
|---|---|---|
| API | Django 5.2 + DRF on Gunicorn `gthread`, 4 workers × 4 threads = 16 concurrent in-flight requests, `timeout=120s` | `backend/gunicorn.conf.py:9-16` |
| DB | PostgreSQL 16 + pgvector, single node, **no PgBouncer**, default `max_connections=100`, no `command:` override | `docker-compose.yml:3-33` |
| Cache / broker | Redis 7, `maxmemory 512mb`, `maxmemory-policy allkeys-lru`, AOF on | `docker-compose.yml:36-49` |
| Object storage | MinIO (dev) or S3 / R2 (prod), path-style addressing, `hls/` public, `uploads/` private | `docker-compose.yml:70-124`, `backend/EchoFlow/settings.py:256-290` |
| Workers | `celery` (default), `celery_feed` (`fast_feed`, concurrency 4), `celery_media` (`heavy_media`, `--pool=solo` — required because Whisper + sentence-transformers + KeyBERT loaded as module singletons exceed per-process RAM), `celery_beat` (DatabaseScheduler) | `docker-compose.yml:189-386` |
| Beat cadence | `update_global_metrics` every 300 s (raw SQL on every ready clip — no batching), `evolve_long_term_user_baselines` every 3600 s (no error handling per user) | `settings.py:222-231` |
| Frontend | Vite + React, no telemetry batching, no service worker, no offline queue, hls.js loaded from CDN at runtime; unread-count polled every 30 s | `frontend/main.jsx:1103, 319-336, 2120-2126` |
| Auth | JWT (SimpleJWT) — ACCESS 15 min, REFRESH 7 days; `dj-rest-auth` registration | `settings.py:329-332` |
| Throttling | DRF defaults: `anon 100/hour`, `user 1000/hour` — **burst-unprotected, only hourly buckets** | `settings.py:317-324` |
| Monitoring | `prometheus_client` exported at `/metrics/`, **nothing scrapes it**; no Grafana / OTel / Sentry | `urls.py:13`, `docker-compose.yml` (absent) |

### 1.2 Models (5 total, all in `backend/app/models.py`)

| Model | Key fields | Hot-spot risk |
|---|---|---|
| `User` (custom, `AUTH_USER_MODEL='app.User'`) | `encrypted_email`, `long_term_semantic[384]`, `long_term_acoustic[128]`, `following` M2M (non-symmetrical) | `save()` enforces email encryption (`models.py:37-43`) — fine, but every save does a Fernet round-trip |
| `AudioClip` (UUID PK) | `original_file`, `hls_playlist_url` (relative object-storage key, **not** a URL), `likes/shares/skips/comment_count` denormalized counters, `tags` JSONField, `semantic_vector[384]`, `acoustic_vector[128]`, `status ∈ {processing, ready, failed}` | HNSW indexes `(m=16, ef_construction=64)` on both vector columns (`models.py:84-103`); `engagement_velocity` and `avg_completion_rate` recomputed by raw SQL every 5 min (`tasks.py:633-658`) |
| `Comment` (UUID PK) | `parent` self-FK, `likes`, `text` (500 char, **no HTML sanitization — see TODO.md:17**) | `save()` and `delete()` mutate `AudioClip.comment_count` via `F()` **only when `parent_id IS NULL`** (top-level only) — replies do not increment the counter (`models.py:119-129`). Subtle: this is correct, but undocumented |
| `ShareEvent` (BigAuto) | `sender`, `receiver`, `clip`, `is_read` (indexed by `receiver, -created_at, is_read`) | No counter decrement when a share is deleted (`views.py:480-484`) — counter can drift permanently upward |
| `UserInteraction` (BigAuto) | `interaction_type ∈ {view, like, share, skip}`, `is_active`, `watch_time_ms`, `completion_rate`, `unique_together(user, clip, interaction_type)` | **`save()` override executes `AudioClip.objects.filter(pk=...).update(likes=F('likes')+1)` for `like/share/skip` on state change** (`models.py:166-190`). Uses `select_for_update()` on the toggle path. **`'view'` deliberately omitted from `field_map`** — views never bump a denormalized counter |

### 1.3 Views (8 viewsets, all in `backend/app/views.py`)

Zero WebSocket, zero SSE, zero `StreamingHttpResponse`, zero async views. Confirmed by grep.

| Endpoint | What it does today | Synchronous DB cost per request | Async dispatch |
|---|---|---|---|
| `POST /clips/` (upload) | `serializer.save()` then `transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))` | 1 INSERT into `audioclip` | `heavy_media` queue |
| `GET /feed/` | `redis.lpop(user_feed:{id}, 10)` → if empty, `refill_user_feed.delay(count=40)` and re-poll → if queue `< 15` after pop, refill again → 1 SELECT on `audioclip` ordered by `preserved_order` | 1 SELECT | `fast_feed` queue (twice on miss) |
| `POST /interactions/{id}/toggle-like/` | `get_or_create`, toggle `is_active`, save → triggers `UserInteraction.save()` → `F()` update on `audioclip.likes` | 1 SELECT + 1 INSERT-or-UPDATE + 1 UPDATE on `audioclip` (row-locked) | none |
| `POST /interactions/{id}/register-skip/` | `UserInteraction.update_or_create(interaction_type='view', ...)` (note: writes a `'view'`, not a `'skip'`, despite the name) | 1 SELECT + 1 INSERT-or-UPDATE; **does not bump any counter** | none |
| `POST /interactions/{id}/log-telemetry/` | `UserInteraction.update_or_create(action_type, defaults=...)`; for `like/share/skip` with new row + `is_active=True`, `UserInteraction.save()` fires the `F()` counter bump | 1 SELECT + 1 INSERT-or-UPDATE + (sometimes) 1 UPDATE on `audioclip` | none |
| `POST /share/{id}/send-share/` | `UserInteraction.get_or_create('share')` (bumps counter) + `ShareEvent.create` | 1 SELECT + 1 INSERT-or-UPDATE + 1 INSERT + 1 UPDATE on `audioclip` | none |
| `POST /comments/` (and `PATCH/DELETE`) | `serializer.save()/delete()`; `Comment.save()` and `delete()` mutate `audioclip.comment_count` via `F()` if top-level | 1 SELECT + 1 INSERT + 1 UPDATE on `audioclip` (top-level only) | none |
| `POST /follow/{id}/toggle-follow/` | `current_user.following.filter().exists()` then `.add()/.remove()` | 1 SELECT + 1 INSERT or 1 DELETE | none |
| `POST /tags/initialize/` | Top-100 liked clips by tag overlap → numpy mean → `user.save()` → `refill_user_feed.delay(user.id, 30)` | 1 SELECT (top-100) + 1 UPDATE on `user` | `fast_feed` queue |
| `GET /suggestions/?category=X` | `CosineDistance` annotation over `(semantic_vector, sem_query)` and `(acoustic_vector, ac_query)`, ordered ascending by sum | 1 SELECT, vector-distance-heavy | none |

The only `transaction.on_commit` in the entire codebase is `views.py:101`. Every other mutation is fire-and-forget within the request thread.

### 1.4 Celery tasks (6 in `backend/app/tasks.py`)

| Task | Queue | Hot-spot |
|---|---|---|
| `process_audio_to_hls(clip_id)` | `heavy_media` (only routed task to this queue) | ML model singletons (`whisper_model`, `embedding_model`, `kw_model`) — **must run `--pool=solo`** or each fork reloads ~1.0–1.5 GB. Reads `clip.original_file` via `default_storage.open()` (object storage), writes HLS via ffmpeg, uploads via `default_storage.save()`. Final `clip.save()` for `status='ready'` and `hls_playlist_url='hls/{clip.id}/master.m3u8'` |
| `refill_user_feed(user_id, count=50)` | `fast_feed` (concurrency 4) | **Short-circuits if `llen >= 20`.** Recomputes per-user context via `calculate_time_decayed_vectors(user)` (a fresh SQL query + numpy). 80/20 exploit/explore + 5 random follow-graph clips + shuffle. `rpush` to Redis. |
| `update_global_metrics` | default `celery` | **Two raw SQL `UPDATE` statements across the whole `audioclip` table every 5 min** — no batching. This is the explicit "will lock on large tables" risk flagged in `AGENTS.md` and confirmed at `tasks.py:633-658` |
| `evolve_long_term_user_baselines` | default `celery` | Iterates **all** active users (`.iterator(chunk_size=100)`), recomputes vectors, `bulk_update(batch_size=100)`. **No try/except per user** — one bad row fails the rest |
| `scrape_and_import(source_name, limit, clip_length)` | default `celery` | Imports 3rd-party audio, then enqueues `process_audio_to_hls.delay(clip.id)` for each |
| `calculate_time_decayed_vectors(user, limit=50)` | helper, not a task | The actual recommendation core. Pulls last 50 `UserInteraction`s, computes per-row weight `time_weight * comp_weight * intent_weight`, blends with `long_term_*` at `ALPHA=0.7` |

Two dead helpers exist (`calculate_dynamic_user_vector` at `tasks.py:409-441` and `calculate_blended_query_vectors` at `tasks.py:443-496`); `views.py:32` imports the second but never calls it. The active function is `calculate_time_decayed_vectors` (lines 564-630).

### 1.5 MinIO / S3 architecture (now mature, not aspirational)

`docs/minio-s3-architecture.md` and the new AGENTS.md "MinIO / S3" section document this; the salient facts:

- `STORAGES["default"]` (`settings.py:256-290`) is `storages.backends.s3.S3Storage` with `addressing_style="path"`, `default_acl=None`, `querystring_auth=True`.
- **Two URL classes** (mandatory, do not collapse):
  - `hls/{clip_id}/...` → **unsigned public** (`get_hls_playback_url` in `media_urls.py:43-59`). Bucket-policy anonymous `download` on `hls/`. Required because HLS relative references don't carry query-string auth per RFC 3986.
  - `uploads/...` → **signed, 1 h TTL** (`get_signed_media_url` in `media_urls.py:62-92`).
- `PUBLIC_MEDIA_ENDPOINT_URL` is the browser-facing endpoint (CDN or `localhost:9000`); `AWS_S3_ENDPOINT_URL` is the internal Compose endpoint. **Never** let the browser resolve `minio:9000`.
- HLS must use `-hls_segment_type mpegts` (not `fmp4`); Chrome MSE rejects FFmpeg's fMP4 AAC config with `DECODER_ERROR_NOT_SUPPORTED`. This is the load-bearing detail of the HLS pipeline and the reason the ffmpeg call at `tasks.py:255-265` is non-negotiable.
- CORS preflight is wired via MinIO env vars + `CORS_ALLOW_HEADERS` including `Range` and `CORS_EXPOSE_HEADERS` exposing `Content-Range` / `Accept-Ranges` (`settings.py:40-51`).

### 1.6 Recommendation engine (actual formula)

`refill_user_feed` (`tasks.py:498-562`) computes, per candidate clip, the composite:

```
composite_score = (vector_similarity * 0.45)
                + (avg_completion_rate * 0.30)
                + (engagement_velocity * 0.25)
```

where `vector_similarity = 1.0 - (sem_dist + ac_dist) / 4.0` (sum of two cosine distances, divided by a curious 4.0). 80% exploit + 20% explore, with 5 follow-graph clips wedge, then `random.shuffle` before `rpush`.

`calculate_time_decayed_vectors` (`tasks.py:564-630`) weights each recent interaction by `1 / (1 + log1p(hours_ago)) * completion_rate * intent_sign` where `intent_sign = 1.5` for like/share, `-0.5` for skip with completion_rate `< 0.2`, else `1.0`. Result blended with `long_term_*` at `ALPHA = 0.7` (70% recent / 30% baseline).

### 1.7 Verification scripts (already exist; not aspirational)

- `scripts/verify_minio_deployment.sh` — 10 checks against MinIO bucket / policy / CORS / playback.
- `scripts/test_minio_edge_cases.py` — concurrent reads, multipart, signed-URL expiry.
- `scripts/verify_clip_url.sh` — quick 200 + preview for any HLS URL.
- `scripts/verify_decoder_rootcause.sh` — confirms `47401111` sync byte (MPEG-TS), not stale fMP4.
- `scripts/verify_hls_playback.html` — isolated hls.js test page.

These are the **only** integration tests in the repo (no pytest, no CI test step beyond `manage.py test backend.app` which is empty in practice — see §3.2).

---

## 2. What Has Changed Since the Original Doc Was Written

A directed diff of the prior doc against today's codebase, restricted to factual claims.

| Claim in original doc | Reality today | Status |
|---|---|---|
| "Synchronous increments on `AudioClip` table" via `UserInteraction.save()` | Still true. `models.py:166-190` executes `AudioClip.objects.filter(pk=...).update(likes=F('likes')+1)` for `like/share/skip` state changes | **Unchanged, still the #1 lock risk** |
| `Comment.save()` and `delete()` mutate `audioclip.comment_count` via `F()` | Still true, but **only for top-level comments** (`parent_id IS NULL`). Replies don't bump the counter (`models.py:119-129`) | Subtler than the doc described |
| "Synchronous `refill_user_feed(user_id, count=10)` inside HTTP request thread" | **Was** true. **Now**: `refill_user_feed.delay(user_id, count=40)` — async dispatch only (`views.py:130`). Same with the `< 15` threshold case (`views.py:140`) | **Fixed** — but the fallback is empty-feed response, not a "cached trending list" as the doc proposed. There is no trending cache yet |
| `update_global_metrics` "raw SQL mass updates every 10 minutes" | Was 10 min, **now 5 min** (`settings.py:222-226`). Still raw SQL, still no batching | **Higher frequency, same risk** |
| `evolve_long_term_user_baselines` "iterates through active users daily" | **Now hourly** (`settings.py:227-230`). Still no per-user error isolation | **Higher frequency, same fragility** |
| HLS "Stored under `media/hls/{clip_id}/` on local disk" | **Wrong now.** HLS lives in object storage under `hls/{clip_id}/...`; served via MinIO bucket policy (`media_urls.py:43-59`, `tasks.py:255-310`) | **Resolved by MinIO integration** |
| "Django REST Framework / Synchronous request/response / Celery + Redis" stack | Still true. **No Channels, no daphne, no SSE, no WebSocket** | **Unchanged** |
| DRF throttle rates not specified | Now explicit: `anon 100/hour, user 1000/hour` (`settings.py:317-324`) — hourly buckets, no burst protection | **Partially addressed** |
| "Will lock on large tables; needs batching at scale" (`update_global_metrics`) | Confirmed and unfixed | **Open** |
| `transaction.on_commit` for upload (`process_audio_to_hls`) | Still single-use at `views.py:101` | **Unchanged** |
| DRF endpoint list in the AGENTS doc matches reality | Mostly — but **`/health/`, `/ready/`, `/metrics/` are also present** (`urls.py:9-13`); `GET /comments/?clip=X` works (DRF `DjangoFilterBackend` with `filterset_fields=['clip','parent']`); `RegisterView` lives at `/auth/register/`, not `/auth/register/` (path is `auth/register/` per `urls.py:25` mounted at `''`) | **Endpoint map clarified** |
| "Counter increments via F() expressions on likes/shares/skips" | **Still true**, but **`'view'` is intentionally excluded** from the `field_map` in `UserInteraction.save()`. This was undocumented in the original | **Subtle fix** |
| "All Celery Beat tasks run on default queue" | Still true except `process_audio_to_hls` and `refill_user_feed` (routed). The other three (`update_global_metrics`, `evolve_long_term_user_baselines`, `scrape_and_import`) run on `celery` | **Unchanged** |
| No service layer | Still true. There is no `backend/app/services/` directory. `backend/app/tests/test_services/` is an empty directory | **Unchanged, blocker for safe refactor** |
| `request.user.following.filter().exists()` for follow checks | Still true at `views.py:567-616` | **Unchanged** |
| **No mention of MinIO / S3 / object storage in original doc** | Major change: the **entire media pipeline has been reworked onto S3-compatible object storage** (see `docs/minio-s3-architecture.md` and `docs/stateful-media-storage-at-scale.md`). `STORAGES["default"]` is `S3Storage`. Two ACLs: `hls/` public, `uploads/` private | **New in repo; original doc is silent** |
| **No mention of `pgvector` HNSW indexes** | Present at `models.py:84-103` (`m=16, ef_construction=64`, `vector_cosine_ops`). 384-dim semantic and 128-dim acoustic | **New in repo; original doc is silent** |
| **No mention of `FIELD_ENCRYPTION_KEY` Fernet for email** | Present at `models.py:18-22, 37-43`. Hard-fails at import time if the key is unset | **New in repo; original doc is silent** |
| **No mention of allauth + dj-rest-auth registration pipeline** | Present (`INSTALLED_APPS` lines 81-87). Unused in practice for the social login flow | **New in repo; original doc is silent** |
| **No mention of `django_prometheus` / `/metrics/`** | Exported but unconsumed. `health.py` and `ready.py` exist and wire `/health/` and `/ready/` | **New in repo; original doc is silent** |

**Net of the diff:** the foundational argument (PostgreSQL will lock under hot-row contention; telemetry belongs in a stream; Redis Streams is the right Phase 1 broker; preserve a service boundary) is correct and load-bearing. The doc's *implementation specifics* are partially out of date and undercount the work that has happened in storage, vectors, and the media pipeline.

---

## 3. Gap Analysis: Current State vs. 10k Live Users

### 3.1 What "10k live users" actually means in this product

Use case is a **short-form audio reel app** (TikTok-for-ears, per the original product brief). For a 10k-concurrent target, derived sizing:

| Metric | Assumption | Value |
|---|---|---|
| Concurrent users (peak) | target | **10,000** |
| Implied DAU (concurrent ~5% of DAU) | peak | ~200,000 DAU |
| Active session length | typical social app | 12 min |
| Reels per active session | 1 reel / 30 s | ~24 reels |
| Reel listen time, average | 50% complete + 50% skip at <2 s | ~30 s |
| Telemetry events per user per minute | 1 view + ~1 skip every 30 s | ~4 events/min |
| Peak QPS — telemetry writes | 10k × 4 / 60 | **~670 events/s** |
| Peak QPS — feed reads | 10k / 180 s avg refresh | **~55 feed/s** |
| Peak QPS — likes/shares | 10k × 0.5 likes/min / 60 | **~83 likes/s** |
| Peak uploads (0.1% of users) | 10 concurrent HLS pipelines | 10 clips / min |
| HLS bandwidth (128 kbps stereo AAC) | 10k × 128 kbps | **~1.28 Gbps** egress (1.5–2 Gbps realistic with seeks/retries) |
| PG connections — gunicorn | 4 web workers × 4 threads = 16 | 16 long-lived (`conn_max_age=600`) |
| PG connections — celery | 3 web-tier workers + 1 feed (concurrency 4) + 1 media (solo) + 1 beat = up to ~8 | 8 |
| PG `max_connections` (default) | no override | 100 |
| Headroom | 100 − 24 = 76 free | only ~3× safety factor for spikes and migrations |

**Verdict:** at 10k concurrent, the codebase as it stands will not fail catastrophically — it will degrade in ugly, specific ways (see §3.2). The system has enough *headroom* to reach 10k with disciplined engineering, but **not enough to reach 50k without architectural change**.

### 3.2 The 12 specific failure modes at 10k concurrent, ranked

1. **Hot-row lock contention on viral clips (the "F() problem").** `UserInteraction.save()` does `AudioClip.objects.filter(pk=...).update(likes=F('likes')+1)` (`models.py:177-190`). On a viral clip receiving ~50 likes/sec at 10k concurrent, every like serializes on the same `AudioClip` row. Each lock takes ~1–5 ms even with `select_for_update()` on the toggle path; 50 of them per second = 50–250 ms of accumulated lock time, plus 50 connections pinned. **Symptom:** 504s on `/toggle-like/` and `/log-telemetry/?action_type=like` only for that clip, but every other request sharing those connections also queues. **This is the original doc's central thesis and it remains true.**
2. **`update_global_metrics` table-wide raw SQL.** Every 5 min (`settings.py:222-226`), two `UPDATE` statements scan every `audioclip` row to recompute `engagement_velocity` and `avg_completion_rate` (`tasks.py:633-658`). At 10k concurrent with ~50k–200k clips, this is 1–5 s of ACCESS SHARE locks every 5 min. The bigger cost is the `AVG(completion_rate) FROM app_userinteraction WHERE clip_id = app_audioclip.id` subquery — an O(N) correlated subquery. **Symptom:** 2–10 s latency spikes every 5 min; if a large `audioclip` set is being updated by ingestion simultaneously, the lock escalates and Postgres CPU spikes.
3. **Autovacuum starvation on `userinteraction`.** Every `update_or_create` on `userinteraction` produces a dead tuple. At 670 events/s × 86,400 s = ~58 M dead tuples/day, plus the toggle path's `select_for_update` produces two. `autovacuum_vacuum_scale_factor=0.2` (default) on a 58 M-row table means ~12 M-row vacuum per run, easily 30+ min. **Symptom:** table bloat, slow index scans, eventually index-only-scan fallback. Critical at 100k DAU, visible at 10k concurrent.
4. **Connection pool exhaustion under spike.** No PgBouncer. `conn_max_age=600` keeps 24 long-lived connections. A spike of 80 concurrent requests exceeds `max_connections=100` minus the long-lived ones; new requests get `OperationalError: too many clients`. **Symptom:** 500s on auth, login, comments — *not* the high-velocity endpoints that look like the obvious problem.
5. **`minio` and `minio-init` have no resource limits** (`docker-compose.yml:70-124`). Under 1.5–2 Gbps HLS egress, MinIO will hit CPU/MEM caps. **Symptom:** segment downloads slow to 200–500 ms, hls.js stalls and re-buffers, frontend users blame the backend.
6. **Redis 512 MB `maxmemory` with `allkeys-lru`.** At 10k concurrent with hot users, `user_feed:*` lists (avg ~50 entries × 36 bytes/UUID ≈ 1.8 KB each → 18 MB for 10k queues) plus Celery broker keys (each task ~1 KB; 670 events/s + 55 feed refills/min + 10 uploads/min = ~3,000 keys/s) means **broker pressure alone is ~3 MB/s of churn**. With stream trimming absent (see point 9), Redis RAM fills in hours. `allkeys-lru` then evicts *active* feed lists — including yours. **Symptom:** `FastFeedViewSet` returns 0 results for a user whose queue was just evicted, which triggers a `refill_user_feed` task which itself uses Redis, which itself triggers eviction. Self-feeding collapse.
7. **No reverse proxy / CDN.** Gunicorn serves HLS segments through Python (`x_sendfile` is not configured for object storage; the `MEDIA_URL='/media/'` route was explicitly removed per `urls.py:27-32`). At 10k concurrent × 1.5 Gbps HLS = 1.5 Gbps through Python, every worker thread blocked on egress. **Symptom:** the "you can only fit 4 workers × 4 threads = 16 concurrent requests, but each request might be a 1 MB HLS segment" problem. This is the single biggest capacity risk.
8. **No HSTS, CSP, X-Content-Type-Options, SECURE_SSL_REDIRECT** (`settings.py:96` enables `SecurityMiddleware` but with all defaults). At 10k concurrent, the absence of TLS-only headers and clickjacking protection becomes a real attack-surface issue. **Symptom:** nothing observable until a pentest or a browser deprecation. Mitigate pre-launch.
9. **No telemetry batching in the frontend.** `main.jsx:1103-1108` calls `API.logTelemetry(...)` directly, with `.catch(()=>{})` swallowing failures. At 10k concurrent × 1 telemetry per ~30 s, that's ~330 outbound requests/s from the browser just for telemetry, each competing for the same connection pool as feed reads. **Symptom:** extra API pressure, plus silent loss of telemetry on network blips.
10. **Beat-managed `evolve_long_term_user_baselines` iterates all users with no per-user error handling.** At 200k DAU this runs in ~10 min (chunked at 100, with vector math per user), but one bad row fails the whole loop (`tasks.py:660-673`). **Symptom:** silent starvation of long-term baselines; recommendation drift toward popular content only.
11. **HLS URL is generated per-request by the serializer** (`serializers.py:51-52`), calling `get_hls_playback_url(obj.hls_playlist_url)` every time. The `FeedClipSerializer` runs on every `/feed/` response, computing 10 URLs per call. At 55 feed reads/s that's 550 URL computations/s; not a CPU crisis, but the design couples the API tier to the CDN origin's URL format. **Symptom:** if `PUBLIC_MEDIA_ENDPOINT_URL` changes, every serializer call returns a stale-looking URL until cache invalidates.
12. **Inbox uses 30 s polling** (`main.jsx:2120-2126`). At 10k concurrent that's 333 polls/s to `/share/unread-count/` from already-busy clients. Cheap individually, but it's exactly the kind of avoidable QPS that disappears when you fix it. **Symptom:** multiplier on top of the feed QPS.

### 3.3 What the codebase does *not* need to become for 10k

Listed explicitly so we don't overshoot:

- **No Kafka.** At 10k concurrent, peak ~670 telemetry events/s — Redis Streams at this scale is a known, well-trodden design. Kafka becomes worth it at ~10k+ events/s sustained across many consumers, or when replay windows exceed what Redis can hold in RAM (multi-day). Not now.
- **No ClickHouse / OLAP sink.** All analytics queries that exist today (`update_global_metrics`, `evolve_long_term_user_baselines`) fit in Postgres with indexing. A separate OLAP store is a Phase 2 concern, not a 10k-concurrency one.
- **No microservices decomposition.** Django modular monolith is fine. The split into separate services buys you independent deploys, which you don't have a deploy-pipeline problem for yet.
- **No gRPC, no service mesh.** HTTP+JSON across one Django process pool is fine.
- **No CDC / Debezium.** The transactional outbox pattern (recommended in the original doc) is the right Phase 2 move. CDC is Phase 3.
- **No Kubernetes / multi-region.** Single Compose stack with one `web` (scale to 2–3 replicas), one `celery_feed` (2 replicas), one `celery_media` (2 replicas) carries 10k concurrent.

---

## 4. North-Star Target Architecture (at 10k concurrent)

```
                       ┌──────────────────────────────────────────┐
                       │  CDN / Object-Storage Public Frontend   │
                       │  (CloudFront / R2 public bucket /       │
                       │   MinIO behind nginx → CDN)             │
                       │  serves hls/* unsigned, cache 1y         │
                       └──────────────────────────────────────────┘
                                       ▲
                                       │  hls-only (public, unsigned)
                                       │
   ┌──────────────────────┐    ┌───────┴────────┐    ┌─────────────────────┐
   │ Client (browser)     │    │  Nginx reverse │    │  Object storage     │
   │ - bundles telemetry  │    │  proxy         │    │  S3 / R2 / MinIO    │
   │   30s batches, sends │    │  /api/*        │    │  hls/  (public)     │
   │   on visibilitychange│    │  static → CDN  │    │  uploads/ (signed)  │
   │   + offline IDB queue│    └───────┬────────┘    └─────────────────────┘
   └────────┬─────────────┘            │
            │  /api/*  (JWT)           │
            ▼                          ▼
   ┌──────────────────────────────────────────────────────────┐
   │  Django API  (gunicorn, 2–3 replicas × 4w × 4t)         │
   │  - /interactions/* writes to Redis Stream, returns 202  │
   │  - /feed/ reads Redis list, refills on miss            │
   │  - /clips/ writes DB row, returns presigned PUT URL     │
   │  - /share/, /comments/, /follow/ all go through        │
   │    InteractionService (one service layer, see §6 P0.2)  │
   └──────────────────────────────────────────────────────────┘
            │              │              │              │
            ▼              ▼              ▼              ▼
   ┌──────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────┐
   │ PostgreSQL   │ │ Redis      │ │ Redis      │ │ Presigned    │
   │  primary     │ │ Streams    │ │ cache +    │ │ upload URL   │
   │  (pgvector,  │ │ telemetry  │ │ user_feed  │ │ (S3/R2)      │
   │   HNSW)      │ │ events:    │ │ :* lists   │ │              │
   │              │ │ clip.*     │ │ + sorted   │ │              │
   │              │ │ user.*     │ │ sets for   │ │              │
   │              │ │ share.*    │ │ dedup      │ │              │
   └──────────────┘ └────────────┘ └────────────┘ └──────────────┘
            ▲              ▲
            │              │
            │  bulk insert │  consume
            │              │
   ┌────────┴────────┐  ┌──┴──────────────────────────────────┐
   │ Celery: counter │  │ Celery: feature + counter           │
   │ batcher (5 min) │  │  - feature-engineers:               │
   │  - batch UPSERT │  │     consume stream → user:context:* │
   │    audioclip    │  │  - counter-batcher:                 │
   │    counters     │  │     accumulate INCRBY → flush UPSERT│
   │                 │  │  - feed-refiller (fast_feed):       │
   │                 │  │     read user:context:*             │
   └─────────────────┘  └─────────────────────────────────────┘
```

The deltas from the original doc's target diagram are small and additive:

- **CDN in front of HLS** (was implicit; now explicit and required).
- **Presigned direct upload** (new — saves 100% of upload bandwidth from API tier; see §6 P1.3).
- **Two streams**, not one: `clip.*` for per-clip hot events (likes, shares, skips), `user.*` for per-user context events (view, completion). Different consumer groups, different SLAs.
- **`user:context:*` materialized in Redis** (per the original doc's "Stage 5") — the recommendation engine reads from there, not from `userinteraction`.
- **Counter batcher replaces `update_global_metrics` raw SQL** for the increment path; the 5-min cadence becomes a 5-min UPSERT flush, not a table-wide recompute.

---

## 5. Capacity Plan: 10k Concurrent

### 5.1 Compute sizing (single Compose stack, 2–3 web replicas)

| Service | Replicas | CPU limit (each) | Mem limit (each) | Rationale |
|---|---|---|---|---|
| `db` | 1 (no replica yet at 10k) | 4 | 4 GB | Bumped from 2/2 GB — `work_mem`, `shared_buffers`, `effective_cache_size` all want headroom under the 5-min `update_global_metrics` job |
| `redis` | 1 | 2 | 2 GB | Bumped from 1/1 GB. Need 2 GB to hold 10k `user_feed:*` (≤ 50 MB) + Celery queues + streams + dedup keys, with `volatile-lru` (see §6 P0.5) |
| `minio` | 1 | 4 (unbounded today) | 4 GB (unbounded today) | Bounded; MinIO + 1.5 Gbps egress requires explicit cgroup limits |
| `web` | 2–3 | 2 | 1 GB | Linear scale; behind nginx |
| `celery` (default) | 1 | 1 | 1 GB | Handles scraping, `evolve_long_term_user_baselines`, and the new counter-batcher |
| `celery_feed` | 2 | 2 | 1 GB | Doubled — feed reads scale with `user_count`, not events |
| `celery_media` | 2 | 4 | 2 GB | Two solo-process workers in parallel; HF models are baked in once per image |
| `celery_beat` | 1 | 0.5 | 256 MB | Unchanged |
| `nginx` (NEW) | 1 | 1 | 512 MB | New — terminates TLS, proxies `/api/*` to web, serves `/static/*`, hands off `/hls/*` to object storage / CDN |
| `prometheus` + `grafana` (NEW) | 1 each | 0.5 / 0.5 | 512 MB / 512 MB | New — observability (§5.4) |

**Total per-replica:** ~22 vCPU, ~14 GB RAM (without counting the new `db` size of 4 GB, which is a one-off for now). Fits on a single beefy box or 2 m6i.2xlarge instances.

### 5.2 Postgres sizing at 200k DAU / 10k concurrent

| Setting | Recommended value | Why |
|---|---|---|
| `max_connections` | **200** (currently default 100) | Headroom for migrations, psql, and spike surge. The original 100 is too close to (16+8) × 2 = 48 baseline + spikes |
| `shared_buffers` | 1 GB (25% of 4 GB) | Standard tuning |
| `effective_cache_size` | 3 GB | Lets planner choose hash joins for vector queries |
| `work_mem` | 32 MB | Vector distance queries benefit; ORDER BY composite_score on 100k clips needs to sort in memory |
| `maintenance_work_mem` | 256 MB | Speeds up HNSW builds if we ever rebuild |
| `random_page_cost` | 1.1 | SSD-backed volumes |
| `effective_io_concurrency` | 200 | NVMe SSD |
| `autovacuum_vacuum_scale_factor` | 0.05 (default 0.2) | Critical — `userinteraction` will bloat fast at 670 events/s |
| `autovacuum_analyze_scale_factor` | 0.025 | Same |
| `autovacuum_max_workers` | 4 | Default is 2; bump for parallel table processing |
| `statement_timeout` | 30 s | Kills rogue queries from `update_global_metrics` |
| `idle_in_transaction_session_timeout` | 60 s | Catches forgotten transactions |

Add a `PgBouncer` in transaction-pool mode at 25 pool size in front of `web` and `celery_*` (NOT `celery_media` — it holds long-running HLS tasks). **Phase 1, see §6 P0.6.** Skip read-replicas at 10k; add when `EXPLAIN` shows read pressure (it won't yet).

### 5.3 Redis sizing at 10k concurrent

| Concern | Today | At 10k |
|---|---|---|
| `user_feed:*` lists (10k users × ~50 entries × 36 B) | ~18 MB | 18 MB |
| `user:context:*` vector keys (10k × 384 float32 × 4 B + 128 × 4 B) | 0 (not implemented) | 19 MB (2 vectors/user) |
| `clip:cnt:*` counter keys (50k clips × 3 counters × ~80 B) | 0 (counters in PG) | 12 MB |
| Celery broker keys (3k/s × 1 KB × ~30 s residence) | ~90 MB today | 90 MB |
| Dedup sets (`processed_event:{event_id}`, 24 h TTL × ~58 M/day) | 0 (not implemented) | ~200 MB (TTL-evicted) |
| Stream history (`stream:interaction.events` with `MAXLEN ~ 50000`) | 0 (not implemented) | ~20 MB |
| Working set | ~108 MB | **~360 MB** |

→ **2 GB Redis with `volatile-lru`** (not `allkeys-lru`) gives us 5× headroom and a clear eviction policy: only expire TTL'd keys, never evict live `user_feed:*`.

### 5.4 Observability at 10k (minimum bar)

| Signal | Tool | Alert threshold |
|---|---|---|
| Postgres replication lag (when added) | `pg_stat_replication` via `postgres_exporter` | > 5 s |
| Postgres lock wait time | `pg_locks` + `pg_stat_activity` | any single lock > 1 s |
| Postgres autovacuum backlog | `pg_stat_user_tables` (n_dead_tup) | > 1 M dead tuples for > 30 min |
| Postgres `max_connections` utilization | `pg_stat_activity` count | > 80% of 200 |
| Redis memory | `redis-cli info memory` | > 80% of 2 GB |
| Redis stream `XPENDING` | per consumer group | > 10,000 pending |
| Celery `fast_feed` queue length | `flower` or custom | > 100 sustained 5 min |
| Celery `heavy_media` queue length | `flower` | > 20 sustained 10 min |
| Gunicorn worker count healthy | gunicorn `/health/` | any replica non-200 > 60 s |
| `/metrics/` 4xx/5xx rate | django-prometheus | > 1% sustained 5 min |
| HLS playback failure rate | frontend (post-fix) | > 0.5% over 5 min |
| Stream consumer lag | `XPENDING`/`XLEN` ratio | > 2× recent ingest rate |
| Dead-letter queue depth | `XLEN stream:*:dlq` | > 0 |

The minimum is `prometheus + grafana + alertmanager + node_exporter + postgres_exporter + redis_exporter` (~250 MB of new infra). No OTel yet — log lines in JSON to stdout are enough until 50k DAU.

### 5.5 Cost-vs-risk gates (so we know when to add capacity)

| Metric | Trigger to add capacity | Action |
|---|---|---|
| PG CPU > 60% sustained 15 min | add read replica, move `/suggestions/` there first | Phase 1.4 |
| Redis memory > 70% | bump to 4 GB; if frequent, change eviction policy | tune |
| HLS bandwidth > 50% of origin capacity | enable CDN, point browsers at CDN | Phase 1.2 |
| `fast_feed` queue lag > 60 s | add `celery_feed` replica | horizontal scale |
| Lock contention > 100 ms p99 on `AudioClip` rows | the F() removal work is overdue | hard requirement |
| Frontend 4xx/5xx on telemetry > 1% | investigate client-side batching | Phase 2.1 |

---

## 6. The Phased Plan: 0 → 10k Concurrent

Phases are **ordered by safety**, not by chronology. Each phase must be **measurable** and **reversible**. P0 fixes are the bare minimum to *not break* under 10k concurrent; P1 removes the load-bearing failure modes; P2 carves out the event-driven migration for the 100k path.

Each phase lists: **what to do** (concrete tasks), **why** (the failure mode it addresses), **how to verify** (the metric that must move), and **rollback** (the revert plan).

### P0 — Stabilize (no behavior change, only safer defaults)

These six fixes protect the existing system from collapsing under the 10k-concurrent load *without* changing any user-visible behavior. They are reversible with a config rollback.

#### P0.1 — Bound Postgres connections and tune autovacuum
- **Why:** #4 and #3 in §3.2 — connection exhaustion, autovacuum starvation.
- **Tasks:**
  1. Add a `command:` override to the `db` service in `docker-compose.yml` (or a `postgres.conf` mount) setting `max_connections=200`, `autovacuum_vacuum_scale_factor=0.05`, `autovacuum_analyze_scale_factor=0.025`, `autovacuum_max_workers=4`, `work_mem=32MB`, `random_page_cost=1.1`, `statement_timeout=30s`, `idle_in_transaction_session_timeout=60s`.
  2. Add a `pg_stat_statements` shared preload so we can see hot queries.
  3. Add a migration to attach a `CHECK (likes >= 0)` constraint on `audioclip.likes` (TODO.md:20) — this is cheap insurance and validates the F() math.
- **Verify:** `SELECT count(*) FROM pg_stat_activity` < 100 sustained; `n_dead_tup` for `userinteraction` < 200k at all times.
- **Rollback:** revert env vars and remove the `command:` override.

#### P0.2 — Introduce a service-layer boundary (no behavior change)
- **Why:** This is the **load-bearing** refactor that unlocks every later phase. The original doc's "Stage 2: Interface Boundary Isolation" — never done.
- **Tasks:**
  1. Create `backend/app/services/__init__.py` and four thin modules:
     - `services/interactions.py` — `record_view`, `record_like_toggle`, `record_skip`, `record_share`. **Each function takes the same args as today's ORM call, and currently delegates to the ORM** — no behavior change.
     - `services/comments.py` — `create_comment`, `update_comment`, `delete_comment`. Same: delegate to ORM today.
     - `services/follows.py` — `toggle_follow`.
     - `services/uploads.py` — `finalize_upload`, `get_signed_put_url` (no-op for now; P1.3 will fill this).
  2. **Do not** remove the `F()` counter logic from `UserInteraction.save()` yet. Just call the service function from the view, and have the service call the ORM. This is the *boundary*, not the *replacement*.
  3. Wire all 8 viewsets to call the service functions instead of ORM directly.
- **Why this is safe:** Identical SQL is issued, identical counters are bumped, identical transactions. Zero behavior change.
- **Verify:** existing `/interactions/toggle-like/`, `/log-telemetry/`, `/comments/`, `/follow/`, `/share/send-share/` all return identical payloads. Run a 1-minute `k6` smoke against the staging environment with 10 RPS and compare response shapes to pre-refactor.
- **Rollback:** revert the call-site changes; service functions can sit unused.

#### P0.3 — Fix `minio` resource limits
- **Why:** #5 in §3.2 — unbounded cgroup will OOM-kill under 10k concurrent HLS egress.
- **Tasks:** add `deploy.resources.limits.cpus: '4'` and `memory: 4G` to the `minio` service in `docker-compose.yml`. Add equivalent to `minio-init` (less critical — it runs once).
- **Verify:** `docker stats` shows MinIO bounded; `mc admin info` healthy.
- **Rollback:** remove the limits.

#### P0.4 — Fix Redis eviction policy and add a `nofile` ceiling
- **Why:** #6 in §3.2 — `allkeys-lru` evicts live `user_feed:*` lists under memory pressure. Self-feeding collapse.
- **Tasks:**
  1. Change `redis-server` command in `docker-compose.yml` to `--maxmemory-policy volatile-lru` and bump `--maxmemory 2gb`.
  2. Add a `ulimit nofile=65536` to the `redis` service to match Postgres.
  3. Add a startup sanity check: if `INFO memory` reports `maxmemory_hits > 0` after 1 hour of warmup, the eviction policy is wrong — log a warning. (Don't fail boot; the warning is enough.)
- **Verify:** `INFO stats` `evicted_keys` should stay near 0 for keys without TTL; `INFO memory` `used_memory < 1.5 GB` under 10k concurrent load.
- **Rollback:** revert command.

#### P0.5 — Enable HSTS, CSP, X-Content-Type-Options, SECURE_SSL_REDIRECT
- **Why:** #8 in §3.2 — pre-launch hardening that is otherwise a security review finding.
- **Tasks:** set in `settings.py` (gated on `DEBUG=False`):
  - `SECURE_SSL_REDIRECT = True`
  - `SECURE_HSTS_SECONDS = 31536000; SECURE_HSTS_INCLUDE_SUBDOMAINS = True; SECURE_HSTS_PRELOAD = True`
  - `SECURE_CONTENT_TYPE_NOSNIFF = True`
  - `X_FRAME_OPTIONS = 'DENY'` (default; explicit)
  - `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')` (only when behind a TLS-terminating proxy)
  - Add a strict CSP via `django-csp` (new dep) once the frontend is ready to receive the right headers.
- **Verify:** Mozilla Observatory grade ≥ B+.
- **Rollback:** toggle the env flags.

#### P0.6 — Add a basic burst-aware throttle
- **Why:** #4 in §3.2 — DRF hourly buckets can't stop a 10k burst.
- **Tasks:**
  1. Switch to `rest_framework.throttling.ScopedRateThrottle` on the high-velocity endpoints:
     - `interactions/*`: 60 req/min per user
     - `feed/`: 30 req/min per user
     - `clips/`: 6 req/hour per user (upload cap)
  2. Keep `AnonRateThrottle` and `UserRateThrottle` for the global per-hour cap.
  3. **Do not** add `django-ratelimit` (overkill); `ScopedRateThrottle` uses the same Redis cache.
- **Verify:** `k6` burst of 200 likes in 5 s from one user → 429 on 61st, no 5xx, no DB load.
- **Rollback:** remove the scope assignments.

### P1 — Offload the load-bearing bottlenecks

These are the steps that materially change system behavior — but each one is contained and reversible.

#### P1.1 — Replace `update_global_metrics` raw SQL with event-driven counter pipeline
- **Why:** #1 and #2 in §3.2 — the table-wide `UPDATE` is the most dangerous operation in the codebase.
- **Tasks:**
  1. **Add Redis distributed counters.** Each like/share/skip event bumps a Redis key: `INCRBY clip:{id}:likes 1` (or `-1` on unlike). The API path (`toggle-like`, `log-telemetry`, `send-share`, `register-skip`) reads the current value from Redis on read, falling back to Postgres if the key is missing.
  2. **Add a counter-batcher Celery task** `flush_counters_to_pg`, scheduled every 60 s (down from 5 min). It does `SCAN` for `clip:*:likes/shares/skips`, computes the deltas, and runs a single `UPDATE audioclip SET likes = likes + %s WHERE id = %s` per dirty key (use `INSERT ... ON CONFLICT DO UPDATE` if you switch to an upsert model). Mark the Redis key as clean by deleting it after PG commit.
  3. **Keep `update_global_metrics` for the derived metrics** (`engagement_velocity`, `avg_completion_rate`) but rewrite as a **batched** update: process 2,000 clip IDs per chunk, 1 s `pg_sleep` between chunks. Replace the correlated subquery for `avg_completion_rate` with a precomputed `userinteraction` materialization (see P2.1).
  4. Add a per-clip `LikeCountCache` model layer (or just a Redis-as-source-of-truth + DB-as-snapshot pattern) so that the `/feed/` serializer can show real counts without a SELECT.
- **Why this is the right cut:** Redis `INCRBY` is O(1) and lock-free. The hot-row problem vanishes — the lock moves from "every write hits the same row" to "every N minutes, one bulk UPDATE per dirty key."
- **Verify:** k6 with 200 likes/sec on a single viral clip — Postgres `pg_stat_user_tables.n_tup_upd` for `audioclip` should stay < 10/min. `redis-cli MONITOR` shows the INCRBY pattern; `INFO stats` shows `evicted_keys == 0` (counters never expire). `fast_feed` queue lag stays < 5 s.
- **Rollback:** revert the service-layer changes; the F() logic in `UserInteraction.save()` is untouched (P0.2 preserves it). Worst case: the F() side-effect still fires on `UserInteraction.save()` if the Redis write fails — counters will *over-count* during the rollback window, not under-count.

#### P1.2 — Add nginx reverse proxy and HLS CDN offload
- **Why:** #7 in §3.2 — Python is not a static file server at 1.5 Gbps.
- **Tasks:**
  1. **Add `nginx` service** to `docker-compose.yml`. Configure:
     - `proxy_pass http://web:8000` for `/api/*`, `/auth/*`, `/admin/*`, `/health/`, `/ready/`, `/metrics/`
     - `proxy_pass http://web:8000` for `/static/*` (still via WhiteNoise for now)
     - `proxy_pass http://minio:9000` for `/hls/*` (only if no CDN yet), with `proxy_buffering on; proxy_cache_valid 200 1y;` and proper `Range` handling (`proxy_force_ranges on;` or `proxy_set_header Range $http_range;`).
     - TLS termination with a real cert (Let's Encrypt via `certbot` in a sidecar, or Caddy with auto-TLS).
  2. **HLS bandwidth cap on the API tier:** the `web` service should never serve HLS bytes. Set `client_max_body_size 25m;` on the API proxy (matches current 25 MB upload cap).
  3. **Add CDN** (CloudFront, Cloudflare, or Bunny CDN) in front of `/hls/*`. Origin is the nginx → MinIO chain (or the MinIO bucket directly if you set the bucket policy right). Cache TTL 1 year; `Range` requests must reach the origin.
  4. **Remove the dev bind mount** of project root into `web`/`celery*` (`docker-compose.yml:159, 205, 264`). The bind mount is what makes `MEDIA_ROOT` "work" for local dev; with nginx + MinIO it's not needed and creates confusion about where state lives.
- **Verify:** `curl -I https://yourcdn/hls/{clip_id}/master.m3u8` returns 200, `Cache-Control: public, max-age=31536000, immutable`. `curl -I -H 'Range: bytes=0-1023' .../segment0.ts` returns 206 with `Content-Range`. `k6` load test shows gunicorn workers are now < 5% busy serving static content.
- **Rollback:** disable the nginx service, point the load balancer directly at the `web` service. CDN remains.

#### P1.3 — Add presigned PUT for audio uploads
- **Why:** bandwidth offload, removal of API tier from the upload path. Currently a 10 MB upload is `web (gunicorn thread blocked) → default_storage.save() → S3`. The thread is pinned for the full upload duration.
- **Tasks:**
  1. Add `POST /clips/presign/` returning `{presigned_put_url, clip_id, expires_in: 300}`. Creates the `AudioClip` row in `pending_upload` state (new status value, requires migration).
  2. The frontend uploads directly to S3 with the presigned URL.
  3. On 2xx response from S3, the frontend calls `POST /clips/{id}/finalize/` which transitions the row to `processing` and dispatches `process_audio_to_hls.delay(id)`.
  4. Add a 15-min S3 lifecycle rule deleting the `pending_upload` row's original object if `finalize` is never called.
  5. Update `tasks.py:process_audio_to_hls` to handle `status='pending_upload'` (the original exists but hasn't been finalized — treat as failure).
- **Why now:** this is the *only* way to scale uploads independently of API tier. 10 concurrent uploads of 10 MB each = 100 MB of pinned gunicorn threads under the current design.
- **Verify:** 100 concurrent 10 MB uploads finish in < 30 s with `web` gunicorn workers at < 10% busy. `pg_stat_user_tables.n_tup_ins` for `audioclip` matches the upload rate (one row per upload), but the upload bytes do not transit `web`.
- **Rollback:** keep the current synchronous upload path; `presign/` is a new endpoint. Frontend flag to choose.

#### P1.4 — Implement the transactional outbox for `AudioPublished` and `UserFollowed`
- **Why:** The original doc's "Stage 3" — the prerequisite for the recommendation pipeline becoming truly decoupled. Without an outbox, the recommendation consumer either polls (waste) or depends on the request thread (coupling).
- **Tasks:**
  1. Migration: create `event_outbox` table (UUID PK, `aggregate_type`, `aggregate_id`, `event_type`, `payload JSONB`, `created_at`, `processed_at NULL`). Index on `(created_at) WHERE processed_at IS NULL` (partial index, ~1 KB always-resident).
  2. New model `EventOutbox` with a `publish()` classmethod.
  3. In `services/uploads.py::finalize_upload` and `services/follows.py::toggle_follow`, write the outbox row inside the same transaction.
  4. New Celery task `relay_outbox` (Beat, every 1 s, low CPU) polls the outbox, publishes to Redis Stream, and marks `processed_at = NOW()`. Use `SELECT ... FOR UPDATE SKIP LOCKED` so multiple relay workers can run safely.
  5. Add a dead-letter table `event_outbox_dead` for rows older than 1 hour with `processed_at IS NULL` — alert on row count > 0.
- **Verify:** k6 100 uploads/min → `event_outbox` lag < 5 s. After `relay_outbox` is paused, outbox grows; on resume, drains in < 30 s.
- **Rollback:** disable `relay_outbox`; the outbox writes inside transactions can be no-ops.

#### P1.5 — Carve out `/log-telemetry/` and `/toggle-like/` onto Redis Streams
- **Why:** #1 in §3.2 — the original doc's "Stage 4" — highest-load carver.
- **Tasks:**
  1. Add Redis Streams: `stream:interaction.events` (the wire) and consumer groups `cg:counter-batcher`, `cg:feature-engineers`, `cg:dlq-consumer`.
  2. Refactor the four interaction views to call the service layer, which does `XADD stream:interaction.events * event_id <uuid> schema_version "1.0.0" payload <json>`, then returns `202 Accepted` in < 5 ms.
  3. **Temporarily disable the `UserInteraction.save()` F() side effect** in code path but keep the row write (the row still exists; counters move to Redis via P1.1).
  4. Build a stream-consumer Celery worker (`celery_stream` queue, new service in compose). Each consumer:
     - Reads with `XREADGROUP COUNT 500 BLOCK 5000` (micro-batch)
     - For `cg:counter-batcher`: increments Redis `clip:{id}:likes` etc.
     - For `cg:feature-engineers`: updates `user:{id}:context` (writes the new `VectorField`-serialized vector when completion rate shifts)
     - Both `XACK` after successful processing
  5. Add a DLQ stream `stream:interaction.dlq` for poison messages; consumer moves after 3 retries.
  6. Add a Prometheus metric for stream lag (`XPENDING` per group).
  7. Add `MAXLEN ~ 200000` on every `XADD` to bound RAM (the original doc's "Blind Spot #2").
- **Verify:** `/log-telemetry/` p99 < 10 ms at 1k RPS. `XLEN stream:interaction.events` stays < 250,000 under any load. `XPENDING` per group < 1,000.
- **Rollback:** flip a feature flag to route the service function back to the synchronous path. The stream consumers can be stopped; the `event_id` dedup keys can be flushed.

### P2 — Decouple the recommendation engine

#### P2.1 — Materialize `user:context:*` vectors in Redis; feed reads from there
- **Why:** Original doc's "Stage 5" — eliminates the live SQL query from the feed-refill path.
- **Tasks:**
  1. In the `cg:feature-engineers` consumer (P1.5), maintain a per-user weighted-mean vector in `user:{id}:context:sem` and `user:{id}:context:ac` as serialized 384- and 128-dim float lists.
  2. Update `refill_user_feed` (`tasks.py:498-562`) to read vectors from Redis (with a fallback to `calculate_time_decayed_vectors` if the key is absent — the cold-start path is unchanged).
  3. **Bound the cold-start fallback**: if the user has no Redis context vector *and* the SQL fallback would take > 200 ms, queue the refill for an async worker and return a cached trending list.
- **Verify:** `refill_user_feed` runtime p99 < 100 ms; SQL queries against `userinteraction` from feed refills = 0.
- **Rollback:** feature flag to use SQL fallback.

#### P2.2 — Decouple the 80/20 mix decision from the SQL fallback path
- **Why:** `refill_user_feed` does 4 separate `LIMIT` queries plus `random.shuffle` plus `rpush`. At 55 feed refills/s with 5+ candidates per query, the DB sees 200+ queries/s. Move the candidate pool to Redis sorted sets.
- **Tasks:**
  1. Materialize a global `clip:candidates:exploit` (Redis sorted set, score = composite_score) refreshed every 5 min from a dedicated `rebuild_candidate_pool` task. Refresh writes the top 10,000 clips.
  2. Materialize per-user `user:{id}:candidates:explore` refreshed hourly with the user's "novel" slice.
  3. `refill_user_feed` does `ZREVRANGEBYSCORE` from these sets. SQL only on the cold-start path.
- **Verify:** `pg_stat_user_tables.seq_scan` for `audioclip` from feed refills = 0; refills complete in < 50 ms.
- **Rollback:** revert to the current SQL-driven refill; sets can sit empty.

#### P2.3 — Per-user error isolation in `evolve_long_term_user_baselines`
- **Why:** One bad row currently aborts the whole iteration.
- **Tasks:** wrap the per-user block in `try/except`; log + continue. Add a `last_evolved_at` timestamp on `User` so we can resume from a checkpoint instead of scanning the whole table each hour.
- **Verify:** inject one malformed user JSON; the loop continues and `evolved_count` matches the user count minus 1.
- **Rollback:** trivial.

#### P2.4 — Frontend telemetry batching and offline queue
- **Why:** #9 in §3.2 — 330 unnecessary outbound requests/s, plus silent loss on network blips.
- **Tasks:**
  1. New `frontend/src/lib/telemetry.js`:
     - In-memory ring buffer of pending events.
     - `flush()` sends up to 50 events in one POST to `/interactions/batch/` (new endpoint, see P2.5).
     - `flush()` on `visibilitychange === 'hidden'` (pagehide).
     - On network error: enqueue to `IndexedDB` (via `idb-keyval` — small dep).
     - On app start: drain IndexedDB queue first, then in-memory.
  2. The polling for inbox (`/share/unread-count/`) stays at 30 s but switches to `fetch` with `keepalive: true` and a `BackgroundSync` registration if available.
- **Verify:** k6 10k virtual users running a feed session with 30s dwell → API sees < 30 telemetry RPS (vs 670+ today), `4xx/5xx on telemetry` < 0.1%.
- **Rollback:** revert to direct `logTelemetry()` calls; the batching helper is unused.

#### P2.5 — New `POST /interactions/batch/` endpoint
- **Why:** Companion to P2.4; receives up to 50 events per call.
- **Tasks:**
  1. New view `BatchInteractionView` accepting `{"events": [...]}` (max 50), each event shaped like today's `log-telemetry` payload.
  2. Each event: validate, write to `event_outbox` (P1.4), return `202` with the count.
  3. Apply `ScopedRateThrottle` 30/min/user on the batch endpoint.
- **Verify:** 1 batch call = 50 telemetry events. Total API QPS for telemetry drops by ~50×.
- **Rollback:** keep the legacy endpoints; frontend flag.

### P3 — Polish and harden (for the 50k path; not strictly required for 10k but cheap now)

#### P3.1 — Remove `update_global_metrics` table-wide raw SQL entirely
- **Why:** Once P1.1 is stable and P2.2's candidate pool refresh handles `engagement_velocity`, the 5-min table-wide SQL is redundant.
- **Tasks:** delete the task; have the `flush_counters_to_pg` task also recompute `engagement_velocity` for the top 1,000 clips by velocity change.
- **Verify:** `pg_stat_user_tables` for `audioclip` shows no `UPDATE` statements from a scheduler.

#### P3.2 — Add Prometheus + Grafana + alertmanager
- **Why:** Observability is the difference between "we're degrading" and "we already failed."
- **Tasks:** compose service stack, scrape `/metrics/`, dashboards for the §5.4 table, alertmanager rules.
- **Verify:** an alert fires on synthetic lock contention within 60 s.

#### P3.3 — Add TLS, security headers, and Caddy/nginx hardening
- **Why:** Defense in depth.
- **Tasks:** as in P0.5, but also `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: microphone=(), camera=()`.

#### P3.4 — Add a `media-processing-status` SSE stream for the upload UX
- **Why:** UX. Currently the frontend has to poll `/clips/{id}/` to detect when HLS is ready. A 30 s SSE is cheaper and friendlier.
- **Tasks:** new lightweight `EventStreamView` for `clip_id` that subscribes to a Redis pub/sub channel. Frontend opens the SSE on upload completion.
- **Verify:** no measurable DB QPS change; UX improvement is qualitative.

#### P3.5 — DRF throttling refactor: per-second + per-hour
- **Why:** The current hourly-only throttling is burst-blind.
- **Tasks:** subclass `UserRateThrottle` to enforce both 60/min and 1000/hour with a single Redis backend.

---

## 7. Critical Workstreams (cross-cutting, not phase-bound)

These run in parallel with the phases and are not optional.

### 7.1 Testing
The repo has **no working test suite**. `backend/app/tests/test_scraper.py` exists but no `pytest.ini`, no `setup.cfg`, no CI test step beyond `manage.py test backend.app` (which runs zero tests). At 10k concurrent, this is a *liability* — every refactor in P0–P2 needs regression coverage or it's a roll of the dice.

**Minimum bar before P1 begins:**
- A working `pytest` setup with `pytest-django`, `pytest-cov`, `responses` (for mocking object storage), and `factory_boy`.
- Coverage gates:
  - `services/interactions.py` 100% (this is the contract)
  - `services/comments.py` 100%
  - `refill_user_feed` 90%
  - `process_audio_to_hls` 60% (mock ffmpeg / Whisper)
- A `pytest --contract-only` that asserts the API response shape on every endpoint against a frozen OpenAPI schema.

### 7.2 CI
The `django.yml` workflow runs migrations + `manage.py test`; once tests exist, it must block merges on failure. Add a `load-smoke.yml` that runs a 30 s `k6` smoke against a per-PR Compose stack and asserts p99 < thresholds from §5.

### 7.3 Documentation hygiene
The original doc's "Architectural Blind Spots" list still applies verbatim:
- Schema versioning (`schema_version` on every event).
- `MAXLEN` on every `XADD` (P1.5 enforces this).
- Feed invalidation on clip delete (current code does not emit an event for clip deletion — **add a `ClipDeleted` event** in P1.4 or P1.5 so feed queues can be drained).

### 7.4 Schema migrations
- The two HNSW indexes were created without `CONCURRENTLY` (`migrations/0001_initial.py:148, 152`). On a fresh DB this is fine; on the production DB it would be a problem. **Document** in the deploy runbook that any future HNSW index must be a separate non-atomic migration with `CREATE INDEX CONCURRENTLY`.
- Adding the new `event_outbox` table, the `clip_pending_upload` state, and any new status enum values must all be backward-compatible — no destructive column drops until 50k DAU.

### 7.5 Secret and config hygiene
`docs/TODO.md:12` notes the leaked HF token; `AGENTS.md` flags `.env` committed despite `.gitignore`. Before P1 begins, rotate the HF token, remove `.env` from the repo, and confirm `.gitignore` covers it. The BuildKit secret mechanism for `HF_TOKEN` is correct; do not switch to `--build-arg`.

---

## 8. Sequencing & Resourcing

| Phase | Est. effort (engineer-weeks) | Risk if skipped at 10k concurrent | Hard dependencies |
|---|---|---|---|
| **P0.1** (PG tuning) | 0.5 | Connection exhaustion; autovacuum starvation; cascading 5xx | none |
| **P0.2** (Service layer) | 1.5 | Cannot safely do P1.x without a refactor seam | none |
| **P0.3** (MinIO limits) | 0.25 | MinIO OOM under HLS egress | none |
| **P0.4** (Redis policy) | 0.25 | Self-feeding feed-list eviction | none |
| **P0.5** (Security headers) | 0.5 | Pentest finding | `DEBUG=False` |
| **P0.6** (Burst throttling) | 0.5 | 5xx storms on hot clips | none |
| **P1.1** (Counter pipeline) | 2 | Hot-row lock crash | **P0.2** |
| **P1.2** (Nginx + CDN) | 2 | API tier melts under HLS bytes | MinIO/S3 in place |
| **P1.3** (Presigned uploads) | 1.5 | Upload path pins gunicorn threads | **P1.2** (or at least, in same PR) |
| **P1.4** (Outbox) | 1.5 | Cannot decouple the recommendation consumer | **P0.2** |
| **P1.5** (Telemetry streams) | 3 | The F() problem compounds at scale | **P0.2, P1.1, P1.4** |
| **P2.1** (Context vectors) | 1 | Feed refills regress under load | **P1.5** |
| **P2.2** (Candidate pool) | 1.5 | `refill_user_feed` reads from PG under load | **P2.1** |
| **P2.3** (Error isolation) | 0.5 | Long-term baseline stalls forever | none |
| **P2.4** (Frontend batching) | 1.5 | Wasted API QPS, silent telemetry loss | **P2.5** |
| **P2.5** (Batch endpoint) | 1 | Companion to P2.4 | **P0.2** |
| **P3.x** | 2 each | Quality-of-life; not 10k-blocking | various |

**Total to 10k ready:** ~18 engineer-weeks (one engineer ≈ 4–5 months; two engineers ≈ 2–3 months). **P0 alone (4 weeks) is the minimum to not break**; the P1 phases are what gets you to 10k clean.

---

## 9. Definition of Done (per phase)

A phase is "done" when:

- All §6 tasks for that phase are merged with passing CI.
- The `Verify:` block in each task holds for 24 hours in staging at the §5 load profile.
- The `Rollback:` block is documented in `docs/runbooks/` (a new dir for ops runbooks).
- A one-page ops note in `docs/runbooks/` covering: how to deploy, how to monitor, how to roll back.
- The Prometheus dashboards in §5.4 are wired and alerting.

A phase is "failed" if it introduces a new failure mode that doesn't have a documented workaround. **Phases may not be skipped** — P1.5 depends on P0.2, P1.1, and P1.4; P2.x depend on P1.5.

---

## 10. What Comes After 10k (P4+, not in this document)

For completeness — the path to 50k and 100k is not in scope here, but the cliff is real:

- **At ~50k concurrent**, the Redis Streams `XADD`/`XREADGROUP` will outpace Redis single-thread throughput. This is when the original doc's "Stage 7" (Kafka or Kinesis) becomes correct, *not before*.
- **At ~50k concurrent**, `pgvector` HNSW begins to slow on the `calculate_time_decayed_vectors` query. Add a precomputed `ANN` candidate generator (FAISS or pgvector's IVF index) before the HNSW.
- **At ~100k concurrent**, add a Postgres read replica for `/suggestions/`. Sharding is a year-2 problem.
- **At ~500k concurrent**, ClickHouse for telemetry + a real recommendation ML service in front of the HNSW index.

These are deliberately not detailed in this document. The codebase is not in a state where they are actionable; doing them now would be premature optimization that adds operational surface for zero user benefit at 10k.

---

## 11. Summary Verdict

> **At 10k concurrent users, the system will *not* collapse on its own** — but it will degrade in five specific, predictable ways (hot-row F() locks, `update_global_metrics` table-wide SQL, autovacuum starvation, connection exhaustion, Redis feed-list eviction). The original `relational-to-event-driven-architecture.md` is correct that a Phase-2 event-driven carver is *eventually* the right answer, but **most of the 10k work is in Phase 0 (stabilize) and Phase 1 (offload load-bearing bottlenecks)** — not in the full streaming-platform migration the original doc foreshadows.
>
> **The single most important early move** is **P0.2: the service-layer boundary**. Every later phase depends on it, and it changes no behavior. **The single most expensive mistake** would be to add Kafka now — it does not solve any 10k problem and it adds operational surface.
>
> **What to build today, in this order:** service layer → PG tuning → burst throttling → MinIO/Redis limits → security headers → outbox → telemetry streams → counter pipeline → nginx + CDN → presigned uploads → context vectors → frontend batching.

See also: `docs/relational-to-event-driven-architecture.md` (foundational EDA reasoning), `docs/high-velocity-telemetry-write-architecture.md` (telemetry-specific math), `docs/minio-s3-architecture.md` (storage boundary), `docs/stateful-media-storage-at-scale.md` (why we no longer use local disk), `docs/scaling-analysis.md` (the 1M-level plan, not actionable until 10k is solid), `docs/backend-architecture-audit.md` and `docs/backend-audit.md` (P0/P1 security and code-quality items).
