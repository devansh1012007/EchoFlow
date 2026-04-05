# EchoFlow
TikTok for your ears. Dive into short audio clips—comedy roasts, song snippets, science bites, quotes, and motivation—while you walk, work, or chill. Auto-plays hands-free, with earphone skips for effortless vibes. No screen required, just pure audio flow.

## Scraper

A simple audio scraper was added to import short, openly-licensed audio clips into the project.

- Management command: `python manage.py scrape_audio --source=wikimedia --limit=3 --clip-length=30`
- Sources supported: `wikimedia`, `internet_archive`, `freesound` (requires API key), `kaggle` (local dataset path).
- Files are saved under `media/audio_scraper/{source}/YYYY/MM/DD/` and `AudioClip` records are created; ingestion continues via existing `process_audio_to_hls` Celery task.

Prerequisites:

- `ffmpeg` must be installed for audio normalization and HLS creation.
- Optional: set `FREESOUND_API_KEY` in environment to enable Freesound.

