"""Comment service layer.

Stage 2 boundary. Preserves the existing counter-side-effect semantics:
the AudioClip.comment_count F() bump on save()/delete() of a TOP-LEVEL
comment (parent_id IS NULL) lives in Comment.save()/delete() in the
model, not here — the service simply calls those methods.

This module is the single place to add business rules (rate, profanity
filter, thread depth cap) without touching the ViewSet.
"""
from __future__ import annotations

from django.db import transaction

from ..models import AudioClip, Comment


def create_comment(user, clip: AudioClip, text: str, parent: Comment | None = None) -> Comment:
    """Create a comment. Reply vs top-level semantics are enforced in the model.

    Reply (parent is not None): does NOT increment AudioClip.comment_count.
    Top-level (parent is None): the model's save() bumps the counter via F().
    """
    return Comment.objects.create(author=user, clip=clip, text=text, parent=parent)


def update_comment(comment: Comment, text: str) -> Comment:
    comment.text = text
    comment.save(update_fields=['text'])
    return comment


@transaction.atomic
def delete_comment(comment: Comment) -> None:
    """Delete a comment. The model's delete() decrements the parent's counter
    if this was a top-level comment."""
    comment.delete()
