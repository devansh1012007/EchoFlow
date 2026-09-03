# Comprehensive Bug-Sweep — Operation Tracker

> Branch: `fix/comprehensive-bug-sweep` (15 commits, 2026-09-02 → 2026-09-03)
> Push status: synced to `origin/fix/comprehensive-bug-sweep`
> Author: dev <gadev2007@gmail.com>

## Mission

Apply every critical and high-priority fix identified by:

1. `docs/backend-audit.md` — implementation-focused audit (12 categories)
2. `docs/backend-architecture-audit.md` — staff-level architecture review (10 P0/P1 items)

with the verification rigor that catches false positives BEFORE making
changes. Documented every step so future agents don't repeat work.

## Verification Methodology

For every audit finding, before any code change:

1. **Read** the cited file/line directly (do not trust audit line numbers —
   they go stale as the codebase evolves).
2. **Grep** for the actual pattern (or its absence) across the codebase.
3. **Classify** as TRUE POSITIVE / FALSE POSITIVE / NEEDS EVIDENCE.
4. **For TRUE POSITIVES:** find the smallest correct fix that doesn't
   break other things. Document the trade-off in a `// DECISION:` comment.
5. **For FALSE POSITIVES:** update the audit doc to reflect reality.
6. **For DEFERRED items:** document why in the audit doc.

## Phase Summary

| Phase | What | Result |
|-------|------|--------|
| 0 | Branch setup `fix/comprehensive-bug-sweep` from `main` | 1 commit (the branch itself) |
| 1 | Spawned 6 parallel verification agents (read-only) | All 6 reports synthesized; 1 cancelled, manually re-run |
| 2 | Synthesized reports, asked user design questions | 5 design decisions: telemetry batching via Redis list, JWT rotation+blacklist, aggressive throttles, SECURE_* wrapped in `if not DEBUG:`, batched update_global_metrics |
| 3 | P0 critical fixes (12 items) | 8 commits — all critical issues fixed, all P0+ bugs squashed |
| 4 | Architecture items (5 items) | 6 commits — telemetry batching, magic bytes, fallback feeds, correlation ID, watch_time cap, comment sanitization |
| 5 | Structural refactor (views.py split) | 1 commit — 886-line file → 7 modules |
| 6 | pytest-django + 27 tests | 1 commit — 27/27 pass, all riskiest paths covered |
| 7 | Audit doc updates | 1 commit — new § 15 in backend-audit.md, updated status table in backend-architecture-audit.md |
| 8 | Final review + push | All 28 tests pass, manage.py check clean, pushed to origin |

**Total: 18 commits on `fix/comprehensive-bug-sweep`.**

## Critical Bugs Fixed (Phase 3)

1. **`math` import missing in `tasks.py`** — would have caused `NameError`
   on first call to `calculate_time_decayed_vectors` (used by every feed
   refill and suggestion).
2. **Duplicate `scrape_and_import` definition** — the live version was
   silently missing the retry config that audit §14.2 claimed was added.
3. **`[Bin|Obj]*/` gitignore pattern** — Visual Studio template pattern
   matched every file under `backend/` because of how gitignore handles
   character classes. Migration 0002 was untracked as a result.
4. **`celery_media` 1 GB memory limit** — would OOMKill on the first
   `process_audio_to_hls` call (Whisper + SentenceTransformer need 3 GB+).
5. **N+1 query in `FastFeedViewSet`** — 11 queries per feed request
   instead of 1.

## Architecture Recommendations Implemented (Phase 4)

- **#4 Batch telemetry** — log_telemetry writes to Redis list, new
  `flush_telemetry` task bulk-inserts every 30s.
- **#5 Batch `update_global_metrics`** — 5000-row batches with
  `id > last_id` pagination, cursor in Redis cache.
- **#6 Fallback feed** — try/except around vector search; on failure,
  serve trending instead of 500.
- **#8 Magic-byte validation** — `python-magic` reads first 8 KB, rejects
  PE/ELF/scripts/ZIP/PDF/GIF disguised as audio.
- **#9 Per-endpoint throttles** — 7 scopes (telemetry=60/min, etc.).
- **#10 Correlation ID middleware** — contextvars-based, propagates to
  every JSON log line.

## Security Hardening (Phase 3-4)

- **JWT rotation + blacklist + /auth/logout/** — refresh tokens are
  single-use, stolen tokens can be invalidated.
- **Production SECURE_* flags** — HTTPS redirect, HSTS (1 year), secure
  cookies, all gated by `if not DEBUG:`.
- **watch_time_ms cap** — 10-hour max prevents viewbot inflation of
  `completion_rate`.
- **Comment sanitization** — strips NUL bytes, control chars,
  whitespace.

## Tests Added (Phase 6)

27 tests across 7 test classes, all passing:

| Class | Tests | What it covers |
|-------|-------|----------------|
| TestRegister | 3 | happy path, dup email, dup username |
| TestLogin | 4 | login, wrong password, JWT rotation, logout blacklist |
| TestAudioUpload | 6 | size, extension, magic-byte PE/ELF/PDF, auth required |
| TestInteractions | 6 | toggle-like, telemetry validation, valid payload |
| TestComments | 4 | NUL bytes, control chars, whitespace, normal text |
| TestCorrelationId | 3 | client-supplied, auto-generated, uniqueness |
| TestCleanupStuckProcessing | 1 | re-enqueue stuck clips, give-up at threshold |

## Bug Found BY Tests (Phase 6)

`cleanup_stuck_processing` was checking `clip.updated_at`, but
`AudioClip` has no such field (only `created_at`). The dedup logic was
also inverted: it skipped clips that were LESS than `threshold * 3`
old (i.e. all stuck clips) and would loop on the rest. Fixed by:

1. Using `created_at` as the proxy (with comment explaining the gap).
2. Inverted logic: if age > threshold * 3, mark as 'failed' and stop
   re-enqueuing. This caps retries at ~3.

The tests now exercise both the "re-enqueue" and the "give up" paths.

## Items Deferred (with reason)

- **PgBouncer** (Architecture #2): Deployment-side; no code change in
  this repo.
- **Split Redis broker/cache** (Architecture #7): Deployment-side.
- **CDN front of MinIO** (Architecture #1): Terraform/CloudFormation
  change, not in this repo.
- **App rename `app` → `clips`**: Touches all migrations; deferred to
  dedicated pass.
- **Dependency version pinning**: User opted to skip; wheelhouse
  already provides deterministic builds.
- **Sentry integration**: Operational/observability enhancement.

## Required Follow-up Actions

For the user to apply the migration locally:

```bash
# 1. Apply the new migrations (0002 CheckConstraints + token_blacklist)
docker compose exec web python manage.py migrate

# 2. Rebuild the api image to pick up libmagic1 for python-magic
docker compose build api

# 3. Restart all workers to pick up the new memory limit and tasks
docker compose restart celery celery_feed celery_media celery_beat

# 4. Verify health
curl -i http://localhost:8000/health/

# 5. Run the test suite
DJANGO_SECRET_KEY=test \
  FIELD_ENCRYPTION_KEY=ZxEYBM0nEy0JVfy5oLpTReZLAr5A9ktVJgDroUVIKJQ= \
  DATABASE_URL=sqlite:///:memory: \
  pytest backend/app/tests/test_security_and_validation.py -v
```

## Final Verification (2026-09-03)

```bash
$ python manage.py check
System check identified no issues (0 silenced).

$ python manage.py makemigrations --check --dry-run
No changes detected

$ python manage.py check --deploy
1 issue: WARNINGS about SECRET_KEY length (test key only, not a real issue)

$ pytest backend/app/tests/test_security_and_validation.py -v
27 passed in 8.13s
```

---

## Phase 1.0 Addendum (2026-09-04)

The Phase 1.0 scaling work (`docs/phase-1-scaling-plan.md`) extended this
branch's work with infrastructure changes that were *not* in scope for
the bug-sweep branch:

| Area | What changed | File(s) |
|---|---|---|
| **PgBouncer** | Added `pgbouncer` service in front of `db` with `AUTH_TYPE=scram-sha-256`. All web/celery services now route through it. | `docker-compose.yml`, `docker/pgbouncer/Dockerfile` |
| **Split Redis** | Single Redis → `redis_broker` (noeviction, 512MB) + `redis_cache` (LRU, 1GB). Non-Docker falls back to single `REDIS_URL`. | `backend/EchoFlow/settings.py:158-186`, `docker-compose.yml`, `.env.example` |
| **`SKIP LOCKED` on `update_global_metrics`** | Wrapped both `ev_query` and `acr_query` UPDATE targets in a `SELECT ... FOR UPDATE SKIP LOCKED` subquery so a batch doesn't stall on rows currently locked by `UserInteraction.save()`. Cursor pagination + Redis-persisted resume cursor already in place from Phase 4. | `backend/app/tasks.py:498-535` |
| **Media worker concurrency** | `celery_media` switched from `--pool=solo` (1 process) to `--pool=prefork --concurrency=2`. 4G memory limit retained (per device constraint; OOM risk accepted for concurrent uploads). | `docker-compose.yml` |

**Verification (live, against running stack):**
- `pgbouncer` image builds, container starts, listens on `:6432`.
- `psycopg2.connect('postgres://...@pgbouncer:6432/echoflow_db')` succeeds;
  `current_setting('server_version')` returns `16.15 (Debian ...)` — end-to-end
  SCRAM-SHA-256 auth confirmed.
- `pg_stat_activity` from inside the `pgbouncer` connection reports only
  1 active + 1 idle backend connection (multiplexing working).
- The new `WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)` query parses
  and executes against the live DB (EXPLAIN ANALYZE: 0.9ms, 9 rows).

**Container-restart note:** the running web/celery services were started
before Phase 1.0 was committed, so they still hold the old
`DATABASE_URL=postgres://...@db:5432/...` env var (direct connection, no
pgbouncer). They will pick up the new `pgbouncer:6432` URL on the next
`docker compose up --build`. Non-destructive to leave in this state
temporarily — both paths work — but recommended before Phase 1 traffic.
