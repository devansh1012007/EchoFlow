import requests
import logging

logger = logging.getLogger(__name__)


def fetch_audio(limit=10):
    """Fetch public audio file URLs from Wikimedia Commons.

    Returns a list of dicts: {'url', 'title', 'page_url', 'mime'}
    """
    API = 'https://commons.wikimedia.org/w/api.php'
    params = {
        'action': 'query',
        'format': 'json',
        'list': 'allimages',
        'ailimit': str(limit),
        'aiprop': 'url|mime'
    }

    try:
        r = requests.get(API, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        items = data.get('query', {}).get('allimages', [])
    except Exception as e:
        logger.exception('Wikimedia fetch failed: %s', e)
        return []

    results = []
    for it in items:
        mime = it.get('mime', '')
        if not mime.startswith('audio'):
            continue
        url = it.get('url')
        name = it.get('name')
        page = f"https://commons.wikimedia.org/wiki/File:{name}"
        results.append({'url': url, 'title': name, 'page_url': page, 'mime': mime})

    return results
