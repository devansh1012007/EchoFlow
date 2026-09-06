"""ViewSets, organized by domain.

This package replaces the monolithic views.py. Each module owns one
ViewSet or a small group of related ViewSets:

  - auth.py        : RegisterView
  - content.py     : AudioUploadViewSet
  - feed.py        : FastFeedViewSet, SuggestionViewSet, TagsViewSet
  - interactions.py: ClipInteractionViewSet (like, skip, telemetry)
  - social.py      : ShareViewSet, FollowViewSet
  - comments.py    : CommentViewSet
  - profile.py     : ProfileViewSet
  - media.py       : PlaybackTokenView
  - subscription.py: SubscriptionStatusView, SubscriptionSyncView,
                     SubscriptionManageView, RevenueCatWebhookView
  - _pagination.py : shared CursorPagination subclasses

The original views.py was 886 lines and 12 classes; after the split
each module is 30-300 lines and the import surface is identical
to callers thanks to the re-exports below.
"""
from .auth import RegisterView
from .comments import CommentViewSet
from .content import AudioUploadViewSet
from .feed import FastFeedViewSet, SuggestionViewSet, TagsViewSet
from .interactions import ClipInteractionViewSet
from .profile import ProfileViewSet
from .social import FollowViewSet, ShareViewSet
from .media import PlaybackTokenView
from .subscription import (
    SubscriptionStatusView,
    SubscriptionSyncView,
    SubscriptionManageView,
    RevenueCatWebhookView,
)

__all__ = [
    'AudioUploadViewSet',
    'FastFeedViewSet',
    'ClipInteractionViewSet',
    'ShareViewSet',
    'CommentViewSet',
    'FollowViewSet',
    'TagsViewSet',
    'SuggestionViewSet',
    'RegisterView',
    'ProfileViewSet',
    'PlaybackTokenView',
    'SubscriptionStatusView',
    'SubscriptionSyncView',
    'SubscriptionManageView',
    'RevenueCatWebhookView',
    'ComplianceContactView',
    'GrievanceCreateView',
    'DataSubjectAccessView',
    'DataSubjectErasureView',
    'TakedownRequestView',
]

from .legal import ComplianceContactView, TakedownRequestView
from .grievance import GrievanceCreateView
from .data_subject import DataSubjectAccessView, DataSubjectErasureView
