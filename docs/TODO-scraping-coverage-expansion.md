# Scraping Coverage Expansion — Master Plan

**Date:** 2026-09-07
**Status:** Planning — awaiting approval before any code changes
**Owns:** `backend/app/scrapers/sources/*`, `docs/EXPLAIN/scraping/01-sources.md`
**Source design doc:** `docs/EXPLAIN/scraping/01-sources.md` (the source of truth for current architecture)

---

## 1. Goal

Expand the scraping surface from **4 sources** (Wikimedia, Internet Archive, Freesound, Kaggle) to a **broad-net catalog** covering every conceivable form of audio content a user could plausibly want to hear:

- Short-form "reel/short" style clips (≤ 60 s)
- Full music tracks (every genre, every era)
- Full-length podcasts and audio dramas
- Full audiobooks and chapter-level narration
- Lectures, courses, and educational audio
- News, current affairs, short spoken clips
- Field recordings, ambient, SFX, ASMR, sleep, meditation
- Public-domain historical recordings (78s, cylinders, wax, early radio)
- Government / institutional / cultural-heritage audio
- Speech corpora and multilingual voice data
- Niche / quirky / long-tail demand (NASA, animal sounds, seismic, etc.)

The catalog should be **license-clean by default** (PD, CC0, CC-BY, CC-BY-SA, CC-BY-NC where explicitly allowed) and respect existing infrastructure:

- Robots.txt checker (`base.RobotsTxtChecker`)
- Per-host rate limiter (`base.RateLimiter`, 30/min default)
- Streaming downloader with `max_bytes` cap (`downloader.download_audio`, 50 MB default)
- License allow-list (`SCRAPER_ALLOW_LICENSES`, enforced in management command)
- `fetch_audio(limit=...) → [{url, title, page_url, license, id}]` connector contract
- `SOURCES` registry in `backend/app/scrapers/sources/__init__.py`

**Relevance is not the scraper's job.** Later recommendation / feed-ranking layers decide what a given user sees. The scraper's job is **breadth + license safety**.

---

## 2. Current State (audit)

| Source | Strengths | Gaps |
|---|---|---|
| Wikimedia Commons | Broad free media, audio MIME filter | No structured license field; manual review needed |
| Internet Archive | Massive (millions of items), license metadata | Quality and licensing vary widely; need collection filters |
| Freesound | High-quality SFX, field recordings, CC licenses, API | Preview-only without OAuth; music limited |
| Kaggle | Local datasets (AudioSet, FSD50K, etc.) | Manual, one-shot, no live API |

**Coverage gaps today:**

- No full audiobooks
- No podcast aggregator / episode-level discovery
- No radio / live audio
- No classical / historical PD music (Musopen, Prelinger, National Jukebox)
- No speech corpora / multilingual voice data
- No short-form "reel-style" CC music (Pixabay, YouTube Audio Library)
- No government / civic audio (C-SPAN, LOC, NASA)
- No aggregator layer (Openverse, Europeana, DPLA) — re-implementing per-source instead of leveraging existing index

---

## 3. Connector Taxonomy — All Candidate Sources

Sources are grouped by the kind of content they cover. Within each group, every entry is sized for the **same modular contract** (a single `fetch_audio(limit)` in a new file under `backend/app/scrapers/sources/`).

### A. Aggregators / Search Engines (highest leverage — one connector, many backends)

| Source | What it gives | License | Access | Tier |
|---|---|---|---|---|
| **Openverse** (`api.openverse.org`) | Cross-search of Wikimedia, Jamendo, Freesound, Free Music Archive, ccMixter, etc. Categories: `audiobook`, `music`, `news`, `podcast`, `pronunciation`, `sound_effect` | Open licenses only; filterable | Official REST API, no key required | **P0** |
| **Europeana** (`api.europeana.eu`) | European cultural heritage audio (museums, libraries, archives) | Mostly PD / CC | API with key | P1 |
| **DPLA (Digital Public Library of America)** (`api.dp.la`) | Aggregated US library/archive audio | Mixed; filter for open rights | API with key | P1 |

> **DECISION:** Openverse is the single highest-leverage connector — it bundles 6+ backends behind one license-clean API. Build first.

### B. Audiobooks & Spoken Word (long-form + chapter-level)

| Source | Scale | License | Access | Tier |
|---|---|---|---|---|
| **LibriVox** | 20k+ PD audiobooks (fiction, nonfiction, poetry, drama); multi-language | Public domain | Internet Archive hosting + chapter MP3s | **P0** |
| **Project Gutenberg Open Audiobook Collection** | AI + human narrations of PD books | Public domain | Bulk via IA | P1 |
| **Loyal Books** (formerly BooksShouldBeFree) | Thousands of PD titles | Public domain | Scrapable catalog | P2 |
| **AudioSilo Meta** | Open audiobook metadata (works, narrators, chapters) | CC0 metadata | DB dump | P2 (metadata enrichment) |

> **SECURITY:** LibriVox chapter URLs are the perfect unit for both full-book users and short-clip extractors. Verify license per item via IA metadata before importing.

### C. Music (full tracks, classical, experimental, podsafe)

| Source | Focus | License | Access | Tier |
|---|---|---|---|---|
| **Free Music Archive (FMA)** | Large CC music library (electronic, hip-hop, jazz, classical, experimental) | Filterable CC | Official API (key required for full) | **P0** |
| **Musopen** | Classical PD recordings + sheet music | Public domain | No official API; page scraping | P0 |
| **ccMixter** | Remixable CC music & stems | CC (filter carefully) | Open API + bulk dump | P1 |
| **Jamendo** | Independent artists; many CC tracks | CC (varies) | OAuth API; also indexed by Openverse | P1 |
| **YouTube Audio Library** | Royalty-free music & SFX (official) | YouTube free license | Official API (OAuth) | P1 |
| **Pixabay Music / Sound Effects** | Simple CC0-style tracks & SFX | Pixabay license (very permissive) | API (key required) | **P0** |
| **Bandcamp (CC-tagged)** | Indie music with CC releases | CC-BY-NC mostly | Page scraping of CC-tagged | P2 |
| **Smithsonian Folkways (CC tracks)** | World / folk music | Per-track CC | Page scraping | P2 |
| **Mixcloud (CC-tagged)** | DJ mixes | Per-track CC | Page scraping | P2 |

### D. Sound Effects, Field Recordings & Ambient (short clips, reels-style)

| Source | Focus | License | Access | Tier |
|---|---|---|---|---|
| **BBC Sound Effects** | 33k+ professional SFX & atmospheres | RemArc (non-commercial / research / educational) | Mirror on Internet Archive | **P0** (with license caveat) |
| **Pixabay Sound Effects** | Large free SFX set | Pixabay permissive | API-less scraping | **P0** |
| **ZapSplat (free tier)** | Professional SFX | Attribution required | API | P1 |
| **Mixkit** | Free SFX & music | Very permissive | API-less | P1 |
| **Lots of Sounds** | REST API for SFX | CC0 | Official API | P1 |
| **SoundJay** | Free SFX | Royalty-free | Page scraping | P2 |
| **SoundBible** | Simple SFX | PD / CC0 | Easy scraper | P2 |
| **ZapSplat / AudioMicro / Sonniss GDC bundles** | Annual free SFX dumps | CC0 | Bulk dumps (annual) | P2 |

> **SECURITY:** BBC SFX is non-commercial — must be **explicitly excluded from any commercial / revenue feature**. Mark with license tag `RemArc-NC` and gate behind a `SCRAPER_ALLOW_NC=true` env flag.

### E. Podcasts & Radio (long-form talk + short excerpts)

| Source | Focus | License / Access | Tier |
|---|---|---|---|
| **Podcast Index** | Open, independent podcast directory (hundreds of thousands of shows) | Varies; many CC / free. Full SQLite dump + API | **P0** |
| **Radio Browser** (`de1.api.radio-browser.info`) | Community directory of internet radio stations (live + archives) | Varies per station. Public REST API | P1 |
| **Podcast Addict open dataset** | Dump of public podcast feeds | Per-show | P2 |
| **Apple Podcasts RSS** | Generic RSS aggregator | Per-show | P2 (generic RSS scraper) |
| **NPR Podcasts** | Free podcast archive (StoryCorps, This American Life, Radiolab, etc.) | Per-show CC | P1 |
| **TED Talks Audio** | TED talk audio | CC-BY-NC-ND | P1 |
| **BBC Podcasts / Radio archive** | BBC Sounds archive | Mixed | P2 |

> **DECISION:** Podcast Index is the canonical podcast discovery layer. Build a generic RSS resolver once; Podcast Index is just one feed source feeding it.

### F. Speech Corpora & Datasets (diverse voices, languages, styles — training signals + short clips)

These are often bulk-downloadable and license-clean — fit the existing Kaggle-style local-path connector pattern with a Hugging Face dataset adapter:

| Source | License | Tier |
|---|---|---|
| **Mozilla Common Voice** (CC0, multilingual, huge) | CC0 | P1 |
| **LibriSpeech / LibriTTS / Libri-Light** (audiobook-derived) | CC-BY / PD | P1 |
| **VoxPopuli** (European Parliament speech) | CC0 | P2 |
| **GigaSpeech** (podcasts + YouTube + audiobooks) | Per-clip | P2 |
| **People's Speech**, **Emilia**, **VoxCeleb** | Per-dataset | P2 |
| **OpenSLR collections** | Per-collection | P1 |
| **ESC-50**, **UrbanSound8K**, **AudioSet** | Per-dataset (Kaggle / direct) | P1 |

> **DECISION:** Speech corpora enter via the existing **Kaggle-style local path** connector + a new **Hugging Face dataset** connector (`huggingface_dataset.py`) — re-use, don't duplicate the bulk-loading pattern.

### G. Historical / Institutional Archives

| Source | Focus | License | Tier |
|---|---|---|---|
| **Library of Congress National Jukebox** + American Memory | Pre-1923 PD music & spoken word | PD | **P0** |
| **Cylinder Preservation and Digitization Project** | Early cylinder recordings | PD | P1 |
| **National Library of Medicine** digital collections | Health / historical | PD / per-clip | P2 |
| **British Library Sounds** | UK audio archive | Per-clip — some CC, some PD | P1 |
| **National Recording Registry (LOC)** | Historically significant recordings | Mostly PD | P1 |
| **Old Time Radio archive (OTR)** | 1930s-60s radio dramas | Mostly PD | P1 |
| **Prelinger Archives (IA)** | Ephemeral films with audio | Mostly PD | P2 |

### H. Government / Public Domain Bulk

| Source | Focus | License | Tier |
|---|---|---|---|
| **C-SPAN Radio** | Political / civic | PD (US gov work) | **P0** |
| **NASA audio archive** | Mission audio, space sounds | PD (US gov) | **P0** |
| **USGS seismic audio** | Earthquake recordings | PD (US gov) | P1 |
| **GovInfo / Federal audio** | US government audio | PD | P1 |
| **Data.gov audio datasets** | Government audio | PD | P2 |
| **UN audio archive** | UN meetings, speeches | PD | P1 |

> **DECISION:** All US-gov audio is clean PD. Build a single `usgov_audio.py` connector that fans out across NASA, C-SPAN, USGS, GovInfo via their respective catalogs.

### I. Audio Drama / Fiction / Storytelling

| Source | License | Tier |
|---|---|---|
| **AudioDrama.net directory** (per-show) | Per-show | P2 |
| **BBC Radio Drama archive** | Mixed | P2 |
| **Star Ship Sofa / Hugo-winning audio fiction** | CC-BY-NC-ND | P2 |
| **Pseudopod / PodCastle / Escape Pod** | CC-BY-NC-ND | P2 |
| **NoSleep podcast** | Per-show | P2 |

### J. ASMR / Sleep / Meditation / Wellness

| Source | License | Tier |
|---|---|---|
| **YouTube ASMR channels (CC)** | Some CC-BY | P2 |
| **Insight Timer (free tier)** | Per-teacher | P2 |
| **Meditation podcasts (via Podcast Index)** | Per-show | P1 (covered by Podcast Index) |
| **White-noise / loop archives** | Varies | P2 |

### K. Music Sub-genres & Cultural / Regional

| Source | Focus | License | Tier |
|---|---|---|---|
| **Ethnomusicology archives (Smithsonian, etc.)** | World / folk music | PD / per-institution | P1 |
| **Cylinder / wax cylinder restorations (IA)** | Historical recordings | PD | P1 |
| **Hindi / Urdu / Tamil / etc. regional music archives** | Indian regional music | Various | P1 (important for IN audience) |
| **xeno-canto** | Wildlife recordings | CC | P2 |

### L. Niche / Quirky

| Source | Focus | License | Tier |
|---|---|---|---|
| **Animal / whale sound archives (ORCASound, Earth Species)** | Animal vocalizations | Various CC | P2 |
| **Pronunciation / language-learning audio** | TTS-style clips | Varies | P2 |
| **Newsreels & historical spoken word (LOC, IA)** | PD | P2 (covered by IA filters) |
| **Abandonware / vintage game audio rips** | Mostly **NOT** redistributable | Default OFF | **EXCLUDED** |

> **SECURITY:** Vintage game audio has near-zero legal-defensibility for redistribution. Explicitly excluded from the allow-list.

---

## 4. Tiered Rollout Plan

### P0 — First wave (broadest coverage, lowest effort)

Rationale: largest catalog delta, all license-clean, all have working APIs or page-scrapable patterns.

| # | Source | Why first |
|---|---|---|
| 1 | **Openverse** | One API → 6+ backends (Wikimedia, Jamendo, FMA, Freesound, ccMixter, etc.) with category filter (`audiobook`, `music`, `news`, `podcast`, `sound_effect`) |
| 2 | **LibriVox** | Closes the audiobook gap; PD; chapter-level URLs ideal for both full-book and short-clip |
| 3 | **Free Music Archive** | Bulk CC music; well-tagged |
| 4 | **Pixabay Music + SFX** | CC0-style permissive; covers reels-style short clips |
| 5 | **Podcast Index** | Unlocks podcast discovery at episode granularity |
| 6 | **BBC Sound Effects** (gated, NC-only) | 33k+ professional SFX |
| 7 | **Musopen** | Classical PD music — unique catalog |
| 8 | **Library of Congress National Jukebox** | Pre-1923 PD music — historical gold |
| 9 | **C-SPAN Radio** | US gov PD, civic / political niche |
| 10 | **NASA audio archive** | US gov PD, unique catalog |

### P1 — Second wave (fill gaps)

Europeana, DPLA, Jamendo, ccMixter, YouTube Audio Library, ZapSplat, Mixkit, Lots of Sounds, Common Voice, LibriSpeech, OpenSLR, Radio Browser, NPR / StoryCorps / TAL, Project Gutenberg Open Audiobook, British Library Sounds, Old Time Radio, Cylinder Preservation, Speech corpora, Smithsonian Folkways (CC tracks), Pixabay Music already covered above.

### P2 — Third wave (long-tail)

Bandcamp (CC-tagged), Mixcloud (CC), Loyal Books, AudioSilo Meta, ZapsSplat, SoundJay, SoundBible, NLM, BBC Radio Drama, Star Ship Sofa, Escape Pod, Pseudopod, NoSleep, Xeno-canto, Earth Species, Insight Timer, etc.

### EXCLUDED by default

Vintage game audio, Gaana/Saavn commercial catalogs (license risk), Spotify Podcasts (gated), anything behind login.

---

## 5. Connector Contract (no change)

Every new source module implements:

```python
# backend/app/scrapers/sources/<name>.py
def fetch_audio(limit: int = 10) -> list[dict]:
    """Return at most `limit` items.

    Each item must contain:
      - url:        direct audio URL or file:// path
      - title:      human-readable title
      - page_url:   canonical page for attribution / license verification
      - license:    string from SCRAPER_ALLOW_LICENSES or 'UNKNOWN'
      - id:         stable identifier within this source
    """
```

Register in `backend/app/scrapers/sources/__init__.py`:

```python
from . import openverse, librivox, free_music_archive, pixabay, \
              podcast_index, bbc_sound_effects, musopen, loc_national_jukebox, \
              cspan_radio, nasa_audio

SOURCES = {
    'wikimedia': wikimedia_commons,
    'internet_archive': internet_archive,
    'freesound': freesound,
    'kaggle': kaggle,
    'openverse': openverse,
    'librivox': librivox,
    'free_music_archive': free_music_archive,
    'pixabay': pixabay,
    'podcast_index': podcast_index,
    'bbc_sound_effects': bbc_sound_effects,
    'musopen': musopen,
    'loc_national_jukebox': loc_national_jukebox,
    'cspan_radio': cspan_radio,
    'nasa_audio': nasa_audio,
}
```

---

## 6. Shared Infrastructure (additions needed once)

### 6.1 New `HuggingFaceDatasetConnector` (`huggingface_dataset.py`)

Bulk-loads speech corpora and audio datasets from Hugging Face Hub. Reuses the same Kaggle-style local-path pattern but points at a downloaded snapshot. Caches to `SCRAPER_HF_CACHE_DIR`.

> **DECISION:** Speech corpora use the existing Kaggle-style local-path pattern + HF dataset adapter — don't fork a new bulk-loader.

### 6.2 New `NCGate` helper in `base.py`

```python
NC_LICENSES = {'RemArc-NC', 'CC-BY-NC', 'CC-BY-NC-SA', 'CC-BY-NC-ND'}

def is_noncommercial(license_str: str) -> bool:
    return any(nc in (license_str or '').upper() for nc in NC_LICENSES)
```

If `SCRAPER_ALLOW_NC` is unset, the management command **skips** any item whose license matches. BBC SFX and other NC-only sources are silently dropped by default — gated behind `SCRAPER_ALLOW_NC=true`.

> **SECURITY:** Default-deny NC content. Operators must opt in explicitly. Logged as a WARNING on every skip.

### 6.3 New `PodcastRssResolver` helper in `base.py`

Generic RSS / Atom feed parser for podcast episodes. Used by Podcast Index connector and any future generic RSS source (TED Talks, NPR, BBC podcasts).

```python
def resolve_podcast_rss(feed_url: str, limit: int = 10) -> list[dict]:
    """Parse an RSS feed and yield episode items with audio enclosures."""
```

### 6.4 No changes to license-enforcement logic

The existing `SCRAPER_ALLOW_LICENSES` check in the management command stays the source of truth. New connectors surface `license` on every item; enforcement is centralized.

---

## 7. Per-Source Implementation Skeleton (P0 examples)

### 7.1 `openverse.py`

```python
SEARCH = 'https://api.openverse.org/v1/audio/'

def fetch_audio(limit=10):
    params = {
        'q': '',                          # blank = browse
        'page_size': min(limit, 100),
        'license': 'by,sa,cc0',           # CC-BY, CC-BY-SA, CC0 only — exclude NC
    }
    # Authorization header if OPENVERSE_API_KEY set
    resp = get_session().get(SEARCH, params=params, timeout=30)
    resp.raise_for_status()
    results = resp.json().get('results', [])
    return [{
        'url': r['url'],
        'title': r['title'],
        'page_url': r['foreign_landing_url'] or r['url'],
        'license': r.get('license', 'UNKNOWN').upper(),
        'id': r.get('id'),
    } for r in results[:limit]]
```

### 7.2 `librivox.py`

LibriVox catalog lives on Internet Archive. Use the existing IA catalog API with `collection:librivox`:

```python
SEARCH = 'https://archive.org/advancedsearch.php'
params = {
    'q': 'collection:librivox AND mediatype:(audio)',
    'fl': 'identifier,title,licenseurl',
    'rows': str(limit),
    'output': 'json',
}
# Then for each identifier, fetch /metadata/<id> and pick the smallest MP3
# (chapter-level preferred for clipping).
```

### 7.3 `free_music_archive.py`

```python
API = 'https://freemusicarchive.org/api/v2/tracks'
# FMA API v2 deprecated/limited — fallback to public catalog scraping
# via /genre/<genre>/?page=N with CC license tags.
```

> **HACK:** FMA's API v2 has been unreliable. Fall back to genre-page scraping with explicit license-tag selectors. Track as `HACK`; document in module docstring.

### 7.4 `podcast_index.py`

```python
API = 'https://api.podcastindex.org/api/1.0/'
HEADERS = {
    'X-Auth-Key': settings.PODCAST_INDEX_API_KEY,
    'X-Auth-Date': str(int(time.time())),
    'User-Agent': 'EchoFlowScraper/1.0',
}
# /search?term=...  →  list of feeds
# For each feed, resolve RSS via PodcastRssResolver
```

### 7.5 `bbc_sound_effects.py`

Mirror lives on Internet Archive as `bbc-sound-archive` / `bbc-sound-effects` collection. Use existing IA scraper with explicit `collection:` filter; mark all items with license `RemArc-NC` and rely on `SCRAPER_ALLOW_NC` gate.

### 7.6 `musopen.py`, `loc_national_jukebox.py`, `cspan_radio.py`, `nasa_audio.py`

All page-scrapers with manual license-tag selectors. PD-clean — no NC gate needed.

---

## 8. Test Strategy

For each new connector:

| Test | Purpose |
|---|---|
| `test_<source>_returns_list_of_dicts` | Contract: returns `[{url, title, page_url, license, id}, ...]` |
| `test_<source>_respects_limit` | Returns ≤ `limit` items |
| `test_<source>_license_field_populated` | Every item has a `license` key (or 'UNKNOWN') |
| `test_<source>_robots_txt_respected` | Mocked robots.txt blocks fetch |
| `test_<source>_rate_limited` | Verifies per-host throttle on repeat invocations |
| `test_<source>_nc_gate_skipped_by_default` | BBC SFX items skipped unless `SCRAPER_ALLOW_NC=true` |
| `test_<source>_in_sources_registry` | Registered in `SOURCES` dict |

Existing tests in `backend/app/tests/test_scraper.py` are the template — extend, don't rewrite.

---

## 9. Atomic Commit Plan

Each commit is independently buildable and reversible:

1. **`scraper: add NC-gate helper to base.py`** — `is_noncommercial()`, default-deny. (Foundational.)
2. **`scraper: add PodcastRssResolver helper to base.py`** — generic feed parser.
3. **`scraper: add openverse connector`** — single highest-leverage source.
4. **`scraper: add librivox connector`** — audiobook gap closer.
5. **`scraper: add free_music_archive connector`** — bulk CC music.
6. **`scraper: add pixabay connector`** — music + SFX.
7. **`scraper: add podcast_index connector`** — podcast discovery.
8. **`scraper: add bbc_sound_effects connector (NC-gated)`** — SFX goldmine.
9. **`scraper: add musopen connector`** — classical PD.
10. **`scraper: add loc_national_jukebox connector`** — historical PD.
11. **`scraper: add cspan_radio + nasa_audio connector`** — US gov PD (single file).
12. **`scraper: register all new sources in SOURCES`**
13. **`docs: update 01-sources.md with P0 coverage + new table`**
14. **`tests: add per-source tests for all P0 connectors`**
15. **`AGENTS.md: add env vars (PODCAST_INDEX_API_KEY, OPENVERSE_API_KEY, PIXABAY_API_KEY, SCRAPER_ALLOW_NC)`**

P1 / P2 sources follow the same atomic pattern in later PRs.

---

## 10. Tradeoffs & Risks

| Risk | Mitigation |
|---|---|
| NC content leaks into commercial platform | Default-deny `SCRAPER_ALLOW_NC`; every NC item logged WARNING at skip; gate is opt-in |
| Aggregators (Openverse) return duplicate items across connectors | Existing dedup-by-URL happens in management command; Openverse items are tagged `source=openverse` for tracing |
| Speech corpora are large (TB-scale) | Use existing Kaggle-style local-path connector; HF cache to `SCRAPER_HF_CACHE_DIR`; sample, don't mirror |
| API rate limits (Podcast Index, Pixabay, FMA) | Inherits `RateLimiter(max_per_min=30)` per-host; tune per-source via env var |
| Page-scrapers (Musopen, LOC, NASA) break when site changes | Mark as `HACK` in module docstring; add explicit selectors test; pin to known catalog pages |
| License tag drift (sites change their license vocabulary) | All items surface `license`; central enforcement stays in management command — no change |
| Scrape-and-import task multiplies (now 14 sources × N items) | Per-source Celery routing optional; default to sequential in management command; document tradeoff |

> **DECISION:** Default NC-gate is OFF. The decision to enable NC content is an operator policy choice, not a default.

---

## 11. Out of Scope (for this PR-set)

- Per-user relevance ranking (separate concern)
- Long-form vs short-form splitting at ingest (use existing `clip-length` arg in management command)
- Ingestion-side clip generation (separate from scrape; happens at `process_audio_to_hls` time)
- Frontend surfacing of source provenance (UI tag in clip detail page) — a small frontend follow-up

---

## 12. Open Questions for Operator

1. **NC policy:** Is `SCRAPER_ALLOW_NC=true` acceptable for BBC SFX (RemArc-NC) and TED Talks (CC-BY-NC-ND)? If yes, those go straight into P0. If no, demote to P2.
2. **API keys:** Confirm we want to require keys for Openverse, Pixabay, Podcast Index — or fall back to anonymous endpoints where available?
3. **Speech corpora storage:** Confirm the policy on `SCRAPER_HF_CACHE_DIR` size cap. Speech corpora can be hundreds of GB.
4. **Source provenance:** Should the catalog show a "Source: NASA / LibriVox / etc." badge in the UI? (Frontend follow-up; affects schema only if yes.)

---

## 13. Approval Requested Before Implementation

**Awaiting greenlight on:**

- P0 source list (10 connectors)
- NC-gate default-deny policy
- Shared infrastructure additions (`NCGate`, `PodcastRssResolver`)
- Atomic commit plan above
- Order of rollout

Once approved, the implementation follows the atomic commit plan (§9) with each commit independently buildable, tested, and reversible.

---

*Source of truth for current architecture: `docs/EXPLAIN/scraping/01-sources.md`. This doc updates that file (per §9 step 13) but is the planning record; the README is the operational reference.*