import os
import tempfile
import logging

from django.core.management.base import BaseCommand
from django.conf import settings
from django.contrib.auth import get_user_model

from app_1.scrapers import downloader, normalizer, uploader
from app_1.scrapers.sources import SOURCES
from app_1.tasks import process_audio_to_hls

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Scrape audio from public sources and import into AudioClip'

    def add_arguments(self, parser):
        parser.add_argument('--source', choices=list(SOURCES.keys()), required=True)
        parser.add_argument('--limit', type=int, default=5)
        parser.add_argument('--clip-length', type=int, default=getattr(settings, 'SCRAPER_DEFAULT_CLIP_SECONDS', 300))

    def handle(self, *args, **options):
        source = options['source']
        limit = options['limit']
        clip_length = options['clip_length']

        module = SOURCES.get(source)
        if not module:
            self.stdout.write(self.style.ERROR('Unknown source'))
            return

        User = get_user_model()
        user = User.objects.filter(is_superuser=True).first()
        if not user:
            user, created = User.objects.get_or_create(username='scraper', defaults={'is_active': False})
            if created:
                user.set_unusable_password()
                user.save()

        items = module.fetch_audio(limit=limit)
        self.stdout.write(f'Found {len(items)} items from {source}')

        for item in items:
            url = item.get('url')
            title = item.get('title') or 'scraped audio'
            page = item.get('page_url') or ''
            lic_raw = item.get('license')
            license = lic_raw or 'unknown'
            # License enforcement: skip if license is present and not in allowed list
            allowed = [s.upper() for s in getattr(settings, 'SCRAPER_ALLOW_LICENSES', [])]
            lic_upper = str(lic_raw).upper() if lic_raw else ''
            if lic_upper and lic_upper != 'UNKNOWN' and not any(a in lic_upper for a in allowed):
                self.stdout.write(self.style.WARNING(f'Skipping {url}: license "{lic_raw}" not allowed'))
                continue
            original_id = item.get('id')

            local_input = None
            tmp_out = None
            try:
                if not url:
                    raise RuntimeError('No URL for item')

                # Local file paths (kaggle local ingestion) are returned as file://
                if url.startswith('file://'):
                    local_input = url[len('file://'):]
                else:
                    local_input = downloader.download_audio(url)

                tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3').name
                normalizer.normalize_and_trim(local_input, tmp_out, max_seconds=clip_length, target_format='mp3')

                clip = uploader.save_clip(
                    user=user,
                    title=title,
                    source_name=source,
                    source_url=page,
                    license=license,
                    attribution_text=page,
                    local_file_path=tmp_out,
                    original_source_id=original_id,
                )

                process_audio_to_hls.delay(str(clip.id))
                self.stdout.write(self.style.SUCCESS(f'Imported clip {clip.id}'))

            except Exception as e:
                logger.exception('Import failed for %s: %s', url, e)
                self.stdout.write(self.style.ERROR(f'Failed to import {url}: {e}'))

            finally:
                for p in (local_input, tmp_out):
                    try:
                        if p and os.path.exists(p) and not p.startswith(settings.MEDIA_ROOT):
                            os.remove(p)
                    except Exception:
                        pass
