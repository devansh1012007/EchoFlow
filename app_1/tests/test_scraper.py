import os
import tempfile
from django.test import TestCase
from django.conf import settings
from django.contrib.auth import get_user_model

from app_1.scrapers import normalizer, uploader

from pydub.generators import Sine
from pydub import AudioSegment


class ScraperUnitTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='testuser')

    def _make_sample_wav(self, duration_ms=3000):
        seg = Sine(440).to_audio_segment(duration=duration_ms)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
        seg.export(tmp.name, format='wav')
        return tmp.name

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
            # ensure file exists on disk
            self.assertTrue(os.path.exists(clip.original_file.path))
        finally:
            for p in (inp, out):
                try:
                    os.remove(p)
                except Exception:
                    pass
