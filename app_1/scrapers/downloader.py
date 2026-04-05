import os
import tempfile
import logging
from django.conf import settings
from .base import get_session, RateLimiter, RobotsTxtChecker

logger = logging.getLogger(__name__)


def download_audio(url, max_bytes=50_000_000, timeout=30):
    """Download an audio file from `url` to a temporary file.

    Returns the path to the downloaded temporary file.
    Raises RuntimeError on non-audio content or other failures.
    """
    robots = RobotsTxtChecker()
    if not robots.allowed(url):
        raise RuntimeError(f"Blocked by robots.txt: {url}")

    limiter = RateLimiter(getattr(settings, 'SCRAPER_MAX_DOWNLOADS_PER_MIN', 30))
    limiter.wait(url)

    session = get_session()
    resp = session.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()

    content_type = resp.headers.get('Content-Type', '')
    if 'audio' not in content_type and not url.lower().endswith(('.mp3', '.wav', '.ogg', '.flac', '.aac')):
        raise RuntimeError(f"URL does not appear to be audio (Content-Type: {content_type})")

    content_length = resp.headers.get('Content-Length')
    if content_length and int(content_length) > max_bytes:
        raise RuntimeError(f"Remote file too large: {content_length} bytes")

    # Choose suffix from content type or URL
    suffix = os.path.splitext(url)[1] or ''
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    total = 0
    with tmp as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                total += len(chunk)
                if total > max_bytes:
                    tmp_name = tmp.name
                    f.close()
                    os.unlink(tmp_name)
                    raise RuntimeError("Downloaded file exceeds maximum allowed size")
                f.write(chunk)

    return tmp.name
