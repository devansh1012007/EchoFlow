import requests
import logging

logger = logging.getLogger(__name__)


def fetch_audio(limit=10):
    """Search Internet Archive for audio items and return direct file URLs.

    Returns list of dicts: {'url','title','page_url','id'}
    """
    SEARCH = 'https://archive.org/advancedsearch.php'
    params = {
        'q': 'mediatype:(audio)',
        'fl': 'identifier,title',
        'rows': str(limit),
        'page': '1',
        'output': 'json'
    }

    try:
        r = requests.get(SEARCH, params=params, timeout=10)
        r.raise_for_status()
        docs = r.json().get('response', {}).get('docs', [])
    except Exception as e:
        logger.exception('Internet Archive search failed: %s', e)
        return []

    results = []
    for d in docs:
        identifier = d.get('identifier')
        title = d.get('title') or identifier
        meta_url = f'https://archive.org/metadata/{identifier}'
        try:
            m = requests.get(meta_url, timeout=10).json()
            files = m.get('files', [])
            # prefer MP3/WAV/OGG
            for f in files:
                fmt = (f.get('format') or '').lower()
                name = f.get('name')
                if not name:
                    continue
                if any(x in fmt for x in ('mp3', 'vbr mp3', 'wav', 'ogg', 'flac')):
                    url = f'https://archive.org/download/{identifier}/{name}'
                    page = f'https://archive.org/details/{identifier}'
                    results.append({'url': url, 'title': title, 'page_url': page, 'id': identifier})
                    break
        except Exception:
            continue

    return results
