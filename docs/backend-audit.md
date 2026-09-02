# EchoFlow — Backend Production Audit Report

> **Date:** 2026-08-18
> **Scope:** All backend files in the EchoFlow project
> **Methodology:** Senior developer review — architecture, security, scalability, silent bugs, sustainability

---

## Table of Contents

1. [Critical: Hardcoded Secrets & Sensitive Data](#1-critical-hardcoded-secrets--sensitive-data)
2. [Security Vulnerabilities](#2-security-vulnerabilities)
3. [Database Design Issues](#3-database-design-issues)
4. [Concurrency & Background Jobs](#4-concurrency--background-jobs)
5. [Performance & Scalability](#5-performance--scalability)
6. [Reliability & Failure Scenarios](#6-reliability--failure-scenarios)
7. [Architecture & Code Organization](#7-architecture--code-organization)
8. [Missing Features / Safeguards](#8-missing-features--safeguards)
9. [Dependency & Deployment Issues](#9-dependency--deployment-issues)
10. [Testing & Observability](#10-testing--observability)
11. [Silent Bugs — Won't Crash But Will Cause Issues](#11-silent-bugs--wont-crash-but-will-issue)
12. [Memory & Resource Leaks](#12-memory--resource-leaks)
13. [Top Issues by Severity](#13-top-issues-by-severity)

---

## 1. Critical: Hardcoded Secrets & Sensitive Data

### Issue 1.1 — Secret key hardcoded in settings.py fallback

- **File:** `EchoFlow/settings.py:12`
- **Code:** `SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY','django-insecure-637(8wp&#7+g)u&10xs!=2dwutofwh1my9la12$ogu9lm^3ye')`
- **Problem:** The fallback secret key is a hardcoded string. If the env var is missing (which it will be in prod if someone forgets to configure it), Django runs with this known-insecure key. JWT tokens, sessions, and CSRF cookies signed with this key are trivially forgeable.
- **Impact:** Complete authentication bypass. An attacker can forge access tokens, refresh tokens, any signed cookie data.
- **Fix:** Remove the hardcoded fallback entirely. Use `django.core.management.utils.get_random_secret_key()` or fail the app startup.

### Issue 1.2 — HuggingFace token in .env file committed to repo

- **File:** `.env:10`
- **Code:** `HF_TOKEN='hf_PUcKKg*****************'`
- **Problem:** The `.gitignore` lists `*.env` but the file is clearly committed (or was committed previously). Even if it was removed from git, the token is still visible in git history. The same HuggingFace token is duplicated in `docker-compose.yml` env vars.
- **Impact:** Token theft, unauthorized API usage, billing abuse.
- **Fix:** Rotate the token immediately. Ensure `.env` is in `.gitignore` and never committed. Use Docker secrets or a secret manager.

### Issue 1.3 — Fernet encryption key hardcoded in models.py

- **File:** `app_1/models.py:16`
- **Code:** `FERNET_KEY = 'OWltmTxL3T9Bw7nN-WCDyyb84DEcOLrZEpcWXsfJCjM='`
- **Problem:** The encryption key for PII (email addresses) is hardcoded as a string literal. Anyone who reads the source code can decrypt all stored emails.
- **Impact:** Complete loss of email encryption. All user emails are trivially decryptable.
- **Fix:** Load from environment variable. Rotate the key (note: rotating a Fernet key means all existing encrypted data becomes unreadable).

### Issue 1.4 — Database password in docker-compose.yml

- **File:** `docker-compose.yml:12`
- **Code:** `POSTGRES_PASSWORD: password`
- **Problem:** The PostgreSQL password is set to literally `password`. This is in the compose file which is committed to version control.
- **Impact:** Anyone with repo access can connect to the database.
- **Fix:** Use environment variables: `POSTGRES_PASSWORD: ${DB_PASSWORD}`.

### Issue 1.5 — Hardcoded auth token in seed_db.py

- **File:** `seed_db.py:8`
- **Code:** `AUTH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."`
- **Problem:** A JWT access token is hardcoded. This token may still be valid.
- **Impact:** Unauthorized access to the API as the token's user.
- **Fix:** Never commit tokens. Pass via environment variable.

### Issue 1.6 — `app_1/.env` also contains secrets

- **File:** `app_1/.env`
- **Problem:** A second `.env` file exists inside the app directory with the same secrets. This is a duplicate that could cause confusion about which env file is actually loaded.
- **Impact:** Secrets in wrong location may be loaded unintentionally or not loaded at all.

---

## 2. Security Vulnerabilities

### Issue 2.1 — DEBUG=True in settings

- **File:** `EchoFlow/settings.py:15`
- **Code:** `DEBUG = True # False`
- **Problem:** Debug mode is enabled. This exposes full stack traces, `settings.py` internals, and SQL queries to attackers. The commented `# False` is not active.
- **Impact:** Information disclosure, SQL injection discovery, full application introspection.
- **Fix:** Set `DEBUG = False` in production. Use environment variable: `DEBUG = os.environ.get('DEBUG', 'False') == 'True'`.

### Issue 2.2 — ALLOWED_HOSTS = ['*']

- **File:** `EchoFlow/settings.py:17`
- **Code:** `ALLOWED_HOSTS = ['*']`
- **Problem:** Accepts requests for any Host header. Combined with DEBUG=True, this enables host header attacks.
- **Impact:** Host header injection, password reset link poisoning, cache poisoning.
- **Fix:** Set to specific domains in production.

### Issue 2.3 — CORS_ALLOW_ALL_ORIGINS = True

- **File:** `EchoFlow/settings.py:58`
- **Code:** `CORS_ALLOW_ALL_ORIGINS = True`
- **Problem:** Any website in the world can make cross-origin requests to your API. Combined with JWT auth, this means any malicious site can make authenticated requests on behalf of users.
- **Impact:** CSRF-style attacks via JavaScript from any domain.
- **Fix:** Set `CORS_ALLOWED_ORIGINS` to specific frontend domains. Remove `CORS_ALLOW_ALL_ORIGINS`.

### Issue 2.4 — No rate limiting on any endpoint

- **Files:** `app_1/views.py` (all ViewSets), `EchoFlow/settings.py`
- **Problem:** There is absolutely no rate limiting configured. No `django-ratelimit`, no custom middleware, no DRF throttling classes. The `SCRAPER_MAX_DOWNLOADS_PER_MIN` setting only applies to the scraper, not API endpoints.
- **Impact:** Brute-force login attacks, unlimited feed scraping, DoS via repeated feed requests, unlimited audio uploads.
- **Fix:** Add DRF throttling:
  ```python
  REST_FRAMEWORK = {
      'DEFAULT_THROTTLE_CLASSES': [...],
      'DEFAULT_THROTTLE_RATES': {'anon': '100/hour', 'user': '1000/hour'}
  }
  ```

### Issue 2.5 — No input validation on audio upload file size/type

- **File:** `app_1/views.py:102`
- **Code:** `parser_classes = [parsers.MultiPartParser, parsers.FormParser]`
- **Problem:** No MAX_FILE_SIZE is set. No file extension validation. No MIME type validation. A user can upload a 10GB file, a malicious `.php` file, or any executable.
- **Impact:** Disk exhaustion DoS, potential server-side code execution if media files are served from a public directory.
- **Fix:** Add `DATA_UPLOAD_MAX_MEMORY_SIZE`, `FILE_UPLOAD_MAX_MEMORY_SIZE`, and a custom validator in the serializer.

### Issue 2.6 — RegisterSerializer exposes email as write_only but still stores/returns it

- **File:** `app_1/serializers.py:109-129`
- **Problem:** `email` is marked `write_only` so it won't appear in responses, but the User model stores it and the `encrypted_email` field uses a hardcoded encryption key (see Issue 1.3). The email is still queryable via the database.
- **Impact:** PII exposure through database access.

### Issue 2.7 — No CSRF protection for JWT-protected API

- **File:** `EchoFlow/settings.py:98`
- **Problem:** `CsrfViewMiddleware` is included, but JWT authentication bypasses CSRF for authenticated requests. However, the token refresh endpoint and register endpoint are not CSRF-protected if accessed from a browser context.
- **Impact:** If any endpoint is accessible from browser-based clients, CSRF attacks are possible.
- **Fix:** Use `SameSite=Strict` cookies for refresh tokens. Consider using `CSRF_TRUSTED_ORIGINS` instead of disabling CSRF entirely.

### Issue 2.8 — Access token lifetime is 1 day

- **File:** `EchoFlow/settings.py:231`
- **Code:** `'ACCESS_TOKEN_LIFETIME': timedelta(days=1)`
- **Problem:** A 1-day access token is extremely long-lived. If stolen, the attacker has 24 hours of access.
- **Impact:** Extended window for token abuse.
- **Fix:** Use 15-60 minute access tokens with longer-lived refresh tokens.

### Issue 2.9 — Refresh token lifetime is 5 days

- **File:** `EchoFlow/settings.py:232`
- **Code:** `'REFRESH_TOKEN_LIFETIME': timedelta(days=5)`
- **Problem:** 5-day refresh tokens are very long. No token rotation or revocation mechanism.
- **Impact:** Stolen refresh tokens remain valid for 5 days. No way to invalidate.
- **Fix:** Implement token blacklisting in Redis. Use shorter lifetimes (15 min access, 7 days refresh with rotation).

### Issue 2.10 — Comment text limited to 500 chars with no sanitization

- **File:** `app_1/models.py:108`
- **Code:** `text = models.CharField(max_length=500)`
- **Problem:** No HTML sanitization or XSS protection. While Django templates auto-escape by default, if the frontend renders comments as HTML, this is vulnerable.
- **Impact:** Stored XSS if frontend renders raw HTML.
- **Fix:** Ensure frontend escapes all comment content. Consider using a sanitization library.

### Issue 2.11 — Share endpoint allows sharing to any user ID

- **File:** `app_1/views.py:464-488`
- **Problem:** `get_object_or_404(User, id=receiver_id)` — no validation that the receiver_id is a valid, active user. No rate limiting on shares. No check that the clip belongs to a valid user who hasn't been deleted.
- **Impact:** Enumeration of user IDs, abuse via mass sharing.
- **Fix:** Add rate limiting per user on shares. Validate receiver exists and is active.

### Issue 2.12 — No content moderation

- **File:** `app_1/models.py:47` (title), `app_1/models.py:108` (comment text)
- **Problem:** No content filtering, profanity check, or moderation queue. Users can upload anything.
- **Impact:** Legal liability for illegal content, spam, abuse.
- **Fix:** Add content moderation pipeline.

---

## 3. Database Design Issues

### Issue 3.1 — No database constraints on counter fields

- **File:** `app_1/models.py:65-68`
- **Code:** `likes`, `shares`, `skips`, `comment_count` are all `BigIntegerField(default=0)`
- **Problem:** No `validators` to prevent negative values. No `CHECK` constraint at the database level. The `UserInteraction.save()` method tries to maintain these counters manually, but there's no unique constraint to prevent duplicate interactions from inflating counts.
- **Impact:** Counter values can become negative if the decrement logic encounters edge cases. No database-level integrity enforcement.
- **Fix:** Add `validators=[MinValueValidator(0)]` and database CHECK constraints.

### Issue 3.2 — UserInteraction save() has a race condition

- **File:** `app_1/models.py:161-184`
- **Problem:** The `save()` method reads the old instance, calculates the delta, then saves, then updates the AudioClip counter. Between reading `old_instance` and updating `AudioClip`, another request could do the same, causing lost updates.
- **Impact:** Incorrect like/share/skip counts under concurrent access.
- **Fix:** Use atomic transactions with `F()` expressions, or use `select_for_update()`.

### Issue 3.3 — Comment save() also has a race condition

- **File:** `app_1/models.py:116-119`
- **Code:** `AudioClip.objects.filter(pk=self.clip.pk).update(comment_count=F('comment_count') + 1)`
- **Problem:** While `F()` expressions are atomic at the DB level, this only runs on `not self.pk` (new objects). If two comments are created simultaneously, the DB-level `F()` handles it correctly, but the model-level check `if not self.pk` could miss edge cases with bulk operations.
- **Impact:** Comment counts could be incorrect after bulk imports.
- **Fix:** Use a signal or override `bulk_create` to handle this.

### Issue 3.4 — No soft delete for any model

- **File:** All models in `app_1/models.py`
- **Problem:** All `on_delete=models.CASCADE`. When a user is deleted, all their clips, comments, interactions, and shares are permanently deleted. There's no way to recover data or preserve analytics.
- **Impact:** Data loss when users delete accounts. Analytics corrupted.
- **Fix:** Use a `SoftDeleteField` or `is_deleted` boolean field with cascading logic.

### Issue 3.5 — ShareEvent uses BigAutoField primary key while other models use UUID

- **File:** `app_1/migrations/0003...:49-51`
- **Problem:** `ShareEvent.id` is `BigAutoField` while `AudioClip`, `Comment`, `UserInteraction` have mixed PK types (`AudioClip` is UUID, `Comment` is UUID, `UserInteraction` is BigAutoField). This inconsistency causes issues with serialization, API design, and distributed systems.
- **Impact:** Inconsistent API URLs, potential client confusion, no distributed PK support.
- **Fix:** Standardize on UUID for all models.

### Issue 3.6 — Missing indexes on frequently queried fields

- **File:** `app_1/models.py`
- **Problem:**
  - `AudioClip.category` has no index (only `category + -likes` composite). Single-field category lookups won't use it efficiently.
  - `UserInteraction.clip` has no index. Queries filtering by clip are common (likes, views) but need to join through the composite unique key.
  - `Comment.author` has no index.
  - `ShareEvent.sender` has no index.
- **Impact:** Slow queries as data grows. O(n) scans on interaction/clip lookups.
- **Fix:** Add individual indexes on `clip`, `author`, `sender`, `receiver`, `category`.

### Issue 3.7 — `update_global_metrics()` uses raw SQL with hardcoded table names

- **File:** `app_1/tasks.py:516-536`
- **Problem:** Raw SQL with `app_1_audioclip` and `app_1_userinteraction` table names. If the app name changes or migrations rename tables, this silently breaks. The `########` comments show awareness this should be ORM but wasn't converted.
- **Impact:** Silent failures if table names change. No migration safety.
- **Fix:** Convert to ORM or use `Connection.queries` logging. Use `AudioClip._meta.db_table` dynamically.

### Issue 3.8 — No database-level unique constraint on username

- **File:** `app_1/migrations/0001_initial.py:34`
- **Note:** `username` does have `unique=True`. But there's no constraint on `encrypted_email` being truly unique when both are null — two users could have `null` encrypted emails.
- **Fix:** Add a partial unique constraint: `unique_together` or `CheckConstraint`.

### Issue 3.9 — Vector dimensions hardcoded inconsistently

- **File:** `app_1/models.py:28,73`
- **Code:** `long_term_semantic` = 384 dims, `semantic_vector` = 384 dims, `acoustic_vector` = 128 dims
- **Problem:** The commented-out code references 1536-dim vectors (OpenAI embedding size). The current 384-dim vectors come from `all-MiniLM-L6-v2`. If someone switches embedding models, the dimensions won't match and the database will reject inserts with a cryptic error.
- **Impact:** Silent failure of recommendation system when switching models.
- **Fix:** Add a field to track embedding model version. Validate dimensions at insertion.

---

## 4. Concurrency & Background Jobs

### Issue 4.1 — Feed refill called twice simultaneously

- **File:** `app_1/views.py:141-142`
- **Code:**
  ```python
  refill_user_feed.delay(user_id, count=10)
  refill_user_feed.delay(user_id, count=40)
  ```
- **Problem:** When the feed is empty, TWO separate `refill_user_feed` tasks are enqueued for the same user. Both will run concurrently, both will compute the same recommendations, and both will push to the same Redis list. This wastes CPU, creates duplicate clips in the feed, and doubles the load on the recommendation algorithm.
- **Impact:** Wasted compute resources, duplicate clips in user feeds, degraded recommendation quality.
- **Fix:** Call `refill_user_feed` once with a larger count. Use Redis SETNX to prevent concurrent refills.

### Issue 4.2 — No task deduplication or idempotency

- **File:** `app_1/tasks.py` (all tasks)
- **Problem:** No mechanism to prevent duplicate task execution. If `process_audio_to_hls` is enqueued twice for the same clip (e.g., duplicate upload), it will process the same file twice. Same for `refill_user_feed`, `update_global_metrics`, etc.
- **Impact:** Wasted resources, incorrect data, potential infinite loops.
- **Fix:** Use Celery task IDs, Redis locks, or database-level uniqueness checks at task start.

### Issue 4.3 — Celery tasks have no retry configuration

- **File:** `app_1/tasks.py`
- **Problem:** No `@shared_task(bind=True, max_retries=3, default_retry_delay=60)` on any task. If Whisper transcription fails due to a transient issue, the task dies permanently with `status='failed'`. No retry mechanism exists.
- **Impact:** Permanent processing failures for transient errors. Clips stuck in 'processing' or 'failed' status forever.
- **Fix:** Add retry configuration to all tasks. Implement exponential backoff.

### Issue 4.4 — Celery worker has no concurrency limit for heavy tasks

- **File:** `docker-compose.yml:99`
- **Code:** `celery_media: ... --pool=solo --loglevel=info`
- **Problem:** `--pool=solo` means only one task runs at a time on the media queue. If one HLS conversion is stuck, all other media tasks are blocked. This is a massive bottleneck.
- **Impact:** Audio processing queue backs up completely. New uploads wait behind stuck tasks.
- **Fix:** Use `--pool=prefork` with appropriate concurrency. Consider multiple workers.

### Issue 4.5 — `evolve_long_term_user_baselines()` loads all users into memory

- **File:** `app_1/tasks.py:539-551`
- **Problem:** `User.objects.filter(is_active=True).iterator(chunk_size=100)` — while it uses iterator, the `users_to_update` list accumulates ALL users before `bulk_update`. With 100K users, this loads 100K objects with 384-dim vectors into memory.
- **Impact:** Memory exhaustion on large datasets. OOM kills.
- **Fix:** Batch the loop: accumulate 100 users, bulk_update, clear the list, repeat.

### Issue 4.6 — No dead letter queue for failed Celery tasks

- **File:** `EchoFlow/settings.py` (Celery config)
- **Problem:** Failed tasks are lost. There's no `CELERY_TASK_ACKS_LATE`, no error tracking, no way to inspect or re-queue failed tasks.
- **Impact:** Silent task failures. No visibility into what went wrong.
- **Fix:** Configure `CELERY_TASK_ACKS_LATE`, integrate Sentry, add a `failed_tasks` table.

### Issue 4.7 — Redis feed queue has no expiration

- **File:** `app_1/tasks.py:378`
- **Code:** `redis_key = f"user_feed:{user_id}"`
- **Problem:** Redis lists are never expired. If a user stops using the app, their feed queue in Redis grows forever with no cleanup. Redis memory grows unbounded.
- **Impact:** Redis OOM. Memory waste for inactive users.
- **Fix:** Set TTL on feed keys: `redis_client.expire(redis_key, 86400)` (24 hours).

### Issue 4.8 — `scrape_and_import` task has no error recovery

- **File:** `app_1/tasks.py:554-620`
- **Problem:** If `module.fetch_audio(limit=limit)` returns 100 items and item #50 fails, items #51-100 are still processed but there's no tracking of which items succeeded/failed. No partial failure handling.
- **Impact:** Incomplete imports with no way to retry failed items specifically.
- **Fix:** Track success/failure per item. Support resume/retry.

### Issue 4.9 — Celery beat has no health monitoring

- **File:** `docker-compose.yml:119-134`
- **Problem:** The beat scheduler runs silently. If it crashes or gets stuck, scheduled tasks (global metrics update, baseline evolution) stop running with no alerting.
- **Impact:** Stale engagement_velocity, stale user vectors, degraded recommendations.
- **Fix:** Add health checks, monitoring, alerting on beat heartbeat.

---

## 5. Performance & Scalability

### Issue 5.1 — N+1 query in `get_is_liked` serializer method

- **File:** `app_1/serializers.py:43-51`
- **Problem:** When `user_has_liked` is NOT annotated (e.g., in `FeedClipSerializer` used without the N+1 fix), this fires a separate database query per clip in the feed. For a page of 10 clips, that's 10 extra queries.
- **Impact:** 10x query multiplier on every feed request. Degrades rapidly with more clips per page.
- **Fix:** Always annotate `user_has_liked` in the view, or use `prefetch_related`.

### Issue 5.2 — `calculate_time_decayed_vectors` loads all interactions without pagination

- **File:** `app_1/tasks.py:441-444`
- **Problem:** `select_related('clip')` joins the entire AudioClip record (including large vector fields) for each interaction. With limit=50, this loads 50 full clip objects. For `evolve_long_term_user_baselines()` with limit=500, this is 500 full clip objects.
- **Impact:** Massive memory usage and slow queries. Vector fields are 384 floats x 8 bytes = ~3KB per clip. 500 clips = 1.5MB just for vectors.
- **Fix:** Only select needed fields: `.only('id', 'semantic_vector', 'acoustic_vector', 'created_at')`.

### Issue 5.3 — `refill_user_feed` queries all seen clips from last 30 days

- **File:** `app_1/tasks.py:384`
- **Problem:** This loads ALL clip IDs the user interacted with in the last 30 days into Python memory. For an active user with thousands of interactions, this is a large list. Then it excludes all of them from the recommendation query.
- **Impact:** Slow refill queries, high memory usage. The `exclude(id__in=seen_ids)` becomes inefficient with large lists.
- **Fix:** Store seen IDs in Redis with TTL. Use Redis SET for O(1) lookups.

### Issue 5.4 — `OwnProfileSerializer.get_liked_clips()` loads 50 liked clips

- **File:** `app_1/serializers.py:160-170`
- **Problem:** Every time a user views their own profile, it loads their 50 most recent liked clips with full serializer data. This is a heavy query for a profile page.
- **Impact:** Slow profile loads, unnecessary database load.
- **Fix:** Make this optional (only load if requested). Use pagination.

### Issue 5.5 — SuggestionViewSet computes full vector similarity per request

- **File:** `app_1/views.py:697-720`
- **Problem:** Every explore request recomputes `calculate_time_decayed_vectors()` (which queries the database), then computes cosine distance for every clip in the category. No caching.
- **Impact:** Slow explore page (~500-2000ms per request). No cache invalidation strategy.
- **Fix:** Cache the user's blended vectors for 5-15 minutes. Use Redis.

### Issue 5.6 — `extract_acoustic_vector` loads entire audio file into memory

- **File:** `app_1/tasks.py:82-121`
- **Problem:** `librosa.load()` loads the entire audio file into RAM. For a 300-second audio file at 22050 Hz stereo, that's 300 x 22050 x 2 x 4 bytes ≈ 53MB. The function also creates multiple intermediate arrays (MFCC, chroma, mel).
- **Impact:** Memory exhaustion when processing large files concurrently.
- **Fix:** Process audio in chunks. Use memory-efficient librosa options.

### Issue 5.7 — No query result caching

- **File:** Throughout `app_1/views.py`
- **Problem:** The Redis cache is only used for feed queues. All other database queries hit the database directly. `FastFeedViewSet.list()`, `SuggestionViewSet.get_queryset()`, `ProfileViewSet.me()` — all query the database fresh every time.
- **Impact:** Every API request is a full database round-trip. No caching benefit.
- **Fix:** Add `@cache_page` decorators or manual caching for read-heavy endpoints.

---

## 6. Reliability & Failure Scenarios

### Issue 6.1 — No handling for `librosa.load()` failure on unsupported formats

- **File:** `app_1/tasks.py:138`
- **Code:** `y, sr = librosa.load(input_file_path, sr=22050)`
- **Problem:** If the uploaded file is corrupted, has an unsupported codec, or is not actually audio, `librosa.load()` raises an exception that is NOT caught by the surrounding try/except (the try block starts at line 147 for Whisper, not for librosa).
- **Impact:** Celery task crashes, clip stuck in 'processing' status forever. No error status set.
- **Fix:** Wrap librosa.load() in a try/except that sets `clip.status = 'failed'`.

### Issue 6.2 — `process_audio_to_hls` doesn't clean up failed HLS output

- **File:** `app_1/tasks.py:191-200`
- **Problem:** If FFmpeg fails, the partially created HLS directory remains on disk under `media/hls/{clip_id}/`. Over time, this accumulates garbage.
- **Impact:** Disk space exhaustion from partial conversions.
- **Fix:** Clean up `output_dir` on failure: `shutil.rmtree(output_dir, ignore_errors=True)`.

### Issue 6.3 — No health check endpoints

- **File:** Throughout project
- **Problem:** No `/health/` or `/ready/` endpoint. Docker has no health check configured. Kubernetes has no liveness/readiness probes. There's no way to know if the app is actually working.
- **Impact:** Unknown system health. Load balancers send traffic to dead containers.
- **Fix:** Add Django health check endpoint. Configure Docker healthcheck.

### Issue 6.4 — `wait_for_db.py` has no timeout

- **File:** `wait_for_db.py:5-11`
- **Problem:** The `while True` loop has no exit condition other than success. If the database never comes up, this script runs forever, blocking container startup.
- **Impact:** Container hangs indefinitely. No graceful degradation.
- **Fix:** Add a max-wait counter and exit with error code.

### Issue 6.5 — No database connection pooling configuration

- **File:** `EchoFlow/settings.py:129-132`
- **Code:** `conn_max_age=600`
- **Problem:** While `conn_max_age=600` enables persistent connections, there's no `CONN_MAX_AGE` tuning, no connection pool size limit, and no handling of stale connections. With gunicorn's 4 workers x 2 threads = 8 concurrent connections, plus Celery workers, this could exceed PostgreSQL's `max_connections`.
- **Impact:** Connection pool exhaustion. "FATAL: too many connections" errors.
- **Fix:** Use `django-db-connection-pool` or configure PgBouncer. Set appropriate `max_connections`.

### Issue 6.6 — No request logging

- **File:** `EchoFlow/settings.py` (MIDDLEWARE)
- **Problem:** `CommonMiddleware` is present but there's no `LoggingMiddleware` or access log configuration. No structured logging for API requests. No request ID tracking.
- **Impact:** No visibility into API usage patterns, error rates, or performance. Impossible to debug production issues.
- **Fix:** Add `django-request-logging` or custom logging middleware. Log request method, path, status, duration.

### Issue 6.7 — `transaction.on_commit` only protects against DB rollback, not task queue failure

- **File:** `app_1/views.py:112`
- **Code:** `transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))`
- **Problem:** This is good practice — it ensures the task is only enqueued after the DB transaction commits. However, if the Redis broker is down, `delay()` will raise an exception and the clip is created but never processed. There's no retry or alerting.
- **Impact:** Clips created but never processed. Silent data loss.
- **Fix:** Add a periodic job to find clips stuck in 'processing' status and re-enqueue them.

### Issue 6.8 — No migration rollback strategy

- **File:** Throughout migrations
- **Problem:** The migration `0001_initial.py` runs `CREATE EXTENSION IF NOT EXISTS vector` but the reverse SQL `DROP EXTENSION IF NOT EXISTS vector` will fail if other tables depend on it. This makes `makemigrations --fake` and rollback dangerous.
- **Impact:** Broken migrations that can't be rolled back.
- **Fix:** Use `atomic=False` for the extension creation. Never drop extensions in reverse.

---

## 7. Architecture & Code Organization

### Issue 7.1 — Single monolithic views.py (867 lines)

- **File:** `app_1/views.py`
- **Problem:** All API endpoints, all business logic, all docstrings are in a single file. No separation of concerns. Adding new features requires editing this file.
- **Impact:** Merge conflicts, hard to review changes, difficult to test specific endpoints.
- **Fix:** Split into separate view modules: `feed_views.py`, `interaction_views.py`, `social_views.py`, etc.

### Issue 7.2 — Duplicate imports throughout views.py

- **File:** `app_1/views.py:1-42`
- **Problem:** Imports are duplicated: `Response` imported at lines 3 and 19, `viewsets` at lines 1 and 21, `permissions` at lines 1 and 21, etc.
- **Impact:** Code smell, indicates copy-paste editing. Confusing for maintainers.
- **Fix:** Deduplicate all imports.

### Issue 7.3 — Admin interface is completely empty

- **File:** `app_1/admin.py`
- **Code:** Just `# Register your models here.`
- **Problem:** No admin registration for any model. Production operators have zero visibility into data through Django admin.
- **Impact:** Impossible to debug data issues via admin. No quick data inspection tool.
- **Fix:** Register all models with appropriate `list_display`, `search_fields`, `list_filter`.

### Issue 7.4 — Scraper logic duplicated between management command and Celery task

- **Files:** `app_1/management/commands/scrape_audio.py` and `app_1/tasks.py:554-620`
- **Problem:** The scrape-and-import logic is implemented in both places with nearly identical code. Any bug fix needs to be applied in two places.
- **Impact:** Diverging behavior, maintenance burden, risk of inconsistency.
- **Fix:** Extract shared logic into a service class. Both the management command and task should call the same service.

### Issue 7.5 — `db_routers.py` is a placeholder with no actual router

- **File:** `app_1/db_routers.py`
- **Code:** `# no need now ; when u get a seprate db for stats that time u will nedd it`
- **Problem:** Dead code that does nothing. It's not even referenced in `settings.py` `DATABASE_ROUTERS`.
- **Impact:** Confusion about whether multi-database support exists.
- **Fix:** Remove or implement actual router.

### Issue 7.6 — No service layer / business logic in views

- **File:** `app_1/views.py`
- **Problem:** All business logic (vector computation, feed ranking, metrics calculation) is inline in views or in tasks. There's no service layer to encapsulate domain logic.
- **Impact:** Logic is scattered, hard to test, hard to reuse.
- **Fix:** Create a `services/` directory with classes like `FeedService`, `RecommendationService`, `InteractionService`.

### Issue 7.7 — App named `app_1` with no meaningful name

- **File:** `app_1/` directory, `EchoFlow/settings.py:76`
- **Problem:** The app is literally named `app_1`. This provides zero semantic value and makes code references confusing.
- **Impact:** Poor code readability, hard to understand project structure.
- **Fix:** Rename to `clips` or `content` or `audio`.

---

## 8. Missing Features / Safeguards

### Issue 8.1 — No file type validation for uploads

- **File:** `app_1/serializers.py:15-24`
- **Problem:** `AudioUploadSerializer` has no validation for file type, size, or format. Any file can be uploaded.
- **Impact:** Malicious file uploads, format incompatibility with FFmpeg/librosa.
- **Fix:** Add `FileExtensionValidator` and MIME type checking.

### Issue 8.2 — No audio duration validation

- **File:** `app_1/serializers.py`
- **Problem:** No validation that uploaded audio is within expected duration range (e.g., 5s to 5 minutes). Silent uploads of 30-minute podcasts that break the short-form audio UX.
- **Impact:** UX inconsistency, processing waste.
- **Fix:** Add duration validation after processing. Reject clips outside range.

### Issue 8.3 — No duplicate detection for uploads

- **File:** `app_1/views.py:106-123`
- **Problem:** No check for duplicate uploads (same file, similar title, etc.). Users can upload the same audio multiple times.
- **Impact:** Duplicate content, wasted storage, corrupted analytics.
- **Fix:** Implement audio fingerprinting (tempo, spectral hash) to detect duplicates.

### Issue 8.4 — No user account deletion / GDPR compliance

- **File:** `app_1/models.py`
- **Problem:** No mechanism for users to delete their accounts and all associated data. No data export feature. No GDPR compliance.
- **Impact:** Legal liability. Cannot comply with data deletion requests.
- **Fix:** Add account deletion endpoint with data purging. Add data export endpoint.

### Issue 8.5 — No API versioning

- **File:** `app_1/urls.py`
- **Problem:** All endpoints are at root level (`/clips/`, `/feed/`, etc.). No version prefix (`/api/v1/`). Any breaking change will break all clients.
- **Impact:** Breaking changes require coordinated client updates.
- **Fix:** Add version prefix: `path('api/v1/', include(router.urls))`.

### Issue 8.6 — No pagination on comment listing

- **File:** `app_1/views.py:532-574`
- **Problem:** `CommentViewSet` has `pagination_class = CommentCursorPagination` but it's a `ModelViewSet` that serves all comments. The pagination is defined but may not be applied consistently with filter backends.
- **Impact:** Loading all comments for a popular clip could be thousands of records.
- **Fix:** Ensure pagination is applied. Test with large comment counts.

### Issue 8.7 — No rate limiting on scraper endpoints

- **File:** `app_1/management/commands/scrape_audio.py`
- **Problem:** The management command can be run repeatedly without any throttling. Each run downloads and processes audio from external sources.
- **Impact:** IP bans from source sites, excessive bandwidth usage, potential legal issues.
- **Fix:** Add rate limiting, caching of fetched items, and a flag to prevent re-scraping.

---

## 9. Dependency & Deployment Issues

### Issue 9.1 — No pinned dependency versions

- **File:** `requirements.txt`
- **Problem:** All dependencies are unpinned. `django`, `celery`, `redis`, etc. will install whatever the latest version is. This causes non-reproducible builds.
- **Impact:** Breaking changes in dependencies cause app failures in production.
- **Fix:** Pin versions: `django==5.1.4`, `celery==5.4.0`, etc. Use `pip-compile` or `poetry`.

### Issue 9.2 — Duplicate dependency: librosa listed twice

- **File:** `requirements.txt:8,28`
- **Problem:** `librosa` appears at lines 8 and 28.
- **Impact:** Minor — installs twice but same version. Code smell.
- **Fix:** Remove duplicate.

### Issue 9.3 — `dotenv` in production dependencies

- **File:** `requirements.txt:14`
- **Problem:** `python-dotenv` is included in production dependencies. It's only needed for local development.
- **Impact:** Unnecessary dependency in production image.
- **Fix:** Move to `requirements-dev.txt`.

### Issue 9.4 — Dockerfile copies everything including node_modules, .git, media

- **File:** `Dockerfile:32`
- **Code:** `COPY . /app/`
- **Problem:** The `.git/` directory, `node_modules/`, `__pycache__/`, and `media/` are all copied into the Docker image. The `.gitignore` doesn't protect Docker builds.
- **Impact:** Larger image, slower builds, potential secret leaks.
- **Fix:** Create a `.dockerignore` file. Exclude `.git`, `__pycache__`, `node_modules`, `media/`, `staticfiles/`.

### Issue 9.5 — Dockerfile uses `python:3.11-slim` but pycache shows Python 3.11, 3.12, 3.13

- **File:** `Dockerfile:2`, `app_1/__pycache__/`
- **Problem:** The Dockerfile targets Python 3.11, but there are `.pyc` files for 3.11, 3.12, and 3.13. This means the code was developed on multiple Python versions, and compatibility is not guaranteed.
- **Impact:** Potential Python version incompatibility in production.
- **Fix:** Test on all supported Python versions. Pin to one version.

### Issue 9.6 — Gunicorn with only 4 workers and 2 threads

- **File:** `docker-compose.yml:34`
- **Code:** `gunicorn EchoFlow.wsgi:application --bind 0.0.0.0:8000 --workers 4 --threads 2`
- **Problem:** 4 workers x 2 threads = 8 concurrent requests. Each request can trigger expensive vector similarity computations. Under load, all slots fill up and requests queue.
- **Impact:** Request timeouts, 502 errors under moderate traffic.
- **Fix:** Increase workers based on CPU cores. Use `--workers $(nproc)` or at least 2x CPU cores + 1.

### Issue 9.7 — No HTTPS enforcement

- **File:** `EchoFlow/settings.py`
- **Problem:** No `SECURE_SSL_REDIRECT`, no `SECURE_HSTS_SECONDS`, no `SECURE_BROWSER_XSS_FILTER`, no `SECURE_CONTENT_TYPE_NOSNIFF`. The `SecurityMiddleware` is present but configured with defaults (all False).
- **Impact:** Man-in-the-middle attacks, XSS, content sniffing attacks.
- **Fix:** Set all `SECURE_*` settings to True in production.

### Issue 9.8 — WhiteNoise configured but media files served by Django in DEBUG

- **File:** `EchoFlow/settings.py:95`, `app_1/urls.py:30`
- **Problem:** WhiteNoise handles static files, but media files are served by Django via `static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)` in urls.py. This is only active when DEBUG=True. In production, media files won't be served unless configured.
- **Impact:** HLS segments not accessible in production.
- **Fix:** Use S3/cloud storage for media in production. Configure `django-storages` (which is installed but not configured).

---

## 10. Testing & Observability

### Issue 10.1 — Only 2 test files exist, covering only scraper

- **File:** `app_1/tests/test_scraper.py`
- **Problem:** No tests for any views, serializers, models, or tasks. The core business logic (feed, interactions, recommendations, vector computation) has zero test coverage.
- **Impact:** Any change to core logic can break functionality with no detection.
- **Fix:** Add tests for all serializers, views, and tasks. Use pytest-django.

### Issue 10.2 — `hidden folder/tests.py` is empty

- **File:** `hidden folder/tests.py`
- **Code:** Just `from django.test import TestCase` and a comment.
- **Problem:** Dead test file that's never run. It's in a "hidden folder" which is gitignored.
- **Impact:** False sense of test coverage.
- **Fix:** Remove or move meaningful tests to `app_1/tests/`.

### Issue 10.3 — No structured logging

- **File:** `app_1/tasks.py:22`
- **Code:** `logger = get_task_logger(__name__)`
- **Problem:** The logger is defined but there's no logging configuration in `settings.py`. No log format, no log level, no file handler. Logs go to stdout with no structure.
- **Impact:** Impossible to aggregate logs, search for errors, or correlate requests.
- **Fix:** Configure `LOGGING` dict in settings with JSON formatter, file handlers, and log levels.

### Issue 10.4 — No metrics collection

- **File:** Throughout project
- **Problem:** No Prometheus metrics, no request counters, no error rates, no latency histograms. No Grafana dashboards.
- **Impact:** No visibility into system performance. Can't detect degradation.
- **Fix:** Add `django-prometheus` or `opentelemetry-django`.

### Issue 10.5 — No error tracking (Sentry, Rollbar, etc.)

- **File:** Throughout project
- **Problem:** No error tracking integration. Exceptions in Celery tasks and views are logged to stdout but not tracked.
- **Impact:** Errors go unnoticed in production. No stack trace aggregation.
- **Fix:** Integrate Sentry with `sentry-sdk`.

---

## 11. Silent Bugs — Won't Crash But Will Cause Issues

### Issue 11.1 — `weights` list never populated in `calculate_time_decayed_vectors`

- **File:** `app_1/tasks.py:449-477`
- **Code:**
  ```python
  sem_vectors, ac_vectors, weights = [], [], []
  ...
  final_weight = time_weight * comp_weight * intent_weight
  ...
  sum_weights = sum(weights)  # Always 0!
  ```
- **Problem:** `weights` is initialized as an empty list but nothing is ever appended to it. `sum_weights` is always 0. The function immediately returns `user.long_term_semantic, user.long_term_acoustic` at line 479 because `sum_weights == 0`.
- **Impact:** **The entire recommendation algorithm is broken.** `calculate_time_decayed_vectors` always returns the user's long-term vectors and never incorporates recent interactions. The feed is essentially random among eligible clips, weighted only by long-term preferences.
- **Severity:** Critical
- **Fix:** Append `final_weight` to `weights` list.

### Issue 11.2 — Same bug in `calculate_blended_query_vectors`

- **File:** `app_1/tasks.py:336-354`
- **Code:**
  ```python
  weights = []
  ...
  weight = 1.0 / (1.0 + math.log(hours_since + 1))
  ...
  total_weight = sum(weights) if weights else 1  # Always 1!
  ```
- **Problem:** Same issue — `weights` is never populated. `total_weight` is always 1. The weighted average is actually just a sum divided by 1, which is incorrect.
- **Impact:** Blended vectors are mathematically wrong.
- **Fix:** Append weights to the list.

### Issue 11.3 — `calculate_dynamic_user_vector` is never called

- **File:** `app_1/tasks.py:287-319`
- **Problem:** This function exists but is never referenced anywhere in the codebase. It's dead code.
- **Impact:** Wasted code. May confuse developers about which function is the actual recommendation engine.
- **Fix:** Remove or integrate into the recommendation pipeline.

### Issue 11.4 — `OPENAI_API_KEY` referenced but never defined

- **File:** `app_1/tasks.py:28, 77`
- **Code:**
  ```python
  #OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # line 28 — commented out
  ...
  if not OPENAI_API_KEY:  # line 77 — NameError!
  ```
- **Problem:** `OPENAI_API_KEY` is commented out at line 28 but referenced at line 77. `get_openai_client()` will raise `NameError: name 'OPENAI_API_KEY' is not defined`.
- **Impact:** If this code path is ever reached (it's in a commented block currently), it crashes.
- **Fix:** Uncomment line 28 or define the variable.

### Issue 11.5 — FeedViewSet is completely commented out

- **File:** `app_1/views.py:164-219`
- **Problem:** The fallback `FeedViewSet` (database-based recommendation) is entirely commented out. If the Redis feed system fails, there's no fallback.
- **Impact:** No graceful degradation. Redis failure = no feed at all.
- **Fix:** Uncomment and enable the fallback feed.

### Issue 11.6 — `scrape_and_import` references missing module exports

- **File:** `app_1/tasks.py:562, 575`
- **Code:** `from app_1.scrapers.sources import SOURCES` and `from app_1.scrapers import downloader, normalizer, uploader`
- **Problem:** `downloader`, `normalizer`, `uploader` are not exported from `app_1.scrapers.__init__.py` — they're not in `__all__`. While Python still imports them (they're modules), this is a maintenance risk.
- **Impact:** Potential import errors if `__init__.py` is refactored.

### Issue 11.7 — `refill_user_feed` uses `calculate_time_decayed_vectors` but the function is broken

- **File:** `app_1/tasks.py:388`
- **Problem:** As identified in Issue 11.1, `calculate_time_decayed_vectors` always returns long-term vectors due to the empty `weights` list. So `refill_user_feed` is computing recommendations based on stale, long-term vectors, not current user preferences.
- **Impact:** Feed recommendations don't adapt to user behavior.
- **Fix:** Fix Issue 11.1 first.

---

## 12. Memory & Resource Leaks

### Issue 12.1 — Global model instances in tasks.py

- **File:** `app_1/tasks.py:24-26`
- **Code:**
  ```python
  whisper_model = None
  embedding_model = None
  kw_model = None
  ```
- **Problem:** These global variables hold large ML model instances in memory across all Celery worker processes. They're never released. With multiple concurrent tasks loading these models, memory grows linearly with concurrency.
- **Impact:** Memory exhaustion in Celery workers. OOM kills.
- **Fix:** Use task-level model loading with `@shared_task(bind=True)` and cleanup in `after_return`. Or use a model caching layer with TTL.

### Issue 12.2 — Scraper `RobotsTxtChecker` caches parsers indefinitely

- **File:** `app_1/scrapers/base.py:14`
- **Code:** `self.parsers = {}`
- **Problem:** The `parsers` dict grows indefinitely as new hosts are scraped. Each entry holds a `RobotFileParser` object.
- **Impact:** Memory leak during long scraping runs.
- **Fix:** Use `functools.lru_cache` with a max size, or TTL-based eviction.

### Issue 12.3 — Temp files may not be cleaned up

- **File:** `app_1/scrapers/downloader.py:37`
- **Code:** `tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)`
- **Problem:** Files are created with `delete=False` and expected to be cleaned up by the caller. If the caller crashes between download and cleanup, orphaned temp files accumulate.
- **Impact:** Disk space exhaustion.
- **Fix:** Use context managers or `atexit` handlers for cleanup.

---

## 13. Top Issues by Severity

| # | Issue | Severity | Confidence |
|---|-------|----------|------------|
| 1 | `weights` list never populated — recommendation algorithm broken | **Critical** | Confirmed |
| 2 | Hardcoded secrets (secret key, DB password, HF token, Fernet key) | **Critical** | Confirmed |
| 3 | No rate limiting on any API endpoint | **Critical** | Confirmed |
| 4 | DEBUG=True + ALLOWED_HOSTS=['*'] + CORS_ALLOW_ALL_ORIGINS=True | **Critical** | Confirmed |
| 5 | No retry configuration for Celery tasks | **High** | Confirmed |
| 6 | No file type/size validation on uploads | **High** | Confirmed |
| 7 | Race condition in UserInteraction.save() counter updates | **High** | Confirmed |
| 8 | Redis feed queue never expires | **High** | Confirmed |
| 9 | No HTTPS enforcement (SECURE_* settings) | **High** | Confirmed |
| 10 | OPENAI_API_KEY NameError (undefined variable) | **High** | Confirmed |
| 11 | No database constraints on counter fields | **High** | Likely |
| 12 | Monolithic views.py with no service layer | **Medium** | Confirmed |
| 13 | No tests for core business logic | **Medium** | Confirmed |
| 14 | No structured logging or error tracking | **Medium** | Confirmed |
| 15 | No API versioning | **Medium** | Confirmed |
| 16 | Docker image includes .git, node_modules, __pycache__ | **Medium** | Confirmed |
| 17 | Unpinned dependency versions | **Medium** | Confirmed |
| 18 | Duplicate imports in views.py | **Low** | Confirmed |
| 19 | Dead code (db_routers.py, commented FeedViewSet, unused function) | **Low** | Confirmed |
| 20 | No admin interface configured | **Low** | Confirmed |

---

*This audit covers every backend file in the repository. The most critical finding is **Issue 11.1**: the recommendation algorithm's `weights` list is never populated, meaning the entire vector-based recommendation system is non-functional — it always falls back to long-term vectors regardless of recent user behavior.*

---

## 14. Verification & Fix Status (post-audit, 2026-09-02)

A direct code-verification pass was done after this audit was written. Several "Confirmed" findings turned out to be inaccurate against the actual source. The status below supersedes the severity table above for the issues that were re-checked.

### 14.1 False positives — claim was wrong, no fix needed

| Audit ID | Claimed | Actual state | Evidence |
|----------|---------|--------------|----------|
| 11.1 | `weights` list never populated in `calculate_time_decayed_vectors` | `weights.append(final_weight)` exists at `backend/app/tasks.py:582` inside the for-loop body | Direct read of the function |
| 11.2 | Same bug in `calculate_blended_query_vectors` | `weights.append(weight)` exists at `backend/app/tasks.py:458` | Direct read |
| 11.4 | `OPENAI_API_KEY` is `NameError` (commented out, referenced) | Defined at `backend/app/tasks.py:30` as `os.getenv("OPENAI_API_KEY") or ""` | Direct read |
| 1.3 | `FERNET_KEY` hardcoded as string literal | Read from `FIELD_ENCRYPTION_KEY` env var at `backend/app/models.py:16`; fails fast via `ImproperlyConfigured` if missing | Direct read |
| 1.4 | `POSTGRES_PASSWORD: password` hardcoded in `docker-compose.yml` | Uses `${DB_PASSWORD}` interpolation; password comes from `.env` | Direct read of compose file |
| 1.5 | Hardcoded JWT in `seed_db.py` | `os.environ.get('SEED_AUTH_TOKEN', '')`; aborts with error if unset | Direct read |
| 2.1 | `DEBUG = True` hardcoded | `DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() == 'true'` — env-driven, defaults to False | `backend/EchoFlow/settings.py:15` |
| 2.2 | `ALLOWED_HOSTS = ['*']` | `os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')` — env-driven | `settings.py:17` |
| 2.4 | No rate limiting | DRF throttling IS configured (`'DEFAULT_THROTTLE_CLASSES'` and `'DEFAULT_THROTTLE_RATES'`) | `settings.py:310-317` |

### 14.2 True positives — confirmed and fixed

The following issues were confirmed against the source and fixed in commits `ef5400a` and `ac9a88a` on the `main` branch.

| Audit ID | Issue | Fix location | Fix summary |
|----------|-------|--------------|-------------|
| 1.1 | Static `SECRET_KEY` fallback `'dev-only-temporary-key'` | `backend/EchoFlow/settings.py:12-21` | Replaced with fail-fast: `raise ImproperlyConfigured` if `DJANGO_SECRET_KEY` unset. Matches the `FERNET_KEY` pattern. A per-process random key would silently break session/CSRF signing across the gunicorn + Celery fleet because each worker would have a different key. |
| 2.3 | `CORS_ALLOW_ALL_ORIGINS = True` hardcoded on `settings.py:49` | `backend/EchoFlow/settings.py:49` | Changed to `False` so the env-driven `DJANGO_CORS_ALL` flag controls the behavior. |
| 3.1 | No DB constraints on counter fields | `backend/app/models.py:108-115, 128-131` | Added `CheckConstraint(likes__gte=0)` etc. to `AudioClip` and `Comment` Meta classes. **Migration required**: `python manage.py makemigrations && python manage.py migrate`. |
| 3.2 | `UserInteraction.save()` race on `is_new` branch | `backend/app/models.py:166-191` | `is_new` branch now wrapped in `transaction.atomic()` with a pre-existence check to prevent double-increment under concurrent `get_or_create`. |
| 4.1 | Double `refill_user_feed` enqueue in `FastFeedViewSet` | `backend/app/views.py:128-140` + `backend/app/tasks.py:518-525` | Two fixes: (1) removed the second `refill_user_feed.delay()` call at `views.py:140` (root cause was at the call site); (2) added a Redis `SETNX` lock in the task as defense-in-depth against cross-request concurrent refills. |
| 4.3 | No retry config on any Celery task | `backend/app/tasks.py:173-178, 512, 666, 694, 710` | Added `bind=True, max_retries=3, default_retry_delay=60, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600` to all 5 tasks. `RETRYABLE_ERRORS` covers `OperationalError, ConnectionError, subprocess.CalledProcessError, OSError`. |
| 4.7 | Redis feed queue no TTL | `backend/app/tasks.py:567-571` | `redis_client.expire(redis_key, 86400)` after every `rpush` and on the queue-sufficient early-return path. |
| 6.1 | `librosa.load()` unprotected | `backend/app/tasks.py:236-243` | Wrapped in `try/except` that sets `clip.status = 'failed'` and saves, matching the existing Whisper error-handling pattern. |
| 6.2 | HLS scratch files leaked on early-failure paths | `backend/app/tasks.py:227-321` | The outer `try/finally` block at line 227 already cleans up `normalized_path` and `local_hls_dir` for any path that enters the try block. The audit's concern about `normalize_to_wav` failure was checked and confirmed safe — `normalized_path` is only assigned on success, so there is no leak on that path. **No code change required**, but the failure paths now route through the retry decorator instead of permanently failing. |
| 8.1 | No file type/size validation | `backend/app/serializers.py:16-37` | Added `ALLOWED_EXT` (8 audio extensions), `MAX_SIZE = 100 MB`, and `validate_original_file()` method to `AudioUploadSerializer`. Rejects unsupported types and oversized files at the serializer boundary for fast client feedback. |
| 12.1 | Global ML model singletons not thread-safe | `backend/app/tasks.py:8, 26, 33-78` | Added `import threading`, `_model_lock = threading.Lock()`, and refactored all three `get_*_model()` functions to use double-checked locking. Prevents duplicate model loads under concurrent first-call. |

### 14.3 Confirmed but not yet fixed (deferred)

These remain as known issues but were not addressed in this pass because they are out of scope for "critical bugs" or require larger architectural changes:

| Audit ID | Reason deferred |
|----------|-----------------|
| 1.2 | Rotating secrets requires coordination with the deployer; this is an ops task, not a code change. The committed `.env` was checked — it is **not** tracked by git (`git ls-files .env` returns nothing), so the leak vector is from working copies only. |
| 1.6 | Duplicate `app_1/.env` — minor, no code change required. |
| 4.2 | Task idempotency needs a per-task deduplication design (Celery `task_id` or DB unique constraint); not a quick fix. |
| 4.4 | `--pool=solo` for `celery_media` is intentional given the 1 GB memory limit per worker (see `docker-compose.yml:329`); changing concurrency requires a resource-limits review. |
| 4.5, 4.6, 4.8, 4.9 | Operational improvements (monitoring, dead letter queue, scrape error recovery, beat health) — need a dedicated observability pass. |
| 5.x | Performance/scalability issues — covered separately in `docs/backend-architecture-audit.md`. |
| 6.3, 6.4, 6.5, 6.6, 6.7, 6.8 | Observability and ops gaps — see architecture audit. |
| 7.x | Architecture cleanup (split views.py, remove dead code, rename `app_1`) — belongs in a refactor pass. |
| 9.x | Dependency and deployment — requires regenerating `wheelhouse/`. |
| 10.x | Testing and observability infrastructure. |
| 11.3, 11.5, 11.6, 11.7 | Dead code / commented blocks — cleanup pass. |
| 12.2, 12.3 | Memory/resource leak in scraper — low priority. |

### 14.4 Migration required

`python manage.py makemigrations && python manage.py migrate` must be run after pulling these changes to apply the new `CheckConstraint` definitions on `AudioClip` and `Comment` models. Existing rows with negative counter values (if any) will block the migration; the recommended pre-check is:

```sql
SELECT id, likes, shares, skips, comment_count FROM app_audioclip
WHERE likes < 0 OR shares < 0 OR skips < 0 OR comment_count < 0;
```

If any rows are returned, correct them before running the migration.
