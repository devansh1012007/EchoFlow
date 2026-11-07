# Mission — 20261107-revenuecat-paytm (orchestrator-owned)

- Plan: `C:\Users\devan\.opencode\plan\2026-11-07-revenuecat-paytm-billing.md` (APPROVED 2026-11-07)
- Branch: `feat/revenuecat-paytm-phase1` · BASE_SHA: `8765b73` (verified `git rev-parse --short HEAD`)
- Scope this run: **Phase 1 only (C01–C07)**. Phase 2+ gated by Phase 0 sign-offs (tax/legal D4, Play enrolment, Paytm digital-goods permission) — not in hand.
- Baseline: tree clean at start. **Backend tests NOT runnable here** — no Docker in this Windows shell; repo requires Docker+Postgres16+pgvector (`AGENTS.md`); Python here is 3.12, repo pins 3.11. Node 24 available for mobile/frontend JS tests. Baseline backend suite therefore NOT captured; record honestly, do not claim green.
- Hotspots (single-writer): `backend/app/migrations/` (numbering), `mobile/package-lock.json`, `frontend/package-lock.json`, `.env.*.example`, `mobile/eas.json`, `mobile/app.config.ts`.
- Assumptions: A1-A3, Q1-Q2 from plan §L. D1-D10 decisions in plan §G govern briefs.
- Commands (when Docker host available): `docker compose -f docker-compose.local.yml --env-file .env.local exec web_local pytest backend/app/tests/test_revenuecat.py -q` (per AGENTS.md; verify service name on host).
