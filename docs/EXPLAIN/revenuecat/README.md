# RevenueCat Integration — Overview

This directory contains detailed documentation for the RevenueCat Pro subscription integration in EchoFlow.

## Files

| File | Description |
|------|-------------|
| `01-architecture.md` | System architecture, data flow, and component responsibilities |
| `02-gating-strategy.md` | Usage-limit-based gating design, enforcement points, and limits |
| `03-sync-strategy.md` | REST API polling design, grace period handling, and sync logic |
| `04-webhooks.md` | Webhook endpoint design (Phase 2), HMAC verification, and activation |
| `05-frontend.md` | Frontend SDK integration, paywall component, and subscription context |
| `06-testing.md` | Test coverage, patterns, and how to run RevenueCat-specific tests |
| `07-operations.md` | Operational runbooks: manual sync, customer portal, secret rotation |

## Quick Reference

### Environment Variables
All RevenueCat env vars are defined in [AGENTS.md #environment-variables-required](../AGENTS.md#environment-variables-required):

| Variable | Required | Purpose |
|---|---|---|
| `REVENUECAT_SECRET_KEY` | Yes (backend) | Secret API key for REST polling |
| `REVENUECAT_PUBLIC_KEY` | Yes (frontend) | Public key for SDK init |
| `REVENUECAT_PROJECT_TOKEN` | Yes | RevenueCat project token |
| `REVENUECAT_ENTITLEMENT_ID` | Yes | Entitlement ID (`pro`) |
| `REVENUECAT_SYNC_INTERVAL_MINUTES` | No (default: 360) | Poll interval |
| `REVENUECAT_WEBHOOK_SECRET` | Phase 2 | HMAC secret for webhooks |

### API Endpoints
| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/subscription/` | GET | IsAuthenticated | Current Pro status + usage limits |
| `/subscription/sync/` | POST | IsAuthenticated | Trigger immediate sync (10/hour) |
| `/subscription/manage/` | GET | IsAuthenticated | Customer Portal URL |
| `/webhooks/revenuecat/` | POST | AllowAny | Webhook receiver (Phase 2) |

### Key Implementation Files

**Backend:**
- `backend/app/models.py` — User model fields + `is_pro()` method
- `backend/app/services/revenuecat.py` — REST API service layer
- `backend/app/tasks.py` — `sync_revenuecat_entitlements` Celery task
- `backend/app/views/subscription.py` — Status/sync/manage views
- `backend/app/views/webhook.py` — Webhook endpoint
- `backend/app/tests/test_revenuecat.py` — 21 backend tests

**Frontend:**
- `frontend/sample_frontend/src/stores/subscription.tsx` — Subscription context
- `frontend/sample_frontend/src/components/subscription/Paywall.tsx` — Paywall UI
- `frontend/sample_frontend/src/stores/__tests__/subscription.test.tsx` — Store tests
- `frontend/sample_frontend/src/components/subscription/__tests__/Paywall.test.tsx` — Paywall tests

### Test Commands
```bash
# Backend tests
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm -e PYTHONPATH=/app web pytest backend/app/tests/test_revenuecat.py -v

# Frontend tests
cd frontend/sample_frontend && npx vitest run src/stores/__tests__/subscription.test.tsx src/components/subscription/__tests__/Paywall.test.tsx
```

### Design Decisions (from AGENTS.md)
- **App User ID = User.uuid** — survives username/email changes
- **Polling, not webhooks** — free RevenueCat plan limitation
- **Grace period** — `User.is_pro()` respects `pro_grace_until`
- **Usage-limit gating** — checked before serializer validation (fail fast)
- **Webhook forward-compat** — HMAC-SHA256 verification gated on env var

See individual files for detailed designs and trade-offs.