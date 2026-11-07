# Brief — INV-BACKEND-B (investigator) · Wave 0 · 20261107-revenuecat-paytm
## 1. Role and mission
Read-only investigator. Verify Phase 1 backend defects 3.3 (webhook fail-open), 3.4 (no-op), 3.6 (duration/HD unenforced), 3.7 (write-on-GET), 3.10 (erasure ignores billing), 3.11 (vendor names) on branch `feat/revenuecat-paytm-phase1`. No edits, no test runs.
## 2. Required reading
1. Approved plan §C, §E (C02, C03, C05, C07), §H at `C:\Users\devan\.opencode\plan\2026-11-07-revenuecat-paytm-billing.md`
2. `docs/agent-ops/20261107-revenuecat-paytm/00-mission.md`, `01-work-ledger.md`, `COMMS.md`
3. Code: `backend/app/views/subscription.py`, `backend/app/services/revenuecat.py:246-291`, `backend/app/serializers.py:380-460`, `backend/app/views/content.py:198-215`, `backend/app/services/erasure.py`, `backend/app/urls.py:135-139`, `backend/EchoFlow/settings.py:1190-1208`, `backend/app/tests/test_revenuecat.py:601-680`
## 3. Task and acceptance
Per defect: verdict + path:line evidence, root cause, coder touch-set, fail-before test. Especially: where duration/HD limits are ACTUALLY enforced if anywhere (grep every usage), and whether erasure.py truly has zero billing handling. Facts vs inferences separated.
## 4. Ownership
WRITE-SET: only `docs/agent-ops/20261107-revenuecat-paytm/reports/w0-investigation/INV-BACKEND-B.md`. All source read-only.
## 5. Siblings: INV-BACKEND-A, INV-MOBILE — disjoint.
## 6. Deliverables
Report with ORCH-DIGEST first. Final message ≤8 lines.
