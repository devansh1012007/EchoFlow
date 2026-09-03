"""Smoke test: just verify pytest-django is wired correctly."""
import pytest


def test_django_setup():
    from django.conf import settings
    assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
