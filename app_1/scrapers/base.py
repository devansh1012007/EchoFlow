import time
import logging
from urllib.parse import urlparse
from urllib import robotparser

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class RobotsTxtChecker:
    def __init__(self):
        self.parsers = {}

    def allowed(self, url, user_agent=None):
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        rp = self.parsers.get(base)
        if not rp:
            rp = robotparser.RobotFileParser()
            rp.set_url(base + "/robots.txt")
            try:
                rp.read()
            except Exception:
                # If robots.txt cannot be read, default to permissive
                logger.debug("Could not read robots.txt for %s", base)
            self.parsers[base] = rp
        ua = user_agent or getattr(settings, 'SCRAPER_USER_AGENT', '*')
        try:
            return rp.can_fetch(ua, url)
        except Exception:
            return True


class RateLimiter:
    def __init__(self, max_per_min=30):
        self.max_per_min = max_per_min or 30
        self.min_interval = 60.0 / float(self.max_per_min)
        self.last_access = {}

    def wait(self, url):
        host = urlparse(url).netloc
        last = self.last_access.get(host)
        if last:
            elapsed = time.time() - last
            if elapsed < self.min_interval:
                to_sleep = self.min_interval - elapsed
                logger.debug("Sleeping %.2fs to respect rate limit for %s", to_sleep, host)
                time.sleep(to_sleep)
        self.last_access[host] = time.time()


def get_session():
    s = requests.Session()
    ua = getattr(settings, 'SCRAPER_USER_AGENT', None)
    contact = getattr(settings, 'SCRAPER_CONTACT_EMAIL', None)
    if ua:
        header = ua
    else:
        header = f"EchoFlowScraper/1.0 (+{contact or 'contact@example.com'})"
    s.headers.update({'User-Agent': header})
    return s
