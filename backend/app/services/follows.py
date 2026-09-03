"""Follow service layer.

Stage 2 boundary. The pre-refactor follow toggle lived in the view;
the relational edge mutation (User.following M2M) is the entire
contract, so this is a thin delegate.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model

User = get_user_model()


def toggle_follow(actor, target) -> str:
    """Toggle actor.following membership of target. Returns 'followed' or 'unfollowed'."""
    if actor.following.filter(pk=target.pk).exists():
        actor.following.remove(target)
        return 'unfollowed'
    actor.following.add(target)
    return 'followed'
