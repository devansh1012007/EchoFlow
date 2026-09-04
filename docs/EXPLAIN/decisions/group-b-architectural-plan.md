# Group B Architectural Fixes — Plan

**Status:** Draft for user approval. No code changes yet.
**Scope:** Group B items 9, 10, 11, 12 (the four `REAL` items). 13, 14, 15, 16, 17 are explicitly out of scope per user decision.
**Branch:** `feat/group-b-architectural` (off current `main`).
**Date:** 2026-09-04.

---

## 0. Verification summary

Four parallel explore agents re-verified every Group B item against the current source. The results are below.

| # | Doc claim | Reality | Verdict |
|---|-----------|---------|---------|
| 9 | F() counter side-effect in `UserInteraction.save()`; 500 concurrent likes → 500 row locks; no Redis INCRBY anywhere | `models.py:200-201` F() still present; `field_map` at `models.py:196`; only same-user race fixed; cross-user viral contention unmitigated; **zero** INCRBY/flush task in repo | **REAL** |
| 10 | `invalidate_user_vectors_cache` exists, never called | Defined at `views/feed.py:50-55` (NOT `services/interactions.py` as doc claims); **zero** production callers; only `hasattr` existence test at `test_adversarial_pass3.py:472-475` | **REAL** (with file-location correction) |
| 11 | `task_prerun` doesn't exist; workers run with empty contextvar | Zero `task_prerun` in `backend/`; `celery.py:4` imports only `task_postrun`, `task_failure`; all 6 `.delay()` call sites lack `headers=`; `%(correlation_id)s` populated in workers but always `'-'` (filter's `or '-'` fallback) | **REAL** |
| 12 | No periodic `cleanup_orphan_hls` task | `tasks.py` has no `cleanup_orphan` definition; `CELERY_BEAT_SCHEDULE` (settings.py:286-345) has 7 entries, none orphan-related; `signals.py:37-41` docstring acknowledges the gap | **REAL** |
| 13 | Sentry not present | `grep -ri sentry backend/` → 0 matches; no SDK, no env var | **REAL** (out of scope) |
| 14 | CDN front of MinIO is deployment-side; nginx is wired | nginx is wired but `proxy_buffering off`; `PUBLIC_MEDIA_ENDPOINT_URL` defaults to `:9000` bypassing nginx; only `.env.example:81` uses nginx path; dev doesn't exercise CDN | **PARTIAL** (out of scope) |
| 15 | `app → clips` rename touches all migrations and AUTH_USER_MODEL | True; 11+ files reference `backend.app`; `App1Config` smell suggests prior incomplete attempts; rename is feasible in 1 day, value is debatable | **REAL** (out of scope per user) |
| 16 | `db_routers.py` is dead code | **FALSE POSITIVE.** It's a 71-line router, wired in `settings.py:187` when `READ_DATABASE_URL` is set, with 14 tests in `test_db_router.py`. Already shipped in commit `a85e298` | **FALSE POSITIVE** (doc contradicts itself §10.7 vs §17.E entry 30) |
| 17 | HF_TOKEN rotation is ops; code-side checks in place | TRUE but phrase "code-side checks in place" misleading — there are zero runtime checks (by design; build-time only). BuildKit secret → offline runtime, no code coordination needed | **PARTIAL FALSE POSITIVE** |

### Doc corrections needed (no code change)

- `docs/backend-bug-fixs.md:595-596` (§10.7) — update `db_routers.py` from "dead code" to "shipped in `a85e298`; conditionally registered when `READ_DATABASE_URL` is set".
- `docs/backend-bug-fixs.md:598-599` (§10.8) — replace "code-side checks in place" with "build-time BuildKit secret only; runtime offline; zero runtime impact".
- `docs/backend-bug-fixs.md:1173-1175` (Item 10) — change `services/interactions.py:50-55` to `views/feed.py:50-55`.
- `docs/EXPLAIN/recommendation/03-feed-pre-computation.md:520` — same file-location fix.

---

## 1. Core logical flaws (the "why", not the "what")

### Item 9 — F() counter race

**Symptom:** 500 concurrent likes on a viral clip → 500 serialized row-level locks on `AudioClip` → latency spike, possible connection pool exhaustion.

**Why it happens:** The counter increment is a **side effect of the data write**. Every `UserInteraction` row insert/update is entangled with an `AudioClip` row update. Two write paths that should be independent (`UserInteraction` for telemetry history, `AudioClip.likes` for the hot counter) share a row lock because they're on the same row.

**Why a quick fix is not enough:** The N2 fix widened the atomic block to cover the F() update. This makes the SAME-USER TOGGLE race correct (no double-count on rapid like/unlike) but does not address the CROSS-USER contention on the same clip. To address cross-user contention, the two write paths must be **physically separated** — the F() update must leave the synchronous critical section entirely. Anything less than physical separation is still 1 lock per like.

**Architectural fix:** Move counter increments to Redis INCRBY (lock-free on the writer side). A separate batch flusher reads Redis → writes Postgres in a single UPDATE per clip per flush. The Postgres row only takes a lock every 5 minutes instead of every like.

**Why this is the right shape (not the only shape):**
- Redis INCRBY is atomic at the key level; the writer path no longer touches Postgres.
- The flusher is bounded and can use `UPDATE ... SET likes = likes + delta WHERE id = ?` — still row-locked but only N times per flush, not N times per like.
- Idempotency under flusher restart: if the flusher crashes mid-batch, the next run picks up the same delta (Redis is source of truth; the flusher just commits deltas).
- The `UserInteraction` row remains the durable source of truth; Redis is the hot counter. This dual-source model is well-understood (Stripe counter pattern, Vercel counters, etc.).

**Future-proofing:** When the user eventually migrates to Kafka (mentioned in the audit doc as a future step), the same `record_like_toggle` interface produces a Kafka event instead of a Redis INCRBY. The service-layer extraction in Group A item N2 means the view never has to change. The architectural fix here buys us the **transition path** to true event-driven.

### Item 10 — Cache invalidation

**Symptom:** After a user likes/skips a clip, `/suggestions/?category=X` may serve recommendations computed from up to 15 minutes of stale interaction data.

**Why it happens:** The cache invalidation helper exists, but no one calls it. The original doc §6.4 cited (a) circular-import risk, (b) acceptable 15-min staleness on explore, (c) minimal-viable fix. Reasons (a) and (c) are correct; reason (b) is the symptom. The cost of wiring is 2 lines of code in 2 services; the benefit is that the staleness window collapses to ~one request.

**Why a quick fix is not enough:** A naive "just call it" would create a circular import between `services/interactions.py` and `views/feed.py` (the helper lives in views because `cache.set/get` is Django's, but the service layer shouldn't import from views). The clean fix is to **move the helper to a location that doesn't have this issue** — `services/interactions.py` itself, since that's where the callers are. The view still imports it (one direction only).

**Architectural fix:** Relocate the helper to `services/interactions.py` (where the callers are). Call it from `record_like_toggle` and `record_skip`. Keep the helper in the `views/feed.py` as a thin re-export for backwards-compat with anything that imports it (currently nothing, but the doc references it).

**Why this is the right shape:** It makes invalidation a first-class concern of the service layer. Any future service that mutates user interaction state is in the same module and should obviously invalidate. Centralizing at the service layer (not the view layer) means the next refactor (Kafka, etc.) preserves invalidation logic without thinking about it.

**Future-proofing:** When telemetry/stream flushers start touching user state, the flusher will import the same helper. No decision to make.

### Item 11 — Celery correlation_id

**Symptom:** Worker logs always have `correlation_id: '-'`. Can't trace an async task back to the HTTP request that triggered it.

**Why it happens:** `CorrelationIdMiddleware` writes to a `contextvars.ContextVar` in the web tier. The contextvar is **process-scoped, not message-scoped**. When the web request enqueues a task via `.delay()`, only the task body is shipped to the worker — the contextvar value is not.

**Why a quick fix is not enough:** A naive "set the contextvar in the task" would set it to the same value for every task the worker processes (since the worker has its own contextvar default). The fix must **transport the value from web to worker** and **reset the worker's contextvar between tasks** (otherwise task A's correlation_id leaks into task B in the same worker process).

**Architectural fix:** Two parts.
1. **Producer side** (web): A small `_publish` wrapper that reads `get_correlation_id()` at enqueue time and passes it as a task header via `.apply_async(headers={...})`. Replace all 6 `.delay()` call sites with this wrapper. The wrapper preserves `countdown`, `eta`, `queue` etc.
2. **Consumer side** (worker): `task_prerun` signal reads the header and calls `set_correlation_id(header_value)`. `task_postrun` signal calls `clear_correlation_id()`. The two existing `task_postrun` and `task_failure` handlers (for metrics) stay; we add NEW handlers that don't conflict.

**Why this is the right shape:**
- No producer-side coupling to specific tasks. Any future `.delay()` site gets correlation for free if it goes through the wrapper.
- `task_prerun` / `task_postrun` are Celery's official signals for "task context" — using them means the context is set even for tasks triggered by beat, retry, or other Celery features.
- The existing metrics signals at `celery.py:44, 63` are SEPARATE handlers. We add two more; total 4. No state is shared between them.

**Future-proofing:** If we add a tracing system (OpenTelemetry, Sentry) later, the header-attachment + `task_prerun` pattern is the standard hook point. We're not locking ourselves out of anything.

### Item 12 — Periodic orphan HLS cleanup

**Symptom:** When the `post_delete` signal's S3 delete fails (network hiccup, S3 outage, bucket perm change), the orphan HLS files persist forever. Storage cost grows linearly with the bug rate.

**Why it happens:** The signal is the **only** cleanup path. The signal runs in the same transaction as the delete, and `transaction.on_commit` semantics don't apply here (the row IS already deleted by the time the signal fires). The signal has no retry; failures are swallowed with a `logger.warning`.

**Why a quick fix is not enough:** A simple "retry the signal 3x" would still miss the case where the row was force-deleted via a DBA intervention, or where the S3 keys are wrong (e.g., a migration that updated the storage prefix). The signal is **per-delete** by design. The orphan-detection problem is **bucket-wide** by nature. They are different problems and need different solutions.

**Architectural fix:** A periodic Celery beat task that lists the `hls/` prefix, extracts clip IDs, and diffs against `AudioClip.objects.values_list('id', flat=True)`. The diff is the orphan set. Bounded to 1000 keys per run; the task is idempotent (re-running is safe). If the bucket has more than 1000 orphans (which means something is seriously wrong), the next day's run picks up the rest. Logs the count; increments a `echoflow_orphan_hls_cleaned_total` Prometheus counter.

**Why this is the right shape:**
- Defense in depth: signal handles 99.9% of cases, periodic handles the 0.1% that slipped through (and other paths the signal can't catch).
- Bounded run size: a 100M-file bucket still scans in O(1000) per day. No operator pager fires for runaway cleanup.
- Idempotency: re-running is safe. The flusher is the only place that deletes files based on a DB cross-reference, so there's no race with the signal.
- A real signal gap: the same task can be repurposed for `uploads/` orphans if that path is ever added.

**Future-proofing:** When the bucket moves to S3 Glacier or cold storage, the scan can be added to a `select_orphan_for_archival` variant. The shape doesn't change.

---

## 2. Architectural decision matrix

| Decision | Chosen | Why | Rejected alternatives |
|----------|--------|-----|------------------------|
| Item 9 — Counter storage | Redis INCRBY + 5-min flusher | Lock-free writer; Postgres lock only on flush; idempotent under crash | Atomic UPDATE batching (more moving parts); skip (escalates to 5000+ locks/clip) |
| Item 9 — Counter keys | `clip:{id}:{type}` where type ∈ {likes, shares, skips} | Stable, predictable, can be enumerated for flushing | Single hash per clip (less granular for selective flush) |
| Item 9 — Flusher trigger | Celery beat, 300s (matches existing `update_global_metrics`) | Reuses existing beat infra; predictable flush window | Real-time per-event (defeats the point); on-demand (no SLA) |
| Item 10 — Helper location | `services/interactions.py` (move from `views/feed.py`) | Same module as callers; no circular import | Keep in views (circular); move to a new `services/cache.py` (extra file for one function) |
| Item 10 — TTL | Unchanged at 15 min | Wiring makes it irrelevant; the cache is invalidated, not waited out | Reduce to 60s (defeats the cache) |
| Item 11 — Header name | `correlation_id` (matches contextvar name) | Symmetric with `set_correlation_id` / `get_correlation_id` | `X-Request-ID` (HTTP header name, semantically wrong inside Celery) |
| Item 11 — Wrapper name | `_publish(task, *args, **kwargs)` in `services/task_publisher.py` | New file isolates the wrapper; clear purpose | Inline at each `.delay()` site (6 duplications); decorator (magic) |
| Item 12 — Schedule | Daily at 03:00 UTC (1 run/day, 1000 keys/run) | Off-peak; predictable; no impact on user-facing traffic | Hourly (more S3 list calls); on-demand (no SLA) |
| Item 12 — Orphan detection | List `hls/` prefix, diff against `AudioClip.id` UUIDs | Direct, auditable, no heuristics | Heuristic (clip-id-shaped prefix) — same result, harder to test |
| Items 13/14/15 | Out of scope | User decision | n/a |
| Item 16/17 | Doc correction only | Code already correct | n/a |
| Test strategy | New tests alongside each fix | Catches regression; reflects the architectural contract | Test file at the end (mixed abstractions); no tests (regressions undetectable) |

---

## 3. Implementation order

The fixes are **mostly independent** but Item 9 touches the most files and the F() side-effect is removed from the model, which has cross-cutting implications. Order:

1. **Item 11 first** (smallest, isolated, no model changes). Sets up the producer-side header pattern that Item 9 can optionally use for cross-counter observability.
2. **Item 10 second** (small, isolated, helper relocation only).
3. **Item 12 third** (medium, new task + beat entry + signal-related test, no model changes).
4. **Item 9 last** (largest, removes the F() side-effect from `UserInteraction.save()`, requires the counter plumbing and flusher to be solid before removal).

Rationale: each step is independently shippable. If we run out of time, Items 11+10+12 are all wins on their own; Item 9 is the one that needs the most confidence before the F() removal.

---

## 4. Item 9 — Detailed plan

### New files

- `backend/app/services/counter_store.py` — Redis-backed counter with INCRBY.
  - `class RedisCounterStore`: init with `redis_client` and `prefix` (default `clip`)
  - `increment(clip_id: UUID, counter_type: str, delta: int = 1) -> int` — INCRBY; returns the new value
  - `drain(clip_id: UUID) -> dict[str, int]` — read all counter types and zero them atomically via Lua script (so a concurrent increment between read and reset isn't lost)
  - `bulk_drain(clip_ids: list[UUID]) -> dict[UUID, dict[str, int]]` — pipelined for the flusher
  - `drain_all() -> dict[UUID, dict[str, int]]` — scan all `clip:*:delta` keys, used by the flusher when a clip is in Redis but not in the per-clip drain result (e.g., newly created)
- `backend/app/tasks_counter_flush.py` — or add to existing `tasks.py`
  - `flush_counters_to_pg(batch_size: int = 500)` — Celery task; for each clip with deltas, issues a single `UPDATE app_audioclip SET likes = likes + d, shares = shares + d, skips = skips + d WHERE id = ?` (one row-lock per clip, not per counter)
- `backend/app/migrations/0005_remove_userinteraction_f_counter_side_effect.py` — none needed. The F() side-effect is removed from `save()`, not from the schema. The columns stay.

### Modified files

- `backend/app/models.py:200-201` — remove the F() side-effect. `save()` now only writes the `UserInteraction` row.
  - Keep the `field_map` constant; move it to `services/counter_store.py` as the canonical mapping.
  - Add a `SECURITY` comment: counter increments now happen in `record_like_toggle` / `record_skip` / `record_share` BEFORE the F() removal would be safe (race). After removal, they MUST go through `RedisCounterStore`.
- `backend/app/services/interactions.py:90-145` — call `RedisCounterStore.increment()` in:
  - `record_like_toggle` after successful `save()` (only if state actually changed)
  - `record_skip` after successful `update_or_create`
  - `record_share` after successful `get_or_create`
  - NOT in `record_telemetry` (telemetry events don't bump counters today; the legacy `field_map` excludes them)
- `backend/EchoFlow/settings.py:286-345` — add `flush-counters-to-pg` to `CELERY_BEAT_SCHEDULE`, every 300s (matches `update_global_metrics` cadence).
- `backend/app/tasks.py:587-671` — `update_global_metrics` may also need adjustment if it now reads the new Redis counter for the engagement velocity formula. Re-read it first; flag separately if so.

### Tests

- `backend/app/tests/test_counter_store.py` — new file, 8-10 tests:
  - `test_increment_returns_new_value`
  - `test_increment_is_atomic_under_concurrent_writers` (skip on LocMem; document)
  - `test_drain_returns_zero_after_drain`
  - `test_drain_does_not_lose_concurrent_increment` (the critical correctness test — uses Lua atomicity)
  - `test_bulk_drain_processes_clips_in_one_pipeline`
  - `test_increment_handles_redis_outage` (fail-safe: don't raise, log, return None)
- `backend/app/tests/test_counter_flush.py` — new file, 5-6 tests:
  - `test_flush_moves_deltas_to_postgres` (sets up a Redis delta, calls the task, asserts Postgres counter increased)
  - `test_flush_handles_no_deltas` (no-op)
  - `test_flush_is_idempotent` (call twice, no double-count)
  - `test_flush_recovers_from_crash` (set delta, run flush, simulate mid-flush crash, re-run, no double-count)
  - `test_flush_bounded_to_batch_size`
- Update `backend/app/tests/test_services_interactions.py` — the existing tests still pass because the F() side-effect is replaced with the Redis increment; but they need a Redis-mock (or a fixture that clears the counter between tests). The existing `test_bumps_skips_counter` test should still pass once we add a flush step in setUp.

### Migration story (zero-downtime)

1. Phase 1 (shipped together): Add `RedisCounterStore` + flusher. `record_like_toggle` writes BOTH to Redis AND triggers the old F() (via a `settings.ECHOFLOW_DUAL_WRITE_COUNTERS=True` flag, default `True` for the first release). Flusher runs and starts accumulating deltas.
2. Phase 2 (1 day later): Set `ECHOFLOW_DUAL_WRITE_COUNTERS=False` in compose env. `record_*` writes ONLY to Redis. Flusher is the only path to Postgres.
3. Phase 3 (after 1 week of Phase 2): Verify Postgres counters match Redis-counted deltas. Remove the legacy F() code from `models.py`. Remove the flag.

**This plan is a 3-phase rollout, not a single-commit flag-flip.** The plan-item 9 commit removes the F() code AND the flag; the rollout is via the flag.

For the test env (LocMem + SQLite), the store falls back to in-memory `dict[UUID, dict[str, int]]` with a threading.Lock to keep the API surface identical. This is what the existing tests already use (LocMem cache).

### What this plan does NOT do

- Does not change the schema (no migration).
- Does not change any public API.
- Does not change the order of operations from the client's perspective.
- Does not introduce a new dependency (`redis-py` is already in `requirements-base.txt` via Celery).

---

## 5. Item 10 — Detailed plan

### New file changes

- `backend/app/services/interactions.py:1-15` (new top) — add a thin module-level `invalidate_user_vectors_cache(user_id: int)` function. The body is the same as `views/feed.py:50-55`.
- `backend/app/views/feed.py:50-55` — re-export the function for backwards-compat (single-line `from ..services.interactions import invalidate_user_vectors_cache`).

### Modified files

- `backend/app/services/interactions.py:90-145` — at the end of `record_like_toggle` and `record_skip`, call `invalidate_user_vectors_cache(user.id)`. Idempotent.
- `docs/EXPLAIN/recommendation/03-feed-pre-computation.md:520` — fix the wrong file reference.

### Tests

- Update `backend/app/tests/test_services_interactions.py`:
  - `TestRecordLikeToggle::test_clears_user_vectors_cache` (new) — caches a value via the real helper, calls `record_like_toggle`, asserts the cache is empty.
  - `TestRecordSkip::test_clears_user_vectors_cache` (new) — same shape.
- `backend/app/tests/test_adversarial_pass3.py:472-475` — the existing `hasattr` test is now redundant; replace it with `TestN11UserVectorCache::test_record_like_toggle_invalidates` + `TestRecordSkip::test_invalidates`.

### What this plan does NOT do

- Does not change the TTL (still 15 min).
- Does not move the cache storage (Django's default cache backend; backed by Redis in Docker).
- Does not invalidate from `record_telemetry` or `record_share` (per user scope decision).

---

## 6. Item 11 — Detailed plan

### New file

- `backend/app/services/task_publisher.py`:
  ```python
  def publish(task, *args, **kwargs):
      """Wrap .apply_async() with a correlation_id header.
      
      Falls back to .delay() if the caller passes no special kwargs.
      Reads get_correlation_id() at call time, not at worker time,
      so the value is captured per-enqueue.
      """
      from .. import correlation
      cid = correlation.get_correlation_id() or ''
      headers = (kwargs.pop('headers', None) or {}).copy()
      if cid:
          headers['correlation_id'] = cid
      return task.apply_async(args=args, kwargs=kwargs, headers=headers)
  ```

### Modified files

- `backend/app/management/commands/scrape_audio.py:87` — `process_audio_to_hls.delay(...)` → `publish(process_audio_to_hls, ...)`
- `backend/app/tasks.py:955, 983, 1043, 1116` — same replacement
- `backend/app/services/uploads.py:25` — `transaction.on_commit(lambda: process_audio_to_hls.delay(clip.id))` → `transaction.on_commit(lambda: publish(process_audio_to_hls, str(clip.id)))`
- `backend/app/views/feed.py:76, 227` — `refill_user_feed.delay(...)` → `publish(refill_user_feed, ...)`
- `backend/EchoFlow/celery.py` — add `task_prerun` and `task_postrun` signal handlers (3 lines each). The existing `task_postrun` and `task_failure` handlers stay (they're for metrics, different concern).

### Tests

- New tests in `backend/app/tests/test_security_and_validation.py` (next to `TestCorrelationId`):
  - `TestTaskPrerunPostrun`:
    - `test_prerun_sets_contextvar_from_header` — fire signal with header, assert `get_correlation_id() == header_value`
    - `test_postrun_clears_contextvar` — fire signal after set, assert cleared
    - `test_log_record_contains_real_id_in_worker` — combine prerun + filter, assert `record.correlation_id != '-'`
    - `test_log_record_falls_back_to_dash_outside_signal` — regression guard
    - `test_eager_mode_preserves_contextvar` — when `CELERY_TASK_ALWAYS_EAGER=True`, `set_correlation_id` is still observable
- New tests in a new `backend/app/tests/test_task_publisher.py`:
  - `test_publish_attaches_correlation_header_from_contextvar`
  - `test_publish_omits_header_when_no_contextvar`
  - `test_publish_preserves_countdown_and_eta`
  - `test_publish_merges_with_caller_headers`
- Update `backend/app/tests/test_security_and_validation.py:314-330` — the existing 3 tests still pass; add 2 more for producer-side header echo.

### What this plan does NOT do

- Does not change the middleware (still web-tier only).
- Does not change the log format string.
- Does not add tracing/span IDs (separate concern; future work).

---

## 7. Item 12 — Detailed plan

### New code

- `backend/app/tasks.py` (add) — `cleanup_orphan_hls(max_keys: int = 1000)` Celery task:
  ```python
  @shared_task
  def cleanup_orphan_hls(max_keys: int = 1000):
      """Scan hls/ prefix; delete prefixes whose clip_id is not in DB.
      
      Bounded to max_keys per run for safety. Idempotent.
      Increments echoflow_orphan_hls_cleaned_total counter.
      """
      _dirs, hls_files = default_storage.listdir('hls')
      if not hls_files:
          return {'scanned': 0, 'deleted': 0}
      # hls_files are clip_id-named directories (UUIDs)
      candidate_ids = {f for f in hls_files if is_uuid(f)}
      candidate_ids = set(itertools.islice(candidate_ids, max_keys))
      existing = set(
          str(i) for i in
          AudioClip.objects.filter(id__in=candidate_ids)
          .values_list('id', flat=True)
      )
      orphans = candidate_ids - existing
      for orphan_id in orphans:
          try:
              _delete_s3_prefix(f'hls/{orphan_id}')
              metrics.orphan_hls_cleaned_total.inc()
          except Exception:
              logger.warning(...)
      return {'scanned': len(candidate_ids), 'deleted': len(orphans)}
  ```

- `backend/EchoFlow/settings.py:286-345` — add `cleanup-orphan-hls` to `CELERY_BEAT_SCHEDULE`, daily at 03:00 UTC.

- `backend/app/metrics.py` — add `orphan_hls_cleaned_total = Counter('echoflow_orphan_hls_cleaned_total', 'HLS prefixes cleaned by cleanup_orphan_hls task')`.

### Tests

- New file `backend/app/tests/test_orphan_cleanup.py`:
  - `test_finds_orphans_in_hls_prefix` — fabricate 2 AudioClip rows + 1 hls/<orphan_uuid>/master.m3u8 in fake storage, run task, assert orphan deleted, real clips untouched
  - `test_no_op_when_no_orphans` — fabricate only real clips, assert 0 deleted
  - `test_bounded_to_max_keys` — fabricate 1500 orphans + 1 real, run with `max_keys=1000`, assert exactly 1000 deleted
  - `test_idempotent` — run twice, second run is a no-op
  - `test_beat_schedule_contains_cleanup_orphan_hls` — structural check (matches the existing `test_n14_cors_regex` style)
  - `test_increments_metric` — runs the task, asserts counter incremented

### What this plan does NOT do

- Does not change the post_delete signal.
- Does not move orphan cleanup inline to a synchronous path.
- Does not delete the `original_file` orphans (separate concern; same task can be extended later).
- Does not add a manual mgmt command (the beat-scheduled task is sufficient; operators can `.delay()` it if they want).

---

## 8. Doc corrections (low-effort, ship alongside first commit)

- `docs/backend-bug-fixs.md:595-596` (§10.7) — fix `db_routers.py` entry
- `docs/backend-bug-fixs.md:598-599` (§10.8) — fix `HF_TOKEN` entry wording
- `docs/backend-bug-fixs.md:1173-1175` (Item 10) — fix file location
- `docs/EXPLAIN/recommendation/03-feed-pre-computation.md:520` — same fix

These land in the first commit of the feature branch (a `docs:` commit) before any code changes.

---

## 9. Branch & commit plan

Branch: `feat/group-b-architectural` (off current `main` at `ed01118`).

Commits (suggested, in this order):

1. `docs: correct 4 false-positives and wrong file references in Group B (audit §10.7, §10.8, item 10)`
2. `feat(backend): task_prerun/task_postrun + correlation_id header propagation (Group B item 11)`
3. `feat(backend): wire invalidate_user_vectors_cache into record_like_toggle/record_skip (Group B item 10)`
4. `feat(backend): periodic cleanup_orphan_hls task (Group B item 12)`
5. `feat(backend): RedisCounterStore + flusher, remove F() side-effect from UserInteraction.save() (Group B item 9)`
6. `docs: add Group B completion report to backend-bug-fixs.md Part 4`

Each commit is independently shippable; the merge to `main` is a `--no-ff` merge (matches Group A's pattern).

---

## 10. Test plan (run from `.venv/bin/python -m pytest backend/app/tests/`)

Per commit:
- Commit 1: 138 → 138 (doc only)
- Commit 2: 138 → 144 (5 new test_correlation tests + 1 new task_publisher test)
- Commit 3: 144 → 146 (2 new test_services_interactions invalidation tests)
- Commit 4: 146 → 152 (6 new test_orphan_cleanup tests)
- Commit 5: 152 → 169 (8 new test_counter_store tests + 6 new test_counter_flush tests + 1 modified test_services_interactions)
- Commit 6: 169 → 169 (doc only)

Final: **169 passed, 4 skipped, 0 failed** (vs the current 138 passed).

Two tests in Commit 5 will need to be marked `@unittest.skip("requires Redis; LocMem is insufficient")` and noted per the AGENTS.md "Known Skipped Tests" pattern — the Lua atomicity test and the concurrent-writer test. The test env (SQLite + LocMem) cannot exercise them; the Docker `web` container can.

---

## 11. Risk register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Redis INCRBY flusher crashes mid-batch, double-counts | Low | High (counts off) | Idempotent drain (Lua atomic); Redis is source of truth; flusher re-runs pick up same delta |
| F() removal breaks a test that depends on the side-effect | Medium | Medium | Phase 1 dual-write flag; existing tests pass under both modes |
| `task_prerun` clears contextvar but the task body spawns a thread that uses the OLD contextvar | Low | Low | contextvars are thread-local in 3.7+; thread inherits parent's snapshot. Not an issue in practice. |
| Cleanup task runs while a `process_audio_to_hls` is still writing the HLS tree | Low | Medium (deletes in-progress tree) | Race window is small; signal's post_delete only fires after row delete; concurrent create writes to NEW clip id. The orphan-task scans prefixes whose clip_id is NOT in DB. In-progress writes are to a clip_id that IS in DB. No race. |
| Cache invalidation creates a stampede (all `/suggestions/` calls simultaneously miss) | Low | Low (recommendation engine still serves; just recomputes) | 15-min TTL is the back-pressure; recompute is O(50) interactions |
| Doc corrections in this pass conflict with parallel agent's branch | Low | Low | Doc-only changes; no code overlap; cherry-pick friendly |

---

## 12. Out of scope (per user decision)

- **Item 13 — Sentry SDK integration:** deferred until items 9, 10, 11, 12, 15 are reported, committed, and merged.
- **Item 14 — CDN front of MinIO flip in `.env`:** same as above.
- **Item 15 — `app → clips` rename:** same as above.
- **Item 16 — `db_routers.py`:** already shipped (`a85e298`); doc correction only.
- **Item 17 — `HF_TOKEN` rotation:** purely ops; doc correction only.

---

## 13. What I need from you before implementing

1. **Approve the plan as written** (or request changes).
2. **Confirm the 3-phase Item 9 rollout** (dual-write → flip → remove) is acceptable. Alternative: a single-commit "trust the tests" flip.
3. **Confirm the 4 new metric / task additions** (Redis counter, flusher, orphan counter, correlation_id header) are OK. None are optional for the architectural fix; just confirming the surface.
4. **Confirm the branch name** `feat/group-b-architectural` is OK (matches the existing convention).
5. **Note the parallel agent's branch** so I don't touch it.
