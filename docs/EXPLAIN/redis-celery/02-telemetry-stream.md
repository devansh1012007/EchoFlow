# Telemetry Stream (Redis Streams + Consumer Group)

## Why

`POST /interactions/{id}/log-telemetry/` is the audit's #1 hot path.
At 10k concurrent users, the synchronous `UserInteraction.update_or_create`
underneath holds a row lock on `userinteraction` for every view, every skip,
and every like — and `UserInteraction.save()`'s F() side-effect also locks
the corresponding `AudioClip` row when the interaction is a `like`/`share`/
`skip`. Result: connection pool exhaustion + hot-row lock contention.

The pre-Stage-2 design had already moved past the *per-request* row lock
by buffering events in a Redis list (`telemetry:queue`) and bulk-inserting
in `flush_telemetry` (Celery Beat, every 30s). That solved the row-lock
problem but left a list (destructive, no replay, no consumer groups).

## Current design (2026-09)

A **Redis Stream** with a **consumer group** is now the primary path.
A Redis list is retained as a fallback for one operational cycle.

### Data structures

| Key | Type | Purpose | Approx size at 10k |
|---|---|---|---|
| `stream:interaction.events` | Stream | Telemetry events, primary | ~20 MB (`MAXLEN ~ 50000`) |
| `cg:telemetry-flush` | Consumer group | Resume offset for stream consumers | n/a |
| `stream:interaction.events:dlq` | Stream | Poison messages + diagnostic info | unbounded; alerted on |
| `telemetry:queue` | List | Legacy fallback, drained by `flush_telemetry_legacy` | ~3 MB at sustained load |
| `processed_event:{event_id}` | String | SETNX dedup key, 24h TTL | ~200 MB across 24h |

### Producer

`backend.app/services/interactions.py::record_telemetry` is the single
producer. Path priority (the comment block in the file documents this):

1. `XADD stream:interaction.events MAXLEN ~ 50000 * event_id <uuid> schema_version "1.0.0" payload <json>`
2. `RPUSH telemetry:queue <json>` (if `ECHOFLOW_TELEMETRY_STREAM=off` OR XADD raised)
3. Synchronous `UserInteraction.update_or_create` (if BOTH Redis calls raised)

Every event carries an `event_id` (UUID4). The consumer SETNX's
`processed_event:{event_id}` with a 24h TTL; duplicate deliveries
(consumer crash before XACK, retries) are silently dropped.

### Consumer

`backend.app/tasks.py::flush_telemetry_stream` runs on the `celery`
queue via Beat, every **10s**. Cadence is faster than the legacy
30s because consumer groups have lower per-tick overhead than LPOP
loops (no polling; XREADGROUP blocks for 5s when idle).

```
XREADGROUP cg:telemetry-flush <consumer-name> COUNT 500 BLOCK 5000
  │
  ├─ for each entry:
  │    ├─ parse payload
  │    ├─ SET processed_event:{event_id} 1 NX EX 86400
  │    │    └─ if NX returns False: drop (duplicate); XACK
  │    ├─ resolve user + clip
  │    ├─ if missing: log warning, XACK (data loss logged for triage)
  │    └─ bulk_create UserInteraction row
  │
  ├─ XACK all successfully-processed entries
  └─ for poison messages (malformed payload):
       XADD stream:interaction.events:dlq * original_id <id> reason "malformed"
       XACK from main stream
```

On `bulk_create` failure (DB down, schema mismatch): route *all* entries
from the failed batch to DLQ; XACK from main stream. The stream
advances; the pipeline does not stall.

### Beat schedule

```python
CELERY_BEAT_SCHEDULE = {
    'flush-telemetry-stream': {
        'task': 'backend.app.tasks.flush_telemetry_stream',
        'schedule': 10.0,
    },
    'flush-telemetry-legacy': {
        'task': 'backend.app.tasks.flush_telemetry_legacy',
        'schedule': 30.0,  # TODO: remove after one cycle of stable operation
    },
}
```

## Idempotency contract

At-least-once delivery (the Redis Streams reality). Three failure modes
to defend against:

1. **Producer crashes after Redis write, before HTTP 202 returns:**
   the client may retry. The XADD is idempotent (different `event_id`
   per call), so the duplicate event is filtered by the consumer's
   `SETNX processed_event:{event_id}` (24h TTL) — *but* only if the
   retry happens after the first event is in the stream. The retry
   before the first XADD would create a second event with a different
   `event_id`; both would be processed. **Implication:** the consumer
   also dedups on `(user, clip, action_type, updated_at)` via the
   `unique_together` constraint + `bulk_create(ignore_conflicts=True)`.
   Duplicate inserts become no-ops at the DB layer.

2. **Consumer crashes after bulk_create, before XACK:** the entry is
   re-read on the next XREADGROUP tick. `processed_event:{event_id}`
   SETNX returns False; the entry is dropped (the DB already has the
   row) and XACK'd.

3. **Consumer crashes mid-batch (some XACK'd, some not):** the
   un-XACK'd entries are re-read on the next tick. The same
   `SETNX` dedup catches them. No row is inserted twice.

## Operational signals

```bash
# Stream length
redis-cli XLEN stream:interaction.events

# Pending (read but not ACKed) — should stay under ~1k
redis-cli XPENDING stream:interaction.events cg:telemetry-flush

# Last delivered ID per consumer
redis-cli XINFO CONSUMERS stream:interaction.events cg:telemetry-flush

# DLQ depth (alert on > 0)
redis-cli XLEN stream:interaction.events:dlq

# Legacy list (should be empty in normal operation)
redis-cli LLEN telemetry:queue
```

Alerts (see `docs/EXPLAIN/architecture/01-system-overview.md`):

| Signal | Threshold |
|---|---|
| `stream:interaction.events` length | > 50k sustained 5 min |
| `XPENDING` count | > 1k sustained 5 min |
| `stream:interaction.events:dlq` length | > 0 |
| `telemetry:queue` length | > 0 (legacy path running) |

## Rollback

Set `ECHOFLOW_TELEMETRY_STREAM=off` in the API container. The producer
returns to the LIST path immediately. The legacy `flush_telemetry_legacy`
task (Beat, every 30s) continues to drain the list. No code change,
no migration, no consumer-group teardown required.

To fully remove the stream:
1. `XGROUP DESTROY stream:interaction.events cg:telemetry-flush`
2. `DEL stream:interaction.events` (only after confirming the consumer
   is stopped)
3. Remove `flush-telemetry-stream` from `CELERY_BEAT_SCHEDULE`.
4. Remove `_use_stream()` / `_xadd_telemetry` from
   `services/interactions.py`.

## Future work

- **Counter batcher (P1.1 in the event-driven plan):** replace the F()
  side-effect in `UserInteraction.save()` with a Redis `INCRBY` per
  `clip:{id}:likes|shares|skips` and a separate `flush_counters_to_pg`
  Beat task. The service layer makes this a swap inside
  `services/interactions.py` rather than a view rewrite.
- **Multiple consumer groups:** today there is one (`cg:telemetry-flush`).
  Adding `cg:counter-batcher` and `cg:feature-engineers` (per the
  recommendation-system event flow in the event-driven plan) requires
  no producer changes — just register a new group with
  `XGROUP CREATE ... $`.
- **DLQ consumer / alerter:** today the DLQ grows without bound and
  requires manual `XRANGE` triage. A `dlq_triage` task that pages on
  Sentry when the DLQ length > 0 is a small follow-up.
- **Replace locmem cache in tests with `fakeredis`:** today's tests mock
  `cache.client.get_client()` to assert XADD calls. `fakeredis` would
  give more realistic behavior. Not blocking; deferred.

## Source of truth

- Producer: `backend/app/services/interactions.py`
- Consumer: `backend/app/tasks.py` (`flush_telemetry_stream`,
  `flush_telemetry_legacy`)
- Beat: `backend/EchoFlow/settings.py` (`CELERY_BEAT_SCHEDULE`)
- Tests: `backend/app/tests/test_services_interactions.py`
- Plan reference: `docs/relational-to-event-driven-architecture.md` (Stage 4)
