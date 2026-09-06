The Rollout reminder is operational guidance for when the code hits production. The merge to main is a code event; the rollout is a separate operational event with timing constraints.
What it actually means
The Group B work shipped in 3 phases of risk:
Phase	What's in code	What's the source of truth	Risk
Phase 1 (shipped now)	F() + Redis both run; flusher drains but doesn't apply	F() (Postgres)	Safe. Two paths writing the same answer; flusher is observability.
Phase 2 (Day 7+ after you flip the env)	F() bypassed; flusher applies	Flusher (Redis → Postgres every 5 min)	If flusher fails, Postgres counter drifts. 5-min blast radius.
Phase 3 (Day 14+)	F() code removed	Flusher only	Same as Phase 2, no rollback path to F().


What you (the operator) should actually do
Step 1 — Right after merge (Day 0):
- Just merge, deploy, and watch. Nothing breaks. Items 10, 11, 12 are immediately active and safe. Item 9 is in Phase 1 (dual-write) — the F() still works exactly like before. The Redis deltas accumulate but nothing acts on them yet (the flusher drains-and-discards).
Step 2 — Day 1 to Day 7 (verification window):
- Spot-check that AudioClip.likes in Postgres matches what you'd expect from the user-facing like traffic.
- Pick 5 viral-ish clips, watch the Redis clip:<uuid>:likes key over a 5-min window in RedisInsight / redis-cli MONITOR. Confirm the deltas match the AudioClip.likes increments in Postgres.
- If they don't match, stop. Phase 1 has a bug; investigate before flipping.
- Check the new echoflow_orphan_hls_cleaned_total metric — non-zero values mean the post_delete signal has been failing in production (this is a real signal, not noise).
Step 3 — Day 7+ (only if Step 2 verified clean):
- Edit docker-compose.yml (or .env): set ECHOFLOW_DUAL_WRITE_COUNTERS=false.
- Restart the web + celery services. The F() is now bypassed at runtime; the flusher is the only path to Postgres.
- Watch AudioClip.likes for 24h. If a flusher run fails, the counter drifts but the next run catches up (Redis is source of truth, never lost).
- Monitor for 1 week.
Step 4 — Day 14+ (only if Step 3 was clean for a week):
- Open a small follow-up commit: remove the F() code block from UserInteraction.save() in backend/app/models.py (the lines guarded by if not counter_store.dual_write_enabled(): plus the F() update below it). Also remove the dual_write_enabled() env check.
- This is a 25-line diff. The counter store becomes the only counter path permanently.
Why the 3 phases
The pattern is expand-verify-contract:
- Expand (Phase 1): add the new path without removing the old one. The system is more redundant than it needs to be, but you can compare them.
- Verify (Phase 1 + 1 week): prove the new path gives the same answer as the old.
- Contract (Phase 2-3): remove the old path. By this point you've proven the new one works.
Skipping straight from "code shipped" to "F() removed" is what causes the production incidents that end up in postmortems. The 7-day window is the cost of doing it safely; in return you get a one-way door, not a reversible experiment.
What you do NOT need to do
- You don't need to merge anything more. This is all behind one merge.
- You don't need to flip the env on Day 0. Phase 1 is the safe state.
- You don't need to monitor Redis on Day 0 in detail. Spot-checking 5 clips once or twice in the first week is enough.
- You don't need to write any new code for Phase 2 or 3. The flag and the F() guard are already in the code; Phase 2 is a one-line env change; Phase 3 is a small follow-up commit.
The reminder is there so you don't forget to come back to it. The docs/backend-bug-fixs.md Part 4 (sections 30, 32) has the same info as a written record. If you want, you can copy the Step 1-4 above into a real runbook or into your task tracker.
TL;DR: Merge was Day 0. The system is in safe Phase 1. The next 2-3 weeks are a verification window before you turn off the F() backup. Nothing else to do until then.