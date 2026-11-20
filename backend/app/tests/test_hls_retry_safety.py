"""Retry-safety of the HLS publication path (audit P1 + P2).

The bug this file pins: the task used to upload every attempt into the FIXED
prefix ``hls/<clip_id>/`` while storage was configured with
``file_overwrite=False`` (EchoFlow/settings.py). On a mid-upload failure
(OSError is in RETRYABLE_ERRORS -> autoretry), the retry's save() calls
collided with the first attempt's objects and the backend returned RENAMED
keys (random suffix) — the manifest kept referencing the original
deterministic names, and the task published the pointer anyway. Broken
playback, published atomically, once per unlucky retry. Django's
FileSystemStorage suffixes colliding names too, so the failure class is not
S3-specific.

The contract under test ("versioned staging prefix + verify + atomic
publish"):

1. every attempt writes ``hls/<clip_id>/<build_id>/`` — save() cannot
   collide, so it cannot rename;
2. playlists are re-read FROM STORAGE and every referenced object is
   ``default_storage.exists()``-checked BEFORE the pointer moves;
3. pointer + status move in ONE narrow save, only after verification;
4. superseded builds are purged later (900s > one 600s token TTL), and the
   purge refuses to delete unless the live pointer proves the build stale.

Test technique: REAL ffmpeg (the fixtures hand it a genuine 1s/8kHz WAV built
with the `wave` module, same as test_cover_upload.py), a real
FileSystemStorage at ``tmp_path`` via the STORAGES override (overriding the
setting — not patching a ``default_storage`` name — is what makes the model
fields and the task agree: Django's ``storages_changed`` receiver clears
``default_storage._wrapped``), and celery eager mode so the purge scheduling
inside publish never needs a broker. The heavy model LOADERS are monkeypatched
on ``backend.app.tasks`` (module import still pulls librosa/numpy — fine;
librosa itself runs for real on a 1s file).

The sabotage storages are task-local PROXIES (``monkeypatch.setattr(tasks,
"default_storage", ...)``) rather than STORAGES backends, because the
sabotage needs per-test state (a call counter, a prefix) that
``STORAGES["default"]["OPTIONS"]`` — constructor kwargs for an importable
class — cannot carry. The proxy delegates everything but the sabotaged
method to a real FileSystemStorage at the SAME location, so both the model
field storage and the task see one tree.
"""
import io
import posixpath
import re
import types
import uuid
import wave
from urllib.parse import unquote

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage

from backend.EchoFlow.celery import app as celery_app
from backend.app import tasks
from backend.app.models import AudioClip

pytestmark = pytest.mark.django_db

# The rename shape FileSystemStorage.get_alternative_name (and
# django-storages with file_overwrite=False) invents on collision:
# "<root>_<7 random chars><ext>".
_COLLISION_SUFFIX_RE = re.compile(r".+_[0-9A-Za-z]{7}(\.[A-Za-z0-9]+)?$")
_BUILD_KEY_RE = re.compile(r"^hls/([^/]+)/([0-9a-f]{12})/([^/]+)$")


# ---------------------------------------------------------------------------
# Fakes for the heavy model loaders (the ONLY runtime stubs; everything else
# — ffmpeg decode, ffmpeg HLS encode, librosa, real storage — runs for real).
# ---------------------------------------------------------------------------

class _FakeWhisper:
    def transcribe(self, path, beam_size=5, **kwargs):
        return [types.SimpleNamespace(text="hello world")], types.SimpleNamespace()


class _FakeVector(list):
    """Stands in for the numpy array SentenceTransformer.encode returns.

    tasks.py calls ``.tolist()`` on encode()'s result; a bare list would
    AttributeError into the task's terminal_error branch and every test here
    would be measuring the wrong branch (same trap
    test_content_moderation._FakeVector documents).
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
# Sabotage storages (task-local proxies; see module docstring for why not
# STORAGES backends).
# ---------------------------------------------------------------------------

class _FailingSaveStorage:
    """Delegates to a real storage; raises OSError on the Nth hls/ save.

    Simulates the S3 hiccup mid-loop that put the P1 bug on the books: the
    attempt dies halfway through its uploads and autoretries.
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
                raise OSError("simulated S3 hiccup mid-upload (audit P1 scenario)")
        return self._inner.save(name, content)

    def __getattr__(self, item):
        return getattr(self._inner, item)


class _MissingObjectsStorage:
    """Delegates to a real storage but reports exists()==False under hls/.

    The other face of the same failure class: uploads claimed success, but
    the objects are not there when the manifest verification looks. Only
    exists() is sabotaged — save/open/listdir/delete must behave, because
    the upload loop and the failed-attempt cleanup run through them.
    """

    def __init__(self, inner):
        self._inner = inner

    def exists(self, name):
        if str(name).startswith("hls/"):
            return False
        return self._inner.exists(name)

    def save(self, name, content):
        return self._inner.save(name, content)

    def __getattr__(self, item):
        return getattr(self._inner, item)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fs_storage(settings, tmp_path):
    """Point STORAGES["default"] at a real FileSystemStorage under tmp_path.

    Overriding the STORAGES setting (not patching a default_storage name) is
    what covers BOTH write paths: the model's original_file save and the
    task's default_storage calls re-resolve through Django's storages handler
    after the setting_changed receiver clears its cache.
    """
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path)},
        },
    }
    return tmp_path


@pytest.fixture
def eager_celery():
    """task_always_eager + task_eager_propagates, restored after the test.

    Required (not just convenient): the publish step calls
    purge_superseded_hls_build.apply_async(...), which without eager mode
    would try to reach a real broker. Eager mode runs the scheduled purge
    inline; a retried failure is re-raised, so failure cases catch broad
    Exception rather than pinning the class celery decides to raise.
    """
    old_eager = celery_app.conf.task_always_eager
    old_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield celery_app
    celery_app.conf.task_always_eager = old_eager
    celery_app.conf.task_eager_propagates = old_propagate


@pytest.fixture
def hls_task(monkeypatch):
    """The task module with every heavy model loader faked in place.

    Patched ON backend.app.tasks (not on the service objects) because the
    task resolves the loaders as module globals at call time.
    """
    monkeypatch.setattr(tasks, "get_whisper_model", lambda: _FakeWhisper())
    monkeypatch.setattr(tasks, "get_embedding_model", lambda: _FakeEmbedder())
    monkeypatch.setattr(tasks, "get_kw_model", lambda: _FakeKeywordExtractor())
    monkeypatch.setattr(tasks, "extract_acoustic_vector", lambda y, sr: [0.1] * 128)
    return tasks


@pytest.fixture
def clip_factory(django_user_model, fs_storage):
    """A processing-state clip whose original is a REAL 1s WAV in storage."""

    def make(**overrides):
        creator = django_user_model.objects.create_user(
            username=f"hls-probe-{uuid.uuid4().hex[:10]}",
            email="hls-probe@example.com",
            password="pw-hls-probe-123",
        )
        stored = default_storage.save(
            "uploads/hls-probe.wav", ContentFile(_wav_bytes())
        )
        fields = dict(
            creator=creator,
            title="hls retry probe",
            category="music",
            status="processing",
            moderation_approved=True,
            original_file=stored,
        )
        fields.update(overrides)
        return AudioClip.objects.create(**fields)

    return make


# ---------------------------------------------------------------------------
# Shared helpers — the playlist parsing here is written ONCE and reused by
# every test; it deliberately re-implements (not imports) the impl's parser
# so a broken parser cannot validate itself.
# ---------------------------------------------------------------------------

def _wav_bytes():
    """1 second, 8kHz mono PCM — same construction as test_cover_upload."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)
    return buf.getvalue()


def _local_storage(location):
    """A real FileSystemStorage at the fixture's location, for proxies."""
    return FileSystemStorage(location=str(location))


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
    """Parse master + variant FROM STORAGE; every referenced object must
    exist and be non-empty. Returns (build_id, referenced_keys)."""
    url = clip.hls_playlist_url or ""
    m = re.fullmatch(
        rf"hls/{re.escape(str(clip.id))}/([0-9a-f]{{12}})/master\.m3u8", url
    )
    assert m, f"pointer is not a versioned build dir: {url!r}"
    build_id = m.group(1)
    build_dir = posixpath.dirname(url)

    master_text = _read_key(url).decode()
    assert master_text.startswith("#EXTM3U"), "master playlist is not an M3U8"
    referenced = []
    variant_uris = _parse_uris(master_text)
    assert variant_uris, "master playlist referenced no variant playlists"
    for uri in variant_uris:
        variant_key = _resolve(build_dir, uri)
        assert default_storage.exists(variant_key), f"variant playlist missing: {variant_key}"
        referenced.append(variant_key)
        variant_dir = posixpath.dirname(variant_key)
        segment_uris = _parse_uris(_read_key(variant_key).decode())
        assert segment_uris, f"variant playlist referenced no segments: {variant_key}"
        for seg_uri in segment_uris:
            seg_key = _resolve(variant_dir, seg_uri)
            assert default_storage.exists(seg_key), f"referenced object missing: {seg_key}"
            assert _read_key(seg_key), f"referenced object is empty: {seg_key}"
            referenced.append(seg_key)
    return build_id, referenced


def _walk_keys(prefix):
    """Every object key under prefix, recursively, via the storage API."""
    try:
        dirs, files = default_storage.listdir(prefix)
    except FileNotFoundError:
        return []
    keys = [f"{prefix}/{name}" for name in files]
    for sub in dirs:
        keys.extend(_walk_keys(f"{prefix}/{sub}"))
    return keys


def _assert_only_versioned_keys(clip, allowed_build_ids=None):
    """Every key in storage is hls/<clip>/<build12>/<file> — no rename debris.

    A collision-suffixed key (<root>_<7 chars><ext>) ANYWHERE is a
    regression: under the versioned policy the backend's rename path is
    unreachable, so one such key means the task published against renamed
    objects again.
    """
    for key in _walk_keys("hls"):
        m = _BUILD_KEY_RE.match(key)
        assert m, f"key outside the versioned layout: {key!r}"
        assert m.group(1) == str(clip.id), (
            f"key from another clip in this test's storage: {key!r}"
        )
        assert not _COLLISION_SUFFIX_RE.match(m.group(3)), (
            f"collision-renamed key (the P1 bug's fingerprint): {key!r}"
        )
        if allowed_build_ids is not None:
            assert m.group(2) in allowed_build_ids, (
                f"key from an unexpected build: {key!r}"
            )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_happy_path_publishes_verified_manifest(clip_factory, hls_task, eager_celery):
    clip = clip_factory()

    tasks.process_audio_to_hls.run(str(clip.id))

    clip.refresh_from_db()
    assert clip.status == "ready"
    build_id, _referenced = _assert_manifest_fully_resolves(clip)
    # The pointer names something that actually exists, and the manifest it
    # names was verified against STORAGE above (every referenced segment
    # present and non-empty) — not against the local ffmpeg scratch dir.
    assert default_storage.exists(clip.hls_playlist_url)
    # Nothing anywhere in storage carries a collision suffix: the versioned
    # prefix made the backend's rename path unreachable, and the whole tree
    # is this one attempt's build dir.
    _assert_only_versioned_keys(clip, allowed_build_ids={build_id})


def test_retry_after_partial_upload_is_safe(
    clip_factory, hls_task, eager_celery, monkeypatch, fs_storage
):
    clip = clip_factory()
    real_storage = tasks.default_storage

    # ffmpeg emits three files for a 1s clip (master.m3u8, index.m3u8, one
    # segment); blowing up on the 2nd save leaves a genuinely partial build.
    sabotaged = _FailingSaveStorage(_local_storage(fs_storage), fail_on_nth=2)
    monkeypatch.setattr(tasks, "default_storage", sabotaged)

    with pytest.raises(Exception):
        # eager mode re-raises the retried exception; the class celery wraps
        # it into is an implementation detail, so do not overpin it.
        tasks.process_audio_to_hls.run(str(clip.id))

    assert sabotaged.raised and sabotaged.save_calls >= 2, (
        "the sabotage did not actually interrupt the upload loop"
    )
    clip.refresh_from_db()
    assert clip.status != "ready"
    assert not clip.hls_playlist_url, "a failed attempt must never move the pointer"

    # Retry cleanly on a fresh attempt (fresh build id, fresh prefix).
    monkeypatch.setattr(tasks, "default_storage", real_storage)
    tasks.process_audio_to_hls.run(str(clip.id))

    clip.refresh_from_db()
    assert clip.status == "ready"
    build_id, _referenced = _assert_manifest_fully_resolves(clip)
    # No key anywhere is collision-renamed despite the failed first attempt —
    # the whole point of the versioned prefix.
    _assert_only_versioned_keys(clip)
    # The first attempt's partial build dir either still exists (its purge,
    # if any, carries a 900s countdown and eager mode may or may not fire it
    # inline) or was cleaned up by the failed attempt itself. Both are
    # contract-valid; the one thing that must never happen — a partial build
    # masquerading as the published one — is already pinned by the pointer
    # and manifest assertions above. Deliberately NOT asserted: that it was
    # purged.


def test_failed_attempt_does_not_clobber_good_pointer(
    clip_factory, hls_task, eager_celery, monkeypatch, fs_storage
):
    clip = clip_factory()
    tasks.process_audio_to_hls.run(str(clip.id))  # build A goes live
    clip.refresh_from_db()
    assert clip.status == "ready"
    url_a = clip.hls_playlist_url
    build_a, keys_a = _assert_manifest_fully_resolves(clip)

    # Attempt 2: everything uploads, but verification sees the objects as
    # missing (the same S3-hiccup class from the write side).
    real_storage = tasks.default_storage
    monkeypatch.setattr(
        tasks, "default_storage", _MissingObjectsStorage(_local_storage(fs_storage))
    )
    with pytest.raises(Exception):
        tasks.process_audio_to_hls.run(str(clip.id))

    monkeypatch.setattr(tasks, "default_storage", real_storage)
    clip.refresh_from_db()
    assert clip.hls_playlist_url == url_a, (
        "a failed attempt must never move the pointer — build A stays live"
    )
    assert clip.status == "ready"
    for key in keys_a:
        assert default_storage.exists(key), f"live build object disappeared: {key}"
    # And the failed attempt left no debris mixed into the live tree.
    _assert_only_versioned_keys(clip, allowed_build_ids={build_a})


def test_task_is_safe_to_run_twice(clip_factory, hls_task, eager_celery):
    clip = clip_factory()

    tasks.process_audio_to_hls.run(str(clip.id))
    clip.refresh_from_db()
    assert clip.status == "ready"
    url_a = clip.hls_playlist_url

    tasks.process_audio_to_hls.run(str(clip.id))

    clip.refresh_from_db()
    assert clip.status == "ready"
    assert clip.hls_playlist_url != url_a, (
        "a second run must publish a fresh build, not overwrite in place"
    )
    build_id, _referenced = _assert_manifest_fully_resolves(clip)
    assert default_storage.exists(clip.hls_playlist_url)
    # Build A may already be purged (eager mode can run the countdown task
    # inline) or still be inside its 900s grace window; both are valid. What
    # must hold regardless: the final pointer resolves fully, and nothing
    # anywhere is collision-renamed. No exception may escape the purge
    # scheduling path — a raise here IS this test failing.
    _assert_only_versioned_keys(clip)


def test_purge_superseded_hls_build_deletes_only_stale(clip_factory, fs_storage):
    clip = clip_factory()
    build_a, build_b = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    names = ("master.m3u8", "index.m3u8", "index0.ts")
    keys_a = [f"hls/{clip.id}/{build_a}/{name}" for name in names]
    keys_b = [f"hls/{clip.id}/{build_b}/{name}" for name in names]
    for key in keys_a + keys_b:
        default_storage.save(key, ContentFile(b"x"))

    clip.hls_playlist_url = f"hls/{clip.id}/{build_b}/master.m3u8"
    clip.save(update_fields=["hls_playlist_url"])

    tasks.purge_superseded_hls_build.run(str(clip.id), build_a)

    for key in keys_a:
        assert not default_storage.exists(key), f"stale build survived: {key}"
    for key in keys_b:
        assert default_storage.exists(key), f"live build deleted: {key}"

    # Purging the CURRENT build must delete nothing: the pointer proves the
    # build is live (e.g. the pointer moved back between scheduling and
    # firing).
    result = tasks.purge_superseded_hls_build.run(str(clip.id), build_b)
    assert result.get("aborted") == "pointer_unchanged"
    for key in keys_b:
        assert default_storage.exists(key)

    # Legacy in-place pointer: never delete on a guess — the pre-versioning
    # layout is owned by clip-level cleanup, and a purge against it would
    # break clients mid-migration.
    legacy_key = f"hls/{clip.id}/master.m3u8"
    default_storage.save(legacy_key, ContentFile(b"x"))
    clip.hls_playlist_url = legacy_key
    clip.save(update_fields=["hls_playlist_url"])
    result = tasks.purge_superseded_hls_build.run(str(clip.id), build_a)
    assert result.get("aborted") == "pointer_not_versioned"
    assert default_storage.exists(legacy_key)

    # Gone clip: the post_delete signal owns cleanup; the purge must be a
    # no-op, not a delete of whatever sits at the (now unowned) prefix.
    result = tasks.purge_superseded_hls_build.run(str(uuid.uuid4()), build_b)
    assert result.get("aborted") == "clip_missing"


def test_post_delete_removes_every_build_of_the_clip(clip_factory, fs_storage):
    """Clip deletion takes EVERY build with the row, not just the pointer's.

    Two half-fixes had to meet here: hls_clip_prefix (signals.py) must derive
    the CLIP-level prefix from a versioned pointer — the old
    rsplit('/', 1) stopped at the deepest directory — AND _delete_s3_prefix
    must RECURSE, because a one-level listing of the clip prefix sees build
    DIRECTORIES, not files, and would strand every build in the bucket. This
    pins the combination, plus the blast radius (another clip's keys
    survive).
    """
    clip = clip_factory()
    other_clip_id = uuid.uuid4()
    build_a, build_b = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    doomed = (
        [f"hls/{clip.id}/{build_a}/{n}" for n in ("master.m3u8", "index.m3u8", "index0.ts")]
        + [f"hls/{clip.id}/{build_b}/master.m3u8"]
        + [f"hls/{clip.id}/master.m3u8"]  # stray legacy-era flat file
    )
    survivor = f"hls/{other_clip_id}/master.m3u8"
    for key in doomed + [survivor]:
        default_storage.save(key, ContentFile(b"x"))

    clip.hls_playlist_url = f"hls/{clip.id}/{build_b}/master.m3u8"
    clip.save(update_fields=["hls_playlist_url"])

    clip.delete()

    for key in doomed:
        assert not default_storage.exists(key), (
            f"build object survived clip deletion: {key}"
        )
    assert default_storage.exists(survivor), "another clip's keys were deleted"


def test_retry_decorator_uses_jitter():
    """P2: the autoretry config must jitter.

    retry_jitter=False pins every worker's retry to the same instant, so one
    blip of a SHARED dependency (S3, Postgres) turns the whole failed cohort
    into a synchronized thundering herd against the dependency that just
    failed. Celery copies decorator options onto the task object and the
    autoretry wrapper reads retry_jitter from there when it schedules the
    retry, so introspection IS the contract.
    """
    task = tasks.process_audio_to_hls
    assert task.max_retries == 3
    try:
        jitter = task.retry_jitter
    except AttributeError:  # pragma: no cover - celery-version dependent
        pytest.skip("this celery build does not expose retry_jitter on the task")
    assert jitter is True
