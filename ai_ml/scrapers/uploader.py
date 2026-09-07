import os
import uuid
import datetime
import logging
from django.core.files import File as DjangoFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


def save_clip(user, title, source_name, source_url, license, attribution_text, local_file_path, original_source_id=None, category=None):
    """Save a normalized audio file into Django's media storage and create an AudioClip.

    Returns the created AudioClip instance.
    """
    # Local import to avoid import-time side effects
    # DECISION: Use the absolute `backend.app.models` import path. The
    # previous `from ..models import AudioClip` was a relative import left
    # over from when the scraper lived at `backend/app/scrapers/uploader.py`.
    # After the move to `ai_ml/scrapers/uploader.py` (commit b4f749d),
    # `..models` resolves to `ai_ml.models` which only contains ML model
    # wrappers (whisper / embedding / keybert / acoustic), NOT the Django
    # `AudioClip` ORM model. Every other file in `ai_ml/` that needs the
    # ORM uses the absolute path (see ai_ml/pipelines/recommendation.py:195
    # for the canonical example) — this file now matches that pattern.
    from backend.app.models import AudioClip

    date = datetime.datetime.utcnow().strftime("%Y/%m/%d")
    dest_rel_dir = f"audio_scraper/{source_name}/{date}"

    ext = os.path.splitext(local_file_path)[1] or '.mp3'
    filename = f"{uuid.uuid4().hex}{ext}"
    upload_path = f"{dest_rel_dir}/{filename}"

    with open(local_file_path, 'rb') as f:
        # DECISION: save via default_storage instead of original_file.save():
        # FieldFile.save() prepends the field's upload_to ('uploads/%Y/%m/%d/'),
        # which double-nests every scraped clip under a spurious uploads/
        # prefix. Storage.save() honors the explicit audio_scraper/ path.
        saved_name = default_storage.save(upload_path, DjangoFile(f))
        clip = AudioClip(
            creator=user,
            title=title or filename,
            category=category or source_name,
            source_name=source_name,
            source_url=source_url,
            license=license,
            attribution_text=attribution_text,
            imported_via_scraper=True,
            original_source_id=original_source_id
        )
        clip.original_file.name = saved_name
        clip.save()

    logger.info("Saved clip %s (%s)", clip.id, saved_name)
    return clip
