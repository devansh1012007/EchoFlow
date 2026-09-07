import os
import shutil
import tempfile
import unittest
from django.test import TestCase
from django.conf import settings
from django.contrib.auth import get_user_model

from ai_ml.scrapers import normalizer, uploader

from pydub.generators import Sine
from pydub import AudioSegment


# Check if ffmpeg is available. In Docker it's installed in the `base`
# stage; in bare-metal dev it may be missing. Use a conditional skip so
# the tests run when ffmpeg IS present (i.e., in Docker CI).
_ffmpeg_available = shutil.which('ffmpeg') is not None


class ScraperUnitTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='testuser')

    def _make_sample_wav(self, duration_ms=3000):
        seg = Sine(440).to_audio_segment(duration=duration_ms)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
        seg.export(tmp.name, format='wav')
        return tmp.name

    @unittest.skipUnless(_ffmpeg_available, "Requires ffmpeg on PATH (installed in Docker image)")
    def test_normalizer_trims_to_max_seconds(self):
        inp = self._make_sample_wav(duration_ms=5000)
        out = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3').name
        try:
            normalizer.normalize_and_trim(inp, out, max_seconds=2, target_format='mp3')
            exported = AudioSegment.from_file(out)
            self.assertLessEqual(exported.duration_seconds, 2.1)
        finally:
            for p in (inp, out):
                try:
                    os.remove(p)
                except Exception:
                    pass

    @unittest.skipUnless(_ffmpeg_available, "Requires ffmpeg on PATH (installed in Docker image)")
    def test_uploader_creates_audioclip(self):
        inp = self._make_sample_wav(duration_ms=1000)
        out = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3').name
        try:
            normalizer.normalize_and_trim(inp, out, max_seconds=5, target_format='mp3')
            clip = uploader.save_clip(
                user=self.user,
                title='unit test',
                source_name='unittest',
                source_url='http://example.com',
                license='CC0',
                attribution_text='test',
                local_file_path=out,
                original_source_id='unittest-1'
            )
            self.assertIsNotNone(clip.id)
            self.assertTrue(clip.original_file.name.startswith('audio_scraper/'))
            # ensure file was stored (works with local or S3/MinIO backends)
            from django.core.files.storage import default_storage
            self.assertTrue(default_storage.exists(clip.original_file.name))
        finally:
            for p in (inp, out):
                try:
                    os.remove(p)
                except Exception:
                    pass
