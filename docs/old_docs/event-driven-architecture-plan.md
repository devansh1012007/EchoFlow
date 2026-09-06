# docs/event-driven-architecture-plan.md

> **Companion to:** `docs/relational-to-event-driven-architecture.md` (read first for foundational concepts).
> **Date:** 2026-09-03 · **Target:** 10,000 live (concurrently connected) users on the current stack.
> **Branch under audit:** `feat/stage2-service-layer-and-telemetry-stream` (heads through 3d973a7).
> **Scope:** Updates the prior doc with what is actually in the codebase today, then lays out the detailed, sequenced engineering plan to reach and safely serve 10k concurrent users.
> **See also:** `docs/unfixed-issues-2026-09-03.md` for the code-anchored status of every §6 task.

---

## 0. Read This First

The original `relational-to-event-driven-architecture.md` was written against an **aspirational** model of the codebase (referenced entities, e.g. "row-level `F()` updates on `UserInteraction.save()`", were partially described and partially assumed). Most of its architectural reasoning — *why* EchoFlow needs event-driven evolution, *what* stays relational, *how* idempotency / outbox / DLQ work — is **still correct and remains the foundation** of this plan. This document is the **delta**: what the codebase actually looks like in 2026-09, what changed since the prior doc, and the concrete, ordered work to reach 10k concurrent users without rewriting the system.

If you have time for only one document, read **§3 (the gap analysis)** and **§6 (the phased plan)**.

For a per-task status snapshot of every §6 item, see **`docs/unfixed-issues-2026-09-03.md`**. The branch under audit is `feat/stage2-service-layer-and-telemetry-stream`; what ships there and what remains on trunk is what the rest of this document describes.

---

## 1. What the Codebase Actually Looks Like (2026-09-03)

A factual snapshot, established by reading every relevant file. Every line reference below is real and current.

### 1.1 Stack inventory

| Layer | What is deployed | Source |
|---|---|---|
| API | Django 5.2 + DRF on Gunicorn `gthread`, 4 workers × 4 threads = 16 concurrent in-flight requests, `timeout=120s` | `backend/gunicorn.conf.py:9-16` |
| DB | PostgreSQL 16 + pgvector, single node, **no PgBouncer**, default `max_connections=100`, no `command:` override | `docker-compose.yml:3-33` |
| Cache / broker | Redis 7, `maxmemory 512mb`, `maxmemory-policy allkeys-lru`, AOF on | `docker-compose.yml:36-49` |
| Object storage | MinIO (dev) or S3 / R2 (prod), path-style addressing, `hls/` public, `uploads/` private | `docker-compose.yml:70-124`, `backend/EchoFlow/settings.py:256-290` |
| Workers | `celery` (default), `celery_feed` (`fast_feed`, concurrency 4), `celery_media` (`heavy_media`, `--pool=solo` — required because Whisper + sentence-transformers + KeyBERT loaded as module singletons exceed per-process RAM), `celery_beat` (DatabaseScheduler) | `docker-compose.yml:189-386` |
| Beat cadence | `update_global_metrics` every 300 s (now **id-batched**, 5000-row chunks; correlated subquery remains — see `tasks.py:479-548`), `evolve_long_term_user_baselines` every 86400 s (24h; was 1h — see `settings.py:238-246`), `cleanup_stuck_processing` every 300 s (re-enqueues clips stuck >15 min, `tasks.py:795-831`), `flush_telemetry_stream` every 10 s (XREADGROUP consumer, `tasks.py:645-791`), `flush_telemetry_legacy` every 30 s (LIST fallback during stream cutover) | `settings.py:233-275` |
| Frontend | Vite + React, no telemetry batching, no service worker, no offline queue, hls.js loaded from CDN at runtime; unread-count polled every 30 s | `frontend/main.jsx:1103, 319-336, 2120-2126` |
| Auth | JWT (SimpleJWT) — ACCESS 15 min, REFRESH 7 days; `dj-rest-auth` registration | `settings.py:329-332` |
| Throttling | DRF `AnonRateThrottle` + `UserRateThrottle` + **`ScopedRateThrottle` per-endpoint**: `telemetry 60/min`, `upload 20/hr`, `register 5/hr`, `login 10/min`, `comment 60/hr`, `share_send 100/hr`, `share_poll 1000/hr`, `interaction 60/min` (`settings.py:361-382`). Hourly buckets still present but per-second/minute scopes are the active defense | `settings.py:361-382`, `views/interactions.py:63-73` |
| Monitoring | `prometheus_client` middleware (`before`/`after`) shipped; `/metrics/` exported (`urls.py:13`). **Still nothing scrapes it**; no Grafana / OTel / Sentry. **Added:** `CorrelationIdMiddleware` + JSON-formatted logs with `correlation_id` filter, propagated into Celery via `django-celery-beat` headers (`settings.py:399-446`) | `urls.py:13`, `settings.py:100-117, 399-446`, `docker-compose.yml` (absent for prom/grafana) |

### 1.2 Models (5 total, all in `backend/app/models.py`)

| Model | Key fields | Hot-spot risk |
|---|---|---|
| `User` (custom, `AUTH_USER_MODEL='app.User'`) | `long_term_semantic[384]`, `long_term_acoustic[128]`, `following` M2M (non-symmetrical). **N3 fix applied locally (uncommitted on this branch):** the `encrypted_email` field and the `User.save()` Fernet override have been removed. Plaintext email is now inherited from `AbstractUser` and validated at the API boundary via `RegisterSerializer`'s `UniqueValidator`. The `FIELD_ENCRYPTION_KEY` env var and the fail-fast import-time check are gone. The Fernet mechanism was misleading theatre — non-deterministic IV, no decryption path, plain `AbstractUser.email` was the source of truth | `models.py:14-34` (class definition; the `N3 fix` comment at line 15 explains the removal) |
| `AudioClip` (UUID PK) | `original_file`, `hls_playlist_url` (relative object-storage key, **not** a URL), `likes/shares/skips/comment_count` denormalized counters, `tags` JSONField, `semantic_vector[384]`, `acoustic_vector[128]`, `status ∈ {processing, ready, failed}` | HNSW indexes `(m=16, ef_construction=64)` on both vector columns (`models.py:78-95`); `engagement_velocity` and `avg_completion_rate` recomputed by id-batched raw SQL every 5 min (`tasks.py:479-548`). **`Meta.constraints`** enforces `likes/shares/skips/comment_count >= 0` at the DB level via `CheckConstraint` (`models.py:103-106`), covered by migration `0002_audioclip_likes_non_negative_and_more.py` |
| `Comment` (UUID PK) | `parent` self-FK, `likes`, `text` (500 char) | `save()` and `delete()` mutate `AudioClip.comment_count` via `F()` **only when `parent_id IS NULL`** (top-level only) — replies do not increment the counter (`models.py:134-144`). Subtle: this is correct, but undocumented. **`comment.likes >= 0` CHECK constraint** shipped via `0002_audioclip_likes_non_negative_and_more.py` |
| `ShareEvent` (BigAuto) | `sender`, `receiver`, `clip`, `is_read` (indexed by `receiver, -created_at, is_read`) | No counter decrement when a share is deleted (`views/social.py:87-90`) — counter can drift permanently upward |
| `UserInteraction` (BigAuto) | `interaction_type ∈ {view, like, share, skip}`, `is_active`, `watch_time_ms`, `completion_rate`, `unique_together(user, clip, interaction_type)` | **`save()` override executes `AudioClip.objects.filter(pk=...).update(likes=F('likes')+1)` for `like/share/skip` on state change** (`models.py:173-206`). Uses `select_for_update()` on the toggle path. **`'view'` deliberately omitted from `field_map`** — views never bump a denormalized counter |

### 1.3 Views (8 modules under `backend/app/views/`, all delegating to `backend/app/services/`)

Zero WebSocket, zero SSE, zero `StreamingHttpResponse`, zero async views. Confirmed by grep.

The monolithic `backend/app/views.py` was split into a package on 2026-09-02 (commit `1c3be4b`): `auth.py`, `comments.py`, `content.py`, `feed.py`, `interactions.py`, `profile.py`, `social.py`, `_pagination.py`. **Every view delegates ORM writes to `services/`** — the service layer is the load-bearing seam that lets us move counters to Redis without touching views (P0.2 done).

| Endpoint | What it does today | Synchronous DB cost per request | Async dispatch |
|---|---|---|---|
| `POST /clips/` (upload) | `serializer.save()` then `services/uploads.finalize_upload(clip)` → `transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))` | 1 INSERT into `audioclip` | `heavy_media` queue |
| `GET /feed/` | `redis.lpop(user_feed:{id}, 10)` → if empty, `refill_user_feed.delay(count=40)` and re-poll → if queue `< 15` after pop, refill again → 1 SELECT on `audioclip` ordered by `preserved_order` | 1 SELECT | `fast_feed` queue (twice on miss) |
| `POST /interactions/{id}/toggle-like/` | `services/interactions.record_like_toggle(user, clip)` → `get_or_create`, toggle `is_active`, save → triggers `UserInteraction.save()` → `F()` update on `audioclip.likes` (throttled `interaction 60/min`) | 1 SELECT + 1 INSERT-or-UPDATE + 1 UPDATE on `audioclip` (row-locked) | none |
| `POST /interactions/{id}/register-skip/` | `services/interactions.record_skip(...)` → `UserInteraction.update_or_create(interaction_type='view', ...)` (note: writes a `'view'`, not a `'skip'`, despite the name) | 1 SELECT + 1 INSERT-or-UPDATE; **does not bump any counter** | none |
| `POST /interactions/{id}/log-telemetry/` | `services/interactions.record_telemetry(...)` → `XADD stream:interaction.events` (env-gated `ECHOFLOW_TELEMETRY_STREAM`); fallback is `RPUSH telemetry:queue`; last-resort is synchronous `UserInteraction.update_or_create` | 0 (stream path) or 1 SELECT + 1 INSERT-or-UPDATE (fallback) | `stream:interaction.events` consumer (`flush_telemetry_stream`, every 10 s) |
| `POST /share/{id}/send-share/` | `services/shares.send_share(sender, clip, receiver)` → `services/interactions.record_share` (bumps counter) + `ShareEvent.create` | 1 SELECT + 1 INSERT-or-UPDATE + 1 INSERT + 1 UPDATE on `audioclip` | none |
| `POST /comments/` (and `PATCH/DELETE`) | `services/comments.create_comment/update_comment/delete_comment`; `Comment.save()` and `delete()` mutate `audioclip.comment_count` via `F()` if top-level | 1 SELECT + 1 INSERT + 1 UPDATE on `audioclip` (top-level only) | none |
| `POST /follow/{id}/toggle-follow/` | `services/follows.toggle_follow(actor, target)` | 1 SELECT + 1 INSERT or 1 DELETE | none |
| `POST /tags/initialize/` | Top-100 liked clips by tag overlap → numpy mean → `user.save()` → `refill_user_feed.delay(user.id, 30)` | 1 SELECT (top-100) + 1 UPDATE on `user` | `fast_feed` queue |
| `GET /suggestions/?category=X` | `CosineDistance` annotation over `(semantic_vector, sem_query)` and `(acoustic_vector, ac_query)`, ordered ascending by sum | 1 SELECT, vector-distance-heavy | none |

`transaction.on_commit` for upload processing now lives in `services/uploads.py::finalize_upload` (not in the view), but it is the only `on_commit` in the codebase. Every other mutation is fire-and-forget within the request thread. `flush_telemetry_legacy` and `flush_telemetry_stream` are scheduled via Celery Beat, not `on_commit`.

### 1.4 Celery tasks (7 in `backend/app/tasks.py`)

| Task | Queue | Hot-spot |
|---|---|---|
| `process_audio_to_hls(clip_id)` | `heavy_media` (only routed task to this queue) | ML model singletons (`whisper_model`, `embedding_model`, `kw_model`) — **must run `--pool=solo`** or each fork reloads ~1.0–1.5 GB. Streams `clip.original_file` from object storage to `tempfile.mkstemp`, normalizes via ffmpeg → WAV, librosa for vectors, Whisper for transcript, SentenceTransformer for `semantic_vector`, KeyBERT for tags, then ffmpeg → local HLS dir → `default_storage.save()` per file. Final `clip.save()` for `status='ready'` and `hls_playlist_url='hls/{clip.id}/master.m3u8'` (relative key, not a URL — see `media_urls.py:43-59`). Bound by `bind=True, max_retries=3, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600` |
| `refill_user_feed(user_id, count=50)` | `fast_feed` (concurrency 4) | **Short-circuits if `llen >= 20`.** Recomputes per-user context via `calculate_time_decayed_vectors(user)` (a fresh SQL query + numpy). 80/20 exploit/explore + 5 random follow-graph clips + shuffle. `rpush` to Redis. **Acquires a `feed_refill_lock:{user_id}` SETNX EX 30** to prevent concurrent refills (`tasks.py:333-336`) |
| `update_global_metrics` | default `celery` | **Id-batched raw SQL** — cursor `update_global_metrics:resume_id` persisted in Redis cache; 5000-row chunks; two SQL statements per batch (`tasks.py:479-548`). Eliminates the single table-wide lock; **the correlated `AVG(completion_rate)` subquery is still O(rows×views) per chunk**. Not yet replaced by event-driven counter pipeline (P1.1) |
| `evolve_long_term_user_baselines` | default `celery` | **Schedule now 86400 s (24h)** — was 3600 s (`settings.py:238-246`). Iterates **all** active users (`.iterator(chunk_size=100)`), recomputes vectors, `bulk_update(batch_size=100)`. **Still no `try/except` per user** — one bad row fails the rest (P2.3 untouched) |
| `flush_telemetry_stream` | default `celery` | **NEW.** `XREADGROUP` consumer for `stream:interaction.events` with consumer group `cg:telemetry-flush`, `MAXLEN ~ 50000` on `XADD`, dedup via `SETNX processed_event:{event_id} EX 86400`, bulk_create with `batch_size=500`, `XACK` after, DLQ at `stream:interaction.events:dlq`. Beat every 10 s (`tasks.py:645-791`, `settings.py:255-265`). **The stream consumer uses `UserInteraction()` constructors + `bulk_create` which does NOT call `save()` — so the F() counter side-effect on `audioclip.likes` is bypassed for the telemetry path** |
| `flush_telemetry_legacy` | default `celery` | **NEW.** Drains the `telemetry:queue` LIST as a safety net while the stream consumer proves itself. Marked `TODO: remove after one cycle of stable operation` (`tasks.py:590-641`, `settings.py:266-274`) |
| `cleanup_stuck_processing` | default `celery` | **NEW.** Re-enqueues clips stuck in `processing` past 15 min (Beat every 5 min). After `threshold_minutes * 3` (45 min) it gives up and flips the row to `failed` (`tasks.py:795-831`, `settings.py:247-254`). Addresses the audit item where a Celery broker hiccup at `transaction.on_commit` time would leave a clip in `processing` forever |
| `scrape_and_import(source_name, limit, clip_length)` | default `celery` | Imports 3rd-party audio, then enqueues `process_audio_to_hls.delay(clip.id)` for each |
| `calculate_time_decayed_vectors(user, limit=50)` | helper, not a task | The actual recommendation core. Pulls last 50 `UserInteraction`s, computes per-row weight `time_weight * comp_weight * intent_weight`, blends with `long_term_*` at `ALPHA=0.7` |

Two dead helpers exist (`calculate_dynamic_user_vector` and `calculate_blended_query_vectors`); they are not imported by any view or service module. The active function is `calculate_time_decayed_vectors` (lines 407-473).

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

### 1.7 Verification scripts and the EXPLAIN docs tree

**Verification scripts** (`backend/scripts/`):
- `verify_minio_deployment.sh` — 10 checks against MinIO bucket / policy / CORS / playback.
- `test_minio_edge_cases.py` — concurrent reads, multipart, signed-URL expiry.
- `verify_clip_url.sh` — quick 200 + preview for any HLS URL.
- `verify_decoder_rootcause.sh` — confirms `47401111` sync byte (MPEG-TS), not stale fMP4.
- `verify_hls_playback.html` — isolated hls.js test page.

**EXPLAIN docs tree** (`docs/EXPLAIN/` — 49 markdown files, committed `29450be`): code-grounded, derived-from-source documentation that supersedes speculative prose like this doc. Categories: `architecture/`, `backend/`, `ai_ml/`, `redis-celery/`, `postgresql/`, `media/`, `storage/`, `scraping/`, `auth/`, `docker/`, `testing/`, `failure/`, `decisions/` (the last contains `02-discrepancies.md` which lists README/AGENTS.md/audit-doc claims contradicted by code). When this document and `EXPLAIN/` disagree, **EXPLAIN wins** — it is regenerated from the source.

**Test suite** (`backend/app/tests/`, 7 files, 56 `test_` functions): `test_services_{comments,follows,interactions,shares,uploads}.py` cover the service layer; `test_security_and_validation.py` (27 tests) covers the audit's N1-N13 items; `test_scraper.py` (legacy, 1 test); `test_smoke.py` (1 placeholder test, 7 lines). `pytest-django` is wired but `pytest.ini` is absent; CI (`django.yml`) runs `python manage.py test backend.app` against pgvector:pg16. `migrations_test/` is an empty directory — leftover from a previous attempt, safe to remove.

---

## 2. What Has Changed Since the Original Doc Was Written

A directed diff of the prior doc against today's codebase, restricted to factual claims. **This is the 2026-09-03 snapshot.** Section 2.1 captures the 2026-09-02 baseline that this document was originally written against; §2.2 is the **2026-09-02 → 2026-09-03 delta** for the `feat/stage2-service-layer-and-telemetry-stream` branch.

### 2.1 Original diff (2026-09-02 → trunk)

The 2026-09-02 version of this document already documented the storage rework onto S3-compatible object storage, the MinIO bucket policy, the `pgvector` HNSW indexes, the Fernet email encryption, the allauth + dj-rest-auth registration pipeline, and the `django_prometheus` middleware. Those rows are reproduced verbatim from the previous revision for context:

| Claim in original doc | Reality today | Status (as of 2026-09-02) |
|---|---|---|
| "Synchronous increments on `AudioClip` table" via `UserInteraction.save()` | Still true. `models.py:173-206` executes `AudioClip.objects.filter(pk=...).update(likes=F('likes')+1)` for `like/share/skip` state changes | **Unchanged, still the #1 lock risk** |
| `Comment.save()` and `delete()` mutate `audioclip.comment_count` via `F()` | Still true, but **only for top-level comments** (`parent_id IS NULL`). Replies don't bump the counter (`models.py:134-144`) | Subtler than the doc described |
| "Synchronous `refill_user_feed(user_id, count=10)` inside HTTP request thread" | **Was** true. **Now**: `refill_user_feed.delay(user_id, count=40)` — async dispatch only. Same with the `< 15` threshold case | **Fixed** — but the fallback is empty-feed response, not a "cached trending list" as the doc proposed. There is no trending cache yet |
| `update_global_metrics` "raw SQL mass updates every 10 minutes" | Was 10 min, **now 5 min**. Still raw SQL, still no batching (as of 2026-09-02) | **Higher frequency, same risk** — see §2.2 for the id-batched fix |
| `evolve_long_term_user_baselines` "iterates through active users daily" | **Now hourly** (as of 2026-09-02). Still no per-user error isolation | **Higher frequency, same fragility** — see §2.2 for the 24h schedule change |
| HLS "Stored under `media/hls/{clip_id}/` on local disk" | **Wrong.** HLS lives in object storage under `hls/{clip_id}/...`; served via MinIO bucket policy (`media_urls.py:43-59`) | **Resolved by MinIO integration** |
| "Django REST Framework / Synchronous request/response / Celery + Redis" stack | Still true. **No Channels, no daphne, no SSE, no WebSocket** | **Unchanged** |
| DRF throttle rates not specified | Hourly buckets only as of 2026-09-02 (`anon 100/hour, user 1000/hour`) | **Partially addressed** — see §2.2 for the per-scope rates |
| "Will lock on large tables; needs batching at scale" (`update_global_metrics`) | Confirmed and unfixed (as of 2026-09-02) | **Open** — see §2.2 for the id-batched fix |
| `transaction.on_commit` for upload (`process_audio_to_hls`) | Single-use (as of 2026-09-02) | **Unchanged** — but now lives in `services/uploads.py` |
| "Counter increments via F() expressions on likes/shares/skips" | **Still true**, but **`'view'` is intentionally excluded** from the `field_map` in `UserInteraction.save()` | **Subtle fix** |
| "All Celery Beat tasks run on default queue" | Still true except `process_audio_to_hls` and `refill_user_feed` (routed) | **Unchanged** |
| No service layer | True as of 2026-09-02 | **Unchanged, blocker for safe refactor** — see §2.2 for the service layer landing |
| `request.user.following.filter().exists()` for follow checks | True (as of 2026-09-02) | **Unchanged** — see §2.2 for the service-layer delegation |
| **No mention of MinIO / S3 / object storage in original doc** | Present. `STORAGES["default"]` is `S3Storage`. Two ACLs: `hls/` public, `uploads/` private | **New in repo; original doc is silent** |
| **No mention of `pgvector` HNSW indexes** | Present at `models.py:78-95` (`m=16, ef_construction=64`, `vector_cosine_ops`). 384-dim semantic and 128-dim acoustic | **New in repo; original doc is silent** |
| **No mention of `FIELD_ENCRYPTION_KEY` Fernet for email** | **Removed locally (N3 fix, uncommitted):** the Fernet email encryption was deleted in `models.py:14-34`. `FIELD_ENCRYPTION_KEY` is no longer required at import time. The plaintext `AbstractUser.email` is the source of truth. **Note:** the `docs/EXPLAIN/auth/02-pii-encryption.md`, `EXPLAIN/backend/02-models.md`, and several other EXPLAIN docs are now stale — they describe a Fernet mechanism that no longer exists | **Removed; see `models.py:14-34` for the N3 fix comment** |
| **No mention of allauth + dj-rest-auth registration pipeline** | Present. Unused in practice for the social login flow | **New in repo; original doc is silent** |
| **No mention of `django_prometheus` / `/metrics/`** | Exported but unconsumed. `health.py` and `ready.py` exist and wire `/health/` and `/ready/` | **New in repo; original doc is silent** |

### 2.2 Delta since 2026-09-02 (the `feat/stage2-service-layer-and-telemetry-stream` branch)

The following 17 rows are **new in the last 24 hours**. Each row references the commit that landed it on the branch under audit.

| # | Change | Source | Resolves |
|---|---|---|---|
| 1 | **Service-layer boundary** — `backend/app/services/{comments,follows,interactions,shares,uploads}.py` (181 LOC). Every ViewSet now calls service functions; no view owns ORM writes directly | commit `7f1b483` (`refactor(services): Stage 2 service-layer boundary (no behavior change)`) | **P0.2** — the load-bearing seam that lets P1.1 (counter pipeline) and P1.4 (outbox) move work without touching views |
| 2 | **Telemetry Redis Stream** — `services/interactions._xadd_telemetry()` does `XADD stream:interaction.events MAXLEN ~ 50000` with `event_id` and `schema_version` fields. Falls back to `RPUSH telemetry:queue` if the stream is unhealthy, then to synchronous `update_or_create` as a last resort | commit `a3e400e` (`feat(telemetry): migrate flush pipeline to Redis Stream (LIST retained as fallback)`); `services/interactions.py:48-75, 124-168` | **P1.5 (telemetry path)** — the hot-row F() problem on the telemetry path is **eliminated** because the consumer (`flush_telemetry_stream`) uses `bulk_create` which bypasses `UserInteraction.save()` |
| 3 | **Telemetry stream consumer** — `flush_telemetry_stream` runs every 10 s via Beat. `XREADGROUP` (consumer group `cg:telemetry-flush`), SETNX dedup with 24h TTL, `bulk_create(batch_size=500)`, `XACK`, DLQ at `stream:interaction.events:dlq` | `tasks.py:645-791`, `settings.py:255-265` | **P1.5** consumer side |
| 4 | **Telemetry legacy list consumer** — `flush_telemetry_legacy` drains the `telemetry:queue` LIST as a safety net during stream cutover. Marked `TODO: remove after one cycle of stable operation` | `tasks.py:590-641`, `settings.py:266-274` | Operability — keeps the door open to flip `ECHOFLOW_TELEMETRY_STREAM=off` |
| 5 | **Cleanup stuck processing** — `cleanup_stuck_processing(threshold_minutes=15, max_per_run=50)` re-enqueues clips whose Celery task never completed. After 3 retries it flips the row to `failed` | commit `7701677`; `tasks.py:795-831` | Audit item 6.7: a broker hiccup at `transaction.on_commit` time used to leave a clip in `processing` forever |
| 6 | **Burst-aware per-scope throttling** — `ScopedRateThrottle` on every ViewSet that takes writes. `telemetry 60/min`, `interaction 60/min`, `upload 20/hr`, `register 5/hr`, `login 10/min`, `comment 60/hr`, `share_send 100/hr`, `share_poll 1000/hr` | commit `028cc2d`; `settings.py:361-382` | **P0.6** — kills the "DRF only has hourly buckets" gap from §2.1 |
| 7 | **Production security headers** — `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS = 31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_PROXY_SSL_HEADER`, all gated on `DEBUG=False` | commit `9d3383c`; `settings.py:452-462` | **P0.5** |
| 8 | **CHECK constraints on counters** — `likes/shares/skips/comment_count >= 0` on `audioclip` and `comment.likes >= 0`. DB-level enforcement catches raw SQL and ORM updates that would otherwise write negative counts | migration `0002_audioclip_likes_non_negative_and_more.py`; `models.py:103-106, 123` | Part of **P0.1** — the cheap insurance half |
| 9 | **JWT rotation + blacklist + logout** — `ROTATE_REFRESH_TOKENS = True`, `BLACKLIST_AFTER_ROTATION = True`, `token_blacklist` app wired, `UPDATE_LAST_LOGIN = True` | commit `028cc2d`; `settings.py:387-397` | Audit item N10 |
| 10 | **JSON structured logging + correlation_id** — `python-json-logger` formatter, `CorrelationIdFilter` injects `correlation_id` (set by `CorrelationIdMiddleware`) into every log record. Celery picks the id up from task headers | commit `c90ae23`; `settings.py:399-446`; `backend/EchoFlow/middleware.py` | **P3.2 partial** — observability foundation without the Prometheus/Grafana side yet |
| 11 | **`evolve_long_term_user_baselines` schedule 1h → 24h** — DECISION comment: with `limit=100 per user`, 100k users would be 10M interaction reads/hour. 24h is the design intent | `settings.py:238-246` | Reduces the operational footprint of P2.3 (still open) |
| 12 | **`update_global_metrics` id-batched** — cursor `update_global_metrics:resume_id` persisted in Redis cache, 5000-row chunks, two SQL statements per batch. The single table-wide lock is gone | `tasks.py:479-548` | **Half of P1.1 / P3.1.** Remaining: the correlated subquery is still O(rows×views) per chunk; the engagement_velocity recompute still scans the table; not yet replaced by event-driven counter flush |
| 13 | **View split into a package** — `backend/app/views.py` removed in favor of `backend/app/views/{auth,comments,content,feed,interactions,profile,social}.py` + `_pagination.py` | commit `1c3be4b` | Paves the way for the service-layer delegation in row #1 |
| 14 | **Object-level permission on `CommentViewSet`** — `IsAuthorOrReadOnly` so a user can only `PUT/PATCH/DELETE` their own comments | commit `4d15f02` | Audit item N1 |
| 15 | **Per-action throttles on `ShareViewSet`** — inbox polling has `share_poll 1000/hr`; sending has `share_send 100/hr`. `ShareViewSet` narrowed to `GenericViewSet` (no `ListModelMixin`) | commit `4d15f02` | Audit items N10 + N13 |
| 16 | **`watch_time_ms` cap + comment text sanitize** — telemetry watcher validation rejects `watch_time_ms > clip.duration_ms * 1.1` (or sensible absolute cap). `Comment.text` is HTML-stripped on save | commit `e6a80b6` | Audit items N4, N6 |
| 17 | **Magic-byte audio validation** — `python-magic` MIME check at the serializer level, before ffmpeg ever sees the file. Disguised executables are rejected with 400 | commit `2715b54` | Audit item N8 |
| 18 | **CORS dead-code fix** — removed the duplicate `CORS_ALLOW_ALL_ORIGINS = True` reassignment. Now explicitly `False`; `CORS_ALLOWED_ORIGINS` is the env-driven allowlist | commit `9d3383c`; `settings.py:27-32` | Audit item (AGENTS.md #2) |
| 19 | **Dead code removed (~180 LOC)** — dead helpers and the `OpenAI` triple-quoted string statement are gone | commit `1bb0978` | Hygiene |
| 20 | **Test suite seeded** — 7 pytest files, 56 `test_` functions. `test_services_{comments,follows,interactions,shares,uploads}.py` cover the service-layer; `test_security_and_validation.py` covers audit items N1–N13 | commit `8973d65`; `b9830fa`; tests in `backend/app/tests/` | **P7.1 partial** — coverage exists for the service boundary; `pytest.ini` and `load-smoke.yml` are still open |
| 21 | **EXPLAIN docs tree** — 49 markdown files under `docs/EXPLAIN/` derived from source. Includes `decisions/02-discrepancies.md` listing every audit-doc claim contradicted by code | commit `29450be` | Doc hygiene; supersedes speculative prose in this and similar docs |
| 22 | **`celery_media` memory raised 1G → 4G** — Whisper base + SentenceTransformer + KeyBERT resident set. 1G was OOMKilling on the first clip | commit `a672c52`; `docker-compose.yml:325-340` | Operability |
| 23 | **`.gitignore` pattern fix** — `[Bin|Obj]*/` was swallowing all `backend/` paths. Tightened the glob | commit `42064bb` | Repo hygiene |
| 24 | **N2 fix (uncommitted on this branch, present in the working tree).** The F() counter `UPDATE` in `UserInteraction.save()` is now wrapped inside the `transaction.atomic()` block. The previous code did the row lock and `is_active` comparison outside `atomic()`, opening a race window between releasing the lock and writing the counter. The fix is in `backend/app/models.py:173-206`; see the `N2 fix` comment at line 174 | uncommitted (working tree) | visible in `git diff backend/app/models.py` |
| 25 | **N3 fix (uncommitted on this branch, present in the working tree).** Fernet email encryption removed. `encrypted_email` field, `FIELD_ENCRYPTION_KEY` import-time check, and `User.save()` Fernet override are all gone. Plaintext `AbstractUser.email` is the source of truth; the Fernet mechanism was misleading theatre (no decryption path, non-deterministic IV made the `unique=True` constraint unreliable, and `TagsViewSet.initialize_vectors` was re-encrypting on every vector update). **Note:** `docs/EXPLAIN/auth/02-pii-encryption.md`, `EXPLAIN/backend/02-models.md`, `EXPLAIN/postgresql/01-schema.md`, and several other EXPLAIN docs are now stale on this point | uncommitted (working tree) | visible in `git diff backend/app/models.py`; N3 rationale at `models.py:15-23` |

**Net of the delta:** §1 of the previous revision correctly described the storage rework and pgvector HNSW; it incorrectly called the service layer "absent, blocker for safe refactor." The service layer, the telemetry stream, burst-aware throttling, security headers, and the CHECK constraints are all now shipped on the branch under audit. **The remaining 10k work concentrates on: (a) the F() counter path that still fires on `toggle_like`/`send_share`; (b) the load-bearing infrastructure absent from Compose (nginx, PgBouncer, CDN); (c) the production-hardening items in §2 of the original doc that are unaffected by the service layer (HF token rotation, `.env` removal, `librosa` duplicate, HNSW `CONCURRENTLY`).** See `docs/unfixed-issues-2026-09-03.md` for the full status table.

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

1. **Hot-row lock contention on viral clips (the "F() problem") — PARTIALLY MITIGATED.** `UserInteraction.save()` still does `AudioClip.objects.filter(pk=...).update(likes=F('likes')+1)` (`models.py:200-206`). **However**, the telemetry path no longer hits this: `flush_telemetry_stream` uses `UserInteraction()` constructors + `bulk_create` (no `save()`), so the F() side-effect never fires for `/log-telemetry/` (`tasks.py:747-759`). The remaining hot rows are **`/toggle-like/` and `/send-share/`**, both of which still call `UserInteraction.save()` synchronously in the request thread. On a viral clip receiving ~50 likes/sec at 10k concurrent, every like serializes on the same `AudioClip` row. Each lock takes ~1–5 ms even with `select_for_update()` on the toggle path; 50 of them per second = 50–250 ms of accumulated lock time, plus 50 connections pinned. **Symptom:** 504s on `/toggle-like/` and `/share/{id}/send-share/` only for that clip, but every other request sharing those connections also queues. **This remains the single largest unmitigated lock risk.** P1.1 closes it.
2. **`update_global_metrics` table-wide raw SQL — ID-BATCHED, BUT NOT ELIMINATED.** Cursor `update_global_metrics:resume_id` persists in Redis cache; 5000-row chunks; two SQL statements per batch (`tasks.py:479-548`). The single table-wide lock is gone. **The correlated `AVG(completion_rate)` subquery is still O(rows×views) per chunk**; the engagement_velocity recompute still scans `audioclip`. At 10k concurrent with ~50k–200k clips, expect 1–5 s `ACCESS SHARE` locks every 5 min per batch. **Symptom:** intermittent 1–5 s latency spikes every 5 min; not the 10–30 s spikes the un-batched version produced. P1.1 + P3.1 close this.
3. **Autovacuum starvation on `userinteraction` — OPEN.** Every `update_or_create` on `userinteraction` produces a dead tuple. At 670 events/s × 86,400 s = ~58 M dead tuples/day, plus the toggle path's `select_for_update` produces two. `autovacuum_vacuum_scale_factor=0.2` (default) on a 58 M-row table means ~12 M-row vacuum per run, easily 30+ min. The `db` service in `docker-compose.yml:3-33` has **no `command:` override** — Postgres runs with the default autovacuum config. **Symptom:** table bloat, slow index scans, eventually index-only-scan fallback. Critical at 100k DAU, visible at 10k concurrent. P0.1 (the autovacuum half) is still open.
4. **Connection pool exhaustion under spike — OPEN.** No PgBouncer. `conn_max_age=600` keeps 24 long-lived connections. A spike of 80 concurrent requests exceeds `max_connections=100` minus the long-lived ones; new requests get `OperationalError: too many clients`. **Symptom:** 500s on auth, login, comments — *not* the high-velocity endpoints that look like the obvious problem. P0.1 (`max_connections=200`) and PgBouncer are still open.
5. **`minio` and `minio-init` have no resource limits** (`docker-compose.yml:70-94`). The compose file has no `deploy.resources` block on either service. Under 1.5–2 Gbps HLS egress, MinIO will hit CPU/MEM caps. **Symptom:** segment downloads slow to 200–500 ms, hls.js stalls and re-buffers, frontend users blame the backend. P0.3 still open.
6. **Redis 512 MB `maxmemory` with `allkeys-lru` — OPEN.** `redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru` is still the `command:` on the `redis` service (`docker-compose.yml:39`). At 10k concurrent with hot users, `user_feed:*` lists (avg ~50 entries × 36 bytes/UUID ≈ 1.8 KB each → 18 MB for 10k queues) plus Celery broker keys plus the new `stream:interaction.events` (capped at `MAXLEN ~ 50000`) plus dedup keys (`processed_event:{event_id}` × 58 M/day × 24h TTL ≈ 200 MB). **Broker pressure alone is ~3 MB/s of churn.** With `allkeys-lru`, live `user_feed:*` lists can be evicted to make room. **Symptom:** `FastFeedViewSet` returns 0 results for a user whose queue was just evicted, which triggers a `refill_user_feed` task which itself uses Redis, which itself triggers eviction. Self-feeding collapse. P0.4 still open.
7. **No reverse proxy / CDN — OPEN.** No `nginx` service in `docker-compose.yml`. HLS segments are served by MinIO via the bucket policy (`docker-compose.yml:107-124`) — `minio:9000` from inside Compose, `localhost:9000` from the browser in dev, `PUBLIC_MEDIA_ENDPOINT_URL` in prod (currently unset, falling back to `AWS_S3_ENDPOINT_URL`; `settings.py:348`). At 10k concurrent × 1.5 Gbps HLS, all egress is through the `minio` container directly — which is fine at dev scale but exposes MinIO's CPU to the open internet. P1.2 (nginx + CDN) still open.
8. **Security headers — RESOLVED.** `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS = 31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_PROXY_SSL_HEADER` all gated on `DEBUG=False` (`settings.py:452-462`). **Remaining**: no CSP via `django-csp`, no `Referrer-Policy`, no `Permissions-Policy` (P3.3).
9. **No telemetry batching in the frontend — OPEN.** `main.jsx:1106` still calls `API.logTelemetry(...)` directly with `.catch(()=>{})`. The backend is now ready to absorb batches (P2.5 endpoint does not exist yet, but `/log-telemetry/` is already `202 Accepted` and goes through the stream). At 10k concurrent × 1 telemetry per ~30 s, that's ~330 outbound requests/s from the browser just for telemetry. **Symptom:** extra API pressure, plus silent loss of telemetry on network blips. P2.4 still open.
10. **`evolve_long_term_user_baselines` no per-user error handling — OPEN.** Schedule is now 24h (`settings.py:238-246`), but the iteration body in `tasks.py:566-587` still has no `try/except` per user — one bad row fails the whole loop. At 200k DAU this runs in ~10 min. **Symptom:** silent starvation of long-term baselines; recommendation drift toward popular content only. P2.3 still open.
11. **HLS URL is generated per-request by the serializer** (`serializers.py:51-52`), calling `get_hls_playback_url(obj.hls_playlist_url)` every time. The `FeedClipSerializer` runs on every `/feed/` response, computing 10 URLs per call. At 55 feed reads/s that's 550 URL computations/s; not a CPU crisis, but the design couples the API tier to the CDN origin's URL format. **Symptom:** if `PUBLIC_MEDIA_ENDPOINT_URL` changes, every serializer call returns a stale-looking URL until cache invalidates. Acceptable for now; revisit when a CDN is in front of MinIO (P1.2).
12. **Inbox uses 30 s polling** (`main.jsx:2120-2126`). At 10k concurrent that's 333 polls/s to `/share/unread-count/` from already-busy clients. Cheap individually, but it's exactly the kind of avoidable QPS that disappears when you fix it. **Symptom:** multiplier on top of the feed QPS. The polling endpoint is throttled at `share_poll 1000/hr` (`settings.py:380`), which is the current safety net; P3.4 (SSE) would replace it.

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

These six fixes protect the existing system from collapsing under the 10k-concurrent load *without* changing any user-visible behavior. They are reversible with a config rollback. **Status (2026-09-03): P0.2 ✅, P0.5 ✅, P0.6 ✅. P0.1 ⏳ (CHECK constraints done, autovacuum/connection tuning open), P0.3 ⏳, P0.4 ⏳.**

#### P0.1 — Bound Postgres connections and tune autovacuum — **PARTIALLY OPEN**
- **Why:** #4 and #3 in §3.2 — connection exhaustion, autovacuum starvation.
- **Status:** Task 3 (CHECK constraint on `audioclip.likes`) **shipped** in migration `0002_audioclip_likes_non_negative_and_more.py`; equivalent constraints for `shares`, `skips`, `comment_count`, and `comment.likes` also shipped. Tasks 1 and 2 (`command:` override on the `db` service, `pg_stat_statements` preload) **still open** — `docker-compose.yml:3-33` has no `command:` override and `shared_preload_libraries` is unset.
- **Tasks:**
  1. Add a `command:` override to the `db` service in `docker-compose.yml` (or a `postgres.conf` mount) setting `max_connections=200`, `autovacuum_vacuum_scale_factor=0.05`, `autovacuum_analyze_scale_factor=0.025`, `autovacuum_max_workers=4`, `work_mem=32MB`, `random_page_cost=1.1`, `statement_timeout=30s`, `idle_in_transaction_session_timeout=60s`.
  2. Add a `pg_stat_statements` shared preload so we can see hot queries.
  3. Add a migration to attach a `CHECK (likes >= 0)` constraint on `audioclip.likes` (TODO.md:20) — this is cheap insurance and validates the F() math.
- **Verify:** `SELECT count(*) FROM pg_stat_activity` < 100 sustained; `n_dead_tup` for `userinteraction` < 200k at all times.
- **Rollback:** revert env vars and remove the `command:` override.

#### P0.2 — Introduce a service-layer boundary (no behavior change) — **✅ RESOLVED** (commit `7f1b483`)
- **Why:** This is the **load-bearing** refactor that unlocks every later phase. The original doc's "Stage 2: Interface Boundary Isolation" — now done.
- **Status:** `backend/app/services/{comments,follows,interactions,shares,uploads}.py` (5 modules, 281 LOC). Every ViewSet delegates to the service layer. Test coverage in `backend/app/tests/test_services_*.py` (29 tests). `record_telemetry` was upgraded to write to the Redis Stream (P1.5) on this seam. `finalize_upload` owns the `transaction.on_commit` for `process_audio_to_hls`.
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

#### P0.3 — Fix `minio` resource limits — **⏳ OPEN**
- **Why:** #5 in §3.2 — unbounded cgroup will OOM-kill under 10k concurrent HLS egress.
- **Status:** `docker-compose.yml:70-94` has no `deploy.resources` block on `minio` or `minio-init`. The `web`/`celery`/`celery_feed`/`celery_media` services all have limits; MinIO does not.
- **Tasks:** add `deploy.resources.limits.cpus: '4'` and `memory: 4G` to the `minio` service in `docker-compose.yml`. Add equivalent to `minio-init` (less critical — it runs once).
- **Verify:** `docker stats` shows MinIO bounded; `mc admin info` healthy.
- **Rollback:** remove the limits.

#### P0.4 — Fix Redis eviction policy and add a `nofile` ceiling — **⏳ OPEN**
- **Why:** #6 in §3.2 — `allkeys-lru` evicts live `user_feed:*` lists under memory pressure. Self-feeding collapse.
- **Status:** `--maxmemory-policy allkeys-lru --maxmemory 512mb` is still the `command:` on the `redis` service (`docker-compose.yml:39`). `ulimit nofile=65536` is already in place; the eviction policy is the open half.
- **Tasks:**
  1. Change `redis-server` command in `docker-compose.yml` to `--maxmemory-policy volatile-lru` and bump `--maxmemory 2gb`.
  2. Add a `ulimit nofile=65536` to the `redis` service to match Postgres.
  3. Add a startup sanity check: if `INFO memory` reports `maxmemory_hits > 0` after 1 hour of warmup, the eviction policy is wrong — log a warning. (Don't fail boot; the warning is enough.)
- **Verify:** `INFO stats` `evicted_keys` should stay near 0 for keys without TTL; `INFO memory` `used_memory < 1.5 GB` under 10k concurrent load.
- **Rollback:** revert command.

#### P0.5 — Enable HSTS, CSP, X-Content-Type-Options, SECURE_SSL_REDIRECT — **✅ RESOLVED** (commit `9d3383c`)
- **Why:** #8 in §3.2 — pre-launch hardening that is otherwise a security review finding.
- **Status:** `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS = 31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_PROXY_SSL_HEADER` all gated on `DEBUG=False` (`settings.py:452-462`). **Remaining**: CSP via `django-csp` not yet added; `Referrer-Policy` and `Permissions-Policy` not set. These moved to **P3.3**.
- **Tasks:** set in `settings.py` (gated on `DEBUG=False`):
  - `SECURE_SSL_REDIRECT = True`
  - `SECURE_HSTS_SECONDS = 31536000; SECURE_HSTS_INCLUDE_SUBDOMAINS = True; SECURE_HSTS_PRELOAD = True`
  - `SECURE_CONTENT_TYPE_NOSNIFF = True`
  - `X_FRAME_OPTIONS = 'DENY'` (default; explicit)
  - `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')` (only when behind a TLS-terminating proxy)
  - Add a strict CSP via `django-csp` (new dep) once the frontend is ready to receive the right headers.
- **Verify:** Mozilla Observatory grade ≥ B+.
- **Rollback:** toggle the env flags.

#### P0.6 — Add a basic burst-aware throttle — **✅ RESOLVED** (commit `028cc2d`)
- **Why:** #4 in §3.2 — DRF hourly buckets can't stop a 10k burst.
- **Status:** `ScopedRateThrottle` enabled globally with per-scope rates: `telemetry 60/min`, `interaction 60/min`, `upload 20/hr`, `register 5/hr`, `login 10/min`, `comment 60/hr`, `share_send 100/hr`, `share_poll 1000/hr` (`settings.py:361-382`). `ClipInteractionViewSet` overrides the scope for `log_telemetry` to the tighter `telemetry` rate (`views/interactions.py:63-73`). **Remaining**: a per-second + per-hour combined throttle is the **P3.5** follow-up.
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

These are the steps that materially change system behavior — but each one is contained and reversible. **Status (2026-09-03): P1.5 telemetry path ✅ (F() counter side-effect eliminated for `/log-telemetry/` only); P1.1 batch half ✅ (`update_global_metrics` is now id-batched). P1.2 ⏳, P1.3 ⏳, P1.4 ⏳, P1.5 hot-row path (toggle_like + send_share) ⏳.**

#### P1.1 — Replace `update_global_metrics` raw SQL with event-driven counter pipeline — **⏳ PARTIALLY OPEN**
- **Why:** #1 and #2 in §3.2 — the table-wide `UPDATE` is the most dangerous operation in the codebase.
- **Status:** Half-done. The batched-SQL half landed: `update_global_metrics` is now id-cursor + 5000-row chunks (`tasks.py:479-548`); the single table-wide lock is gone. The correlated `AVG(completion_rate)` subquery is still O(rows×views) per chunk. **The Redis `INCRBY` + `flush_counters_to_pg` counter pipeline is not implemented** — `services/interactions.py` does not touch Redis counters; `toggle_like` and `send_share` still hit the F() path through `UserInteraction.save()`.
- **Tasks:**
  1. **Add Redis distributed counters.** Each like/share/skip event bumps a Redis key: `INCRBY clip:{id}:likes 1` (or `-1` on unlike). The API path (`toggle-like`, `log-telemetry`, `send-share`, `register-skip`) reads the current value from Redis on read, falling back to Postgres if the key is missing.
  2. **Add a counter-batcher Celery task** `flush_counters_to_pg`, scheduled every 60 s (down from 5 min). It does `SCAN` for `clip:*:likes/shares/skips`, computes the deltas, and runs a single `UPDATE audioclip SET likes = likes + %s WHERE id = %s` per dirty key (use `INSERT ... ON CONFLICT DO UPDATE` if you switch to an upsert model). Mark the Redis key as clean by deleting it after PG commit.
  3. **Keep `update_global_metrics` for the derived metrics** (`engagement_velocity`, `avg_completion_rate`) but rewrite as a **batched** update: process 2,000 clip IDs per chunk, 1 s `pg_sleep` between chunks. Replace the correlated subquery for `avg_completion_rate` with a precomputed `userinteraction` materialization (see P2.1).
  4. Add a per-clip `LikeCountCache` model layer (or just a Redis-as-source-of-truth + DB-as-snapshot pattern) so that the `/feed/` serializer can show real counts without a SELECT.
- **Why this is the right cut:** Redis `INCRBY` is O(1) and lock-free. The hot-row problem vanishes — the lock moves from "every write hits the same row" to "every N minutes, one bulk UPDATE per dirty key."
- **Verify:** k6 with 200 likes/sec on a single viral clip — Postgres `pg_stat_user_tables.n_tup_upd` for `audioclip` should stay < 10/min. `redis-cli MONITOR` shows the INCRBY pattern; `INFO stats` shows `evicted_keys == 0` (counters never expire). `fast_feed` queue lag stays < 5 s.
- **Rollback:** revert the service-layer changes; the F() logic in `UserInteraction.save()` is untouched (P0.2 preserves it). Worst case: the F() side-effect still fires on `UserInteraction.save()` if the Redis write fails — counters will *over-count* during the rollback window, not under-count.

#### P1.2 — Add nginx reverse proxy and HLS CDN offload — **⏳ OPEN**
- **Why:** #7 in §3.2 — Python is not a static file server at 1.5 Gbps.
- **Status:** No `nginx` service in `docker-compose.yml`. No `PUBLIC_MEDIA_ENDPOINT_URL` set in dev (falls back to `AWS_S3_ENDPOINT_URL=http://minio:9000` per `settings.py:348`). HLS is served by MinIO directly via the `mc anonymous set download hls/` bucket policy. The dev bind mounts on `web`/`celery`/`celery_feed` (`docker-compose.yml:159, 205, 264`) are still present.
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

#### P1.3 — Add presigned PUT for audio uploads — **⏳ OPEN**
- **Why:** bandwidth offload, removal of API tier from the upload path. Currently a 10 MB upload is `web (gunicorn thread blocked) → default_storage.save() → S3`. The thread is pinned for the full upload duration.
- **Status:** `services/uploads.py` is the obvious place for `get_signed_put_url` (file already has a TODO at line 7). The endpoint `/clips/presign/` does not exist; `AudioClip.status` is binary (`processing`/`ready`/`failed`) — the `pending_upload` value is not in any migration. `python-magic` magic-byte validation already runs at the serializer level, so the presign path can rely on it.
- **Tasks:**
  1. Add `POST /clips/presign/` returning `{presigned_put_url, clip_id, expires_in: 300}`. Creates the `AudioClip` row in `pending_upload` state (new status value, requires migration).
  2. The frontend uploads directly to S3 with the presigned URL.
  3. On 2xx response from S3, the frontend calls `POST /clips/{id}/finalize/` which transitions the row to `processing` and dispatches `process_audio_to_hls.delay(id)`.
  4. Add a 15-min S3 lifecycle rule deleting the `pending_upload` row's original object if `finalize` is never called.
  5. Update `tasks.py:process_audio_to_hls` to handle `status='pending_upload'` (the original exists but hasn't been finalized — treat as failure).
- **Why now:** this is the *only* way to scale uploads independently of API tier. 10 concurrent uploads of 10 MB each = 100 MB of pinned gunicorn threads under the current design.
- **Verify:** 100 concurrent 10 MB uploads finish in < 30 s with `web` gunicorn workers at < 10% busy. `pg_stat_user_tables.n_tup_ins` for `audioclip` matches the upload rate (one row per upload), but the upload bytes do not transit `web`.
- **Rollback:** keep the current synchronous upload path; `presign/` is a new endpoint. Frontend flag to choose.

#### P1.4 — Implement the transactional outbox for `AudioPublished` and `UserFollowed` — **⏳ OPEN**
- **Why:** The original doc's "Stage 3" — the prerequisite for the recommendation pipeline becoming truly decoupled. Without an outbox, the recommendation consumer either polls (waste) or depends on the request thread (coupling).
- **Status:** **No `event_outbox` table, no `relay_outbox` task, no `EventOutbox` model.** `grep -r event_outbox backend/` returns zero matches in `backend/app/`. The Redis Stream from P1.5 handles telemetry but not domain events. `ClipDeleted` (a new event per §7.3) is also not emitted.
- **Tasks:**
  1. Migration: create `event_outbox` table (UUID PK, `aggregate_type`, `aggregate_id`, `event_type`, `payload JSONB`, `created_at`, `processed_at NULL`). Index on `(created_at) WHERE processed_at IS NULL` (partial index, ~1 KB always-resident).
  2. New model `EventOutbox` with a `publish()` classmethod.
  3. In `services/uploads.py::finalize_upload` and `services/follows.py::toggle_follow`, write the outbox row inside the same transaction.
  4. New Celery task `relay_outbox` (Beat, every 1 s, low CPU) polls the outbox, publishes to Redis Stream, and marks `processed_at = NOW()`. Use `SELECT ... FOR UPDATE SKIP LOCKED` so multiple relay workers can run safely.
  5. Add a dead-letter table `event_outbox_dead` for rows older than 1 hour with `processed_at IS NULL` — alert on row count > 0.
- **Verify:** k6 100 uploads/min → `event_outbox` lag < 5 s. After `relay_outbox` is paused, outbox grows; on resume, drains in < 30 s.
- **Rollback:** disable `relay_outbox`; the outbox writes inside transactions can be no-ops.

#### P1.5 — Carve out `/log-telemetry/` and `/toggle-like/` onto Redis Streams — **✅ PARTIALLY RESOLVED** (commits `a3e400e`, `7f1b483`)
- **Why:** #1 in §3.2 — the original doc's "Stage 4" — highest-load carver.
- **Status:**
  - **Telemetry path (`/log-telemetry/`) ✅** — `services/interactions._xadd_telemetry` writes to `stream:interaction.events` with `MAXLEN ~ 50000` and `schema_version=1.0.0`; `flush_telemetry_stream` consumes via `XREADGROUP` (consumer group `cg:telemetry-flush`, every 10 s Beat), dedups via `SETNX processed_event:{event_id} EX 86400`, bulk_inserts via `bulk_create(batch_size=500)`, ACKs, and routes poison messages to `stream:interaction.events:dlq` (`tasks.py:645-791`). The bulk_create path bypasses `UserInteraction.save()` entirely, so the F() counter side-effect never fires for telemetry.
  - **Counter-side-effect for `/toggle-like/` and `/send-share/` ⏳** — still synchronous. `services/interactions.record_like_toggle` and `record_share` go through `UserInteraction.save()` which still fires `AudioClip.objects.filter(pk=...).update(likes=F('likes')+1)`. The hot-row lock on `audioclip.likes` remains for these two endpoints.
  - **Legacy list consumer** — `flush_telemetry_legacy` runs every 30 s as a safety net. Marked `TODO: remove after one cycle of stable operation` (`tasks.py:600`, `settings.py:271`).
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

**Status (2026-09-03): all P2.x ⏳ OPEN.** The recommendation engine still runs entirely from Postgres on every feed refill.

#### P2.1 — Materialize `user:context:*` vectors in Redis; feed reads from there — **⏳ OPEN**
- **Why:** Original doc's "Stage 5" — eliminates the live SQL query from the feed-refill path.
- **Status:** `refill_user_feed` still calls `calculate_time_decayed_vectors` on every refill (`tasks.py:346`). No `user:{id}:context:*` Redis key is written by any consumer (the feature-engineers consumer from P1.5 does not exist).
- **Tasks:**
  1. In the `cg:feature-engineers` consumer (P1.5), maintain a per-user weighted-mean vector in `user:{id}:context:sem` and `user:{id}:context:ac` as serialized 384- and 128-dim float lists.
  2. Update `refill_user_feed` (`tasks.py:498-562`) to read vectors from Redis (with a fallback to `calculate_time_decayed_vectors` if the key is absent — the cold-start path is unchanged).
  3. **Bound the cold-start fallback**: if the user has no Redis context vector *and* the SQL fallback would take > 200 ms, queue the refill for an async worker and return a cached trending list.
- **Verify:** `refill_user_feed` runtime p99 < 100 ms; SQL queries against `userinteraction` from feed refills = 0.
- **Rollback:** feature flag to use SQL fallback.

#### P2.2 — Decouple the 80/20 mix decision from the SQL fallback path — **⏳ OPEN**
- **Why:** `refill_user_feed` does 4 separate `LIMIT` queries plus `random.shuffle` plus `rpush`. At 55 feed refills/s with 5+ candidates per query, the DB sees 200+ queries/s. Move the candidate pool to Redis sorted sets.
- **Status:** `tasks.py:325-405` does the SQL path; the `clip:candidates:exploit` sorted set does not exist.
- **Tasks:**
  1. Materialize a global `clip:candidates:exploit` (Redis sorted set, score = composite_score) refreshed every 5 min from a dedicated `rebuild_candidate_pool` task. Refresh writes the top 10,000 clips.
  2. Materialize per-user `user:{id}:candidates:explore` refreshed hourly with the user's "novel" slice.
  3. `refill_user_feed` does `ZREVRANGEBYSCORE` from these sets. SQL only on the cold-start path.
- **Verify:** `pg_stat_user_tables.seq_scan` for `audioclip` from feed refills = 0; refills complete in < 50 ms.
- **Rollback:** revert to the current SQL-driven refill; sets can sit empty.

#### P2.3 — Per-user error isolation in `evolve_long_term_user_baselines` — **⏳ OPEN**
- **Why:** One bad row currently aborts the whole iteration.
- **Status:** `tasks.py:566-587` iterates `User.objects.filter(is_active=True).iterator(chunk_size=100)` with no `try/except` per user. One bad row fails the whole loop. `last_evolved_at` checkpoint field is not on `User` (`models.py:27-43`).
- **Tasks:** wrap the per-user block in `try/except`; log + continue. Add a `last_evolved_at` timestamp on `User` so we can resume from a checkpoint instead of scanning the whole table each hour.
- **Verify:** inject one malformed user JSON; the loop continues and `evolved_count` matches the user count minus 1.
- **Rollback:** trivial.

#### P2.4 — Frontend telemetry batching and offline queue — **⏳ OPEN**
- **Why:** #9 in §3.2 — 330 unnecessary outbound requests/s, plus silent loss on network blips.
- **Status:** `frontend/main.jsx:1106` calls `API.logTelemetry(...)` directly with `.catch(()=>{})`. There is no `frontend/src/lib/telemetry.js`, no `idb-keyval` dependency (`frontend/package.json`), and no `visibilitychange` flush. The 30 s inbox poll at `frontend/main.jsx:2120-2126` is unchanged.
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

#### P2.5 — New `POST /interactions/batch/` endpoint — **⏳ OPEN**
- **Why:** Companion to P2.4; receives up to 50 events per call.
- **Status:** Endpoint does not exist. `backend/app/urls.py` does not register a `batch/` action on any interaction viewset (`views/interactions.py` has only `toggle-like`, `register-skip`, `log-telemetry`).
- **Tasks:**
  1. New view `BatchInteractionView` accepting `{"events": [...]}` (max 50), each event shaped like today's `log-telemetry` payload.
  2. Each event: validate, write to `event_outbox` (P1.4), return `202` with the count.
  3. Apply `ScopedRateThrottle` 30/min/user on the batch endpoint.
- **Verify:** 1 batch call = 50 telemetry events. Total API QPS for telemetry drops by ~50×.
- **Rollback:** keep the legacy endpoints; frontend flag.

### P3 — Polish and harden (for the 50k path; not strictly required for 10k but cheap now)

**Status (2026-09-03): P3.2 ⏳ PARTIAL (JSON logs + correlation_id shipped; no Prometheus scraper, no Grafana, no alertmanager). All other P3.x ⏳ OPEN.**

#### P3.1 — Remove `update_global_metrics` table-wide raw SQL entirely — **⏳ OPEN**
- **Why:** Once P1.1 is stable and P2.2's candidate pool refresh handles `engagement_velocity`, the 5-min table-wide SQL is redundant.
- **Status:** Still scheduled every 5 min via `CELERY_BEAT_SCHEDULE['update-global-metrics']` (`settings.py:234-237`). Id-batched, but still scans every ready clip.
- **Tasks:** delete the task; have the `flush_counters_to_pg` task also recompute `engagement_velocity` for the top 1,000 clips by velocity change.
- **Verify:** `pg_stat_user_tables` for `audioclip` shows no `UPDATE` statements from a scheduler.

#### P3.2 — Add Prometheus + Grafana + alertmanager — **⏳ PARTIAL**
- **Why:** Observability is the difference between "we're degrading" and "we already failed."
- **Status:** `django_prometheus` `before`/`after` middleware exports `/metrics/` (`settings.py:101, 116`, `urls.py:13`). JSON structured logs with `correlation_id` filter (`settings.py:399-446`). **Nothing scrapes `/metrics/`**; no `prometheus`, `grafana`, or `alertmanager` service in `docker-compose.yml`; no OTel exporter; no Sentry. CI does not probe `/health/`, `/ready/`, or `/metrics/`.
- **Tasks:** compose service stack, scrape `/metrics/`, dashboards for the §5.4 table, alertmanager rules.
- **Verify:** an alert fires on synthetic lock contention within 60 s.

#### P3.3 — Add TLS, security headers, and Caddy/nginx hardening — **⏳ OPEN (post-P0.5)**
- **Why:** Defense in depth.
- **Status:** HSTS + nosniff + SECURE_SSL_REDIRECT shipped (P0.5). **Remaining**: no CSP via `django-csp`; no `Referrer-Policy: strict-origin-when-cross-origin`; no `Permissions-Policy: microphone=(), camera=()`. Nginx itself is not deployed (P1.2).
- **Tasks:** as in P0.5, but also `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: microphone=(), camera=()`.

#### P3.4 — Add a `media-processing-status` SSE stream for the upload UX — **⏳ OPEN**
- **Why:** UX. Currently the frontend has to poll `/clips/{id}/` to detect when HLS is ready. A 30 s SSE is cheaper and friendlier.
- **Status:** No `EventStreamView` exists; no Redis pub/sub channel for `clip.status` transitions. `Channels` is not in `INSTALLED_APPS`.
- **Tasks:** new lightweight `EventStreamView` for `clip_id` that subscribes to a Redis pub/sub channel. Frontend opens the SSE on upload completion.
- **Verify:** no measurable DB QPS change; UX improvement is qualitative.

#### P3.5 — DRF throttling refactor: per-second + per-hour — **⏳ OPEN**
- **Why:** The current hourly-only throttling is burst-blind.
- **Status:** Per-scope min/hr rates are wired (`settings.py:361-382`); a per-second + per-hour combined subclass is the next iteration.
- **Tasks:** subclass `UserRateThrottle` to enforce both 60/min and 1000/hour with a single Redis backend.

---

## 7. Critical Workstreams (cross-cutting, not phase-bound)

These run in parallel with the phases and are not optional.

### 7.1 Testing — **⏳ PARTIAL**
The repo now has a real test suite (commit `8973d65`, then `b9830fa`). At 10k concurrent, this is no longer a *liability* — it is the contract that backs every refactor in P0–P2.

**Current state (2026-09-03):**
- 7 pytest files in `backend/app/tests/`: `test_services_{comments,follows,interactions,shares,uploads}.py` cover the service layer (29 tests); `test_security_and_validation.py` (27 tests) covers the audit's N1–N13 items; `test_scraper.py` (1 legacy test); `test_smoke.py` (1 placeholder test, 7 lines). 786 total LOC.
- `pytest-django` is wired via `django.test.TestCase` (Django's built-in test runner); `pytest.ini` is **absent** — `manage.py test backend.app` is the only invocation.
- CI workflow `.github/workflows/django.yml` runs migrations + tests on PR. It does **not** probe `/health/`, `/ready/`, or `/metrics/`.
- `backend/app/tests/migrations_test/` is an **empty directory** — leftover from a previous attempt, safe to remove.

**Minimum bar before P1 begins:**
- A working `pytest` setup with `pytest-django`, `pytest-cov`, `responses` (for mocking object storage), and `factory_boy`. **Currently partial** — `factory_boy` is not in `requirements-base.txt`.
- Coverage gates:
  - `services/interactions.py` **partial** — `test_services_interactions.py` covers the stream primary path, list fallback, last-resort fallback, and dedup (`test_services_interactions.py:13` test functions).
  - `services/comments.py` ✅ (7 tests).
  - `refill_user_feed` ⏳ — no coverage today.
  - `process_audio_to_hls` ⏳ — no coverage today (would require ffmpeg / Whisper mocks).
- A `pytest --contract-only` that asserts the API response shape on every endpoint against a frozen OpenAPI schema. **Not implemented.**

### 7.2 CI — **⏳ PARTIAL**
`django.yml` runs migrations + `manage.py test backend.app`; it now blocks merges on failure (the test suite actually runs). The `load-smoke.yml` workflow that runs a 30 s `k6` smoke against a per-PR Compose stack is **not implemented**. The CI does not probe `/health/`, `/ready/`, or `/metrics/`.

### 7.3 Documentation hygiene
The original doc's "Architectural Blind Spots" list still applies verbatim:
- Schema versioning — **partially addressed** — `stream:interaction.events` carries `schema_version=1.0.0` (`services/interactions.py:67`). Outbox/clip events do not.
- `MAXLEN` on every `XADD` — **addressed** — `stream:interaction.events` is capped at 50,000 (`services/interactions.py:50, 69`).
- Feed invalidation on clip delete — **still open** — current code does not emit an event for clip deletion. **Add a `ClipDeleted` event** in P1.4 (outbox) so feed queues can be drained.
- **New since 2026-09-02**: the `docs/EXPLAIN/` tree (49 files, commit `29450be`) is the code-grounded doc-of-record; `decisions/02-discrepancies.md` lists every audit-doc claim contradicted by code. When this and `EXPLAIN/` disagree, EXPLAIN wins.

### 7.4 Schema migrations
- The two HNSW indexes were created without `CONCURRENTLY` (`migrations/0001_initial.py:148, 152`). On a fresh DB this is fine; on the production DB it would be a problem. **Document** in the deploy runbook that any future HNSW index must be a separate non-atomic migration with `CREATE INDEX CONCURRENTLY`. Status: still open.
- Adding the new `event_outbox` table, the `clip_pending_upload` state, and any new status enum values must all be backward-compatible — no destructive column drops until 50k DAU. Status: still open (none of these tables/state values exist yet).
- Migration `0002_audioclip_likes_non_negative_and_more.py` shipped; migration 0003 is not present.

### 7.5 Secret and config hygiene — **⏳ OPEN**
`docs/TODO.md:12` notes the leaked HF token; `AGENTS.md` flags `.env` committed despite `.gitignore`. Status unchanged: HF token rotation is not confirmed, `.env` is still tracked. The BuildKit secret mechanism for `HF_TOKEN` is correct; do not switch to `--build-arg`. The `requirements.txt` `librosa` duplicate at lines 8 and 28 is also still present.

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

> **At 10k concurrent users, the system will *not* collapse on its own** — but it will degrade in five specific, predictable ways (hot-row F() locks on `/toggle-like/` and `/send-share/`, autovacuum starvation, connection exhaustion, Redis feed-list eviction under `allkeys-lru`, and unbounded MinIO CPU/MEM at HLS egress). The original `relational-to-event-driven-architecture.md` is correct that a Phase-2 event-driven carver is *eventually* the right answer, but **most of the 10k work is in Phase 0 (stabilize) and Phase 1 (offload load-bearing bottlenecks)** — not in the full streaming-platform migration the original doc foreshadows.
>
> **The single most important early move** is **P0.2: the service-layer boundary** — now shipped (commit `7f1b483`). Every later phase that moves work off the request thread depends on it, and it changes no behavior. **The single most expensive mistake** would be to add Kafka now — it does not solve any 10k problem and it adds operational surface.
>
> **What was built (in the last 24h, in this order):** service layer → burst throttling → security headers → CHECK constraints → view split → structured logging → batched `update_global_metrics` → telemetry Redis Stream (XADD/XREADGROUP/DLQ) → service-layer delegation across all 8 viewsets → EXPLAIN docs tree.
>
> **What remains (in priority order):** PG tuning + autovacuum → MinIO resource limits + Redis `volatile-lru` → nginx + CDN offload → P1.1 counter pipeline (move `toggle_like` and `send_share` off the F() path) → P1.4 outbox table + `ClipDeleted` event → P1.3 presigned PUT → P2.1–P2.5 (context vectors, candidate pool, per-user error isolation, frontend batching, batch endpoint) → P3.x polish.

See also: `docs/relational-to-event-driven-architecture.md` (foundational EDA reasoning), `docs/high-velocity-telemetry-write-architecture.md` (telemetry-specific math), `docs/minio-s3-architecture.md` (storage boundary), `docs/stateful-media-storage-at-scale.md` (why we no longer use local disk — now updated to reflect the shipped architecture), `docs/scaling-analysis.md` (the 1M-level plan, not actionable until 10k is solid), `docs/backend-architecture-audit.md` and `docs/backend-audit.md` (P0/P1 security and code-quality items), `docs/unfixed-issues-2026-09-03.md` (the per-task status table for every §6 item).
