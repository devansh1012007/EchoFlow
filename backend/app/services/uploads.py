"""Upload service layer.

Stage 2 boundary. The only call here today is `finalize_upload`, which
moves the `transaction.on_commit(process_audio_to_hls.delay(...))` line
out of the view so the view does not own Celery dispatch.

When P1.3 (presigned PUT) lands, this module grows a `get_signed_put_url`
function and the view becomes a thin wrapper.
"""
from __future__ import annotations

from django.db import transaction

from ..models import AudioClip
from ..tasks import process_audio_to_hls
from .task_publisher import publish


def finalize_upload(clip: AudioClip) -> None:
    """Schedule HLS processing for `clip` after the current transaction commits.

    transaction.on_commit guarantees the worker only picks up the task
    if the row actually persisted. If the surrounding transaction rolls
    back, no orphaned task is enqueued.
    """
    # Group B item 11: route through publish() so the correlation_id
    # from the upload request reaches the worker. Without this, the
    # worker log line for this task has correlation_id='-'.
    transaction.on_commit(lambda: publish(process_audio_to_hls, str(clip.id)))
