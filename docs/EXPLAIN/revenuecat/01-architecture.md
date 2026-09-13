# RevenueCat Integration — Architecture

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Frontend (React + Vite)                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐    │
│  │  Purchases  │  │ Subscription│  │   Paywall   │  │  AppShell (Pro  │    │
│  │    SDK      │◄─│  Context    │──│  Component  │──│   Upgrade CTA)  │    │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘    │
└─────────│─────────────────│─────────────────│─────────────────│─────────────┘
          │                 │                 │                 │
          │ VITE_REVENUECAT_│                 │                 │
          │   PUBLIC_KEY    │                 │                 │
          ▼                 ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Backend (Django + DRF)                            │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐ │
│  │ Subscription    │  │  Subscription   │  │    Subscription             │ │
│  │  Status View    │  │  Sync View      │  │    Manage View              │ │
│  │  GET /sub/      │  │  POST /sub/     │  │    GET /sub/manage/         │ │
│  │  IsAuthenticated│  │  IsAuthenticated│  │    IsAuthenticated          │ │
│  └────────┬────────┘  └────────┬────────┘  └──────────────┬──────────────┘ │
└───────────│────────────────────│──────────────────────────│──────────────────┘
            │                    │                          │
            │                    │                          │
            ▼                    ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         RevenueCat Service Layer                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ sync_entitlements(user)                                                │  │
│  │   1. Check REVENUECAT_SECRET_KEY                                       │  │
│  │   2. GET https://api.revenuecat.com/v1/subscribers/{app_user_id}      │  │
│  │   3. Parse entitlements (is_active, expires_date_ms, grace_period)    │  │
│  │   4. Update User: has_pro_entitlement, pro_expires_at, pro_grace_until │  │
│  │   5. Return True if changed                                            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
            │                    │                          │
            ▼                    ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Celery Beat + Worker                            │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ Task: sync_revenuecat_entitlements                                     │  │
│  │ Schedule: Every REVENUECAT_SYNC_INTERVAL_MINUTES (default 360 = 6h)   │  │
│  │ Queue: default                                                         │  │
│  │ Logic:                                                                 │  │
│  │   - Sync users with expiring subscriptions OR stale last_synced       │  │
│  │   - Per-user: call sync_entitlements(user)                             │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              PostgreSQL (User Model)                         │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │ User Model Fields (added via 0002_user_revenuecat_fields migration)   │  │
│  │   - revenuecat_app_user_id: UUIDField(default=uuid4, null=True)       │  │
│  │   - has_pro_entitlement: BooleanField(default=False)                  │  │
│  │   - pro_expires_at: DateTimeField(null=True)                          │  │
│  │   - pro_grace_until: DateTimeField(null=True)                         │  │
│  │   - pro_last_synced: DateTimeField(auto_now=True)                     │  │
│  │   - is_pro() → bool (property method)                                 │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. User Registration
```
User registers ───→ revenuecat_app_user_id = uuid4() ───→ User saved
```

### 2. Frontend Purchase Flow
```
Frontend: Purchases.setup(publicKey, appUserId)
        ↓
User completes purchase in RevenueCat SDK
        ↓
RevenueCat updates subscriber record
        ↓
Frontend calls GET /subscription/ (or waits for next poll)
```

### 3. Backend Sync (Celery Beat)
```
Celery Beat (every 6h)
        ↓
sync_revenuecat_entitlements task
        ↓
For each user needing sync:
  GET https://api.revenuecat.com/v1/subscribers/{app_user_id}
        ↓
Parse: is_active, expires_date_ms, grace_period_expire_date_ms
        ↓
Update User fields:
  - has_pro_entitlement
  - pro_expires_at
  - pro_grace_until
  - pro_last_synced = now()
```

### 4. Pro Status Check (Request Time)
```
Every protected endpoint / gated feature:
  if user.is_pro():
      allow
  else:
      return 403 / show paywall
```

## Gating Enforcement Points

| Layer | File | Check |
|---|---|---|
| View (upload) | `AudioUploadViewSet.create()` | Daily upload count (before serializer) |
| Serializer | `AudioUploadSerializer.validate()` | File size limit (free tier) |
| Task (HLS) | `process_audio_to_hls` | Duration + quality limits |
| Feed | Feed views | HD quality filtering |

## App User ID Mapping

- **Source**: `User.uuid` (UUID4, generated on user creation)
- **RevenueCat field**: `app_user_id` (string)
- **Mapping**: `str(user.revenuecat_app_user_id)` → RevenueCat `app_user_id`
- **Rationale**: UUID is immutable, survives username/email changes, no PII

## Security Boundaries

| Credential | Location | Exposed to Frontend? |
|---|---|---|
| `REVENUECAT_SECRET_KEY` | Backend `.env` only | **No** |
| `REVENUECAT_PUBLIC_KEY` | Backend `.env`, Frontend `.env` | Yes (SDK init only) |
| `REVENUECAT_WEBHOOK_SECRET` | Backend `.env` only | **No** |

The public key is safe for browser use — it only enables SDK initialization and purchase flows. The secret key enables REST API polling and must never leave the backend.

## Scalability Considerations

- **Polling interval**: Default 6h balances freshness vs API rate limits
- **Per-user sync**: Task iterates only users with expiring subs OR stale sync (not all users)
- **Grace period**: `pro_grace_until` field absorbs polling delays
- **Manual sync**: `POST /subscription/sync/` allows on-demand refresh (rate-limited 10/hour)
- **Webhook future**: Endpoint exists at `/webhooks/revenuecat/` with HMAC verification; enabled when `REVENUECAT_WEBHOOK_SECRET` is set

## Failure Modes

| Failure | Behavior |
|---|---|
| RevenueCat API down | Sync fails silently, returns False; user keeps existing Pro state |
| Secret key missing | Sync skipped, logs warning; Pro state unchanged |
| Network timeout (15s) | Requests timeout, retry on next Beat cycle |
| Invalid App User ID | 404 from RevenueCat, user stays non-Pro |
| Clock skew | `timezone.now()` used consistently; grace period absorbs minor skew |