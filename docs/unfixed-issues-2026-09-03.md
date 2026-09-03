# docs/unfixed-issues-2026-09-03.md

> **Purpose:** Code-anchored status table for every item in `docs/event-driven-architecture-plan.md` §6 (P0/P1/P2/P3), §7 (cross-cutting workstreams), plus the storage-architecture items in `docs/stateful-media-storage-at-scale.md`.
>
> **Branch under audit:** `feat/stage2-service-layer-and-telemetry-stream` (heads through 3d973a7, dated 2026-09-02 → 2026-09-03).
>
> **Source of truth:** When this report and the source code disagree, the code wins. Every claim below cites a `file:line` anchor against the working tree.
>
> **Status legend:**
> - **✅ RESOLVED** — shipped on the branch under audit; no further work.
> - **🟡 PARTIAL** — partly shipped; the remaining work is enumerated.
> - **⏳ OPEN** — not started. Includes items the docs explicitly call "blocker for production" or "launch checklist."
> - **🚧 BLOCKED** — open and depending on a different open item.

---

## 1. Executive Summary

| Bucket | Resolved | Partial | Open | Blocked | Total |
|---|---:|---:|---:|---:|---:|
| §6 P0 (Stabilize) | 3 | 1 | 2 | 0 | 6 |
| §6 P1 (Offload bottlenecks) | 1 | 1 | 3 | 0 | 5 |
| §6 P2 (Decouple recommendation) | 0 | 0 | 5 | 0 | 5 |
| §6 P3 (Polish) | 0 | 1 | 4 | 0 | 5 |
| §7 Cross-cutting | 0 | 3 | 6 | 0 | 9 |
| Storage architecture | 4 | 1 | 5 | 0 | 10 |
| **Total** | **8** | **7** | **27** | **0** | **42** |

**The single largest unmitigated risk** is the F() counter side-effect that still fires on `/toggle-like/` and `/send-share/`. The telemetry path (`/log-telemetry/`) was carved off the F() path by the bulk_create consumer in P1.5; the two remaining hot rows are unmitigated at 10k concurrent.

**The second-largest unmitigated risks** are infrastructure-level: no nginx/CDN in front of MinIO, no PgBouncer, no Postgres tuning, Redis still on `allkeys-lru`, MinIO without resource limits. None of these are code changes — they are compose-file and config-file changes.

**The third bucket** is the event-driven migration of the recommendation engine and the outbox: the patterns are documented, the service-layer seam is in place, but the actual `event_outbox` table, `relay_outbox` task, `user:context:*` Redis keys, and `clip:candidates:exploit` sorted set are all absent. These are P1.4 / P2.x work.

---

## 2. Resolved (✅)

Each row in this section was either shipped on the branch under audit or was already correct in the codebase and is now explicitly documented. Code anchor + commit + verification cite.

| # | Item | Anchor | Commit / PR | Verification |
|---|---|---|---|---|
| 1 | **P0.2 — Service-layer boundary.** `backend/app/services/{comments,follows,interactions,shares,uploads}.py` (5 modules, 281 LOC). All 8 ViewSets call service functions; no view owns ORM writes directly. | `backend/app/services/__init__.py` (empty), `services/{comments,follows,interactions,shares,uploads}.py` | `7f1b483` (`refactor(services): Stage 2 service-layer boundary (no behavior change)`) | 29 service-layer tests in `backend/app/tests/test_services_*.py` |
| 2 | **P0.5 — Production security headers.** `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS = 31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`, `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_PROXY_SSL_HEADER` gated on `DEBUG=False`. | `backend/EchoFlow/settings.py:452-462` | `9d3383c` (`fix(backend): remove dead CORS double-assignment + add production SECURE_*`) | Headers visible at `curl -I` against `web` when `DJANGO_DEBUG=False` |
| 3 | **P0.6 — Burst-aware per-scope throttling.** `ScopedRateThrottle` enabled globally with per-scope rates. `log_telemetry` overrides to the tighter `telemetry 60/min` scope. | `backend/EchoFlow/settings.py:361-382`, `backend/app/views/interactions.py:63-73` | `028cc2d` (`fix(security): enable JWT rotation/blacklist + per-endpoint throttles + logout`) | 60th `/log-telemetry/` per minute → 429 |
| 4 | **P0.1 (partial) — DB-level CHECK constraints on counters.** `likes/shares/skips/comment_count >= 0` on `audioclip`; `comment.likes >= 0`. | `backend/app/models.py:103-106, 123`; migration `0002_audioclip_likes_non_negative_and_more.py` | (shipped with 0002) | Migration runs clean against a fresh DB |
| 5 | **P1.5 (telemetry path) — `/log-telemetry/` on Redis Stream.** `XADD` with `MAXLEN ~ 50000` and `schema_version=1.0.0`; `flush_telemetry_stream` consumer (XREADGROUP, SETNX dedup, bulk_create, XACK, DLQ). | `backend/app/services/interactions.py:48-75, 124-168`; `backend/app/tasks.py:645-791`; `settings.py:255-265` | `a3e400e` (`feat(telemetry): migrate flush pipeline to Redis Stream (LIST retained as fallback)`); `7f1b483` (service-layer move) | 13 stream tests in `test_services_interactions.py`; `XLEN stream:interaction.events` bounded |
| 6 | **`update_global_metrics` id-batched.** Cursor `update_global_metrics:resume_id` in Redis cache; 5000-row chunks; two SQL statements per batch. Single table-wide lock is gone. | `backend/app/tasks.py:479-548` | `7701677` (`fix(backend): cleanup_stuck_processing + batched global metrics + slower baseline schedule`) | `pg_locks` shows no ACCESS SHARE on `audioclip` between batches |
| 7 | **`evolve_long_term_user_baselines` 1h → 24h.** Schedule changed from 3600s to 86400s. | `backend/EchoFlow/settings.py:238-246` | `7701677` | Inline DECISION comment explains the math |
| 8 | **`cleanup_stuck_processing` (audit item 6.7).** Re-enqueues clips stuck in `processing` past 15 min; flips to `failed` after `threshold_minutes * 3` (45 min). | `backend/app/tasks.py:795-831`; `settings.py:247-254` | `7701677` | Beat every 5 min; re-enqueue cap at `max_per_run=50` |
| 9 | **JWT rotation + blacklist + logout.** `ROTATE_REFRESH_TOKENS = True`, `BLACKLIST_AFTER_ROTATION = True`, `token_blacklist` in `INSTALLED_APPS`, `UPDATE_LAST_LOGIN = True`. | `backend/EchoFlow/settings.py:387-397` | `028cc2d` | `/auth/logout/` blacklists the refresh token; next `/auth/token/refresh/` returns 401 |
| 10 | **JSON structured logging + correlation_id.** `python-json-logger` formatter, `CorrelationIdFilter` injects per-request id; `CorrelationIdMiddleware` runs before `SecurityMiddleware` so even 301s get an id. | `backend/EchoFlow/settings.py:100-117, 399-446` | `c90ae23` (`feat(observability): correlation_id middleware + JSON log field`) | Every log line has `correlation_id`; Celery picks up from task headers |
| 11 | **View split into a package.** `backend/app/views.py` → `backend/app/views/{auth,comments,content,feed,interactions,profile,social}.py` + `_pagination.py`. | `backend/app/views/__init__.py` (8 modules) | `1c3be4b` (`refactor(backend): split monolithic views.py into 7 modules`) | `wc -l backend/app/views/*.py` totals 624 LOC across 8 files |
| 12 | **Object-level permission on `CommentViewSet`.** `IsAuthorOrReadOnly` on `PUT/PATCH/DELETE`. | `backend/app/views/comments.py` | `4d15f02` (`fix(security): object-level IsAuthorOrReadOnly on CommentViewSet (N1)`) | 27 security/validation tests in `test_security_and_validation.py` |
| 13 | **Per-action throttles on `ShareViewSet`.** Inbox polling `share_poll 1000/hr`; sending `share_send 100/hr`. `GenericViewSet` (no `ListModelMixin`). | `backend/app/views/social.py` | `4d15f02` | `inbox/` returns 429 after 1000/hr; `send_share/` after 100/hr |
| 14 | **`watch_time_ms` cap + comment text sanitize.** Telemetry validation rejects `watch_time_ms > clip.duration_ms * 1.1` (or sensible absolute cap). `Comment.text` HTML-stripped on save. | `backend/app/serializers.py` | `e6a80b6` (`fix(security): cap watch_time_ms + sanitize comment text`) | Audit N4, N6 |
| 15 | **Magic-byte audio validation.** `python-magic` MIME check at the serializer level, before ffmpeg ever sees the file. Disguised executables rejected with 400. | `backend/app/serializers.py` | `2715b54` (`feat(security): magic-byte audio validation via python-magic`) | Audit N8 |
| 16 | **CORS dead-code fix.** Removed the duplicate `CORS_ALLOW_ALL_ORIGINS = True` reassignment. Now explicitly `False`; `CORS_ALLOWED_ORIGINS` is the env-driven allowlist. | `backend/EchoFlow/settings.py:27-32` | `9d3383c` | AGENTS.md #2 |
| 17 | **Object storage architecture (MinIO + S3Storage + split ACL).** `STORAGES["default"]` is `S3Storage` with `addressing_style="path"`, `default_acl=None`, `querystring_auth=True`. `hls/` public-read via `mc anonymous set download`; `uploads/` private with signed URLs (1h TTL). | `backend/EchoFlow/settings.py:300-334`; `docker-compose.yml:70-124`; `backend/app/media_urls.py:43-92` | (multiple commits in the minio-s3-architecture era) | `verify_minio_deployment.sh` (10 checks) |
| 18 | **HLS upload pipeline uses ephemeral scratch.** `tempfile.mkstemp` for input; `tempfile.mkdtemp(prefix=f'hls-{clip_id}-')` for output; `finally:` block deletes both. | `backend/app/tasks.py:185, 212, 308-314` | (multiple commits) | Local scratch never persists across container restarts |
| 19 | **`process_audio_to_hls` retry config.** `bind=True, max_retries=3, default_retry_delay=60, autoretry_for=RETRYABLE_ERRORS, retry_backoff=True, retry_backoff_max=600`. | `backend/app/tasks.py:163` | `7c6608b` (`fix(backend): critical P0 fixes in tasks.py`) | Transient `OperationalError` / `ConnectionError` / `subprocess.CalledProcessError` / `OSError` are auto-retried |
| 20 | **HLS MPEG-TS segments (not fMP4).** `ffmpeg -hls_segment_type mpegts` explicit. | `backend/app/tasks.py:270-277` | (in place before the branch; documented in the `verify_decoder_rootcause.sh` era) | `verify_decoder_rootcause.sh` confirms `47401111` sync byte |
| 21 | **`celery_media` 1G → 4G.** Whisper base + SentenceTransformer + KeyBERT resident. 1G was OOMKill-ing. | `docker-compose.yml:325-340` | `a672c52` (`fix(deploy): raise celery_media memory to 4G to fit ML models`) | `docker stats` shows the container at ~3 GB steady state |
| 22 | **`.gitignore` `[Bin|Obj]*/` fix.** Pattern was swallowing all `backend/` paths. | `.gitignore` | `42064bb` (`fix(repo): .gitignore pattern [Bin|Obj]*/ swallowed all backend/ paths`) | `git status` doesn't see `backend/` as ignored |
| 23 | **N2 fix (UNCOMMITTED in working tree, on this branch).** The F() counter `UPDATE` in `UserInteraction.save()` is now wrapped inside the `transaction.atomic()` block. The previous code did the row lock and `is_active` comparison outside `atomic()`, opening a race window between releasing the lock and writing the counter — two concurrent toggle-like requests for the same (user, clip) could each read the old `is_active=True` and each bump the counter, double-counting. The fix is in `backend/app/models.py:173-206`; see the `N2 fix` comment at line 174 | uncommitted (working tree) | visible in `git diff backend/app/models.py`; fixes audit item N2 |
| 24 | **N3 fix (UNCOMMITTED in working tree, on this branch).** Fernet email encryption removed. `encrypted_email` field, `FIELD_ENCRYPTION_KEY` import-time check, and `User.save()` Fernet override are all gone. Plaintext `AbstractUser.email` is the source of truth; the Fernet mechanism was misleading theatre (no decryption path, non-deterministic IV made the `unique=True` constraint unreliable, `TagsViewSet.initialize_vectors` was re-encrypting on every vector update). **Note:** `docs/EXPLAIN/auth/02-pii-encryption.md`, `EXPLAIN/backend/02-models.md`, `EXPLAIN/postgresql/01-schema.md`, and several other EXPLAIN docs are now stale on this point | uncommitted (working tree) | visible in `git diff backend/app/models.py`; N3 rationale at `models.py:15-23`; fixes audit item N3 |
| 23 | **Dead code removed (~180 LOC).** Dead helpers and the OpenAI triple-quoted string statement are gone. | `backend/app/tasks.py` (see `git show 1bb0978`) | `1bb0978` (`chore(backend): remove dead code (~180 lines)`) | `grep calculate_blended_query_vectors backend/app/` returns 0 hits in `views/` and `services/` |
| 24 | **Test suite seeded.** 7 pytest files, 56 `test_` functions. Service layer covered by `test_services_*.py`; security/validation by `test_security_and_validation.py`. | `backend/app/tests/` (7 files, 786 LOC) | `8973d65` (`test(backend): pytest-django + 27 security/validation tests + cleanup_stuck_processing bug fix`); `b9830fa` (`test(services): coverage for Stage 2 service layer + telemetry stream paths`) | `manage.py test backend.app` runs the suite green |
| 25 | **EXPLAIN docs tree.** 49 markdown files under `docs/EXPLAIN/` derived from source. Includes `decisions/02-discrepancies.md` listing every audit-doc claim contradicted by code. | `docs/EXPLAIN/` | `29450be` (`added /EXPLAIN directory explaining everythin in ./docs`) | `ls docs/EXPLAIN/` shows 16 category directories |
| 26 | **Verification scripts.** `verify_minio_deployment.sh`, `test_minio_edge_cases.py`, `verify_clip_url.sh`, `verify_decoder_rootcause.sh`, `verify_hls_playback.html`. | `backend/scripts/` | (multiple commits) | Scripts run green against a fresh `docker compose up` |
| 27 | **Object-level permission on `CommentViewSet`.** `IsAuthorOrReadOnly`. | `backend/app/views/comments.py` | `4d15f02` | Already in row 12 (audit N1) |

---

## 3. Partial (🟡)

Items in this section are partly shipped. The remaining work is enumerated.

### 3.1 P0.1 — Postgres tuning (CHECK constraints done; connection / autovacuum tuning open)

- **Shipped:** Migration `0002_audioclip_likes_non_negative_and_more.py` adds `CheckConstraint(likes/shares/skips/comment_count >= 0)` on `audioclip` and `comment.likes >= 0`. The DB-level safety net is in place.
- **Remaining (open):**
  1. Add a `command:` override to the `db` service in `docker-compose.yml` (or a `postgres.conf` mount) setting `max_connections=200`, `autovacuum_vacuum_scale_factor=0.05`, `autovacuum_analyze_scale_factor=0.025`, `autovacuum_max_workers=4`, `work_mem=32MB`, `random_page_cost=1.1`, `statement_timeout=30s`, `idle_in_transaction_session_timeout=60s`.
  2. Add a `pg_stat_statements` shared preload (`shared_preload_libraries = 'pg_stat_statements'`) so hot queries are visible.
  3. Deploy PgBouncer in transaction-pool mode at 25 pool size in front of `web` and `celery_*` (NOT `celery_media` — it holds long-running HLS tasks).
- **Anchor:** `docker-compose.yml:3-33` has no `command:` override; no `pg_stat_statements` preload; no `pgbouncer` service.
- **Why partial matters:** The CHECK constraints are the "cheap insurance" half; the autovacuum and connection half are the "cascade of 5xx" half (failure mode #3 and #4 in `docs/event-driven-architecture-plan.md:222-223`). Both halves are needed before 10k concurrent.

### 3.2 P1.1 — Counter pipeline (id-batched SQL done; Redis INCRBY + flush_counters_to_pg open)

- **Shipped:** `update_global_metrics` is id-cursor + 5000-row chunks (`backend/app/tasks.py:479-548`). The single table-wide lock is gone.
- **Remaining (open):**
  1. Add Redis distributed counters: `INCRBY clip:{id}:likes 1` on toggle_like / send_share / log-telemetry, with `-1` on un-toggle. Read path falls back to Postgres if the Redis key is missing.
  2. Add a `flush_counters_to_pg` Celery task scheduled every 60 s. SCAN for `clip:*:likes/shares/skips`, compute deltas, run `UPDATE audioclip SET likes = likes + %s WHERE id = %s` per dirty key, then delete the Redis key.
  3. Replace the correlated `AVG(completion_rate)` subquery in `update_global_metrics` with a precomputed `userinteraction` materialization.
- **Anchor:** `backend/app/services/interactions.py` does not touch Redis counters; `record_like_toggle` and `record_share` still go through `UserInteraction.save()` (`models.py:173-206`) which fires the F() update.
- **Why partial matters:** The hot-row F() problem is **only** eliminated for `/log-telemetry/`. `/toggle-like/` and `/send-share/` still serialize on `audioclip.likes` for viral clips. This is the **single largest unmitigated lock risk** at 10k concurrent.

### 3.3 P1.5 — Telemetry stream (telemetry path done; counter side-effect on toggle_like / send_share open)

- **Shipped:** Telemetry `/log-telemetry/` writes to `stream:interaction.events` (`services/interactions.py:48-75`); `flush_telemetry_stream` consumes via `XREADGROUP` (`tasks.py:645-791`). `bulk_create` bypasses `UserInteraction.save()`, so the F() counter side-effect never fires for telemetry.
- **Remaining (open):** Move `record_like_toggle` and `record_share` to Redis Streams + Redis counters + bulk_create, so the F() side-effect is bypassed for these two endpoints too.
- **Anchor:** `services/interactions.py:84-95, 171-181` still call `UserInteraction.save()` and trigger `models.py:200-206`.
- **Why partial matters:** Same as 3.2 — this is the largest unmitigated lock risk. P1.1 and P1.5 share the same fix (move toggle_like / send_share off the F() path).

### 3.4 P3.2 — Observability (middleware + JSON logs done; scraping / dashboards / alerting open)

- **Shipped:** `django_prometheus` `before`/`after` middleware (`settings.py:101, 116`); `/metrics/` exported (`urls.py:13`); `health.py` / `ready.py` exist and wire `/health/` / `/ready/`; JSON structured logs with `correlation_id` filter (`settings.py:399-446`).
- **Remaining (open):**
  1. No service scrapes `/metrics/`. No `prometheus` / `grafana` / `alertmanager` in `docker-compose.yml`.
  2. CI (`django.yml`) does not probe `/health/`, `/ready/`, or `/metrics/`.
  3. No OTel exporter. No Sentry.
  4. Dashboards for the §5.4 alert thresholds (lock wait > 1s, autovacuum backlog > 1M dead tuples, Redis memory > 80%, etc.) are not built.
- **Anchor:** `docker-compose.yml` has no observability services; `.github/workflows/django.yml` does not curl `/health/`.
- **Why partial matters:** Observability is the difference between "we're degrading" and "we already failed." At 10k concurrent, partial observability = blind.

### 3.5 §7.1 — Test coverage (service layer + security/validation covered; refill_user_feed / process_audio_to_hls / contract tests open)

- **Shipped:** 7 pytest files (786 LOC, 56 `test_` functions). `test_services_{comments,follows,interactions,shares,uploads}.py` cover the service layer. `test_security_and_validation.py` (27 tests) covers audit N1–N13.
- **Remaining (open):**
  1. No coverage for `refill_user_feed` (`backend/app/tasks.py:325-405`).
  2. No coverage for `process_audio_to_hls` (`backend/app/tasks.py:163-322`) — would need ffmpeg / Whisper mocks.
  3. No `pytest.ini`; the project uses `manage.py test backend.app`.
  4. No `factory_boy` in `requirements-base.txt`.
  5. No `pytest --contract-only` that asserts API response shape against a frozen OpenAPI schema.
  6. `backend/app/tests/migrations_test/` is an **empty directory** — leftover from a previous attempt.
- **Anchor:** `backend/app/tests/`; `requirements-base.txt`.
- **Why partial matters:** The service layer is the load-bearing contract; coverage exists for it. The recommendation engine and the HLS pipeline (the most complex and most-likely-to-regress code) are uncovered.

### 3.6 §7.2 — CI (tests + migrations gated on PR; load-smoke and health/ready/metrics probes open)

- **Shipped:** `.github/workflows/django.yml` runs migrations + `manage.py test backend.app` on PR; blocks merges on failure.
- **Remaining (open):**
  1. No `load-smoke.yml` workflow that runs a 30 s `k6` smoke against a per-PR Compose stack and asserts p99 < §5 thresholds.
  2. CI does not probe `/health/`, `/ready/`, or `/metrics/` after `migrate` + `collectstatic` + `gunicorn` start.
- **Anchor:** `.github/workflows/django.yml`; `.github/workflows/codeql.yml` exists but is unrelated.
- **Why partial matters:** Tests catch logic bugs; load-smoke catches capacity regressions. Without it, every refactor in P1.x is a roll of the dice at 10k concurrent.

### 3.7 Storage — Ephemeral scratch + object storage (done; CDN + OAC + lifecycle open)

- **Shipped:** See Resolved row 18.
- **Remaining (open):** See Open row 4 (CDN + OAC) and Open row 6 (Glacier lifecycle).

---

## 4. Open (⏳)

Items in this section have not been started. They are ordered by **priority for 10k concurrent**, then by **risk if skipped**, then by **effort**.

### 4.1 P0.3 — MinIO resource limits

- **What:** Add `deploy.resources.limits.cpus: '4'` and `memory: 4G` to the `minio` service in `docker-compose.yml`. Add equivalent to `minio-init` (less critical).
- **Anchor:** `docker-compose.yml:70-94` has no `deploy.resources` block on either service.
- **Effort:** 0.25 engineer-weeks. **5 minutes of YAML.**
- **Risk if skipped at 10k concurrent:** MinIO OOM-kills under 1.5–2 Gbps HLS egress (failure mode #5 in `event-driven-architecture-plan.md:224`). The container is the bottleneck for every HLS read in the absence of a CDN.
- **No dependencies.**

### 4.2 P0.4 — Redis eviction policy + memory bump

- **What:** Change `redis-server` command in `docker-compose.yml` to `--maxmemory-policy volatile-lru` and bump `--maxmemory 2gb`. Add a startup sanity check that logs a warning if `INFO memory` reports `maxmemory_hits > 0` after 1 hour of warmup.
- **Anchor:** `docker-compose.yml:39` is still `--appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru`.
- **Effort:** 0.25 engineer-weeks.
- **Risk if skipped at 10k concurrent:** Self-feeding collapse — live `user_feed:*` lists evicted under memory pressure, triggering `refill_user_feed` tasks that themselves use Redis, triggering more eviction. (Failure mode #6 in `event-driven-architecture-plan.md:225`.)
- **No dependencies.**

### 4.3 P1.2 — nginx reverse proxy + HLS CDN offload

- **What:** Add `nginx` service to `docker-compose.yml` with:
  - `proxy_pass http://web:8000` for `/api/*`, `/auth/*`, `/admin/*`, `/health/`, `/ready/`, `/metrics/`
  - `proxy_pass http://web:8000` for `/static/*` (WhiteNoise)
  - `proxy_pass http://minio:9000` for `/hls/*` (until CDN is in place) with `proxy_buffering on; proxy_cache_valid 200 1y;` and `proxy_force_ranges on;`
  - TLS termination (Let's Encrypt via `certbot` sidecar, or Caddy with auto-TLS)
- **What (CDN):** Add CloudFront / Cloudflare / Bunny CDN in front of `/hls/*`. Origin is the nginx → MinIO chain (or MinIO directly with OAC). Cache TTL 1 year; `Range` requests must reach the origin.
- **What (cleanup):** Remove the dev bind mount of project root into `web` / `celery` / `celery_feed` (`docker-compose.yml:159, 205, 264`).
- **Anchor:** No `nginx` service in `docker-compose.yml`; no CDN env var; `PUBLIC_MEDIA_ENDPOINT_URL` falls back to `AWS_S3_ENDPOINT_URL` (`settings.py:348`).
- **Effort:** 2 engineer-weeks.
- **Risk if skipped at 10k concurrent:** Python serves 1.5 Gbps HLS bytes through gunicorn threads (failure mode #7 in `event-driven-architecture-plan.md:226`). The "16 concurrent requests, but each might be a 1 MB HLS segment" problem.
- **Dependencies:** None, but logically grouped with P1.3 (presigned PUT — both depend on the same DNS / TLS / domain assumptions).

### 4.4 P1.3 — Presigned PUT for direct-to-S3 uploads

- **What:** Add `POST /clips/presign/` returning `{presigned_put_url, clip_id, expires_in: 300}`. Creates the `AudioClip` row in `pending_upload` state (new status value, requires migration). Frontend uploads directly to S3 with the presigned URL. On 2xx, frontend calls `POST /clips/{id}/finalize/` which transitions to `processing` and dispatches `process_audio_to_hls.delay(id)`. Add a 15-min S3 lifecycle rule deleting orphan `pending_upload` original objects.
- **Anchor:** `backend/app/services/uploads.py:7` has a TODO for `get_signed_put_url`. No `/clips/presign/` endpoint; `AudioClip.status` is binary (`processing` / `ready` / `failed`).
- **Effort:** 1.5 engineer-weeks.
- **Risk if skipped at 10k concurrent:** 10 concurrent uploads of 10 MB each = 100 MB of pinned gunicorn threads (`event-driven-architecture-plan.md:498-507`).
- **Dependencies:** None independent of P1.2; the doc lists them together because they share a DNS / TLS / domain story.

### 4.5 P1.4 — Transactional outbox for `AudioPublished`, `UserFollowed`, `ClipDeleted`

- **What:** Migration: create `event_outbox` table (UUID PK, `aggregate_type`, `aggregate_id`, `event_type`, `payload JSONB`, `created_at`, `processed_at NULL`). Index on `(created_at) WHERE processed_at IS NULL`. New model `EventOutbox` with `publish()` classmethod. In `services/uploads.py::finalize_upload` and `services/follows.py::toggle_follow`, write the outbox row inside the same transaction. New Celery task `relay_outbox` (Beat, every 1 s) polls the outbox, publishes to Redis Stream, marks `processed_at = NOW()`. Use `SELECT ... FOR UPDATE SKIP LOCKED` for multiple relay workers. Dead-letter table `event_outbox_dead` for rows older than 1 hour.
- **Anchor:** `grep -r event_outbox backend/` returns zero matches in `backend/app/`. No `EventOutbox` model. No `relay_outbox` task.
- **Effort:** 1.5 engineer-weeks.
- **Risk if skipped at 10k concurrent:** The recommendation consumer either polls (waste) or depends on the request thread (coupling). `ClipDeleted` event also missing (referenced in `event-driven-architecture-plan.md:590`).
- **Dependencies:** Service layer (✅ shipped).

### 4.6 P2.1 — `user:context:*` materialized in Redis

- **What:** In the `cg:feature-engineers` consumer (P1.5), maintain a per-user weighted-mean vector in `user:{id}:context:sem` and `user:{id}:context:ac` as serialized 384- and 128-dim float lists. Update `refill_user_feed` to read from Redis with a fallback to `calculate_time_decayed_vectors` if the key is absent. Bound the cold-start fallback: if the user has no Redis context vector *and* the SQL fallback would take > 200 ms, queue the refill for an async worker and return a cached trending list.
- **Anchor:** `refill_user_feed` (`backend/app/tasks.py:325-405`) still calls `calculate_time_decayed_vectors` on every refill; no `user:{id}:context:*` key is written by any consumer.
- **Effort:** 1 engineer-week.
- **Risk if skipped at 10k concurrent:** Feed refills regress under load. The recommendation engine hits Postgres on every refill.
- **Dependencies:** P1.5 (the cg:feature-engineers consumer must exist; the current implementation has only `cg:telemetry-flush`).

### 4.7 P2.2 — Candidate pool in Redis sorted sets

- **What:** Materialize a global `clip:candidates:exploit` (Redis sorted set, score = `composite_score`) refreshed every 5 min from a dedicated `rebuild_candidate_pool` task. Refresh writes the top 10,000 clips. Materialize per-user `user:{id}:candidates:explore` refreshed hourly. `refill_user_feed` does `ZREVRANGEBYSCORE` from these sets. SQL only on the cold-start path.
- **Anchor:** `backend/app/tasks.py:325-405` does the SQL path; the `clip:candidates:exploit` sorted set does not exist.
- **Effort:** 1.5 engineer-weeks.
- **Risk if skipped at 10k concurrent:** 200+ SQL queries/s for feed refills at 55 feed refills/s with 5+ candidates per query.
- **Dependencies:** P2.1.

### 4.8 P2.3 — Per-user error isolation in `evolve_long_term_user_baselines`

- **What:** Wrap the per-user block in `try/except`; log + continue. Add a `last_evolved_at` timestamp on `User` so we can resume from a checkpoint instead of scanning the whole table each cycle.
- **Anchor:** `backend/app/tasks.py:566-587` iterates `User.objects.filter(is_active=True).iterator(chunk_size=100)` with no `try/except` per user. `last_evolved_at` field is not on `User` (`backend/app/models.py:27-43`).
- **Effort:** 0.5 engineer-weeks.
- **Risk if skipped at 10k concurrent:** Silent starvation of long-term baselines; recommendation drift toward popular content only. (Failure mode #10 in `event-driven-architecture-plan.md:229`.)
- **No dependencies.**

### 4.9 P2.4 — Frontend telemetry batching and offline queue

- **What:** New `frontend/src/lib/telemetry.js`: in-memory ring buffer of pending events; `flush()` sends up to 50 events in one POST to `/interactions/batch/`; `flush()` on `visibilitychange === 'hidden'` (pagehide); on network error, enqueue to IndexedDB (via `idb-keyval`); on app start, drain IndexedDB queue first. Inbox polling stays at 30 s but switches to `fetch` with `keepalive: true` and a `BackgroundSync` registration if available.
- **Anchor:** `frontend/main.jsx:1106` calls `API.logTelemetry(...)` directly with `.catch(()=>{})`. No `frontend/src/lib/telemetry.js`. No `idb-keyval` in `frontend/package.json`.
- **Effort:** 1.5 engineer-weeks.
- **Risk if skipped at 10k concurrent:** ~330 outbound requests/s from the browser just for telemetry. Silent loss on network blips. (Failure mode #9 in `event-driven-architecture-plan.md:228`.)
- **Dependencies:** P2.5 (the batch endpoint must exist first).

### 4.10 P2.5 — `POST /interactions/batch/` endpoint

- **What:** New view `BatchInteractionView` accepting `{"events": [...]}` (max 50), each shaped like today's `log-telemetry` payload. Each event: validate, write to `event_outbox` (P1.4), return `202` with the count. Apply `ScopedRateThrottle` 30/min/user.
- **Anchor:** `backend/app/urls.py` does not register a `batch/` action on any interaction viewset. `backend/app/views/interactions.py` has only `toggle-like`, `register-skip`, `log-telemetry`.
- **Effort:** 1 engineer-week.
- **Risk if skipped at 10k concurrent:** Telemetry QPS is unmitigated on the client side. Each browser call competes for the same connection pool as feed reads.
- **Dependencies:** Service layer (✅ shipped); P1.4 (outbox table) for the write path.

### 4.11 P3.1 — Remove `update_global_metrics` table-wide raw SQL entirely

- **What:** Delete the task. Have `flush_counters_to_pg` (P1.1) also recompute `engagement_velocity` for the top 1,000 clips by velocity change.
- **Anchor:** `backend/app/tasks.py:479-548` still scheduled every 5 min via `settings.py:234-237`. Id-batched, but still scans every ready clip.
- **Effort:** 0.5 engineer-week.
- **Risk if skipped at 10k concurrent:** The 1–5 s lock spikes every 5 min persist.
- **Dependencies:** P1.1 (the Redis counter pipeline must be in place so we have a source of truth for the counters `update_global_metrics` currently recomputes).

### 4.12 P3.3 — CSP, Referrer-Policy, Permissions-Policy

- **What:** Add `django-csp` to `requirements-base.txt`. Configure a strict CSP for the API responses. Add `Referrer-Policy: strict-origin-when-cross-origin` and `Permissions-Policy: microphone=(), camera=()` to the security-headers block.
- **Anchor:** `backend/EchoFlow/settings.py:452-462` ships HSTS + nosniff + SSL redirect; CSP/Referrer/Permissions are absent. `django-csp` is not in `INSTALLED_APPS`.
- **Effort:** 0.5 engineer-week.
- **Risk if skipped at 10k concurrent:** Defense-in-depth gap; surfacing in pentest findings, not in production load.
- **No dependencies.**

### 4.13 P3.4 — `media-processing-status` SSE stream

- **What:** New `EventStreamView` for `clip_id` that subscribes to a Redis pub/sub channel. Frontend opens the SSE on upload completion.
- **Anchor:** No `EventStreamView` exists; no Redis pub/sub channel for `clip.status` transitions. `Channels` is not in `INSTALLED_APPS`.
- **Effort:** 1 engineer-week.
- **Risk if skipped at 10k concurrent:** UX gap; the frontend polls `/clips/{id}/` instead.
- **No dependencies.**

### 4.14 P3.5 — DRF throttling refactor: per-second + per-hour

- **What:** Subclass `UserRateThrottle` to enforce both 60/min and 1000/hour with a single Redis backend. Replace the current per-scope min/hr rates.
- **Anchor:** Per-scope min/hr rates are wired (`settings.py:361-382`); a per-second + per-hour combined subclass is the next iteration.
- **Effort:** 0.5 engineer-week.
- **Risk if skipped at 10k concurrent:** Per-second burst protection is approximate.
- **No dependencies.**

### 4.15 Storage — CDN distribution in front of `/hls/`

- **What:** Provision CloudFront / Cloudflare / Bunny distribution in front of `hls/`. Origin is the nginx → MinIO chain (or MinIO directly with OAC). Cache TTL 1 year. `Range` requests must reach the origin.
- **Anchor:** `PUBLIC_MEDIA_ENDPOINT_URL` defaults to `AWS_S3_ENDPOINT_URL` (`settings.py:348`); no CDN env var.
- **Effort:** 2 engineer-weeks (part of P1.2).
- **Risk if skipped at 10k concurrent:** MinIO is exposed to the open internet without rate limiting, no cache, and no OAC. Egress costs at 1.5 Gbps HLS.
- **Dependencies:** P1.2.

### 4.16 Storage — S3 lifecycle rule for `uploads/` (Glacier transition)

- **What:** Configure an S3 lifecycle rule on the `uploads/` prefix to transition objects to Glacier / Deep Archive after 7 days. Originals are no longer needed once the HLS chunks are generated.
- **Anchor:** No S3 lifecycle rule on the bucket. `uploads/` stays in standard storage.
- **Effort:** 0.25 engineer-week.
- **Risk if skipped at 10k concurrent:** Storage cost at 10M objects / 150 TB scale is ~$3,500/month instead of a fraction of that.
- **No dependencies.**

### 4.17 Storage — OAC bucket policy restricting `hls/` to CDN-only

- **What:** Replace `mc anonymous set download local/.../hls` with a CloudFront OAC bucket policy. The `hls/` prefix becomes private; only the CloudFront distribution can read it.
- **Anchor:** `docker-compose.yml:107-108` sets `mc anonymous set download` on the `hls/` prefix.
- **Effort:** 0.5 engineer-week (part of P1.2).
- **Risk if skipped at 10k concurrent:** Direct S3 bandwidth theft; anyone can read `/hls/` and serve it themselves.
- **Dependencies:** P1.2 (CDN distribution must exist).

### 4.18 Storage — `task_soft_time_limit` on `heavy_media`

- **What:** Configure `task_soft_time_limit=600` on the `celery_media` service. If a long-clip transcoding hits the limit, the worker gets a `SoftTimeLimitExceeded` exception that the task handler can catch and clean up scratch.
- **Anchor:** `celery_media` service command in `docker-compose.yml:299-301` has no `--soft-time-limit` flag.
- **Effort:** 0.25 engineer-week.
- **Risk if skipped at 10k concurrent:** Long audio files (>3 min) may hit a hard kill before the `finally:` block runs, leaking local scratch.
- **No dependencies.**

### 4.19 §7.3 — `ClipDeleted` event

- **What:** Emit a `ClipDeleted` event in `services/uploads.py` (or a new `services/clip_deletion.py`) so feed queues can be drained on clip delete.
- **Anchor:** No `ClipDeleted` event in `services/`; no outbox table to write it to (P1.4).
- **Effort:** 0.5 engineer-week.
- **Risk if skipped at 10k concurrent:** Feed lists retain references to deleted clips; users see broken playback.
- **Dependencies:** P1.4.

### 4.20 §7.4 — HNSW `CREATE INDEX CONCURRENTLY` for future indexes

- **What:** **Document** in the deploy runbook that any future HNSW index must be a separate non-atomic migration with `CREATE INDEX CONCURRENTLY`. Audit any future migration that adds an index.
- **Anchor:** `backend/app/migrations/0001_initial.py:148, 152` uses default `CREATE INDEX` (would block writes on prod).
- **Effort:** 0.25 engineer-week.
- **Risk if skipped at 10k concurrent:** A migration that adds an index on a large table will block writes for the duration of the build.
- **No dependencies.**

### 4.21 §7.5 — Secret and config hygiene

- **What:** Rotate the HF token, remove `.env` from the repo, confirm `.gitignore` covers it. The BuildKit secret mechanism for `HF_TOKEN` is correct; do not switch to `--build-arg`. The `requirements.txt` `librosa` duplicate at lines 8 and 28 is also still present.
- **Anchor:** `docs/TODO.md:12` notes the leaked HF token; `AGENTS.md` flags `.env` committed despite `.gitignore`. Status unchanged.
- **Effort:** 0.5 engineer-week.
- **Risk if skipped at 10k concurrent:** Operational hygiene; not load-bearing but a real concern.
- **No dependencies.**

### 4.22 §7.5 — Remove `flush_telemetry_legacy`

- **What:** Mark `flush_telemetry_legacy` and its Beat schedule for removal after one cycle of stable operation.
- **Anchor:** `backend/app/tasks.py:600` has `TODO: remove after one cycle of stable operation`; `settings.py:271` has the same comment.
- **Effort:** 0.1 engineer-week.
- **Risk if skipped at 10k concurrent:** Dead code accumulates; the safety-net consumer runs every 30 s.
- **No dependencies.**

### 4.23 §7.5 — Remove `backend/app/tests/migrations_test/`

- **What:** Remove the empty `backend/app/tests/migrations_test/` directory. `rm -rf backend/app/tests/migrations_test/`.
- **Anchor:** `backend/app/tests/migrations_test/__init__.py` (6 lines, imports nothing) and an empty `__pycache__/`.
- **Effort:** 0.05 engineer-week.
- **Risk if skipped at 10k concurrent:** None; hygiene only.
- **No dependencies.**

### 4.24 Storage — Dev bind mounts on `web` / `celery` / `celery_feed`

- **What:** Remove the dev bind mount of project root into `web` / `celery` / `celery_feed` (`docker-compose.yml:159, 205, 264`). The bind mount is what makes `MEDIA_ROOT` "work" for local dev; with MinIO it's not needed and creates confusion about where state lives.
- **Anchor:** `docker-compose.yml:159, 205, 264`.
- **Effort:** 0.5 engineer-week (part of P1.2).
- **Risk if skipped at 10k concurrent:** None; cleanup.
- **Dependencies:** P1.2 (the new nginx service should be in place first so dev isn't broken).

### 4.25 `pytest.ini` + `factory_boy` + contract tests

- **What:** Add a `pytest.ini` to wire `pytest-django` properly. Add `factory_boy` to `requirements-base.txt`. Build a `pytest --contract-only` that asserts the API response shape on every endpoint against a frozen OpenAPI schema.
- **Anchor:** No `pytest.ini`; `factory_boy` not in `requirements-base.txt`; no contract test file.
- **Effort:** 1 engineer-week.
- **Risk if skipped at 10k concurrent:** Tests run via `manage.py test`; coverage of the API contract is implicit. API shape regressions slip through.
- **No dependencies.**

### 4.26 CI — `load-smoke.yml` + health probes

- **What:** Add a `load-smoke.yml` workflow that runs a 30 s `k6` smoke against a per-PR Compose stack and asserts p99 < thresholds from `event-driven-architecture-plan.md` §5. Add `/health/`, `/ready/`, `/metrics/` probes to `django.yml`.
- **Anchor:** `.github/workflows/django.yml` does not probe these endpoints.
- **Effort:** 1 engineer-week.
- **Risk if skipped at 10k concurrent:** Capacity regressions ship unnoticed.
- **No dependencies.**

### 4.27 P1.4 — `ClipDeleted` event + outbox table

- **What:** See row 4.5 for the outbox. `ClipDeleted` is a separate event in the same outbox.
- **Anchor:** Same as 4.5.
- **Risk if skipped at 10k concurrent:** Feed lists retain references to deleted clips.

---

## 5. Blocked (🚧)

No items are currently blocked on the branch under audit. The dependencies between items are documented in §4. None of those dependencies are circular.

The previously-implicit dependency "service layer → P1.x" is now unblocked because the service layer (P0.2) shipped.

The previously-implicit dependency "telemetry stream consumer exists → P2.1 user:context" is **not yet unblocked**: the current `cg:telemetry-flush` consumer (P1.5) handles telemetry persistence only. The `cg:feature-engineers` consumer that would write `user:{id}:context:*` Redis keys does not exist. **P2.1 should be re-scoped to depend on a new consumer group landing.**

---

## 6. Cross-Reference with `docs/event-driven-architecture-plan.md`

| Doc item | Section | Status | Anchor in this report |
|---|---|---|---|
| P0.1 | 6.P0.1 | 🟡 Partial | §3.1 |
| P0.2 | 6.P0.2 | ✅ Resolved | §2 #1 |
| P0.3 | 6.P0.3 | ⏳ Open | §4.1 |
| P0.4 | 6.P0.4 | ⏳ Open | §4.2 |
| P0.5 | 6.P0.5 | ✅ Resolved | §2 #2 |
| P0.6 | 6.P0.6 | ✅ Resolved | §2 #3 |
| P1.1 | 6.P1.1 | 🟡 Partial | §3.2 |
| P1.2 | 6.P1.2 | ⏳ Open | §4.3 |
| P1.3 | 6.P1.3 | ⏳ Open | §4.4 |
| P1.4 | 6.P1.4 | ⏳ Open | §4.5 |
| P1.5 (telemetry) | 6.P1.5 | ✅ Resolved | §2 #5 |
| P1.5 (counter side-effect) | 6.P1.5 | 🟡 Partial | §3.3 |
| P2.1 | 6.P2.1 | ⏳ Open | §4.6 |
| P2.2 | 6.P2.2 | ⏳ Open | §4.7 |
| P2.3 | 6.P2.3 | ⏳ Open | §4.8 |
| P2.4 | 6.P2.4 | ⏳ Open | §4.9 |
| P2.5 | 6.P2.5 | ⏳ Open | §4.10 |
| P3.1 | 6.P3.1 | ⏳ Open | §4.11 |
| P3.2 | 6.P3.2 | 🟡 Partial | §3.4 |
| P3.3 | 6.P3.3 | ⏳ Open | §4.12 |
| P3.4 | 6.P3.4 | ⏳ Open | §4.13 |
| P3.5 | 6.P3.5 | ⏳ Open | §4.14 |
| §7.1 Testing | 7.1 | 🟡 Partial | §3.5 |
| §7.2 CI | 7.2 | 🟡 Partial | §3.6 |
| §7.3 Doc hygiene (ClipDeleted) | 7.3 | ⏳ Open | §4.19 |
| §7.3 Doc hygiene (schema_version) | 7.3 | ✅ Resolved (telemetry path) | §2 #5 |
| §7.3 Doc hygiene (MAXLEN) | 7.3 | ✅ Resolved | §2 #5 |
| §7.4 Schema migrations (CONCURRENTLY) | 7.4 | ⏳ Open | §4.20 |
| §7.5 Secret hygiene | 7.5 | ⏳ Open | §4.21 |
| §7.5 flush_telemetry_legacy removal | 7.5 | ⏳ Open | §4.22 |
| §7.5 empty migrations_test dir | 7.5 | ⏳ Open | §4.23 |

---

## 7. Cross-Reference with `docs/stateful-media-storage-at-scale.md`

| Doc item | Status | Anchor in this report |
|---|---|---|
| Current Media Architecture | ✅ Resolved | §2 #17, #18 |
| Hidden Stateful Assumptions | ✅ Resolved | §2 #17, #18 |
| Horizontal Scaling — Split-Brain | ✅ Resolved | §2 #17, #18 |
| Horizontal Scaling — Disk Exhaustion | ✅ Resolved | §2 #17, #18 |
| Horizontal Scaling — Ghost Playlist | ✅ Resolved | §2 #17, #18 |
| MinIO resource limits | ⏳ Open | §4.1 |
| Public `hls/` bucket restricted to CDN | ⏳ Open | §4.17 |
| `uploads/` lifecycle (Glacier) | ⏳ Open | §4.16 |
| Upload Architecture (presigned PUT) | ⏳ Open | §4.4 |
| Media Processing State Machine (`pending_upload`) | ⏳ Open | §4.4 |
| Object Storage Strategy (key design) | ✅ Resolved | §2 #17 |
| Object Storage Strategy (Glacier) | ⏳ Open | §4.16 |
| CDN Strategy | ⏳ Open | §4.3, #4.15 |
| Consistency Model (outbox / soft-delete) | ⏳ Open | §4.5, #4.19 |
| Idempotency — retry config | ✅ Resolved | §2 #19 |
| Idempotency — `finally:` cleanup | ✅ Resolved | §2 #18 |
| Idempotency — SHA-256 dedup | ⏳ Open | §4.4 (presign stage) |
| Failure — Celery broker hiccup | ✅ Resolved | §2 #8 |
| Failure — Orphan S3 on delete | ⏳ Open | §4.5, #4.19 |
| Failure — Viral clip / Origin Shield | ⏳ Open | §4.3, #4.15 |
| Failure — Upload spike to gunicorn | ⏳ Open | §4.4 |
| Security — OAC bucket policy | ⏳ Open | §4.17 |
| Security — Magic-byte validation | ✅ Resolved | §2 #15 |
| Security — Predictable keys | ✅ Resolved | §2 #17 |
| Roadmap P0 | 🟡 Partial | §2 #17, #18, §3.7, §4.1 |
| Roadmap P1 (presigned PUT) | ⏳ Open | §4.4 |
| Roadmap P2 (CDN + Glacier) | ⏳ Open | §4.3, #4.15, #4.16 |
| Roadmap P3 (cross-region) | ⏳ Open (out of 10k scope) | (deferred to 50k) |
| Blind Spot — FFmpeg time | 🟡 Partial (retry config + finally done; soft_time_limit open) | §2 #19, #4.18 |
| Blind Spot — workers on same compute | ✅ Resolved | §2 #21 |

---

## 8. Launch Checklist (priority order for the 10k sprint)

Each item lists effort, dependency, and a "Why this is the launch checklist" rationale. Items 1–9 are the **10k launch checklist**; items 10+ are 10k–50k polishing.

1. **P0.3** MinIO resource limits (4 lines of YAML, 0.25 wk) — the cheapest 5-minute fix in the report; the highest-leverage infra item.
2. **P0.4** Redis `volatile-lru` + 2 GB (1 line of YAML, 0.25 wk) — kills the self-feeding collapse scenario.
3. **P1.5 / P1.1 (counter half)** Move `toggle_like` and `send_share` off the F() path (3 wk combined with P1.1) — closes the largest unmitigated lock risk.
4. **P0.1 (PG half)** `command:` override on the `db` service (1 wk including PgBouncer) — closes autovacuum and connection-exhaustion failure modes.
5. **P1.2** nginx + CDN (2 wk) — moves HLS bytes off the API tier.
6. **P1.3** Presigned PUT (1.5 wk) — moves upload bytes off the API tier.
7. **P1.4** Outbox table (1.5 wk) — prerequisite for the recommendation engine being truly decoupled.
8. **P2.3** Per-user error isolation in `evolve_long_term_user_baselines` (0.5 wk) — the smallest of the P2 items with the largest correctness payoff.
9. **Storage — OAC + Glacier + soft_time_limit** (1 wk combined) — security and cost.
10. **P2.1** + **P2.2** user:context + candidate pool (2.5 wk combined) — recommendation engine at 10k.
11. **P2.4 + P2.5** Frontend batching + batch endpoint (2.5 wk combined) — 50× reduction in telemetry QPS.
12. **P3.x** Polish (CSP, SSE, observability stack, contract tests) — 5–6 wk across all P3 items.

**Total: ~18 engineer-weeks for 10k launch.** One engineer ≈ 4–5 months; two engineers ≈ 2–3 months.

---

## 9. Items Where the Audit Was Wrong (false positives from prior docs)

For the record, the following claims in `docs/backend-architecture-audit.md` and `docs/backend-audit.md` were **re-verified against current code** and found to be incorrect or out-of-date. They are **not** in the "Open" or "Partial" sections of this report. Source: `docs/EXPLAIN/decisions/02-discrepancies.md`.

| Audit claim | Reality | Anchor |
|---|---|---|
| "Recommendation algorithm broken — weights never populated" | `weights.append(final_weight)` IS called in `calculate_time_decayed_vectors` | `backend/app/tasks.py:442` |
| "OPENAI_API_KEY NameError in tasks.py" | `OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""`; `get_openai_client()` checks for empty | `backend/app/tasks.py:33` |
| "Static FERNET_KEY in models.py" | **Resolved (uncommitted N3 fix).** The Fernet mechanism is gone from `backend/app/models.py`; `FIELD_ENCRYPTION_KEY` is no longer required. `backend/app/models.py:14-34` | (N3 fix uncommitted; visible in `git diff backend/app/models.py`) |
| "No rate limiting configured" | DRF throttling configured and per-scope (P0.6) | `settings.py:361-382` |
| "CORS hardcoded to allow all" | `CORS_ALLOW_ALL_ORIGINS = False`; `CORS_ALLOWED_ORIGINS` from env | `settings.py:27-32` |
| "Batch telemetry not done" | Resolved by P1.5 | `services/interactions.py`, `tasks.py:645-791` |
| "Validate audio by magic bytes not done" | Resolved (commit `2715b54`) | `serializers.py` |
| "Request tracing not done" | `CorrelationIdMiddleware` shipped (commit `c90ae23`) | `settings.py:100-117` |

These are excluded from the "Open" section because they are not open. The `EXPLAIN/decisions/02-discrepancies.md` doc is the source of truth for discrepancies between docs and code.

---

## 10. Verification Method

This report was constructed by:

1. **Reading the source.** Every `file:line` cite in this report was read from the working tree, not from a prior doc.
2. **Git log inspection.** `git log --oneline -30` and `git log --all --oneline | grep -iE "p0|p1|stage|service|outbox|presign|cdn"` to identify what was actually merged on the branch.
3. **Grep validation.** `grep -r "event_outbox\|presign\|nginx\|cloudfront"` against `backend/` and `frontend/` to confirm absences are absences, not just missed files.
4. **Cross-checking EXPLAIN discrepancies.** `docs/EXPLAIN/decisions/02-discrepancies.md` was used to identify which audit-doc claims were already false positives; those are excluded from the "Open" section.
5. **Discrepancies between this report and the source:** If you find one, **the source wins**. Please update this report and the planning docs.
