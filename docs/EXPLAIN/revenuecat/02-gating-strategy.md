# RevenueCat Integration — Gating Strategy

## Design Decision: Usage-Limit-Based Gating (Option A)

We chose **usage-limit-based gating** over feature-flag-based gating for the Pro tier.

### Rationale

| Approach | Pros | Cons | Decision |
|---|---|---|---|
| **Usage limits (Option A)** | Simple, no feature flags, natural monetization, easy to explain | Requires enforcement at multiple layers | ✅ **Chosen** |
| Feature flags (Option B) | Fine-grained control | Complex state management, "feature creep" risk | Rejected |
| Hybrid (Option C) | Best of both | Over-engineering for v1 | Rejected |

**Trade-off accepted**: Multiple enforcement points (view, serializer, task) vs. single source of truth. The enforcement points are documented and testable.

## Free vs Pro Limits

| Feature | Free Tier | Pro Tier | Enforcement Point |
|---|---|---|---|
| **Daily uploads** | 5 clips | Unlimited | `AudioUploadViewSet.create()` |
| **Max clip duration** | 60 seconds | 300 seconds (`MAX_DURATION_SECONDS`) | `process_audio_to_hls` task |
| **Upload file size** | 10 MB | 100 MB | `AudioUploadSerializer.validate()` |
| **HD quality (48kHz+)** | Blocked | Allowed | `process_audio_to_hls` + Feed views |
| **Audio quality** | 128 kbps | 320 kbps | `process_audio_to_hls` task |

All limits are configurable via environment variables (see [Environment Variables](../AGENTS.md#environment-variables-required)).

## Enforcement Point Details

### 1. Daily Upload Count — View Layer (`AudioUploadViewSet.create()`)

```python
# Checked BEFORE serializer validation — fail fast, no wasted work
if not request.user.is_pro():
    daily_limit = settings.REVENUECAT_DAILY_UPLOAD_LIMIT_FREE  # default 5
    today = timezone.now().date()
    created_today = AudioClip.objects.filter(
        creator=request.user, created_at__date=today
    ).count()
    if created_today >= daily_limit:
        raise PermissionDenied(
            f"Free tier limit of {daily_limit} daily uploads reached. "
            "Upgrade to Pro for unlimited uploads."
        )
```

**Why here?** Earliest possible check — rejects over-limit uploads before any file processing or serializer work.

### 2. File Size Limit — Serializer Layer (`AudioUploadSerializer.validate()`)

```python
def _enforce_free_limits(self, user, original_file):
    max_mb = getattr(django_settings, "REVENUECAT_UPLOAD_MAX_SIZE_MB_FREE", 10)
    max_size = max_mb * 1024 * 1024
    if original_file and original_file.size > max_size:
        raise serializers.ValidationError(
            {"original_file": f"Free tier upload limit is {max_mb}MB. Upgrade to Pro for unlimited uploads."}
        )
```

**Why here?** Second line of defense — catches large uploads that bypassed view check (e.g., direct API calls).

### 3. Duration & Quality Limits — Task Layer (`process_audio_to_hls`)

```python
# Duration check (applies to all, but free tier has lower MAX_DURATION_SECONDS)
max_seconds = getattr(django_settings, 'MAX_DURATION_SECONDS', 300)
if clip.duration_ms > max_seconds * 1000:
    # Mark as failed or reject
    
# Quality check
if not user.is_pro() and settings.REVENUECAT_HD_QUALITY_BLOCKED_FREE:
    # Force 128kbps instead of 320kbps
```

**Why here?** Duration is only known after audio decoding; quality settings are applied during HLS transcoding.

### 4. HD Quality Filtering — Feed Views

```python
# In feed serialization / query
if not request.user.is_pro() and settings.REVENUECAT_HD_QUALITY_BLOCKED_FREE:
    # Filter out HD clips or downgrade quality in serializer
```

## Configuration

All free-tier limits are configurable via environment variables:

```bash
# Free tier limits (override in .env)
REVENUECAT_DAILY_UPLOAD_LIMIT_FREE=5
REVENUECAT_UPLOAD_MAX_SIZE_MB_FREE=10
REVENUECAT_CLIP_DURATION_LIMIT_FREE=60
REVENUECAT_HD_QUALITY_BLOCKED_FREE=True
```

Pro tier uses the global settings:
- `MAX_DURATION_SECONDS` (default 300) — max clip duration for Pro
- File size effectively unlimited (100MB serializer max)

## Edge Cases Handled

| Scenario | Behavior |
|---|---|
| User upgrades mid-day | Next upload check sees `user.is_pro() = True`, allows unlimited |
| User downgrades mid-day | Next upload check sees `user.is_pro() = False`, enforces limits |
| Free user hits limit at 4:59 PM | Next upload rejected; resets at midnight UTC |
| Free user tries 11MB file | Rejected at serializer with clear error message |
| Free user uploads 70s clip | Rejected at HLS task (duration > 60s) |
| Pro user uploads 301s clip | Rejected (global `MAX_DURATION_SECONDS=300`) |

## Testing Coverage

Backend tests (`test_revenuecat.py::TestFreeTierUploadLimits`):
- `test_free_user_blocked_after_daily_limit` — 5 clips created, 6th rejected (403)
- `test_pro_user_unlimited_uploads` — Pro user bypasses daily count check

Frontend tests:
- `Paywall.tsx` shows upgrade CTA for free users
- `useSubscription` context correctly reflects `isPro` state

## Future Considerations

If moving to feature-flag-based gating:
1. Add `ProFeature` model with feature flags
2. Create middleware/decorator for feature checks
3. Migrate enforcement points to use feature flags
4. Keep usage limits as hard safety net