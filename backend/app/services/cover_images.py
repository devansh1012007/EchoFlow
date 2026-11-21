"""Cover images for clips: validation, sanitising and the review gate.

Why this module exists
----------------------
``AudioClip.cover_image`` has been in the model and in the feed contract for a
long time, but no route could set it, so it was NULL on every row. Adding an
upload path opens three holes that an audio-only product never had:

1. **Hostile files.** A cover is the first user-supplied *image* the service
   stores and serves to other people. Extension and size checks are not
   enough: the file must decode, must not be animated, must not claim a pixel
   count that makes decoding a memory bomb, and must not carry metadata.
2. **Metadata leaks.** Phone photos carry GPS coordinates and device ids in
   EXIF. Serving the original publishes the uploader's location. The image is
   therefore always *re-encoded* from decoded pixels; nothing from the upload
   survives except what the pixels say.
3. **Moderation.** The audio pipeline moderates a transcript, tags and an
   audio fingerprint (``content_moderation.run_moderation_check``). None of
   that looks at an image, so a harmless clip could carry an explicit cover
   straight into the public feed. Covers therefore start ``pending`` and are
   served only once ``cover_status == "approved"``. See ``approve_cover`` /
   ``reject_cover``.

What this module does NOT do
----------------------------
It does not classify image content. There is no automated screening here, on
purpose: doing it means either sending user images to a third party (a
cross-border transfer the privacy docs have to cover first) or running a local
model. Until that decision is made the gate is a human one, through the Django
admin. The gate fails closed: without an approval the cover is never served,
and the clip still plays with the client's waveform fallback.
"""
from __future__ import annotations

import io
import uuid
from typing import Iterable

from django.core.files.base import ContentFile
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

#: Bytes accepted from the client, before any decoding.
MAX_BYTES = 5 * 1024 * 1024
#: Pixels accepted from the client. Checked from the header, before decoding,
#: so a tiny file that expands to gigabytes is refused without being expanded.
MAX_PIXELS = 25_000_000
#: Shortest side accepted. Smaller than this is an icon, not cover art.
MIN_SIDE = 200
#: Longest side stored. The feed shows covers at most ~600 CSS px wide.
MAX_SIDE = 1080
#: Decoder verdicts accepted. The *decoded format* decides, not the file name.
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
#: WebP quality for the stored copy.
WEBP_QUALITY = 82

COVER_NONE = "none"
COVER_PENDING = "pending"
COVER_APPROVED = "approved"
COVER_REJECTED = "rejected"
COVER_STATUSES = (COVER_NONE, COVER_PENDING, COVER_APPROVED, COVER_REJECTED)


class CoverImageError(ValueError):
    """The uploaded cover cannot be accepted. The message is shown to the user."""


def _read_bounded(fileobj, limit: int) -> bytes:
    """Read at most ``limit`` bytes; refuse anything larger without buffering it."""
    data = fileobj.read(limit + 1)
    if len(data) > limit:
        raise CoverImageError(
            f"Image exceeds the {limit // 1024 // 1024} MB limit."
        )
    return data


def sanitize_cover_image(fileobj) -> ContentFile:
    """Validate an uploaded cover and return a clean, re-encoded copy.

    The returned file is a WebP of at most ``MAX_SIDE`` px on its long edge,
    RGB, with EXIF orientation applied and **no** metadata of any kind.

    Raises ``CoverImageError`` with a user-facing message on any refusal.
    """
    size = getattr(fileobj, "size", None)
    if size is not None and size > MAX_BYTES:
        raise CoverImageError(f"Image exceeds the {MAX_BYTES // 1024 // 1024} MB limit.")
    if hasattr(fileobj, "seek"):
        fileobj.seek(0)
    data = _read_bounded(fileobj, MAX_BYTES)
    if not data:
        raise CoverImageError("The image file is empty.")

    try:
        # Image.open reads only the header; nothing is decoded yet.
        image = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        # Pillow refuses at open() once the header claims more than twice its
        # own limit. Same user-facing answer as our (lower) MAX_PIXELS check.
        raise CoverImageError(
            f"Image is too large. Keep it under {MAX_PIXELS // 1_000_000} megapixels."
        )
    except (UnidentifiedImageError, OSError, ValueError):
        raise CoverImageError("That file is not a readable image. Use a JPEG, PNG or WebP.")

    if image.format not in ALLOWED_FORMATS:
        raise CoverImageError("Unsupported image type. Use a JPEG, PNG or WebP.")

    width, height = image.size
    if width * height > MAX_PIXELS:
        raise CoverImageError(
            f"Image is too large ({width}x{height}). Keep it under {MAX_PIXELS // 1_000_000} megapixels."
        )
    if min(width, height) < MIN_SIDE:
        raise CoverImageError(
            f"Image is too small. Both sides must be at least {MIN_SIDE}px."
        )
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
        raise CoverImageError("Animated images are not supported. Use a still image.")

    try:
        # Full decode happens here, inside the pixel budget checked above.
        image.load()
        image = ImageOps.exif_transpose(image)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise CoverImageError("The image file is damaged or incomplete.")

    # Flatten transparency onto the app's canvas colour so a transparent PNG
    # does not render as a hole, then drop to plain RGB.
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (5, 6, 7))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        image = canvas
    else:
        image = image.convert("RGB")

    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    # No exif=, icc_profile= or xmp= arguments: nothing is carried across.
    image.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)
    return ContentFile(out.getvalue(), name=f"{uuid.uuid4().hex}.webp")


# ---------------------------------------------------------------------------
# Review gate
# ---------------------------------------------------------------------------

def approve_cover(clip, reviewer=None) -> None:
    """Mark a pending cover as safe to serve."""
    if not clip.cover_image or not clip.cover_image.name:
        raise CoverImageError("This clip has no cover image to approve.")
    clip.cover_status = COVER_APPROVED
    clip.cover_moderation_reason = ""
    clip.cover_moderated_at = timezone.now()
    clip.save(update_fields=["cover_status", "cover_moderation_reason", "cover_moderated_at"])


def reject_cover(clip, reason: str = "", reviewer=None) -> None:
    """Refuse a cover and delete the stored image.

    The clip itself is untouched: it keeps playing, and clients fall back to
    the waveform. The file is deleted rather than hidden so a rejected image
    does not sit in object storage behind a guessable key.
    """
    stored = clip.cover_image
    if stored and stored.name:
        stored.delete(save=False)
    clip.cover_image = None
    clip.cover_status = COVER_REJECTED
    clip.cover_moderation_reason = (reason or "")[:500]
    clip.cover_moderated_at = timezone.now()
    clip.save(
        update_fields=[
            "cover_image",
            "cover_status",
            "cover_moderation_reason",
            "cover_moderated_at",
        ]
    )


def cover_is_servable(clip) -> bool:
    """True only for a stored cover that a reviewer approved."""
    return bool(
        clip.cover_image
        and clip.cover_image.name
        and getattr(clip, "cover_status", COVER_NONE) == COVER_APPROVED
    )


def pending_covers(queryset) -> Iterable:
    """Clips waiting for a reviewer, oldest first."""
    return queryset.filter(cover_status=COVER_PENDING).order_by("created_at")
