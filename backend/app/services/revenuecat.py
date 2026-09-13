"""RevenueCat REST API integration service.

Handles entitlement verification via the RevenueCat REST API and updates
the local User model's Pro fields. Uses REST API polling only (no webhooks
in Phase 1).

SECURE: REVENUECAT_SECRET_KEY is read from settings and must NEVER be
exposed to the frontend. The public key (REVENUECAT_PUBLIC_KEY) is the
only RevenueCat credential safe for browser use.
"""
import logging
import time
from datetime import datetime, timezone

import requests
from django.conf import settings
from django.utils import timezone as dt_timezone

logger = logging.getLogger(__name__)

REVENUECAT_API_BASE = "https://api.revenuecat.com/v1"
REQUEST_TIMEOUT = 15  # seconds


def _headers():
    return {
        "Authorization": f"Bearer {settings.REVENUECAT_SECRET_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def get_subscriber_info(app_user_id: str) -> dict | None:
    """Fetch subscriber info from RevenueCat for a given app_user_id.

    Returns the `subscriber` dict from the response, or None if the
    subscriber doesn't exist or the request fails.
    """
    url = f"{REVENUECAT_API_BASE}/subscribers/{app_user_id}"
    try:
        resp = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            logger.info("RevenueCat subscriber %s not found", app_user_id)
            return None
        resp.raise_for_status()
        return resp.json().get("subscriber")
    except requests.RequestException as exc:
        logger.warning("RevenueCat API error for %s: %s", app_user_id, exc)
        return None


def _parse_date_ms(date_ms: str | int | None) -> datetime | None:
    """Parse a RevenueCat date-ms string/int into a timezone-aware datetime."""
    if not date_ms:
        return None
    try:
        ms = int(date_ms)
    except (ValueError, TypeError):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _is_active_entitlement(subscriber: dict):
    """Determine entitlement status and dates from subscriber info.

    Returns (is_active, expires_at, grace_until).
    """
    if not subscriber:
        return False, None, None

    entitlement_id = getattr(settings, "REVENUECAT_ENTITLEMENT_ID", "pro")
    entitlements = subscriber.get("entitlements", {})

    for _, ent_data in entitlements.items():
        if isinstance(ent_data, dict) and ent_data.get("product_id") == settings.REVENUECAT_ENTITLEMENT_ID:
            is_active = bool(ent_data.get("is_active", False))
            expires_at = _parse_date_ms(ent_data.get("expires_date_ms")) or _parse_date_ms(ent_data.get("expire_date_ms"))
            grace_until = _parse_date_ms(ent_data.get("grace_period_expire_date_ms"))
            return is_active, expires_at, grace_until

    return False, None, None


def sync_entitlements(user) -> bool:
    """Sync Pro entitlement state from RevenueCat to the Django User.

    Returns True if the user's Pro status changed.
    """
    if not getattr(settings, "REVENUECAT_SECRET_KEY", ""):
        logger.debug("RevenueCat secret key not configured — skipping sync")
        return False

    if not user.revenuecat_app_user_id:
        logger.debug("User %s has no revenuecat_app_user_id — skipping sync", user.id)
        return False

    app_user_id = str(user.revenuecat_app_user_id)
    subscriber = get_subscriber_info(app_user_id)
    is_active, expires_at, grace_until = _is_active_entitlement(subscriber)

    changed = False
    was_active = user.has_pro_entitlement

    if is_active != was_active:
        user.has_pro_entitlement = is_active
        changed = True

    if expires_at != user.pro_expires_at:
        user.pro_expires_at = expires_at
        changed = True

    if grace_until != user.pro_grace_until:
        user.pro_grace_until = grace_until
        changed = True

    if changed:
        user.pro_last_synced = dt_timezone.now()
        user.save(update_fields=[
            "has_pro_entitlement",
            "pro_expires_at",
            "pro_grace_until",
            "pro_last_synced",
        ])
        logger.info(
            "User %s Pro status updated: active=%s, expires=%s, grace=%s",
            user.id, is_active, expires_at, grace_until,
        )

    return changed


def get_customer_portal_url(user) -> str:
    """Return the RevenueCat Customer Portal URL for a user.

    The URL is generated client-side by RevenueCat's SDK; this returns
    a deep-link that the frontend can redirect the user to.
    """
    from ..models import User
    app_user_id = str(user.revenuecat_app_user_id) if user.revenuecat_app_user_id else str(user.uuid)

    base = getattr(settings, "REVENUECAT_CUSTOMER_PORTAL_URL", "")
    if base:
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}app_user_id={app_user_id}"

    return f"https://rcat.page/p/echoflow?app_user_id={app_user_id}"
