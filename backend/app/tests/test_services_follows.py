"""Service-layer tests for backend.app.services.follows."""
import pytest

from backend.app.services import follows as follows_svc


pytestmark = pytest.mark.django_db


class TestToggleFollow:
    def test_first_call_follows(self, user, other_user):
        result = follows_svc.toggle_follow(actor=user, target=other_user)
        assert result == 'followed'
        assert user.following.filter(pk=other_user.pk).exists()

    def test_second_call_unfollows(self, user, other_user):
        follows_svc.toggle_follow(actor=user, target=other_user)
        result = follows_svc.toggle_follow(actor=user, target=other_user)
        assert result == 'unfollowed'
        assert not user.following.filter(pk=other_user.pk).exists()

    def test_third_call_re_follows(self, user, other_user):
        follows_svc.toggle_follow(actor=user, target=other_user)
        follows_svc.toggle_follow(actor=user, target=other_user)
        result = follows_svc.toggle_follow(actor=user, target=other_user)
        assert result == 'followed'

    def test_does_not_create_symmetrical_follow_back(self, user, other_user):
        follows_svc.toggle_follow(actor=user, target=other_user)
        # M2M is symmetrical=False on the model.
        assert not other_user.following.filter(pk=user.pk).exists()
