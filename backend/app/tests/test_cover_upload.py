"""Cover image upload: sanitising, the review gate, and the API contract.

`AudioClip.cover_image` could not be set through any route before this change.
Adding the upload path opened three holes an audio-only product never had, and
each has tests here:

* hostile or oversized files           -> TestSanitize
* metadata (EXIF GPS) reaching viewers -> TestSanitize.test_exif_is_not_carried_over
* unmoderated images in the feed       -> TestReviewGate / TestUploadApi

Fixtures build real images with Pillow. A synthetic header would only test
Pillow's rejection of garbage, not the rules in services/cover_images.py.
"""
import io
import os
import struct
import zlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from backend.app.models import AudioClip, User
from backend.app.services import cover_images
from backend.app.services.cover_images import CoverImageError, sanitize_cover_image


def _image_bytes(fmt="JPEG", size=(640, 480), color=(120, 90, 60), noisy=False, exif=None):
    if noisy:
        img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    else:
        img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    kwargs = {}
    if exif is not None:
        kwargs["exif"] = exif
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def _upload(data, name="cover.jpg", content_type="image/jpeg"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class TestSanitize:
    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
    def test_accepts_real_images_and_outputs_webp(self, fmt):
        out = sanitize_cover_image(_upload(_image_bytes(fmt), f"c.{fmt.lower()}"))
        assert out.name.endswith(".webp")
        assert Image.open(io.BytesIO(out.read())).format == "WEBP"

    def test_downscales_long_edge(self):
        out = sanitize_cover_image(_upload(_image_bytes("PNG", size=(3000, 1500))))
        w, h = Image.open(io.BytesIO(out.read())).size
        assert max(w, h) == cover_images.MAX_SIDE

    def test_exif_is_not_carried_over(self):
        """The reason covers are re-encoded: phone photos carry GPS."""
        exif = Image.Exif()
        exif[0x010F] = "SecretPhoneMaker"  # Make
        exif[0x8825] = {1: "N", 2: (28.0, 36.0, 0.0), 3: "E", 4: (77.0, 12.0, 0.0)}  # GPS IFD
        data = _image_bytes("JPEG", exif=exif.tobytes())
        assert b"SecretPhoneMaker" in data, "fixture must actually contain the metadata"

        out = sanitize_cover_image(_upload(data)).read()
        assert b"SecretPhoneMaker" not in out
        assert len(Image.open(io.BytesIO(out)).getexif()) == 0
        assert not Image.open(io.BytesIO(out)).info.get("exif")

    def test_exif_orientation_is_applied_not_lost(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 CW when displayed
        data = _image_bytes("JPEG", size=(600, 300), exif=exif.tobytes())
        out = sanitize_cover_image(_upload(data))
        w, h = Image.open(io.BytesIO(out.read())).size
        assert (w, h) == (300, 600), "pixels must be rotated before the tag is dropped"

    def test_transparent_png_is_flattened_not_a_hole(self):
        img = Image.new("RGBA", (400, 400), (255, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out = Image.open(io.BytesIO(sanitize_cover_image(_upload(buf.getvalue())).read()))
        assert out.mode == "RGB"
        r, g, b = out.getpixel((10, 10))
        assert (r, g, b) != (255, 0, 0), "fully transparent pixels must not turn red"

    @pytest.mark.parametrize(
        "payload",
        [
            b"MZ\x90\x00" + b"\x00" * 300,
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            b"this is just text pretending to be a jpeg" * 20,
            b"%PDF-1.4\n" + b"\x00" * 300,
        ],
    )
    def test_rejects_non_images_even_with_an_image_name(self, payload):
        with pytest.raises(CoverImageError):
            sanitize_cover_image(_upload(payload, "cover.jpg"))

    def test_rejects_empty_file(self):
        with pytest.raises(CoverImageError):
            sanitize_cover_image(_upload(b"", "cover.jpg"))

    def test_rejects_truncated_image(self):
        data = _image_bytes("PNG", size=(800, 800), noisy=True)
        with pytest.raises(CoverImageError):
            sanitize_cover_image(_upload(data[: len(data) // 2], "cover.png"))

    def test_rejects_animated_gif_even_though_gif_is_not_allowed(self):
        frames = [Image.new("RGB", (300, 300), c) for c in ((255, 0, 0), (0, 255, 0))]
        buf = io.BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
        with pytest.raises(CoverImageError):
            sanitize_cover_image(_upload(buf.getvalue(), "cover.gif"))

    def test_rejects_animated_webp(self):
        frames = [Image.new("RGB", (300, 300), c) for c in ((255, 0, 0), (0, 255, 0))]
        buf = io.BytesIO()
        frames[0].save(buf, format="WEBP", save_all=True, append_images=frames[1:])
        with pytest.raises(CoverImageError, match="Animated"):
            sanitize_cover_image(_upload(buf.getvalue(), "cover.webp"))

    def test_rejects_too_small(self):
        with pytest.raises(CoverImageError, match="too small"):
            sanitize_cover_image(_upload(_image_bytes("PNG", size=(120, 120))))

    def test_rejects_over_byte_limit_before_decoding(self):
        big = _image_bytes("PNG", size=(1500, 1500), noisy=True)
        assert len(big) > cover_images.MAX_BYTES, "fixture must exceed the cap"
        with pytest.raises(CoverImageError, match="MB"):
            sanitize_cover_image(_upload(big, "cover.png"))

    def test_rejects_pixel_bomb_from_the_header_alone(self):
        """A few hundred bytes that claim 20000x20000 must be refused unexpanded."""

        def chunk(tag, body):
            crc = zlib.crc32(tag + body) & 0xFFFFFFFF
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", 20000, 20000, 8, 2, 0, 0, 0)
        bomb = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00" * 64))
            + chunk(b"IEND", b"")
        )
        assert len(bomb) < 200
        with pytest.raises(CoverImageError, match="too large"):
            sanitize_cover_image(_upload(bomb, "cover.png"))


# ---------------------------------------------------------------------------
# API + review gate (need the database)
# ---------------------------------------------------------------------------

@pytest.fixture
def creator(db):
    return User.objects.create_user(username="cover_creator", email="cc@example.com", password="pw-12345-abcde")


def _make_clip(creator, **overrides):
    fields = dict(
        creator=creator, title="Clip", category="music", status="ready",
        moderation_approved=True, license_type="CC-BY", duration_ms=4200,
    )
    fields.update(overrides)
    return AudioClip.objects.create(**fields)


@pytest.mark.django_db
class TestReviewGate:
    def _clip_with_cover(self, creator, status):
        clip = _make_clip(creator)
        clip.cover_image.name = "covers/2026/11/18/abc.webp"
        clip.cover_status = status
        clip.save(update_fields=["cover_image", "cover_status"])
        return clip

    @pytest.mark.parametrize("status", ["none", "pending", "rejected"])
    def test_unapproved_cover_is_never_served(self, creator, status):
        from backend.app.serializers import FeedClipSerializer
        clip = self._clip_with_cover(creator, status)
        assert FeedClipSerializer(clip, context={}).data["cover_image"] is None

    def test_approved_cover_is_served(self, creator):
        from backend.app.serializers import FeedClipSerializer
        clip = self._clip_with_cover(creator, "approved")
        assert FeedClipSerializer(clip, context={}).data["cover_image"]

    def test_approve_then_reject_roundtrip(self, creator):
        clip = self._clip_with_cover(creator, "pending")
        cover_images.approve_cover(clip)
        clip.refresh_from_db()
        assert clip.cover_status == "approved" and clip.cover_moderated_at

        from unittest.mock import patch
        with patch("django.db.models.fields.files.FieldFile.delete") as deleted:
            cover_images.reject_cover(clip, "not allowed")
        deleted.assert_called_once()
        clip.refresh_from_db()
        assert clip.cover_status == "rejected"
        assert not clip.cover_image
        assert clip.cover_moderation_reason == "not allowed"

    def test_cannot_approve_a_clip_without_a_cover(self, creator):
        clip = _make_clip(creator)
        with pytest.raises(CoverImageError):
            cover_images.approve_cover(clip)


@pytest.mark.django_db
class TestUploadApi:
    """Drive the real serializer the way POST /clips/ does."""

    def _serializer(self, creator, data, files):
        from rest_framework.test import APIRequestFactory
        from backend.app.serializers import AudioUploadSerializer
        request = APIRequestFactory().post("/clips/")
        request.user = creator
        merged = {"title": "T", "category": "music", "copyright_acknowledgement": True, **data}
        merged.update(files)
        return AudioUploadSerializer(data=merged, context={"request": request})

    def _audio(self):
        # Minimal valid WAV so validate_original_file's header checks pass.
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 8000)
        return SimpleUploadedFile("a.wav", buf.getvalue(), content_type="audio/wav")

    def test_upload_without_cover_has_status_none(self, creator, tmp_path, settings):
        settings.STORAGES = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": str(tmp_path)}}}
        s = self._serializer(creator, {}, {"original_file": self._audio()})
        assert s.is_valid(), s.errors
        clip = s.save()
        assert clip.cover_status == "none" and not clip.cover_image

    def test_upload_with_cover_is_pending_and_reencoded(self, creator, tmp_path, settings):
        settings.STORAGES = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": str(tmp_path)}}}
        s = self._serializer(creator, {}, {"original_file": self._audio(), "cover_image": _upload(_image_bytes("JPEG", size=(900, 600)))})
        assert s.is_valid(), s.errors
        clip = s.save()
        assert clip.cover_status == "pending"
        assert clip.cover_image.name.endswith(".webp")
        assert "cover_image" not in s.data, "write-only: never echo the storage URL"
        assert s.data["cover_status"] == "pending"

    def test_bad_cover_fails_the_whole_upload(self, creator):
        s = self._serializer(creator, {}, {"original_file": self._audio(), "cover_image": _upload(b"not an image" * 30)})
        assert not s.is_valid()
        assert "cover_image" in s.errors
        assert AudioClip.objects.count() == 0

    def test_cover_cannot_be_set_through_update(self, creator):
        from rest_framework.test import APIRequestFactory
        from backend.app.serializers import AudioUploadSerializer
        clip = _make_clip(creator)
        request = APIRequestFactory().patch("/clips/")
        request.user = creator
        s = AudioUploadSerializer(clip, data={"cover_image": _upload(_image_bytes("JPEG"))}, partial=True, context={"request": request})
        assert not s.is_valid()
        assert "cover_image" in s.errors
