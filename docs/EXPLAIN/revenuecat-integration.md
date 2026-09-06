# RevenueCat Integration Design Document

## Overview

Integrate RevenueCat Billing (Stripe-backed) into EchoFlow for Pro subscription
management. Uses REST API polling (no webhooks initially) and usage-limit-based
gating to differentiate free vs Pro tiers.

## Architecture

### RevenueCat Billing Model

| Entity | Value |
|---|---|
| Offerings | Single "Pro" offering |
| Entitlements | Single `pro` entitlement |
| Products | Stripe products (created in RevenueCat dashboard) |
| Customer Portal | RevenueCat-hosted management URL |

### App User ID Mapping

Django `User.uuid` (UUID4) is used as the string representation of
`app_user_id` in RevenueCat. This survives username/email changes and is
immutable per-user.

```
User.uuid -> str(uuid) -> RevenueCat app_user_id
```

### Backend State Model (User model fields)

| Field | Type | Description |
|---|---|---|
| `revenuecat_app_user_id` | UUIDField (nullable) | Maps Django user to RevenueCat app_user_id |
| `has_pro_entitlement` | BooleanField (default False) | Cached Pro status flag |
| `pro_expires_at` | DateTimeField (nullable) | Current entitlement expiration |
| `pro_grace_until` | DateTimeField (nullable) | Grace period end (billing grace period) |
| `pro_last_synced` | DateTimeField (auto_now) | Last successful sync timestamp |

### Data Flow

```
Frontend (purchases-js SDK)        Backend                        RevenueCat API
        │                            │                                    │
        ├─── purchase ──────────────→│                                    │
        │                             │                                    │
        ├─── identify(app_user_id) ───→│                                    │
        │                             │                                    │
        │                             │─── GET /subscribers/{id} ─────────→│
        │                             │                                ←───┤
        │                             │─── update user fields ────────────→│(DB)
        │                             │                                    │
        │←─── show Pro features ───────│                                    │
        │                             │                                    │
        ├─── GET /subscription ───────→│                                    │
        │←─── 200 {is_pro: true} ──────│                                    │
        │                             │                                    │
        ├─── GET /manage ─────────────→│                                    │
        │←─── {url: "https://..."} ────│                                    │
```

### Gating Strategy: Usage Limits (Option A)

Pro gating is enforced via usage limits checked at request time:

| Feature | Free Limit | Pro Limit |
|---|---|---|
| Daily uploads | 5 clips | Unlimited |
| Max clip duration | 60 seconds | 300 seconds |
| Upload file size | 10 MB | 100 MB |
| HD quality (48kHz+) | Blocked | Allowed |
| Audio quality | 128 kbps | 320 kbps |

Limits are checked in:
- `serializers.py:ClipUploadSerializer` — upload count + file size
- `tasks.py:process_audio_to_hls` — clip duration + quality
- Feed views — HD quality filtering

### Grace Period Handling

During RevenueCat's billing grace period (3 days for annual, 3 days for
monthly by default), `has_pro_entitlement` remains `True` until the grace
period ends AND the subscription is in a non-active state (e.g., `expired`).

Logic in `services/revenuecat.py`:
```python
if sub_status in ('active', 'in_trial', 'in_billing_grace_period', 'in_grace_period'):
    has_pro = entitlement_active or grace_period_active
else:
    has_pro = False
```

The grace period end date is stored in `pro_grace_until` so we can continue
serving Pro features during grace even if polling is delayed.

### Sync Strategy: REST API Polling

No webhooks are used in Phase 1. A Celery Beat task polls the RevenueCat REST
API every 6 hours (configurable via `REVENUECAT_SYNC_INTERVAL_MINUTES`):

```
GET https://api.revenuecat.com/v1/subscribers/{app_user_id}
```

Response parsed for:
- `subscribed` (active entitlements)
- `expire_date_ms` or `grace_period_expire_date_ms`
- `subscriptions.{product_id}.status`

If the polling response indicates the subscription is inactive AND grace
period has expired, `has_pro_entitlement` is set to `False`.

### Public API Key (Frontend)

The `REVCAT_PUBLIC_KEY` is safe to expose via the frontend via:
- `VITE_REVENUECAT_PUBLIC_KEY` in the frontend `.env`
- Used only for SDK initialization (`Purchases.setup()`)

### Secret API Key (Backend)

`REVCAT_SECRET_KEY` is backend-only, stored in the Docker `.env` and
never exposed to the frontend. Used for REST API polling.

## Environment Variables

### Backend (.env)

| Variable | Required | Description |
|---|---|---|
| `REVENUECAT_SECRET_KEY` | Yes (backend) | RevenueCat secret API key |
| `REVENUECAT_PUBLIC_KEY` | No | Public API key (for reference in docs) |
| `REVENUECAT_PROJECT_TOKEN` | Yes | RevenueCat project token (SDK) |
| `REVENUECAT_ENTITLEMENT_ID` | Yes | Entitlement ID string (`pro`) |
| `REVENUECAT_SYNC_INTERVAL_MINUTES` | No | Poll interval (default: 360) |
| `REVENUECAT_DAILY_UPLOAD_LIMIT_FREE` | No | Free daily uploads (default: 5) |
| `REVENUECAT_UPLOAD_MAX_SIZE_MB_FREE` | No | Free max upload size (default: 10) |
| `REVENUECAT_CLIP_DURATION_LIMIT_FREE` | No | Free max duration (default: 60) |
| `REVENUECAT_HD_QUALITY_BLOCKED_FREE` | No | Block HD for free users (default: True) |

### Frontend (.env.local)

| Variable | Description |
|---|---|
| `VITE_REVENUECAT_PUBLIC_KEY` | Public API key for SDK init |

## API Endpoints

### `GET /api/v1/subscription/`

Returns the current Pro subscription status for the authenticated user.

```
Response 200:
{
  "is_pro": true,
  "expires_at": "2026-10-15T10:30:00Z",
  "grace_until": null,
  "last_synced": "2026-09-06T12:00:00Z",
  "limits": {
    "daily_uploads_remaining": "unlimited",
    "max_clip_duration_seconds": 300,
    "max_upload_size_mb": 100,
    "hd_quality_allowed": true
  }
}
```

### `POST /api/v1/subscription/sync/`

Forces an immediate sync with RevenueCat (for manual refresh / debug).

```
Response 200:
{
  "synced": true,
  "is_pro": true,
  "expires_at": "2026-10-15T10:30:00Z"
}
```

### `GET /api/v1/subscription/manage/`

Returns the RevenueCat Customer Portal URL for subscription management.

```
Response 200:
{
  "url": "https://rcat.page/p/your-project?app_user_id=..."
}
```

### Webhook Placeholder (Future)

```
POST /api/v1/webhooks/revenuecat/
```

Currently returns 200 but logs the payload. Full webhook verification
(HMAC) will be implemented when upgrading to RevenueCat Pro plan.

## Database Migration

New migration appends Pro-related fields to the User model:

```python
class Migration:
    dependencies = [
        ('backend', '0002_audioclip_moderation_approved'),
    ]
    operations = [
        migrations.AddField(
            model_name='user',
            name='revenuecat_app_user_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='has_pro_entitlement',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='user',
            name='pro_expires_at',
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='pro_grace_until',
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='pro_last_synced',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
```

## Permissions

| Endpoint | Permission |
|---|---|
| `GET /subscription/` | IsAuthenticated |
| `POST /subscription/sync/` | IsAuthenticated |
| `GET /subscription/manage/` | IsAuthenticated |
| `POST /webhooks/revenuecat/` | AllowAny (verifies HMAC internally) |

## Throttling

| Endpoint | Rate Limit |
|---|---|
| `GET /subscription/` | Default (see throttle config) |
| `POST /subscription/sync/` | 10/hour/user (manual refresh) |

## Frontend Integration

### SDK Initialization

In `main.tsx`, after auth context is available:

```typescript
import { Purchases } from '@revenuecat/purchases-js';
const publicKey = import.meta.env.VITE_REVENUECAT_PUBLIC_KEY;
const appUserID = user.uuid;
Purchases.setup(publicKey, appUserID);
```

### Paywall Component

`Paywall.tsx` uses `Purchases.getOfferings()` to fetch the Pro package,
renders `Purchases.createCustomerPortalWebPurchase()` on click, and shows
the management URL on success.

### Pro Gating UI

In `AppShell.tsx`, check `GET /api/v1/subscription/` on mount and when
the user navigates to Pro-gated features. If `is_pro` is false, show the
paywall overlay.

## Testing

### Backend Tests

- `test_revenuecat_sync.py` — mock RevenueCat API responses, verify User model updates
- `test_subscription_views.py` — subscription status, sync, management endpoints
- Pro gating tests in existing test files (upload, feed, quality)

### Frontend Tests

- `src/components/subscription/__tests__/Paywall.test.tsx`
- `src/stores/__tests__/subscription.test.ts`

## Rollback Strategy

If the RevenueCat integration needs to be rolled back:

1. Revert the model migration
2. Remove the env vars from `.env.example`
3. Disable the Celery Beat task
4. The frontend paywall degrades gracefully (Pro features show "upgrade" CTA)
