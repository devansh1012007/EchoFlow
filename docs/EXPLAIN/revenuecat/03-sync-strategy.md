# RevenueCat Integration — Sync Strategy

## Overview

EchoFlow uses **REST API polling** (no webhooks) to synchronize Pro subscription state from RevenueCat. This is a Phase 1 limitation due to the free RevenueCat plan tier not supporting webhooks.

## Polling Mechanism

### Celery Beat Schedule

```python
# backend/EchoFlow/settings.py
CELERY_BEAT_SCHEDULE = {
    'sync-revenuecat-entitlements': {
        'task': 'backend.app.tasks.sync_revenuecat_entitlements',
        'schedule': int(os.environ.get('REVENUECAT_SYNC_INTERVAL_MINUTES', '360')) * 60,
    },
}
```

- **Default interval**: 360 minutes (6 hours)
- **Configurable**: Via `REVENUECAT_SYNC_INTERVAL_MINUTES` env var
- **Queue**: `default` (not `heavy_media` or `fast_feed`)

### Task Logic (`sync_revenuecat_entitlements`)

```python
@shared_task
def sync_revenuecat_entitlements(user_id: str | None = None):
    """
    Sync Pro entitlement state from RevenueCat REST API to local User model.
    
    If user_id provided: sync only that user.
    Otherwise: sync users whose subscription may have expired OR whose
    last sync is stale (older than sync interval + buffer).
    """
    # 1. Check configuration
    if not settings.REVENUECAT_SECRET_KEY:
        return "skipped: not configured"
    
    # 2. Determine users to sync
    if user_id:
        users = [User.objects.get(id=user_id)]
    else:
        now = timezone.now()
        stale_threshold = now - timedelta(
            minutes=settings.REVENUECAT_SYNC_INTERVAL_MINUTES + 1
        )
        users = User.objects.filter(
            Q(has_pro_entitlement=True, pro_expires_at__lte=now + timedelta(days=1))
            | Q(pro_last_synced__lt=stale_threshold)
        )
    
    # 3. Sync each user
    for user in users.iterator():
        sync_entitlements(user)
    
    return f"synced {len(users)} users"
```

### Targeted Sync Strategy

The task only syncs users who **need** syncing:

| User Category | Why Sync |
|---|---|
| `has_pro_entitlement=True` AND `pro_expires_at <= now + 1 day` | Subscription expiring soon or expired |
| `pro_last_synced < stale_threshold` | Sync is stale (older than interval + 1min buffer) |

This avoids polling all users on every beat (saves API calls and DB writes).

## RevenueCat API Call

```python
# backend/app/services/revenuecat.py
def get_subscriber_info(app_user_id: str) -> dict | None:
    url = f"https://api.revenuecat.com/v1/subscribers/{app_user_id}"
    headers = {
        "Authorization": f"Bearer {settings.REVENUECAT_SECRET_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code == 404:
        return None  # Subscriber not found in RevenueCat
    resp.raise_for_status()
    return resp.json().get("subscriber")
```

### Response Parsing

```python
def _is_active_entitlement(subscriber: dict) -> tuple[bool, datetime | None, datetime | None]:
    entitlement_id = getattr(settings, "REVENUECAT_ENTITLEMENT_ID", "pro")
    entitlements = subscriber.get("entitlements", {})
    
    for _, ent_data in entitlements.items():
        if isinstance(ent_data, dict) and ent_data.get("product_id") == entitlement_id:
            is_active = bool(ent_data.get("is_active", False))
            expires_at = _parse_date_ms(ent_data.get("expires_date_ms")) \
                       or _parse_date_ms(ent_data.get("expire_date_ms"))
            grace_until = _parse_date_ms(ent_data.get("grace_period_expire_date_ms"))
            return is_active, expires_at, grace_until
    
    return False, None, None
```

### Fields Updated on User

| User Field | Source | Meaning |
|---|---|---|
| `has_pro_entitlement` | `is_active` | Current Pro status |
| `pro_expires_at` | `expires_date_ms` / `expire_date_ms` | Subscription expiration |
| `pro_grace_until` | `grace_period_expire_date_ms` | Billing grace period end |
| `pro_last_synced` | `auto_now=True` | Last successful sync timestamp |

## Grace Period Handling

RevenueCat provides a billing grace period (typically 3 days for monthly/annual). During this period:
- `is_active` may be `False`
- `grace_period_expire_date_ms` is set to the grace period end
- User should retain Pro access

### `User.is_pro()` Logic

```python
def is_pro(self) -> bool:
    now = timezone.now()
    # Active entitlement + not expired
    if self.has_pro_entitlement and self.pro_expires_at and self.pro_expires_at > now:
        return True
    # Grace period active
    if self.pro_grace_until and self.pro_grace_until > now:
        return True
    return False
```

### Grace Period Scenarios

| Scenario | `has_pro_entitlement` | `pro_expires_at` | `pro_grace_until` | `is_pro()` |
|---|---|---|---|---|
| Active subscription | True | Future | None | True |
| Expired, in grace | False | Past | Future | True |
| Expired, grace ended | False | Past | Past | False |
| No subscription | False | None | None | False |

## Manual Sync

For immediate sync (e.g., after purchase):

```bash
# Frontend calls
POST /api/v1/subscription/sync/
# → Triggers sync_revenuecat_entitlements.delay(user.id)
```

**Rate limit**: 10 requests/hour/user (throttle scope: `subscription_sync`)

## Error Handling

| Error | Behavior |
|---|---|
| Missing `REVENUECAT_SECRET_KEY` | Skip sync, log warning, return early |
| Network timeout (15s) | `requests` timeout, retry on next beat |
| 404 (subscriber not found) | Treat as no entitlement, set `has_pro_entitlement=False` |
| Invalid JSON / 5xx | Exception caught, logged, sync returns early |
| Invalid App User ID | 404 from RevenueCat, treated as no entitlement |

## Testing

```bash
# Run sync tests
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm \
  -e PYTHONPATH=/app web pytest backend/app/tests/test_revenuecat.py::TestSyncEntitlements -v
```

Key test cases:
- `test_sync_sets_pro_when_entitlement_active` — mocks active entitlement, verifies fields updated
- `test_sync_removes_pro_when_entitlement_inactive` — mocks inactive, verifies `has_pro_entitlement=False`
- `test_sync_no_app_user_id_skipped` — user without `revenuecat_app_user_id` skipped
- `test_sync_no_secret_key_skipped` — sync skipped when secret not configured

## Operational Notes

- **Stale data window**: Max 6h + 1min (sync interval + buffer)
- **Manual override**: `POST /subscription/sync/` for immediate sync
- **Customer Portal**: `GET /subscription/manage/` returns RevenueCat management URL
- **Monitoring**: Check `pro_last_synced` for sync health; alert if > 12h stale

## Future: Webhook Support (Phase 2)

When upgrading to RevenueCat Pro plan:

1. Set `REVENUECAT_WEBHOOK_SECRET` in `.env`
2. Configure webhook URL in RevenueCat dashboard: `https://api.yourdomain.com/webhooks/revenuecat/`
3. Webhook endpoint (`/webhooks/revenuecat/`) verifies HMAC-SHA256 signature
4. On verified event, call `sync_entitlements(user)` for immediate sync

See [04-webhooks.md](04-webhooks.md) for full webhook design.