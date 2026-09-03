"""Service-layer tests for backend.app.services.shares.

send_share must atomically:
  1. Bump AudioClip.shares (via UserInteraction(type='share') save())
  2. Create a ShareEvent row in the recipient's inbox
"""
import pytest

from backend.app.models import ShareEvent
from backend.app.services import shares as shares_svc


pytestmark = pytest.mark.django_db


class TestSendShare:
    def test_creates_shareevent_and_bumps_counter(self, user, other_user, ready_clip):
        shares_svc.send_share(sender=user, clip=ready_clip, receiver=other_user)

        assert ShareEvent.objects.filter(
            sender=user, receiver=other_user, clip=ready_clip,
        ).exists()
        ready_clip.refresh_from_db()
        assert ready_clip.shares == 1

    def test_two_shares_create_two_inbox_events(self, user, other_user, ready_clip):
        shares_svc.send_share(sender=user, clip=ready_clip, receiver=other_user)
        shares_svc.send_share(sender=user, clip=ready_clip, receiver=other_user)
        assert ShareEvent.objects.filter(
            sender=user, receiver=other_user, clip=ready_clip,
        ).count() == 2

    def test_inbox_unread_count(self, user, other_user, ready_clip):
        shares_svc.send_share(sender=user, clip=ready_clip, receiver=other_user)
        assert ShareEvent.objects.filter(receiver=other_user, is_read=False).count() == 1
