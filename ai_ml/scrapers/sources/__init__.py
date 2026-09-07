"""Source connectors for the scraper.

Each source module should provide a `fetch_audio(limit)` function that returns
an iterable of dicts with keys: `url`, `title`, `page_url`, and optionally `license` and `id`.
"""

from . import wikimedia_commons, internet_archive, freesound, kaggle

SOURCES = {
    'wikimedia': wikimedia_commons,
    'internet_archive': internet_archive,
    'freesound': freesound,
    'kaggle': kaggle,
}
