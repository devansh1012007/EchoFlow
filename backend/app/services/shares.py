"""Share service layer.

Stage 2 boundary. A share has two side-effects today:
  1. Increment AudioClip.shares via UserInteraction(type='share') save().
  2. Insert a ShareEvent row in the recipient's inbox.

Both must happen together; the ViewSet no longer has to know that.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction

from ..models import AudioClip, ShareEvent

from .interactions import record_share

User = get_user_model()


@transaction.atomic
def send_share(sender, clip: AudioClip, receiver) -> ShareEvent:
    """Send a share: bump the share counter and create the inbox event."""
    record_share(sender, clip)
    return ShareEvent.objects.create(sender=sender, receiver=receiver, clip=clip)
