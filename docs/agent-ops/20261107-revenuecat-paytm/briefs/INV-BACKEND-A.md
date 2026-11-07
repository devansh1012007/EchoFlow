# Brief — INV-BACKEND-A (investigator) · Wave 0 · 20261107-revenuecat-paytm
## 1. Role and mission
Read-only investigator. Verify Phase 1 backend defects 3.1 (lifetime), 3.2 (entitlement ID), 3.5 (stale poll) against code on branch `feat/revenuecat-paytm-phase1` (base 8765b73). No edits, no test runs (Docker unavailable here).
## 2. Required reading
1. Approved plan §C, §E (C01, C04), §H (T01, T05) at `C:\Users\devan\.opencode\plan\2026-11-07-revenuecat-paytm-billing.md`
2. `docs/agent-ops/20261107-revenuecat-paytm/00-mission.md`, `01-work-ledger.md`, `COMMS.md`
3. Code: `backend/app/services/revenuecat.py`, `backend/app/models.py:60-92`, `backend/app/tasks.py:500-548`, `backend/EchoFlow/settings.py:1190-1208`, `backend/app/tests/test_revenuecat.py`
## 3. Task and acceptance
For each defect: verdict EXISTS/NOT-REPRODUCIBLE/PARTIAL with exact path:line chain; root cause; exact files/symbols a coder would touch; test that must fail before fix. Separate facts from inferences. No design proposals beyond fix options with tradeoffs.
## 4. Ownership
WRITE-SET: only your report file `docs/agent-ops/20261107-revenuecat-paytm/reports/w0-investigation/INV-BACKEND-A.md`. All source read-only. FORBIDDEN: any source edit, any other report file.
## 5. Siblings: INV-BACKEND-B (webhook/limits/erasure), INV-MOBILE (client) — disjoint, no shared files.
## 6. Deliverables
Report with ORCH-DIGEST block first, then per-defect evidence. Final message ≤8 lines: STATUS | agent | report path | verdicts | blockers.
## 7. Stop conditions: same read failing 3x, plan contradicts code irreconcilably → BLOCKED with evidence.
