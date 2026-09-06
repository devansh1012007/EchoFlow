# RevenueCat Integration — Webhooks

## Phase 1: Polling Only (Current)

**Webhooks are NOT used in Phase 1.** The free RevenueCat plan does not support webhooks. All syncing is done via REST API polling every 6 hours.

The webhook endpoint exists at `/webhooks/revenuecat/` but:
- Returns 200 OK for any POST
- Logs the payload (event type, signature length)
- Does not process events

## Phase 2: Webhook Support (Future)

When upgrading to RevenueCat Pro plan, webhooks will be enabled.

### Webhook Configuration

1. **Set secret in `.env`:**
   ```bash
   REVENUECAT_WEBHOOK_SECRET=your-webhook-secret-from-revenuecat
   ```

2. **Configure in RevenueCat Dashboard:**
   - Navigate to Project Settings → Webhooks
   - Add webhook URL: `https://api.yourdomain.com/webhooks/revenuecat/`
   - Select events: `ENTITLEMENT_ACTIVE`, `ENTITLEMENT_EXPIRED`, `ENTITLEMENT_RENEWED`, `ENTITLEMENT_GRANTED`, `ENTITLEMENT_REVOKED`, `SUBSCRIPTION_CANCELLED`, `SUBSCRIPTION_UNCANCELLED`, `SUBSCRIPTION_PAUSED`, `SUBSCRIPTION_UNPAUSED`, `BILLING_ISSUE`, `BILLING_ISSUE_RESOLVED`, `GRACE_PERIOD_STARTED`, `GRACE_PERIOD_ENDED`
   - Save → RevenueCat will show the signing secret

3. **Verify in logs:**
   ```
   INFO: RevenueCat webhook received: type=ENTITLEMENT_ACTIVE, sig_len=64
   ```

### HMAC-SHA256 Verification

```python
# backend/app/views/subscription.py — RevenueCatWebhookView.post()
def post(self, request):
    signature = request.META.get("HTTP_X_REVENUECAT_SIGNATURE", "")
    event_type = request.data.get("event_type", "unknown")
    
    # Log for debugging
    logger.info(f"RevenueCat webhook: type={event_type}, sig_len={len(signature)}")
    
    # Verify HMAC if secret configured
    if settings.REVENUECAT_WEBHOOK_SECRET and signature:
        secret = settings.REVENUECAT_WEBHOOK_SECRET
        body = request.body
        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return Response({"detail": "Invalid signature"}, status=401)
    
    # Process event (Phase 2 implementation)
    # sync_entitlements(user) based on event_type + app_user_id
    
    return Response({"status": "received"})
```

### Signature Details

- **Header**: `X-RevenueCat-Signature`
- **Algorithm**: HMAC-SHA256
- **Input**: Raw request body (bytes)
- **Secret**: `REVENUECAT_WEBHOOK_SECRET` (from RevenueCat dashboard)
- **Comparison**: Constant-time `hmac.compare_digest()`

### Event Processing (Phase 2)

When webhooks are enabled, the endpoint will:

```python
def process_webhook_event(event_type: str, app_user_id: str):
    """Process webhook event by triggering immediate sync for affected user."""
    try:
        user = User.objects.get(revenuecat_app_user_id=app_user_id)
    except User.DoesNotExist:
        logger.warning(f"Webhook: user not found for app_user_id={app_user_id}")
        return
    
    # Trigger immediate sync for this user
    sync_revenuecat_entitlements.delay(str(user.id))
```

### Events to Handle

| Event Type | Action |
|---|---|
| `ENTITLEMENT_ACTIVE` | Sync — user gained Pro |
| `ENTITLEMENT_EXPIRED` | Sync — user lost Pro |
| `ENTITLEMENT_RENEWED` | Sync — subscription renewed |
| `ENTITLEMENT_GRANTED` | Sync — manual grant |
| `ENTITLEMENT_REVOKED` | Sync — manual revoke |
| `SUBSCRIPTION_CANCELLED` | Sync — subscription cancelled (may still be in grace) |
| `SUBSCRIPTION_UNCANCELLED` | Sync — cancellation reversed |
| `SUBSCRIPTION_PAUSED` | Sync — subscription paused |
| `SUBSCRIPTION_UNPAUSED` | Sync — pause ended |
| `BILLING_ISSUE` | Sync — payment failed (may enter grace) |
| `BILLING_ISSUE_RESOLVED` | Sync — payment recovered |
| `GRACE_PERIOD_STARTED` | Sync — entered grace period |
| `GRACE_PERIOD_ENDED` | Sync — grace period ended |

### Security

| Aspect | Implementation |
|---|---|
| Signature verification | HMAC-SHA256, constant-time compare |
| Replay protection | Not needed — idempotent sync |
| Rate limiting | Not applied (webhooks are server-to-server) |
| IP allowlist | Optional — RevenueCat IPs can be allowlisted |

### Testing Webhooks

```bash
# Local testing with ngrok
ngrok http 8000
# Configure webhook URL in RevenueCat to ngrok URL

# Or use RevenueCat's "Test Webhook" button in dashboard
```

### Monitoring

```bash
# Check webhook logs
docker compose logs web | grep "RevenueCat webhook"

# Alert on 401 responses (signature verification failures)
```

### Rollback Plan

If webhook issues arise:
1. Remove `REVENUECAT_WEBHOOK_SECRET` from `.env`
2. Webhook endpoint reverts to Phase 1 behavior (logs only, returns 200)
3. Polling continues to work as before