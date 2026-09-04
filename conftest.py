"""Pytest fixtures and configuration for the EchoFlow test suite.

DESIGN:
  - Tests run against an in-memory SQLite database (fast, no Docker).
  - Cache uses Django's local-memory backend (no Redis dependency).
  - Each test gets a fresh DB via pytest-django's --create-db / --reuse-db.
  - The User model and AudioClip/Comment/UserInteraction models are exercised.

WHY SQLite:
  - PostgreSQL-only features (pgvector, HNSW indexes, full CheckConstraint
    parsing) are not used by the tests we care about (validation, rate
    limiting, model invariants). The tests that DO need postgres are
    skipped with @pytest.mark.skip_postgres for now.
  - SQLite gives sub-100ms test setup, which is what we want for fast CI.
  - When we add coverage that needs pgvector (vector similarity), those
    tests will be marked and run in the Docker CI lane only.
"""
import os
import sys
from pathlib import Path

# Set required env vars BEFORE django.setup() — settings.py reads them.
os.environ.setdefault('DJANGO_SECRET_KEY', 'test-secret-key-not-for-prod')
os.environ.setdefault('DJANGO_DEBUG', 'True')
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('AWS_STORAGE_BUCKET_NAME', 'test-bucket')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'test')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'test')

# Add the repo root to sys.path so 'backend.EchoFlow.settings' resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import django
from django.conf import settings


def _override_settings_for_tests():
    """Force SQLite + locmem cache for unit tests. NOT called at import
    time — see the `_force_sqlite_for_unit_tests` autouse fixture below for
    the conditional invocation."""
    settings.DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    }
    settings.CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'test-cache',
        }
    }
    # Disable throttling in tests unless the test specifically enables it.
    settings.REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] = []
    # CELERY_TASK_ALWAYS_EAGER: tasks run synchronously in tests
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    # Don't redirect to HTTPS in tests.
    settings.SECURE_SSL_REDIRECT = False
    settings.SECURE_HSTS_SECONDS = 0
    settings.SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    settings.SECURE_HSTS_PRELOAD = False
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False


django.setup()


# In tests, override the app's migrations to skip the pgvector-specific
# 0001_initial.py (which has HNSW indexes and CREATE EXTENSION that
# SQLite cannot parse). We replace the entire app migration set with a
# no-op stub that just marks the app as having no migrations; the test
# DB schema is created from the current models via create_all, which
# is fine because we don't exercise vector fields in these tests.
import sys
import types
TEST_MIGRATIONS_DIR = Path(__file__).resolve().parent / 'backend' / 'app' / 'tests' / 'migrations_test'
fake_migrations = types.ModuleType('backend.app.migrations_test')
fake_migrations.__file__ = str(TEST_MIGRATIONS_DIR / '__init__.py')
sys.modules['backend.app.migrations_test'] = fake_migrations
settings.MIGRATION_MODULES = {'app': 'backend.app.migrations_test'}


# Filter out HnswIndex from the model's _meta.indexes for SQLite tests.
# Even with --no-migrations, Django's create_all uses the model's index
# list. HnswIndex emits Postgres-only SQL (WITH (m=16, ...)) that SQLite
# can't parse. Removing them lets the schema be created.
from backend.app.models import AudioClip as _AudioClip
from pgvector.django import HnswIndex as _HnswIndex
_AudioClip._meta.indexes = [
    idx for idx in _AudioClip._meta.indexes if not isinstance(idx, _HnswIndex)
]


import pytest


@pytest.fixture
def user(django_user_model):
    """A standard active user."""
    return django_user_model.objects.create_user(
        username='alice', email='alice@example.com', password='test-pass-1234'
    )


@pytest.fixture
def other_user(django_user_model):
    """A second user for social tests (follow, share, comment)."""
    return django_user_model.objects.create_user(
        username='bob', email='bob@example.com', password='test-pass-1234'
    )


@pytest.fixture
def api_client():
    """An unauthenticated DRF test client."""
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def auth_client(api_client, user):
    """An authenticated DRF test client (logged in as `user`)."""
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def ready_clip(user):
    """An AudioClip in 'ready' state with valid vectors."""
    from backend.app.models import AudioClip
    return AudioClip.objects.create(
        title='Test Clip',
        category='comedy',
        creator=user,
        status='ready',
        duration_ms=60_000,
        likes=0, shares=0, skips=0, comment_count=0,
        semantic_vector=[0.1] * 384,
        acoustic_vector=[0.1] * 128,
    )


@pytest.fixture(autouse=True)
def _force_sqlite_for_unit_tests(request):
    """Apply the unit-suite overrides for tests that are NOT marked `integration`.

    DECISION: the SQLite + locmem override was previously unconditional
    (called at conftest import time). That broke the integration suite (D25):
    in CI, `pytest -m integration` would inherit `DATABASE_URL=postgresql://...`
    from the job env, but the conftest was still forcing settings.DATABASES
    to SQLite. That both prevented the integration tests from connecting to
    the real Postgres AND triggered the integration skip fixture (which
    checks `settings.DATABASES['default']['ENGINE']`).

    Moving the override into an autouse fixture lets us check
    `request.keywords` for the `integration` marker and skip the override
    for those tests, leaving `settings.DATABASES` / `settings.CACHES` at
    whatever settings.py + the env var constructed (Postgres + Redis in CI).
    """
    if 'integration' in request.keywords:
        return
    _override_settings_for_tests()


@pytest.fixture(autouse=True)
def _skip_integration_without_real_services(request):
    """Skip tests marked `integration` when running without real Postgres + Redis.

    Integration tests exercise pgvector HNSW indexes, Postgres row-level locks,
    real Redis Streams, and S3 semantics — none of which work on SQLite + LocMem.
    The unit suite (default) runs against SQLite + LocMem for speed; the
    integration suite is selected explicitly with `pytest -m integration` and
    runs in CI against the real Postgres + Redis services.

    Two checks: a non-SQLite DATABASE engine AND a non-locmem cache backend.
    Either failing -> skip with an actionable message.

    DECISION: this fixture is autouse but conditional — it only fires for
    tests marked `integration` (via `request.keywords`). The companion
    fixture `_force_sqlite_for_unit_tests` is also conditional on
    `integration` being ABSENT, so the two fixtures do not conflict.
    """
    if 'integration' not in request.keywords:
        return
    db_engine = settings.DATABASES['default']['ENGINE']
    if db_engine == 'django.db.backends.sqlite3':
        pytest.skip("integration tests require a non-SQLite DATABASE_URL (Postgres)")
    cache_backend = settings.CACHES['default']['BACKEND']
    if 'locmem' in cache_backend.lower() or 'local' in cache_backend.lower():
        pytest.skip("integration tests require a real Redis cache backend")


@pytest.fixture
def processing_clip(user):
    """An AudioClip in 'processing' state (for cleanup_stuck_processing tests).

    The cleanup_stuck_processing task uses created_at to detect clips
    older than `threshold_minutes`. We set created_at to 30 min ago so
    the task considers this clip stuck. (AudioClip has auto_now_add=True
    on created_at, so we must use .update() to bypass the auto-set.)
    """
    from backend.app.models import AudioClip
    from django.utils import timezone
    from datetime import timedelta
    old = timezone.now() - timedelta(minutes=30)
    clip = AudioClip.objects.create(
        title='Stuck Clip',
        category='comedy',
        creator=user,
        status='processing',
        duration_ms=60_000,
    )
    # Bypass auto_now_add by writing directly via .update().
    AudioClip.objects.filter(pk=clip.pk).update(created_at=old)
    clip.refresh_from_db()
    return clip
