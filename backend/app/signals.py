"""Signal handlers for app models.

N9 fix: when an AudioClip is deleted, remove its files from object
storage. Without this, every user-deletion cascade (creator FK is
on_delete=CASCADE) leaves the original upload and the HLS segment
tree in S3/MinIO forever. Same for explicit DELETE /clips/{id} or
any future admin delete.

S3 prefix to clean on delete:
  uploads/YYYY/MM/DD/<uuid>.<ext>  (the original_file)
  hls/<clip_id>/<build_id>/master.m3u8, index.m3u8, seg-N.ts  (the rendered
    HLS — one directory per processed build; superseded builds are removed
    earlier by tasks.purge_superseded_hls_build, 15 min after the pointer
    moves)
  hls/<clip_id>/master.m3u8  (legacy flat layout, written before builds
    were versioned)

The delete covers the whole clip-level prefix ``hls/<clip_id>/`` (derived
by hls_clip_prefix), so every build of the clip goes with the row — under
the versioned layout the pointer names only the CURRENT build, and the old
rsplit-based prefix would strand every superseded build in the bucket
forever.

Both are stored in default_storage (S3Storage in production,
FileSystemStorage in dev). The S3 backend supports listdir() and
delete() on keys.
"""
import logging
import os
import uuid

from django.core.files.storage import default_storage
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import AudioClip

logger = logging.getLogger(__name__)


def hls_clip_prefix(clip_id, url):
    """The clip-level storage prefix (``hls/<clip_id>``) for a pointer URL.

    hls_playlist_url has two shapes (see tasks.parse_hls_pointer):
      legacy:    "hls/<clip_id>/master.m3u8"
      versioned: "hls/<clip_id>/<build_id>/master.m3u8"   (current)
    The whole clip-level prefix is what clip deletion must remove — every
    build of this clip, not just the one the pointer currently names.

    The parsed clip id must be the UUID the row actually carries. That is
    not pedantry: a malformed value must never widen the delete (e.g.
    "hls//master.m3u8" must not become "hls/" and empty the whole hls/
    tree). Anything that does not parse as one of the two shapes falls back
    to the historical rsplit behaviour (deepest directory) — what the
    pre-versioning code did for values the task writes.
    """
    url = (url or '').strip()
    parts = url.split('/')
    # versioned: ['hls', clip_id, build_id, 'master.m3u8']
    # legacy:    ['hls', clip_id, 'master.m3u8']
    if (
        len(parts) in (3, 4)
        and parts[0] == 'hls'
        and parts[-1] == 'master.m3u8'
        and parts[1]
    ):
        try:
            uuid.UUID(parts[1])
        except ValueError:
            pass
        else:
            if clip_id is None or str(clip_id) == parts[1]:
                return f"hls/{parts[1]}"
    return url.rsplit('/', 1)[0]  # drop "master.m3u8"


@receiver(post_delete, sender=AudioClip)
def cleanup_audioclip_storage(sender, instance, **kwargs):
    """Remove the original upload and the HLS segment tree from object
    storage when the AudioClip is deleted.

    Failure handling: the signal runs in the same transaction as the
    delete. If the S3 delete fails (network, credentials, bucket
    perms), the failure is logged and the function returns — the DB
    row is already deleted, the orphan files are the cost. For a real
    production deployment, a periodic Celery task (cleanup_orphan_hls)
    should scan for hls/<id>/ prefixes whose <id> is not in
    AudioClip.objects.values_list('id', flat=True) and delete them.
    Out of audit scope.
    """
    if instance.original_file:
        try:
            instance.original_file.delete(save=False)
        except Exception as exc:
            logger.warning(
                "post_delete: failed to delete original_file %s for clip %s: %s",
                instance.original_file.name, instance.id, exc,
            )

    # B3 (2026-09-29): cover_image was never deleted. It is a user-supplied
    # image in object storage, so leaving it behind on erasure retains
    # personal data outside the database — and the DB half of a DPDP erasure
    # that claims completeness while the bucket keeps the photo is the worst
    # combination. A user's cover image is personal data whatever the clip
    # was.
    if instance.cover_image:
        try:
            instance.cover_image.delete(save=False)
        except Exception as exc:
            logger.warning(
                "post_delete: failed to delete cover_image %s for clip %s: %s",
                instance.cover_image.name, instance.id, exc,
            )

    # hls_playlist_url is "hls/<clip_id>/<build_id>/master.m3u8" (or the
    # legacy "hls/<clip_id>/master.m3u8", or absent if processing never
    # reached HLS). The whole clip-level prefix is ours to clean — see
    # hls_clip_prefix for why the old rsplit('/', 1) derivation was wrong
    # under the versioned layout.
    if instance.hls_playlist_url:
        prefix = hls_clip_prefix(instance.id, instance.hls_playlist_url)
        try:
            _delete_s3_prefix(prefix)
        except Exception as exc:
            logger.warning(
                "post_delete: failed to delete HLS prefix %s for clip %s: %s",
                prefix, instance.id, exc,
            )


def _delete_s3_prefix(prefix):
    """Delete all S3 objects under a prefix, recursively.

    S3Storage's delete() is single-object only; for a prefix we need
    to list + batch-delete. Falls back to single-key delete if the
    storage backend doesn't expose listdir (e.g. some S3-compatible
    backends).

    RECURSION (versioned-layout fix): the clip-level prefix now holds
    per-build DIRECTORIES (``hls/<clip_id>/<build_id>/``) instead of the
    legacy flat files, so a one-level listing would strand every build in
    the bucket on clip deletion — the exact failure hls_clip_prefix exists
    to prevent. Subdirectories are walked depth-first; for the legacy flat
    layout (files directly under the prefix) the walk is a no-op. All
    storage errors are swallowed at WARNING: the DB row is already gone,
    and every caller (the post_delete signal, purge_superseded_hls_build,
    the HLS task's failed-attempt cleanup) must never fail because cleanup
    could not.
    """
    if not hasattr(default_storage, 'listdir'):
        # Best-effort: try delete on the prefix itself. On a real
        # S3 backend this is a no-op; on a flat filesystem it's a
        # path delete.
        try:
            default_storage.delete(prefix)
        except Exception:
            pass
        return

    try:
        dirs, files = default_storage.listdir(prefix)
    except Exception as exc:
        logger.warning("listdir(%s) failed: %s", prefix, exc)
        return

    for fname in files:
        key = f"{prefix}/{fname}".replace(os.sep, '/')
        try:
            default_storage.delete(key)
        except Exception as exc:
            logger.warning("delete(%s) failed: %s", key, exc)
    for sub in dirs or []:
        _delete_s3_prefix(f"{prefix}/{sub}".replace(os.sep, '/'))
