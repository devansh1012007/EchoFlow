# Event-Driven Metrics Pipeline

**Status:** implemented (commit `fix/update-global-metrics-scan`, 2026-09).
**Supersedes:** the legacy `update_global_metrics` task that ran an
O(N) full-table scan with a correlated subquery every 5 minutes.

## Why

The architecture audit (`docs/backend-bug-fixs.md` and the
"group C / engagement-metrics" concern) flagged two pathologies in
the prior pipeline:

1. **Correlated subquery on every clip, every beat.** The old
   `update_global_metrics` recomputed `avg_completion_rate` with
   `SELECT AVG(completion_rate) FROM userinteraction WHERE clip_id = …`
   for every ready AudioClip row, paginated in 5,000-row chunks but
   still touching the entire catalog. At 1M clips × 100 views, that
   is 100M index lookups per beat.
2. **Per-event row locks on the hot path.** Every like, share, or
   skip took a row lock on AudioClip (via the F() side-effect in
   `UserInteraction.save()`) to bump the counter. Viral clips
   serialized behind hundreds of row locks per second.

Both are O(N) in the clip catalog and grow linearly with traffic.
The fix is to invert the data flow: O(1) writes on the request
path, periodic batched updates on the flusher.

## The new pipeline

```
  HTTP request                          Celery Beat (5 min)
  ────────────                          ───────────────────
  record_like_toggle()
    └─ UserInteraction.save()
         └─ counter_store.increment(likes, +1)   ──►  Redis INCRBY
                                                 (O(1) on the request path)

  record_skip()
    └─ counter_store.add_completion(user, clip, rate)
         └─ INCRBYFLOAT completion_sum              ──►  Redis
         └─ INCR       completion_count

  flush_counters_to_pg()                  [beat]
    1. counter_store.drain()              ◄─── atomic Lua GETALL+DEL
    2. _apply_counter_deltas()            ──►  one F() UPDATE per clip
    3. _apply_completion_deltas()         ──►  one UPDATE per clip (ACR)
                                              + one UserInteraction row
                                                per (user, clip) (materialize)
    4. _apply_engagement_velocity()       ──►  one batched UPDATE for
                                              the dirty clip set
```

The key invariant: **the request path is O(1) Redis operations**.
The Postgres workload is bounded by the number of clips that
actually received activity in the beat (the "dirty" set), not by
the total catalog size.

## Why per-(user, clip) completion keys

`avg_completion_rate` is a per-(user, clip) measurement: each
user's watch time / clip duration produces an independent sample,
and the recommendation engine downstream reads those samples to
weight engagement signals. Two options were considered:

| Option | Tradeoff |
|---|---|
| **A. Per-(user, clip) keys** (chosen) | Preserves the per-user signal; the flusher materializes a `UserInteraction(interaction_type='view')` row per (user, clip) per beat with the aggregated rate. Downstream consumers see the same row shape they always did. |
| B. Per-clip aggregated keys | Simpler flusher; loses the per-user signal. The recommendation engine's `UserInteraction`-shaped inputs would need to migrate to a new read path. |

We chose A because the consumer migration would be a much larger
blast radius than the flusher. The keyspace grows roughly 2× for
completion data, but the per-user traffic per beat is bounded (a
user can only skip or view a clip so many times in 5 minutes).

## Failure modes

| Failure | Effect | Mitigation |
|---|---|---|
| Redis unavailable | `counter_store.increment` raises; logged at DEBUG. The user-facing write still succeeds. The denormalized counter goes stale until Redis recovers. | Pre-existing: a single missing skip or like sample degrades the per-clip ranking by a negligible amount on the next beat. The flusher's `_apply_counter_deltas` skips clips with F() errors and continues. |
| Flush task crash mid-run | The drained deltas are lost (Lua script does read-and-DEL atomically, so partial drains are not possible). Next beat re-bumps from the next round of writes. | The flusher is idempotent on the dirty-set semantics; losing a beat means a 5-minute staleness window for engagement metrics. |
| Postgres UPDATE failure on one clip | Logged at WARNING, the rest of the batch continues. | The next beat will see the same delta in Redis (NO — the drain cleared it) so it is lost. Document this as a known limitation; in practice the flusher is a single Celery task with retry decorators at the function level. |

## Compatibility with existing consumers

- `AudioClip.likes` / `shares` / `skips`: still updated, but by the
  flusher instead of synchronously. The `flush_counters_to_pg` task
  applies the deltas within 5 minutes of the user request.
- `AudioClip.avg_completion_rate`: same per-clip field; computed from
  the drained per-(user, clip) samples instead of a correlated
  subquery. Same numeric value (within floating-point precision).
- `AudioClip.engagement_velocity`: same formula, same field. Applied
  to the dirty set only (not the full catalog).
- `UserInteraction(interaction_type='view')` rows: materialized by the
  flusher, one per (user, clip) per beat. The recommendation engine's
  existing `UserInteraction.objects.filter(user=…, interaction_type=…)`
  queries continue to work; the row shape is identical.
- `UserInteraction(interaction_type='skip')` rows: no longer written
  synchronously by `record_skip`. The flusher materializes
  `UserInteraction(interaction_type='view')` rows for the same data
  (with the aggregated completion_rate). This is a small behavior
  change: any consumer that explicitly filtered
  `interaction_type='skip'` for skip telemetry will now need to look
  at the `view` rows OR at the materialized `AudioClip.skips` counter.

## Migration timeline

- **2026-09-05**: this commit lands. The dual-write env flag
  (`ECHOFLOW_DUAL_WRITE_COUNTERS`) is now a no-op (always False).
  Deployments that had it set can leave it set; the no-op preserves
  the contract.
- **2026-09-12** (planned follow-up): the `update-global-metrics`
  Beat entry is removed. The task body is already a no-op stub.

## See also

- `docs/EXPLAIN/decisions/group-b-architectural-plan.md` — the
  earlier design notes for the counter store.
- `docs/EXPLAIN/decisions/partial-issues-completion-plan.md` —
  the rollout playbook.
- `backend/app/services/counter_store.py` — the counter store
  implementation.
- `backend/app/tasks.py::flush_counters_to_pg` — the flusher.
- `backend/app/models.py::UserInteraction.save` — the F()-free save
  path.
