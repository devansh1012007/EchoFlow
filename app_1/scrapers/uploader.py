import os
import uuid
import datetime
import logging
from django.conf import settings
from django.core.files import File as DjangoFile

logger = logging.getLogger(__name__)


def save_clip(user, title, source_name, source_url, license, attribution_text, local_file_path, original_source_id=None, category=None):
    """Save a normalized audio file into Django's media storage and create an AudioClip.

    Returns the created AudioClip instance.
    """
    # Local import to avoid import-time side effects
    from ..models import AudioClip

    date = datetime.datetime.utcnow().strftime("%Y/%m/%d")
    dest_rel_dir = f"audio_scraper/{source_name}/{date}"
    full_dir = os.path.join(settings.MEDIA_ROOT, dest_rel_dir)
    os.makedirs(full_dir, exist_ok=True)

    ext = os.path.splitext(local_file_path)[1] or '.mp3'
    filename = f"{uuid.uuid4().hex}{ext}"
    upload_path = f"{dest_rel_dir}/{filename}"

    with open(local_file_path, 'rb') as f:
        djf = DjangoFile(f)
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
        clip.original_file.save(upload_path, djf, save=False)
        clip.save()

    logger.info("Saved clip %s (%s)", clip.id, upload_path)
    return clip
