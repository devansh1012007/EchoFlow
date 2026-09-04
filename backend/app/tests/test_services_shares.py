"""Service-layer tests for backend.app.services.shares.

send_share must atomically:
  1. Bump AudioClip.shares via the Redis INCRBY path
     (UserInteraction(type='share') save() -> counter_store.increment)
  2. Create a ShareEvent row in the recipient's inbox

The flush_counters_to_pg task applies the Redis delta to
AudioClip.shares in a periodic batch. The synchronous path no
longer mutates the AudioClip row.
"""
import pytest

from backend.app.models import ShareEvent
from backend.app.services import shares as shares_svc
from backend.app.services import counter_store


pytestmark = pytest.mark.django_db


class TestSendShare:
    def test_creates_shareevent_and_increments_redis(self, user, other_user, ready_clip):
        counter_store._reset_backend_for_tests()

        shares_svc.send_share(sender=user, clip=ready_clip, receiver=other_user)

        assert ShareEvent.objects.filter(
            sender=user, receiver=other_user, clip=ready_clip,
        ).exists()
        # AudioClip.shares is NOT bumped synchronously anymore;
        # the counter advance lives in Redis until the flusher runs.
        ready_clip.refresh_from_db()
        drained = counter_store.drain()
        assert drained['counters'][str(ready_clip.id)] == {'shares': 1}

    def test_two_shares_create_two_inbox_events(self, user, other_user, ready_clip):
        shares_svc.send_share(sender=user, clip=ready_clip, receiver=other_user)
        shares_svc.send_share(sender=user, clip=ready_clip, receiver=other_user)
        assert ShareEvent.objects.filter(
            sender=user, receiver=other_user, clip=ready_clip,
        ).count() == 2

    def test_inbox_unread_count(self, user, other_user, ready_clip):
        shares_svc.send_share(sender=user, clip=ready_clip, receiver=other_user)
        assert ShareEvent.objects.filter(receiver=other_user, is_read=False).count() == 1

    def test_send_share_invalidates_user_vectors_cache(
        self, user, other_user, ready_clip,
    ):
        # A3 Part 1: send_share is the view-callable path that
        # creates both the UserInteraction (counter) and the
        # ShareEvent (inbox). It must invalidate the sender's
        # cached user_vectors via the @transaction.atomic + on_commit
        # deferral. The cache key is cleared only after the outer
        # atomic commits.
        from django.core.cache import cache
        from django.test import TestCase

        cache_key = f'user_vectors:{user.id}'
        cache.set(cache_key, ('sem-stale', 'ac-stale'), timeout=900)
        assert cache.get(cache_key) is not None

        with TestCase.captureOnCommitCallbacks(execute=True):
            shares_svc.send_share(sender=user, clip=ready_clip, receiver=other_user)

        assert cache.get(cache_key) is None
