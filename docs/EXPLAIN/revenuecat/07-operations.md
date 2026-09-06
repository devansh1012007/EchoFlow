# RevenueCat Integration — Operations

## Quick Reference

| Task | Command / Location |
|---|---|
| Check Pro status | `GET /subscription/` |
| Trigger manual sync | `POST /subscription/sync/` |
| Get management URL | `GET /subscription/manage/` |
| View sync logs | `docker compose logs web \| grep "sync_entitlements\|RevenueCat"` |
| Check last sync time | Query `User.pro_last_synced` |

## Environment Variables (Production)

```bash
# Required
REVENUECAT_SECRET_KEY=sk_live_xxx
REVENUECAT_PUBLIC_KEY=pk_live_xxx
REVENUECAT_PROJECT_TOKEN=proj_xxx
REVENUECAT_ENTITLEMENT_ID=pro

# Optional
REVENUECAT_SYNC_INTERVAL_MINUTES=360      # 6 hours
REVENUECAT_CUSTOMER_PORTAL_URL=https://yourdomain.rcat.page
REVENUECAT_WEBHOOK_SECRET=whsec_xxx       # Phase 2 only

# Free tier limits
REVENUECAT_DAILY_UPLOAD_LIMIT_FREE=5
REVENUECAT_UPLOAD_MAX_SIZE_MB_FREE=10
REVENUECAT_CLIP_DURATION_LIMIT_FREE=60
REVENUECAT_HD_QUALITY_BLOCKED_FREE=True
```

## Daily Operations

### Monitoring Sync Health

```bash
# Check recent sync activity
docker compose logs web --since=1h | grep "sync_entitlements"

# Check users with stale sync (> 12h)
docker compose exec web python manage.py shell -c "
from django.utils import timezone
from datetime import timedelta
from backend.app.models import User
stale = User.objects.filter(pro_last_synced__lt=timezone.now()-timedelta(hours=12))
print(f'Stale syncs: {stale.count()}')
for u in stale[:10]: print(f'  {u.username}: {u.pro_last_synced}')
"
```

### Alerting Rules (Suggested)

| Alert | Condition | Severity |
|---|---|---|
| Sync stale | `pro_last_synced > 12h` for any Pro user | Warning |
| Sync failures | `sync_entitlements` returns error > 3x in row | Critical |
| Secret missing | `REVENUECAT_SECRET_KEY` not set | Critical |
| Pro user count drop | > 10% decrease in 1h | Warning |

## Manual Sync

### For a Single User

```bash
# Via API (rate-limited: 10/hour/user)
curl -X POST https://api.yourdomain.com/subscription/sync/ \
  -H "Authorization: Bearer <access_token>"

# Or via Django shell
docker compose exec web python manage.py shell -c "
from backend.app.tasks import sync_revenuecat_entitlements
from backend.app.models import User
user = User.objects.get(username='prouser')
sync_revenuecat_entitlements.delay(str(user.id))
"
```

### For All Users (Emergency)

```bash
docker compose exec web python manage.py shell -c "
from backend.app.tasks import sync_revenuecat_entitlements
sync_revenuecat_entitlements.delay()
"
```

## Customer Portal

### Getting the URL

```bash
curl -H "Authorization: Bearer <token>" https://api.yourdomain.com/subscription/manage/
# Returns: {"url": "https://rcat.page/p/yourproject?app_user_id=..."}
```

### User Flow

1. User clicks "Manage Subscription" in app
2. Frontend calls `GET /subscription/manage/`
3. Backend returns RevenueCat Customer Portal URL
4. Frontend opens URL in new tab (`window.open(url, '_blank')`)
5. User manages subscription (cancel, update payment, view history)
6. User returns to app → status auto-refreshes (5min interval)

### Custom Portal URL

```bash
# Optional: Custom branded portal
REVENUECAT_CUSTOMER_PORTAL_URL=https://billing.yourdomain.com
```

## Secret Rotation

### RevenueCat Secret Key

1. **Generate new key** in RevenueCat Dashboard → Project Settings → API Keys
2. **Update `.env`** on all backend instances:
   ```bash
   REVENUECAT_SECRET_KEY=sk_live_new_xxx
   ```
3. **Restart web + celery workers**:
   ```bash
   docker compose up -d --force-recreate web celery celery_beat
   ```
4. **Verify sync works**:
   ```bash
   docker compose exec web python manage.py shell -c "
   from backend.app.tasks import sync_revenuecat_entitlements
   sync_revenuecat_entitlements.delay()
   "
   ```

### Public Key

1. **Generate in RevenueCat Dashboard**
2. **Update both `.env` files**:
   - Backend: `REVENUECAT_PUBLIC_KEY=pk_live_xxx`
   - Frontend: `VITE_REVENUECAT_PUBLIC_KEY=pk_live_xxx`
3. **Rebuild frontend** (Vite embeds env at build time):
   ```bash
   cd frontend/sample_frontend && npm run build
   ```
4. **Deploy frontend**

### Webhook Secret (Phase 2)

```bash
# Get from RevenueCat Dashboard → Project Settings → Webhooks
REVENUECAT_WEBHOOK_SECRET=whsec_new_xxx

# Restart web
docker compose up -d --force-recreate web
```

## Troubleshooting

### Sync Not Working

```bash
# 1. Check secret key
docker compose exec web python -c "import os; print(os.getenv('REVENUECAT_SECRET_KEY')[:10] + '...')"

# 2. Test API connectivity
docker compose exec web python -c "
import requests
import os
url = f'https://api.revenuecat.com/v1/subscribers/test'
headers = {'Authorization': f'Bearer {os.getenv(\"REVENUECAT_SECRET_KEY\")}'}
r = requests.get(url, headers=headers, timeout=10)
print(r.status_code, r.text[:200])
"

# 3. Check Celery Beat is running
docker compose logs celery_beat --tail=20 | grep "sync-revenuecat"
```

### Users Stuck in Wrong State

```bash
# Force re-sync for specific user
docker compose exec web python manage.py shell -c "
from backend.app.models import User
from backend.app.tasks import sync_revenuecat_entitlements
user = User.objects.get(username='problem_user')
sync_revenuecat_entitlements.delay(str(user.id))
print(f'Triggered sync for {user.username}')
"

# Check user's Pro state
docker compose exec web python manage.py shell -c "
from backend.app.models import User
u = User.objects.get(username='problem_user')
print(f'is_pro: {u.is_pro()}')
print(f'has_pro: {u.has_pro_entitlement}')
print(f'expires: {u.pro_expires_at}')
print(f'grace: {u.pro_grace_until}')
print(f'last_synced: {u.pro_last_synced}')
"
```

### RevenueCat API Errors

| Error | Cause | Fix |
|---|---|---|
| 401 Unauthorized | Invalid secret key | Rotate secret key |
| 404 Not Found | Subscriber doesn't exist | User never purchased; normal for new users |
| 429 Too Many Requests | Rate limit hit | Increase sync interval, add backoff |
| 5xx Server Error | RevenueCat outage | Wait, retry on next beat |

## Scaling Considerations

### Polling Load

| Users | Syncs/beat (est.) | API calls/hour |
|---|---|---|
| 1,000 | ~100 (10% expiring) | 1,000 |
| 10,000 | ~1,000 | 10,000 |
| 100,000 | ~10,000 | 100,000 |

**Optimization for >10k users**: Switch to webhooks (Phase 2) or increase interval.

### Database Indexes

```sql
-- Already exists from migration
CREATE INDEX idx_user_pro_expires ON app_user (pro_expires_at);
CREATE INDEX idx_user_last_synced ON app_user (pro_last_synced);
```

## Compliance & Auditing

### Data Retention

| Data | Retention | Basis |
|---|---|---|
| Sync logs | 90 days | Operational |
| Pro status changes | 7 years | Financial audit |
| RevenueCat webhook payloads | 90 days | Debugging |

### Audit Trail

The `pro_last_synced` field + Celery task logs provide audit trail for Pro status changes.

## Backup & Recovery

### RevenueCat Data

RevenueCat is the source of truth for subscription state. Local DB is a cache.

**Recovery procedure:**
1. Restore Django DB from backup
2. Run full sync: `sync_revenuecat_entitlements.delay()` (no user_id = all users)
3. Verify Pro status matches RevenueCat

### Point-in-Time Recovery

```bash
# If Pro state corrupted:
docker compose exec web python manage.py shell -c "
from backend.app.models import User
User.objects.all().update(has_pro_entitlement=False, pro_expires_at=None, pro_grace_until=None)
# Then trigger full sync
from backend.app.tasks import sync_revenuecat_entitlements
sync_revenuecat_entitlements.delay()
"
```

## Related Documentation

- [Architecture](01-architecture.md) — System design
- [Gating Strategy](02-gating-strategy.md) — Free vs Pro limits
- [Sync Strategy](03-sync-strategy.md) — Polling design
- [Webhooks](04-webhooks.md) — Phase 2 webhook design
- [Frontend](05-frontend.md) — SDK integration
- [Testing](06-testing.md) — Test coverage