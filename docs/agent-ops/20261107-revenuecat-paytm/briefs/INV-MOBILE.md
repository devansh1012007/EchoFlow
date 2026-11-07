# Brief — INV-MOBILE (investigator) · Wave 0 · 20261107-revenuecat-paytm
## 1. Role and mission
Read-only investigator. Verify mobile defects 3.8 (charged-then-error), 3.9 (cancel-as-error), second consumer `studio.tsx`, entitlement default mismatch, OTA/runtimeVersion reality on branch `feat/revenuecat-paytm-phase1`. No edits. Node 24 available; you may run `node --version` only — no installs, no test suites.
## 2. Required reading
1. Approved plan §C, §E (C06), §H (T07, T08) at `C:\Users\devan\.opencode\plan\2026-11-07-revenuecat-paytm-billing.md`
2. `docs/agent-ops/20261107-revenuecat-paytm/00-mission.md`, `01-work-ledger.md`, `COMMS.md`
3. Code: `mobile/src/hooks/useSubscription.ts`, `mobile/app/settings.tsx`, `mobile/app/(tabs)/studio.tsx`, `mobile/src/lib/revenuecat.ts`, `mobile/src/api/endpoints/subscription.ts`, `mobile/app.config.ts:81-94,139-140`, `mobile/eas.json`, `mobile/package.json`
## 3. Task and acceptance
Per item: verdict + path:line evidence; exact error strings shown to user; whether user-cancel is distinguishable in the installed SDK version (check package.json version + any node_modules types if present, else mark UNKNOWN); what removing/changing the hook breaks (studio + settings + tests). Facts vs inferences separated.
## 4. Ownership
WRITE-SET: only `docs/agent-ops/20261107-revenuecat-paytm/reports/w0-investigation/INV-MOBILE.md`. All source read-only.
## 5. Siblings: INV-BACKEND-A/B — disjoint.
## 6. Deliverables
Report with ORCH-DIGEST first. Final message ≤8 lines.
