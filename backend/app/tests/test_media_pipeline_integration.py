"""REAL end-to-end integration path: upload -> moderation -> processing ->
HLS playlist -> playback token -> feed response (audit P1 work item).

This module drives the production request/task path against LIVE services:
Postgres+pgvector (via the root conftest), Redis (broker + cache split) and
an S3-compatible object store (MinIO in CI). It does NOT override STORAGES —
the default storage backend is whatever the environment configures
(``storages.backends.s3.S3Storage`` pointed at ``AWS_S3_ENDPOINT_URL``), so
every object read/write here is a real network round trip to MinIO.

Gate shaping (deliberate):
  * ``ECHOFLOW_INTEGRATION_TESTS=1`` (module skipif) — the rest of the suite
    must stay runnable anywhere Django+Postgres exist; these four tests need
    real ffmpeg, real Redis and a real bucket, so they opt in via env var
    instead of silently failing CI hosts that lack the services.
  * ``docker_required`` marker (registered in pytest.ini) — one marker to
    select exactly this integration path in CI: ``pytest -m docker_required``.
  * ``django_db`` — the DB is the real Postgres test DB created by conftest.

What is faked (and only this): the four heavy model loaders are monkeypatched
ON ``backend.app.tasks``, exactly like ``test_hls_retry_safety.py`` does —
whisper/embedder/keyword-model/acoustic-extractor. Everything else is real:
multipart upload through ``AudioUploadSerializer``, the approve-moderation
endpoint, ffmpeg decode + HLS encode, librosa.load on the normalized file,
S3 upload + the task's manifest verification, the feed viewset's Redis queue
refill (``refill_user_feed`` runs eagerly), and the HMAC playback-token
service. No test ever talks to the public media edge: playback authorization
is asserted with the production validator (``services.hls_token``) against
the token's own scope, per the module's "assert on keys, not URLs" contract.

The corrupt-input test's file is constructed so it passes ALL THREE upload
gates in ``AudioUploadSerializer.validate_original_file`` (serializers.py:423)
— the pure-Python magic-byte allowlist, libmagic (``audio/x-wav``), and the
pydub/ffmpeg duration probe — but the worker's authoritative decode
(``tasks.normalize_to_wav``, tasks.py:210-218, which adds ``-ac 1``) fails
hard: the file is a well-formed RIFF/WAVE header declaring **200 PCM
channels**, and every swresample build since ffmpeg 2.x refuses more than
``SWR_CH_MAX`` (64) input channels (verified empirically against ffmpeg
7.1 and by source against n5.1.6: libswresample/swresample.c returns EINVAL
at the ``nb_channels > SWR_CH_MAX`` guard). The probe passes because its
pipeline (pydub ``from_file`` on a BytesIO -> ``ffmpeg -read_ahead_limit -1
-i cache:pipe:0 ... -f wav -``) never resamples or downmixes, and the pcm
encoder/channel writer has no cap — the asymmetry is real and version-stable.
"""
import io
import os
import posixpath
import re
import struct
import types
import wave
from urllib.parse import unquote

import pytest
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from backend.EchoFlow.celery import app as celery_app
from backend.app import tasks
from backend.app.models import AudioClip
from backend.app.services import hls_token
from backend.app.services.content_moderation import (
    check_transcript_for_prohibited_content,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.docker_required,
    pytest.mark.skipif(
        os.environ.get("ECHOFLOW_INTEGRATION_TESTS") != "1",
        reason=(
            "integration path needs ECHOFLOW_INTEGRATION_TESTS=1 plus live "
            "Docker services (Postgres + Redis + MinIO); the rest of the "
            "suite must stay unaffected"
        ),
    ),
]

#: Transcript that ``check_transcript_for_prohibited_content`` rejects. The
#: blocklist is the code-level multi-word CSAM list in
#: services/content_moderation.py (_BLOCKED_PHRASES) — there is nothing to
#: seed in the DB, which is why the service module itself is imported here
#: to derive the expected reason from the same source the task calls.
_BLOCKED_TRANSCRIPT = (
    "this recording discusses child sexual abuse material distribution"
)


# ---------------------------------------------------------------------------
# Fakes for the heavy model loaders (the ONLY runtime stubs; ffmpeg decode,
# ffmpeg HLS encode, librosa.load, real Redis, real MinIO all run for real).
# Same shape as test_hls_retry_safety.py — patched ON backend.app.tasks
# because the task resolves the loaders as module globals at call time.
# ---------------------------------------------------------------------------

class _ControllableWhisper:
    """Fake faster-whisper whose transcript the test can set per scenario."""

    transcript = "hello world"

    def transcribe(self, path, beam_size=5, **kwargs):
        return [types.SimpleNamespace(text=self.transcript)], types.SimpleNamespace()


class _FakeVector(list):
    """Stands in for the numpy array SentenceTransformer.encode returns.

    tasks.py calls ``.tolist()`` on encode()'s result; a bare list would
    AttributeError into the task's terminal_error branch and every test here
    would be measuring the wrong branch (same trap
    test_content_moderation._FakeVector and test_hls_retry_safety._FakeVector
    document).
    """

    def tolist(self):
        return list(self)


class _FakeEmbedder:
    def encode(self, text):
        return _FakeVector([0.1] * 384)


class _FakeKeywordExtractor:
    def extract_keywords(self, text, **kwargs):
        return [("instrumental", 1.0)]


# ---------------------------------------------------------------------------
# Sabotage proxy for the transient-failure test. Mirrors
# test_hls_retry_safety._FailingSaveStorage, but delegates to the LIVE
# storage backend instead of a local FileSystemStorage: this module's
# contract is that the transient object-store failure happens against real
# MinIO, so the successful retry's manifest verification runs over the
# network against the bucket the failed attempt interrupted.
# ---------------------------------------------------------------------------

class _FailingHlsSaveStorage:
    """Delegates to the real storage; raises OSError on the Nth hls/ save.

    Simulates the S3 hiccup mid-loop that the P1 fix's autoretry exists for.
    Only save() is sabotaged; open/exists/delete/listdir all delegate, so
    the original-file streaming and the failed-attempt cleanup behave.
    """

    def __init__(self, inner, fail_on_nth, prefix="hls/"):
        self._inner = inner
        self._fail_on_nth = fail_on_nth
        self._prefix = prefix
        self.save_calls = 0
        self.raised = False

    def save(self, name, content):
        if str(name).startswith(self._prefix):
            self.save_calls += 1
            if self.save_calls == self._fail_on_nth:
                self.raised = True
                raise OSError(
                    "simulated object-store hiccup mid-upload (audit P1 scenario)"
                )
        return self._inner.save(name, content)

    def __getattr__(self, item):
        return getattr(self._inner, item)


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def _wav_bytes():
    """1 second, 8kHz mono 16-bit PCM WAV — genuinely decodable everywhere.

    Same construction as test_hls_retry_safety/test_cover_upload: a real
    RIFF header a real ffmpeg decodes, so the happy path exercises the
    actual transcode, not a stub.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)
    return buf.getvalue()


def _corrupt_wav_bytes():
    """A WAV that passes all three upload gates but fails the worker decode.

    Construction: valid RIFF/WAVE container, fmt chunk = WAVE_FORMAT_PCM,
    sample_rate 8000, bits 16, **nChannels = 200**, data chunk = deterministic
    garbage bytes.

    Why each upload gate passes:
      * pure-Python magic-byte check (serializers.py:_BLOCKED_MAGIC_SIGNATURES)
        — 'RIFF' is not a blocked signature;
      * libmagic (python-magic) — identifies ``audio/x-wav`` (verified with
        file(1)/libmagic on this exact byte layout);
      * pydub duration probe (serializers.py:validate_original_file) — the
        probe pipes the bytes to ``ffmpeg -read_ahead_limit -1 -i
        cache:pipe:0 ... -f wav -`` with NO channel conversion, and neither
        the pcm decoder, the pcm encoder, nor the wav header writer caps
        channel count, so it succeeds and reports a ~2ms duration.

    Why the task still fails TERMINALLY: ``normalize_to_wav`` (tasks.py)
    decodes with ``-ac 1 -ar 22050``, and swresample rejects more than
    SWR_CH_MAX (64) input channels (libswresample/swresample.c, the
    ``nb_channels > SWR_CH_MAX`` EINVAL guard) in every supported ffmpeg
    line (5.1 bookworm, 6.1 ubuntu, 7.x). CalledProcessError lands in the
    task's terminal branch (tasks.py ~line 526) -> status 'failed', never a
    retry.
    """
    garbage = bytes((i * 37 + 11) % 256 for i in range(8192))
    fmt = struct.pack(
        "<HHIIHH",
        1,            # wFormatTag: PCM
        200,          # nChannels: > SWR_CH_MAX(64) — the terminal trigger
        8000,         # nSamplesPerSec
        8000 * 400,   # nAvgBytesPerSec (block_align * rate)
        400,          # nBlockAlign: 200ch * 16bit/8
        16,           # wBitsPerSample
    )
    body = (
        b"WAVE"
        + b"fmt " + struct.pack("<I", len(fmt)) + fmt
        + b"data" + struct.pack("<I", len(garbage)) + garbage
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---------------------------------------------------------------------------
# HTTP helpers — the REAL request path (no serializer shortcuts)
# ---------------------------------------------------------------------------

def _authed(user):
    """APIClient with force_authenticate, the suite's established auth idiom
    (conftest.auth_client / test_upload_license_derivation._authed)."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _upload_clip(client, data, name="integration-tone.wav", **overrides):
    """POST /clips/ — the AudioUploadSerializer flow (urls.py:92 -> content.py:198).

    Requires title, category, copyright_acknowledgement, original_file
    (serializers.py:331). The multipart body carries the real bytes, so the
    file lands in the REAL storage backend (MinIO in CI).
    """
    payload = {
        "title": "Integration clip",
        "category": "music",
        "copyright_acknowledgement": "true",
        "original_file": SimpleUploadedFile(name, data, content_type="audio/wav"),
    }
    payload.update(overrides)
    return client.post("/clips/", payload, format="multipart")


def _approve_moderation(client, clip_id, monkeypatch, on_commit):
    """POST /clips/<id>/approve-moderation/ — production self-approval.

    The endpoint flips moderation_approved=True and dispatches HLS via
    ``uploads_svc.trigger_hls_processing`` ->
    ``transaction.on_commit(publish(process_audio_to_hls, ...))``
    (content.py:531-542, services/uploads.py:74-82). Under pytest-django the
    test wraps everything in an atomic block, so the on_commit callback is
    captured and executed here (``on_commit`` is pytest-django's
    ``django_capture_on_commit_callbacks`` fixture in execute=True mode);
    ``publish`` is stubbed at the exact seam the existing suite uses
    (test_upload_license_derivation.approve) so the heavy task runs at the
    explicit ``.apply()`` call sites below instead of inside the HTTP
    request. The recorded call doubles as evidence that the production
    enqueue path really ran.
    """
    import backend.app.services.uploads as uploads_svc

    published = []
    monkeypatch.setattr(
        uploads_svc,
        "publish",
        lambda task, *args, **kwargs: published.append((task, args, kwargs)),
    )
    with on_commit(execute=True):
        response = client.post(
            f"/clips/{clip_id}/approve-moderation/", {}, format="json"
        )
    assert response.status_code == 200, response.data
    assert published, (
        "approve-moderation did not enqueue HLS processing; the production "
        "dispatch path did not run"
    )
    dispatched_task, dispatched_args, _kwargs = published[0]
    assert dispatched_task is tasks.process_audio_to_hls
    assert dispatched_args == (str(clip_id),)
    return response


# ---------------------------------------------------------------------------
# Storage helpers — bucket/key assertions against the REAL backend
# ---------------------------------------------------------------------------

def _walk_keys(prefix):
    """Every object key under prefix, recursively, via the storage API."""
    if not prefix.endswith("/"):
        prefix += "/"
    try:
        dirs, files = default_storage.listdir(prefix)
    except (FileNotFoundError, OSError):
        # FileSystemStorage raises FileNotFoundError for a missing dir;
        # S3Storage returns empty lists for a missing prefix.
        return []
    keys = [f"{prefix}{name}" for name in files]
    for sub in dirs:
        keys.extend(_walk_keys(f"{prefix}{sub}"))
    return keys


def _delete_prefix(prefix):
    """Best-effort teardown: remove everything the test wrote under prefix.

    The DB rolls back between tests but a bucket does not, so each test
    cleans its own prefixes ('uploads/' for the serializer's upload_to,
    'hls/' for the task's build dirs). Same shape as signals._delete_s3_prefix.
    """
    if not prefix.endswith("/"):
        prefix += "/"
    for key in _walk_keys(prefix):
        try:
            default_storage.delete(key)
        except Exception:  # noqa: BLE001 — teardown must never mask the result
            pass


# ---------------------------------------------------------------------------
# Manifest verification — OUR OWN copy of the small parser.
#
# Deliberately NOT imported from test_hls_retry_safety.py: that file is owned
# by a different fix agent, and its parser is kept independent of the
# implementation's tasks.py parser so a broken impl parser cannot validate
# itself. Duplicating the ~30 lines here keeps that independence intact and
# keeps this module self-contained, while still validating against the same
# source of truth (the objects AS STORED, via default_storage).
# ---------------------------------------------------------------------------

def _read_key(key):
    with default_storage.open(key, "rb") as fh:
        return fh.read()


def _parse_uris(text):
    """URIs one playlist body references: non-comment lines, plus the
    #EXT-X-MAP init segment on fMP4 output."""
    uris = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#EXT-X-MAP"):
                m = re.search(r'URI="([^"]+)"', line)
                if m:
                    uris.append(m.group(1))
            continue
        uris.append(line)
    return uris


def _resolve(base_dir, uri):
    if uri.startswith("/"):
        return unquote(uri).lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, unquote(uri)))


def _assert_manifest_fully_resolves(clip):
    """Parse master + variant playlists FROM STORAGE; every referenced object
    must exist and be non-empty. Returns the build_id."""
    url = clip.hls_playlist_url or ""
    m = re.fullmatch(
        rf"hls/{re.escape(str(clip.id))}/([0-9a-f]{{12}})/master\.m3u8", url
    )
    assert m, f"pointer is not a versioned build dir: {url!r}"
    build_id = m.group(1)
    build_dir = posixpath.dirname(url)

    master_text = _read_key(url).decode()
    assert master_text.startswith("#EXTM3U"), "master playlist is not an M3U8"
    variant_uris = _parse_uris(master_text)
    assert variant_uris, "master playlist referenced no variant playlists"
    for uri in variant_uris:
        variant_key = _resolve(build_dir, uri)
        assert default_storage.exists(variant_key), f"variant playlist missing: {variant_key}"
        variant_dir = posixpath.dirname(variant_key)
        segment_uris = _parse_uris(_read_key(variant_key).decode())
        assert segment_uris, f"variant playlist referenced no segments: {variant_key}"
        for seg_uri in segment_uris:
            seg_key = _resolve(variant_dir, seg_uri)
            assert default_storage.exists(seg_key), f"referenced object missing: {seg_key}"
            assert _read_key(seg_key), f"referenced object is empty: {seg_key}"
    return build_id


def _assert_no_hls_objects_for(clip_id):
    keys = _walk_keys("hls")
    offenders = [k for k in keys if k.startswith(f"hls/{clip_id}/")]
    assert not offenders, f"HLS objects were written for clip {clip_id}: {offenders!r}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def eager_celery():
    """task_always_eager + task_eager_propagates, restored after the test.

    Required, not just convenient: the approve endpoint's publish, the feed
    viewset's refill publish and the publish step's purge scheduling all go
    through apply_async, which without eager mode would need a real broker.
    ``.apply()`` re-raises the retried exception when task_eager_propagates
    is on (celery sets request.is_eager=True for apply()), so failure cases
    catch broad Exception rather than pinning the class celery raises.
    """
    old_eager = celery_app.conf.task_always_eager
    old_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield celery_app
    celery_app.conf.task_always_eager = old_eager
    celery_app.conf.task_eager_propagates = old_propagate


@pytest.fixture
def fake_heavy_models(monkeypatch):
    """Every heavy model loader faked in place on backend.app.tasks.

    Returns the whisper fake so a test can choose the transcript (the
    rejection scenario) before running the task.
    """
    whisper = _ControllableWhisper()
    monkeypatch.setattr(tasks, "get_whisper_model", lambda: whisper)
    monkeypatch.setattr(tasks, "get_embedding_model", lambda: _FakeEmbedder())
    monkeypatch.setattr(tasks, "get_kw_model", lambda: _FakeKeywordExtractor())
    monkeypatch.setattr(tasks, "extract_acoustic_vector", lambda y, sr: [0.1] * 128)
    return whisper


@pytest.fixture
def token_secret(settings):
    """A non-placeholder HMAC secret for the REAL playback-token service.

    hls_token._get_secret() rejects empty/placeholder values outright (no
    bypass flag reaches it), so the token endpoint cannot run without this.
    The value avoids every substring in EchoFlow/secrets.py's placeholder
    vocabulary. CI also exports MEDIA_TOKEN_SECRET (production shape); the
    fixture makes the module self-sufficient regardless of the env.
    """
    settings.MEDIA_TOKEN_SECRET = (
        "integration-hls-hmac-7c1f2b3d4e5f60718293a4b5c6d7e8f9"
    )
    return settings.MEDIA_TOKEN_SECRET


@pytest.fixture(autouse=True)
def bucket_cleanup():
    """Yield, then delete every object the test wrote under uploads/ and hls/.

    This module never overrides STORAGES, so everything the upload serializer
    and the HLS task write goes to the real bucket. Django rolls the DB back
    per test; the bucket does not roll back, so the teardown does it.
    """
    yield
    _delete_prefix("uploads/")
    _delete_prefix("hls/")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_full_path_upload_moderation_process_playback_feed(
    django_user_model, eager_celery, fake_heavy_models, token_secret,
    monkeypatch, django_capture_on_commit_callbacks,
):
    """upload -> approve-moderation -> process_audio_to_hls -> playlist in the
    bucket -> GET /feed/ -> POST /media/playback-token/, all against live
    Postgres + Redis + MinIO with real ffmpeg."""
    user = django_user_model.objects.create_user(
        username=f"itn-full-{uuid.uuid4().hex[:10]}",
        email="itn-full@example.com",
        password="pw-itn-full-123",
    )
    client = _authed(user)

    # 1. REAL upload through the serializer (storage receives the file).
    response = _upload_clip(client, _wav_bytes())
    assert response.status_code == 202, response.data
    assert response.data["status"] == "processing"
    clip_id = response.data["clip_id"]
    clip = AudioClip.objects.get(id=clip_id)
    assert clip.moderation_approved is False  # finalize_upload keeps it gated
    assert clip.original_file, "upload did not persist the file"
    assert default_storage.exists(clip.original_file.name), (
        "original upload missing from the real bucket"
    )

    # 2. Approve the way production does (owner self-approval, v1 flow).
    _approve_moderation(
        client, clip.id, monkeypatch, django_capture_on_commit_callbacks
    )

    # 3. The worker task, eagerly.
    result = tasks.process_audio_to_hls.apply(args=[str(clip.id)])
    assert result.state == "SUCCESS", getattr(result, "result", None)

    clip.refresh_from_db()
    assert clip.status == "ready"
    # The task flips the moderation verdict on pass (tasks.py ~655).
    assert clip.moderation_approved is True
    assert clip.moderation_reason == ""

    # 4. The published pointer matches hls/<clip>/<build>/master.m3u8 and
    #    EVERY manifest-referenced object exists in the real bucket.
    build_id = _assert_manifest_fully_resolves(clip)
    assert default_storage.exists(clip.hls_playlist_url)

    # 5. The clip is served by the real feed path (Redis queue + SQL).
    feed = client.get("/feed/")
    assert feed.status_code == 200, getattr(feed, "data", None)
    results = feed.data["results"]
    match = [c for c in results if str(c["id"]) == str(clip.id)]
    assert match, "ready clip missing from GET /feed/"
    served = match[0]
    # Playback URL assertion: on the KEY, never on a host (the media endpoint
    # is build-internal; media_urls.get_hls_playback_url renders either the
    # bucket-prefixed or the edge style depending on HLS_URL_STYLE).
    served_url = served["hls_playlist_url"]
    if settings.HLS_URL_STYLE == "edge":
        assert served_url.endswith(f"/{clip.hls_playlist_url}")
    else:
        bucket = settings.STORAGES["default"]["OPTIONS"]["bucket_name"]
        assert served_url.endswith(f"/{bucket}/{clip.hls_playlist_url}")

    # 6. Playback token authorizes the master playlist path.
    token_response = client.post(
        f"/media/playback-token/{clip.id}/",
        HTTP_X_ECHOFLOW_CLIENT="native",  # native transport returns the token
    )
    assert token_response.status_code == 200, token_response.data
    assert token_response.data["status"] == "ok"
    token = token_response.data["token"]
    assert hls_token.COOKIE_NAME in token_response.cookies
    # The token scope binds the clip's storage-key prefix: exactly
    # hls/<clip_id>/<build_id> (services.uploads.clip_storage_key strips the
    # filename from the pointer).
    payload = hls_token.verify_token(token)
    assert payload is not None, "issued token failed the production validator"
    assert payload["c"] == f"hls/{clip.id}/{build_id}"
    # The edge's path check (hls_token.validate_playback_token) accepts the
    # master playlist under that scope — asserted with the real validator,
    # not by hitting any nginx/worker.
    assert hls_token.validate_playback_token(
        token, f"/hls/{clip.id}/{build_id}/master.m3u8"
    ) is not None


def test_rejected_transcript_marks_clip_rejected(
    django_user_model, eager_celery, fake_heavy_models, monkeypatch,
    django_capture_on_commit_callbacks,
):
    """A transcript the moderation service rejects must land the clip in the
    terminal 'rejected' state with evidence persisted, and must not write a
    single HLS object."""
    # Sanity: the service rejects this transcript on its own — the same
    # function the task calls at tasks.py ~627, so the expected reason comes
    # from the production source, not from this test's imagination.
    approved, reason = check_transcript_for_prohibited_content(_BLOCKED_TRANSCRIPT)
    assert not approved and reason, (
        "blocklist mechanism in services/content_moderation.py changed; "
        "this scenario no longer exercises the rejection branch"
    )

    user = django_user_model.objects.create_user(
        username=f"itn-rej-{uuid.uuid4().hex[:10]}",
        email="itn-rej@example.com",
        password="pw-itn-rej-123",
    )
    client = _authed(user)

    response = _upload_clip(client, _wav_bytes())
    assert response.status_code == 202, response.data
    clip = AudioClip.objects.get(id=response.data["clip_id"])

    _approve_moderation(
        client, clip.id, monkeypatch, django_capture_on_commit_callbacks
    )

    fake_heavy_models.transcript = _BLOCKED_TRANSCRIPT
    tasks.process_audio_to_hls.apply(args=[str(clip.id)])

    clip.refresh_from_db()
    assert clip.status == "rejected"
    assert clip.moderation_approved is False
    assert clip.moderation_reason == reason
    assert clip.transcript_text == _BLOCKED_TRANSCRIPT, (
        "rejection evidence (transcript) was not persisted"
    )
    _assert_no_hls_objects_for(clip.id)


def test_corrupt_upload_fails_terminal(
    django_user_model, eager_celery, fake_heavy_models, monkeypatch,
    django_capture_on_commit_callbacks,
):
    """A file that passes all three upload gates (magic bytes, libmagic,
    pydub/ffmpeg duration probe) but that the worker's authoritative ffmpeg
    decode cannot convert must end TERMINALLY 'failed' — no pointer, no
    HLS objects, no retry.

    See _corrupt_wav_bytes for the construction and the ffmpeg-version
    evidence behind it.
    """
    user = django_user_model.objects.create_user(
        username=f"itn-corrupt-{uuid.uuid4().hex[:10]}",
        email="itn-corrupt@example.com",
        password="pw-itn-corrupt-123",
    )
    client = _authed(user)

    # The corrupt file passes the REAL upload serializer — 202, not 400.
    response = _upload_clip(
        client, _corrupt_wav_bytes(), name="corrupt.wav"
    )
    assert response.status_code == 202, response.data
    clip = AudioClip.objects.get(id=response.data["clip_id"])
    assert default_storage.exists(clip.original_file.name)

    _approve_moderation(
        client, clip.id, monkeypatch, django_capture_on_commit_callbacks
    )

    result = tasks.process_audio_to_hls.apply(args=[str(clip.id)])
    # Terminal branch: the task COMPLETES (no autoretry) after marking the
    # clip failed — tasks.py normalize_to_wav CalledProcessError handler.
    assert result.state == "SUCCESS", getattr(result, "result", None)

    clip.refresh_from_db()
    assert clip.status == "failed"
    assert not clip.hls_playlist_url, "a failed attempt must never publish a pointer"
    _assert_no_hls_objects_for(clip.id)


def test_transient_object_store_failure_then_retry_succeeds(
    django_user_model, eager_celery, fake_heavy_models, monkeypatch,
    django_capture_on_commit_callbacks,
):
    """Audit P1, integration level: an OSError raised by the REAL storage
    backend mid-upload must not publish anything; a clean retry with a fresh
    build prefix must publish a manifest that fully verifies against MinIO
    and surface in the feed."""
    user = django_user_model.objects.create_user(
        username=f"itn-retry-{uuid.uuid4().hex[:10]}",
        email="itn-retry@example.com",
        password="pw-itn-retry-123",
    )
    client = _authed(user)

    response = _upload_clip(client, _wav_bytes())
    assert response.status_code == 202, response.data
    clip = AudioClip.objects.get(id=response.data["clip_id"])

    _approve_moderation(
        client, clip.id, monkeypatch, django_capture_on_commit_callbacks
    )

    # ffmpeg emits three files for a 1s clip (master.m3u8, index.m3u8, one
    # segment); blowing up on the 2nd save leaves a genuinely partial build.
    real_storage = tasks.default_storage
    sabotaged = _FailingHlsSaveStorage(real_storage, fail_on_nth=2)
    monkeypatch.setattr(tasks, "default_storage", sabotaged)

    with pytest.raises(Exception):
        # eager mode re-raises the retried exception; the class celery wraps
        # it into is an implementation detail, so do not overpin it.
        tasks.process_audio_to_hls.apply(args=[str(clip.id)])

    assert sabotaged.raised and sabotaged.save_calls >= 2, (
        "the sabotage did not actually interrupt the upload loop"
    )
    clip.refresh_from_db()
    assert clip.status != "ready"
    assert not clip.hls_playlist_url, "a failed attempt must never move the pointer"

    # Retry cleanly on a fresh attempt (fresh build id, fresh prefix) against
    # the real bucket, un-sabotaged.
    monkeypatch.setattr(tasks, "default_storage", real_storage)
    tasks.process_audio_to_hls.apply(args=[str(clip.id)])

    clip.refresh_from_db()
    assert clip.status == "ready"
    _assert_manifest_fully_resolves(clip)
    assert default_storage.exists(clip.hls_playlist_url)

    # And the retried clip is served end to end: feed shows it.
    feed = client.get("/feed/")
    assert feed.status_code == 200, getattr(feed, "data", None)
    assert any(
        str(c["id"]) == str(clip.id) for c in feed.data["results"]
    ), "clip missing from GET /feed/ after the successful retry"
