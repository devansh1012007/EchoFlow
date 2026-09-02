# Scraping Pipeline

## End-to-End Flow

```
MANAGEMENT COMMAND / CELERY TASK
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  SOURCE CONNECTOR (fetch_audio)                             │
│  ├── wikimedia_commons.fetch_audio()                        │
│  ├── internet_archive.fetch_audio()                         │
│  ├── freesound.fetch_audio()                                │
│  └── kaggle.fetch_audio()                                   │
│                                                             │
│  Returns: [{url, title, page_url, license, id}, ...]        │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  LICENSE FILTER (Management Command only)                   │
│  ├── Check license against SCRAPER_ALLOW_LICENSES           │
│  ├── Skip if not allowed                                    │
│  ├── Warn if UNKNOWN                                        │
│  └── (Celery task skips this — assumes pre-filtered)        │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  DOWNLOADER (download_audio)                                │
│  ├── robots.txt check                                       │
│  ├── Rate limit per host (30/min default)                   │
│  ├── Stream download with 50MB limit                        │
│  ├── Content-Type validation (audio/*)                      │
│  └── Returns: local temp file path                          │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  NORMALIZER (normalize_and_trim)                            │
│  ├── pydub: load any format                                 │
│  ├── Trim to max_seconds (default 300s)                     │
│   │   stereo 44.1kHz MP3 @ 192kbps                         │
│  └── Returns: local temp MP3 path                           │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  UPLOADER (save_clip)                                       │
│  ├── Generate path: audio_scraper/{source}/{YYYY/MM/DD}/    │
│  ├── UUID filename                                          │
│  ├── Upload to S3 via default_storage                       │
│  ├── Create AudioClip with provenance metadata              │
│  │   imported_via_scraper=True                              │
│  │   source_name, source_url, license, attribution          │
│  │   original_source_id                                     │
│  └── Returns: AudioClip instance                            │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  AI PROCESSING (process_audio_to_hls.delay)                 │
│  └── Same pipeline as user uploads                          │
└─────────────────────────────────────────────────────────────┘
```

---

## Management Command (`scrape_audio.py`)

```python
def handle(self, *args, **options):
    source = options['source']
    limit = options['limit']
    clip_length = options['clip_length']
    
    module = SOURCES.get(source)
    items = module.fetch_audio(limit=limit)
    
    for item in items:
        # License check (ONLY in management command)
        lic_raw = item.get('license')
        allowed = [s.upper() for s in settings.SCRAPER_ALLOW_LICENSES]
        if lic_upper and lic_upper != 'UNKNOWN' and not any(a in lic_upper for a in allowed):
            continue  # Skip
        
        # Download
        if url.startswith('file://'):
            local_input = url[7:]  # strip file://
        else:
            local_input = downloader.download_audio(url)
        
        # Normalize
        tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3').name
        normalizer.normalize_and_trim(local_input, tmp_out, max_seconds=clip_length)
        
        # Upload + Create AudioClip
        clip = uploader.save_clip(
            user=scraper_user,
            title=title,
            source_name=source,
            source_url=page_url,
            license=license,
            attribution_text=page_url,
            local_file_path=tmp_out,
            original_source_id=original_id,
        )
        
        # Trigger AI pipeline
        process_audio_to_hls.delay(str(clip.id))
```

---

## Celery Task (`tasks.py:scrape_and_import`)

```python
@shared_task
def scrape_and_import(source_name, limit=5, clip_length=300):
    module = SOURCES.get(source_name)
    items = module.fetch_audio(limit=limit)
    
    for item in items:
        # NO license check here (assumes pre-filtered or manual)
        # ... same download/normalize/upload flow ...
        
        clip = uploader.save_clip(...)
        process_audio_to_hls.delay(str(clip.id))
```

**Key difference:** Management command does license filtering; Celery task does not.

---

## Scraper User

```python
User = get_user_model()
user = User.objects.filter(is_superuser=True).first()
if not user:
    user, created = User.objects.get_or_create(username='scraper', defaults={'is_active': False})
    if created:
        user.set_unusable_password()
        user.save()
```

- All scraped clips attributed to this user
- `is_active=False` — not a real login account

---

## Provenance Metadata

**Stored on AudioClip:**
| Field | Source |
|-------|--------|
| `source_name` | `wikimedia`, `internet_archive`, `freesound`, `kaggle` |
| `source_url` | Original page URL (Wikimedia file page, IA details, Freesound page) |
| `license` | License string from source |
| `attribution_text` | Same as source_url (for display) |
| `imported_via_scraper` | `True` |
| `original_source_id` | Source-specific ID (Wikimedia filename, IA identifier, Freesound ID) |

---

## Error Handling

### Per-Item Try/Catch
```python
try:
    # download → normalize → upload → create clip → enqueue AI
except Exception as e:
    logger.exception('Import failed for %s: %s', url, e)
    self.stdout.write(self.style.ERROR(f'Failed to import {url}: {e}'))
finally:
    # Cleanup temp files
    for p in (local_input, tmp_out):
        if p and os.path.exists(p) and not p.startswith(settings.MEDIA_ROOT):
            os.remove(p)
```

### Celery Task Retries
```python
@shared_task(bind=True, max_retries=3, autoretry_for=RETRYABLE_ERRORS, ...)
def scrape_and_import(self, source_name, limit=5, clip_length=300):
    # Transient errors (network, DB) → retry
    # Permanent errors (invalid license, corrupt file) → log, continue
```

---

## Cleanup

### Temp Files
```python
finally:
    for p in (local_input, tmp_out):
        try:
            if p and os.path.exists(p) and not p.startswith(settings.MEDIA_ROOT):
                os.remove(p)
        except Exception:
            pass
```

**Safety check:** `not p.startswith(settings.MEDIA_ROOT)` prevents deleting S3-backed files.

---

## Running

### Management Command
```bash
# Wikimedia, 3 clips, 30s each
python manage.py scrape_audio --source=wikimedia --limit=3 --clip-length=30

# Internet Archive, 5 clips, default 300s
python manage.py scrape_audio --source=internet_archive --limit=5

# Freesound (needs API key)
python manage.py scrape_audio --source=freesound --limit=5

# Kaggle (needs local path)
python manage.py scrape_audio --source=kaggle --limit=5
```

### Celery Task
```python
from backend.app.tasks import scrape_and_import
scrape_and_import.delay('internet_archive', limit=10, clip_length=60)
```

### Scheduled (Not Configured)
```python
# Could add to CELERY_BEAT_SCHEDULE:
'daily-scraper-wikimedia': {
    'task': 'backend.app.tasks.scrape_and_import',
    'schedule': crontab(hour=3, minute=0),
    'args': ('wikimedia', 20, 300),
},
```

---

## Monitoring

### Logs
```bash
docker compose logs -f celery | grep -i scrap
# Or management command output directly
```

### Metrics (Not Implemented)
- Clips imported per source
- License distribution
- Failure rate per source
- Processing time

---

## Extending

### New Source
1. `backend/app/scrapers/sources/newsource.py` with `fetch_audio(limit)`
2. Register in `scrapers/sources/__init__.py`
3. Add API key to settings if needed
4. Test: `python manage.py scrape_audio --source=newsource --limit=1`

### Custom Normalization
```python
# Override in source module
def normalize_and_trim(in_path, out_path, max_seconds=300):
    # Custom logic
    ...
```

---

*Source: `backend/app/management/commands/scrape_audio.py`, `backend/app/tasks.py:710-796`, `backend/app/scrapers/`*