"""Service-layer tests for backend.app.services.uploads.

finalize_upload only enqueues the HLS-processing task on
transaction.on_commit. With CELERY_TASK_ALWAYS_EAGER=True in tests
(no broker available), the task runs synchronously when committed.
"""
import pytest


pytestmark = pytest.mark.django_db


class TestFinalizeUpload:
    def test_enqueues_process_audio_to_hls_on_commit(self, ready_clip, settings, monkeypatch):
        from django.test import TestCase
        from backend.app.services import uploads as uploads_svc

        # In EAGER mode Celery dispatches by task name, so we patch the
        # shared_task object itself rather than the Python import in
        # uploads.py. .delay() on the shared_task is what on_commit fires.
        from backend.app import tasks as app_tasks
        monkeypatch.setattr(app_tasks.process_audio_to_hls, 'delay',
                            lambda clip_id: app_tasks._eager_calls.append(clip_id))
        app_tasks._eager_calls = []

        with TestCase.captureOnCommitCallbacks(execute=True):
            uploads_svc.finalize_upload(ready_clip)

        assert app_tasks._eager_calls == [ready_clip.id]

    def test_no_task_enqueued_if_transaction_rolls_back(self, ready_clip, settings, monkeypatch):
        from django.db import IntegrityError
        from django.test import TestCase
        from backend.app.services import uploads as uploads_svc
        from backend.app import tasks as app_tasks

        from django.db import transaction
        monkeypatch.setattr(app_tasks.process_audio_to_hls, 'delay',
                            lambda clip_id: app_tasks._eager_calls.append(clip_id))
        app_tasks._eager_calls = []

        with TestCase.captureOnCommitCallbacks(execute=True):
            with pytest.raises(IntegrityError):
                with transaction.atomic():
                    uploads_svc.finalize_upload(ready_clip)
                    raise IntegrityError('simulated rollback')

        assert app_tasks._eager_calls == []
