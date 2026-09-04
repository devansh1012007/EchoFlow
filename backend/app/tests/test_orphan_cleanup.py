"""Tests for the cleanup_orphan_hls periodic Celery task (Group B item 12).

Architectural contract:
- Scan hls/ prefix for UUID-shaped subdirectories.
- Diff against AudioClip.objects.values_list('id', flat=True).
- Delete the orphan prefixes (defense-in-depth for post_delete signal
  failures).
- Bounded to max_keys per run.
- Idempotent: re-running is a no-op.

These tests use Django 5.x's InMemoryStorage as a fake S3 backend.
The real production storage is boto3-backed (S3Storage) with
listdir() that returns the same shape, so the contract is preserved.
"""
import uuid
from unittest.mock import patch

import pytest
from django.core.files.storage import InMemoryStorage


pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_storage(settings):
    """Replace default_storage with an InMemoryStorage for the test.

    Yields the storage instance so tests can pre-populate it.
    """
    storage = InMemoryStorage()
    with patch('backend.app.tasks.default_storage', storage):
        yield storage


def _hls_key_for(clip_id):
    return f'hls/{clip_id}/master.m3u8'


class TestCleanupOrphanHls:
    def test_no_op_when_no_orphans(self, fake_storage, ready_clip):
        # Real clip exists; populate its HLS files
        fake_storage.save(_hls_key_for(ready_clip.id), __import__('io').BytesIO(b'fake'))
        from backend.app.tasks import cleanup_orphan_hls

        result = cleanup_orphan_hls.run()
        assert result == {'scanned': 1, 'deleted': 0}
        # Real clip's files untouched
        assert fake_storage.exists(_hls_key_for(ready_clip.id)) is True

    def test_finds_orphans_and_deletes_them(self, fake_storage, ready_clip):
        # One real clip + one orphan
        real_key = _hls_key_for(ready_clip.id)
        orphan_id = uuid.uuid4()
        orphan_key = _hls_key_for(orphan_id)
        fake_storage.save(real_key, __import__('io').BytesIO(b'real'))
        fake_storage.save(orphan_key, __import__('io').BytesIO(b'orphan'))

        from backend.app.tasks import cleanup_orphan_hls
        result = cleanup_orphan_hls.run()

        assert result == {'scanned': 2, 'deleted': 1}
        # Real clip's HLS untouched
        assert fake_storage.exists(real_key) is True
        # Orphan's HLS deleted
        assert fake_storage.exists(orphan_key) is False

    def test_idempotent(self, fake_storage, ready_clip):
        from backend.app.tasks import cleanup_orphan_hls

        orphan_id = uuid.uuid4()
        fake_storage.save(_hls_key_for(orphan_id), __import__('io').BytesIO(b'orphan'))

        first = cleanup_orphan_hls.run()
        second = cleanup_orphan_hls.run()
        assert first['deleted'] == 1
        # Second run: the orphan dir is now empty (file deleted, but
        # the dir node persists in InMemoryStorage). listdir on the
        # empty prefix returns no files, so _delete_prefix returns
        # False and deleted=0.
        assert second['deleted'] == 0

    def test_bounded_to_max_keys(self, fake_storage, ready_clip):
        # 1500 orphans + 1 real clip
        for _ in range(1500):
            fake_storage.save(
                _hls_key_for(uuid.uuid4()),
                __import__('io').BytesIO(b'x'),
            )
        # Real clip with a real ID
        fake_storage.save(
            _hls_key_for(ready_clip.id),
            __import__('io').BytesIO(b'real'),
        )

        from backend.app.tasks import cleanup_orphan_hls
        result = cleanup_orphan_hls.run(max_keys=1000)

        # 1000 candidates examined (the real one is in there too but
        # not in the orphan set since it has a real DB row). All 1000
        # are orphans.
        assert result['scanned'] == 1000
        assert result['deleted'] == 1000
        # Real clip untouched
        assert fake_storage.exists(_hls_key_for(ready_clip.id)) is True

    def test_ignores_non_uuid_subdirs(self, fake_storage, ready_clip):
        # A stray non-UUID subdir under hls/ — defensive: don't delete
        # arbitrary directories just because they're not in the DB.
        fake_storage.save('hls/not-a-uuid/garbage', __import__('io').BytesIO(b'x'))

        from backend.app.tasks import cleanup_orphan_hls
        result = cleanup_orphan_hls.run()

        # Non-UUID dirs are skipped at the regex filter
        assert result == {'scanned': 0, 'deleted': 0}
        # Stray file still there
        assert fake_storage.exists('hls/not-a-uuid/garbage') is True

    def test_handles_empty_hls_prefix(self, fake_storage, ready_clip):
        from backend.app.tasks import cleanup_orphan_hls
        result = cleanup_orphan_hls.run()
        assert result == {'scanned': 0, 'deleted': 0}

    def test_increments_metric(self, fake_storage, ready_clip):
        from backend.app import metrics
        before = metrics.orphan_hls_cleaned_total._value.get()
        orphan_id = uuid.uuid4()
        fake_storage.save(_hls_key_for(orphan_id), __import__('io').BytesIO(b'x'))

        from backend.app.tasks import cleanup_orphan_hls
        cleanup_orphan_hls.run()

        after = metrics.orphan_hls_cleaned_total._value.get()
        assert after - before == 1


class TestCleanupOrphanHlsBeatSchedule:
    """Static check that the beat schedule registers the task."""

    def test_beat_schedule_contains_cleanup_orphan_hls(self, settings):
        from django.conf import settings as dj_settings
        schedule = dj_settings.CELERY_BEAT_SCHEDULE
        assert 'cleanup-orphan-hls' in schedule
        entry = schedule['cleanup-orphan-hls']
        assert entry['task'] == 'backend.app.tasks.cleanup_orphan_hls'
        # Schedule is a crontab instance (daily at 03:00 UTC); just
        # assert it has a non-empty representation.
        assert str(entry['schedule'])
