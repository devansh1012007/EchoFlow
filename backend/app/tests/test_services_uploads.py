"""Service-layer tests for backend.app.services.uploads.

finalize_upload only enqueues the HLS-processing task on
transaction.on_commit. With CELERY_TASK_ALWAYS_EAGER=True in tests
(no broker available), the task runs synchronously when committed.

Group B item 11 (correlation_id propagation): the enqueue path is
now `publish(process_audio_to_hls, str(clip.id))` instead of
`process_audio_to_hls.delay(clip.id)`. The contract tested here is
still "uploads service enqueues the HLS task"; we patch publish()
to record the call.
"""
import pytest


pytestmark = pytest.mark.django_db


class TestFinalizeUpload:
    def test_enqueues_process_audio_to_hls_on_commit(self, ready_clip, settings, monkeypatch):
        from django.test import TestCase
        from backend.app.services import uploads as uploads_svc

        # Patch the publisher BEFORE the view/service code runs. The
        # patch is on the module attribute that uploads_svc.finalize_upload
        # looks up at call time (it imports `from .task_publisher import publish`
        # so the binding is the local name; we monkeypatch the module
        # so the local import resolves to our mock).
        from backend.app.services import task_publisher
        recorded = []
        monkeypatch.setattr(
            task_publisher, 'publish',
            lambda task, *args, **kwargs: recorded.append((getattr(task, 'name', str(task)), args, kwargs)),
        )
        # uploads_svc already did `from .task_publisher import publish`;
        # rebind the local name to the patched function.
        monkeypatch.setattr(uploads_svc, 'publish', task_publisher.publish)

        with TestCase.captureOnCommitCallbacks(execute=True):
            uploads_svc.finalize_upload(ready_clip)

        assert len(recorded) == 1
        task_name, args, kwargs = recorded[0]
        assert task_name == 'backend.app.tasks.process_audio_to_hls'
        assert args == (str(ready_clip.id),)

    def test_no_task_enqueued_if_transaction_rolls_back(self, ready_clip, settings, monkeypatch):
        from django.db import IntegrityError
        from django.test import TestCase
        from backend.app.services import uploads as uploads_svc
        from backend.app.services import task_publisher

        from django.db import transaction
        recorded = []
        monkeypatch.setattr(
            task_publisher, 'publish',
            lambda task, *args, **kwargs: recorded.append((getattr(task, 'name', str(task)), args, kwargs)),
        )
        monkeypatch.setattr(uploads_svc, 'publish', task_publisher.publish)

        with TestCase.captureOnCommitCallbacks(execute=True):
            with pytest.raises(IntegrityError):
                with transaction.atomic():
                    uploads_svc.finalize_upload(ready_clip)
                    raise IntegrityError('simulated rollback')

        assert recorded == []
