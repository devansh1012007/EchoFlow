# Work ledger — 20261107-revenuecat-paytm (source of truth)

## Plan coverage (Phase 1)
| Plan item | Task | Status |
|---|---|---|
| C01 lifetime (3.1) | T-001 | queued (investigation) |
| C02 webhook fail-closed (3.3) | T-002 | queued |
| C03 limits enforce/rename (3.6, 3.11) | T-003 | queued |
| C04 stale poll (3.5) | T-004 | queued |
| C05 write-on-GET (3.7) | T-005 | queued |
| C06 charged-then-error/cancel (3.8, 3.9) mobile | T-006 | queued |
| C07 erasure warning (3.10) | T-007 | queued — warning now, block after Paytm adapter |
| C08–C15 | — | deferred: Phase 2+ gated by Phase 0 sign-offs |

## Waves
### Wave 0 — Investigation (read-only, parallel)
- Agents: INV-BACKEND-A (3.1, 3.2, 3.5), INV-BACKEND-B (3.3, 3.4, 3.6, 3.7, 3.10, 3.11), INV-MOBILE (3.8, 3.9, studio consumer, OTA)
- Pre-mortem: investigators verify lead-agent findings independently; any disagreement stops dispatch until resolved by reading code.
- Gate: reports landed + spot-checked. Backend test baseline: BLOCKED (no Docker) — recorded, not waived.
- Status: COMPLETE (reports committed 6f5c8aa; verdicts: 3.1 EXISTS, 3.2 PARTIAL, 3.3-3.11 CONFIRMED; RevenueCatHost third consumer found; cancel signal UNKNOWN).

### Wave 1 — Phase 1 coders (QUEUED, PAUSED by operator decision 2026-11-07)
- Blocked on: Docker host for backend fail-before/pass-after verification (no Docker in this Windows env; Python 3.12 vs pinned 3.11; no node_modules).
- On resume: capture backend baseline first (`docker compose exec web_local pytest backend/app/tests/ --tb=short` per AGENTS.md scoping), write coder briefs from Wave 0 reports, dispatch T-001..T-007 with disjoint ownership, independent code-reviewer after each.
- Operator decision recorded: "Pause until Docker host (Recommended)" — NOT-RUN implementation explicitly rejected.

## Review / improve cycles
- Per developer protocol: independent code-reviewer after every coder task + final review on combined diff. Not yet started.
