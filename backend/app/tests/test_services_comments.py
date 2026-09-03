"""Service-layer tests for backend.app.services.comments.

Stage 2 boundary: comments route through the service so the view stays
a thin controller. These tests verify the documented quirk that
replies do NOT bump AudioClip.comment_count (only top-level comments do).
"""
import pytest

from backend.app.models import Comment
from backend.app.services import comments as comments_svc


pytestmark = pytest.mark.django_db


class TestCreateComment:
    def test_top_level_comment_bumps_counter(self, user, ready_clip):
        ready_clip.refresh_from_db()
        assert ready_clip.comment_count == 0

        comments_svc.create_comment(user, ready_clip, text='hello')

        ready_clip.refresh_from_db()
        assert ready_clip.comment_count == 1

    def test_reply_does_not_bump_counter(self, user, ready_clip):
        parent = comments_svc.create_comment(user, ready_clip, text='parent')
        ready_clip.refresh_from_db()
        assert ready_clip.comment_count == 1

        comments_svc.create_comment(user, ready_clip, text='reply', parent=parent)

        ready_clip.refresh_from_db()
        # counter still 1; only the parent counted.
        assert ready_clip.comment_count == 1

    def test_created_comment_has_author(self, user, ready_clip):
        comment = comments_svc.create_comment(user, ready_clip, text='hi')
        assert comment.author_id == user.id
        assert comment.author == user


class TestUpdateComment:
    def test_updates_text(self, user, ready_clip):
        comment = comments_svc.create_comment(user, ready_clip, text='original')
        comments_svc.update_comment(comment, text='updated')
        comment.refresh_from_db()
        assert comment.text == 'updated'

    def test_does_not_bump_counter_on_update(self, user, ready_clip):
        comment = comments_svc.create_comment(user, ready_clip, text='original')
        ready_clip.refresh_from_db()
        before = ready_clip.comment_count

        comments_svc.update_comment(comment, text='updated')
        ready_clip.refresh_from_db()
        assert ready_clip.comment_count == before


class TestDeleteComment:
    def test_top_level_delete_decrements_counter(self, user, ready_clip):
        comments_svc.create_comment(user, ready_clip, text='one')
        comments_svc.create_comment(user, ready_clip, text='two')
        ready_clip.refresh_from_db()
        assert ready_clip.comment_count == 2

        comment = Comment.objects.filter(clip=ready_clip, text='one').first()
        comments_svc.delete_comment(comment)
        ready_clip.refresh_from_db()
        assert ready_clip.comment_count == 1

    def test_reply_delete_does_not_change_counter(self, user, ready_clip):
        parent = comments_svc.create_comment(user, ready_clip, text='parent')
        reply = comments_svc.create_comment(user, ready_clip, text='reply', parent=parent)
        ready_clip.refresh_from_db()
        before = ready_clip.comment_count

        comments_svc.delete_comment(reply)
        ready_clip.refresh_from_db()
        assert ready_clip.comment_count == before
