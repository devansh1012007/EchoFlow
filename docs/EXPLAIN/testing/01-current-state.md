# Testing & Observability — Current State

> **Status: significantly out of date (2026-09-03 snapshot).** The test suite grew from 1 file to 20 files (179 → 230 passed). pytest + pytest-django is configured. CI runs the full suite + the new `pytest -m integration` step. The most accurate testing reference is now [AGENTS.md](../../AGENTS.md#testing--linting) and the runbook in this directory at [04-integration-test-suite.md](04-integration-test-suite.md). This file is preserved as the original state-of-the-world snapshot for the audit trail.

## Testing

### Current Test Suite (as of 2026-09-03 — pre-Group-A)
**File:** `backend/app/tests/test_scraper.py`

```python
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
                try: os.remove(p)
                except: pass

    def test_uploader_creates_audioclip(self):
        inp = self._make_sample_wav(duration_ms=1000)
        out = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3').name
        try:
            normalizer.normalize_and_trim(inp, out, max_seconds=5, target_format='mp3')
            clip = uploader.save_clip(...)
            self.assertIsNotNone(clip.id)
            self.assertTrue(clip.original_file.name.startswith('audio_scraper/'))
            self.assertTrue(os.path.exists(clip.original_file.path))  # Local filesystem
        finally:
            for p in (inp, out):
                try: os.remove(p)
                except: pass
```

### What's Tested
| Test | Component | Coverage |
|------|-----------|----------|
| `test_normalizer_trims_to_max_seconds` | `normalizer.py` | Trim logic |
| `test_uploader_creates_audioclip` | `uploader.py` + `normalizer.py` | S3 upload, AudioClip creation |

### What's NOT Tested
| Area | Missing Tests |
|------|---------------|
| API Views | All ViewSets (Feed, Upload, Interactions, Share, Comments, Follow, Tags, Suggestions, Profile) |
| Serializers | Validation, HLS URL generation |
| Tasks | `process_audio_to_hls`, `refill_user_feed`, `update_global_metrics`, `evolve_long_term_user_baselines` |
| Models | Vector fields, constraints, denormalized counters |
| Recommendations | `calculate_time_decayed_vectors`, `refill_user_feed` scoring |
| Auth | JWT, registration, token refresh |
| Scrapers | Downloader, license filtering, source connectors |
| Frontend | Components, stores, API client |

---

## Test Infrastructure (as of 2026-09-03 — pre-Group-A)

### No Test Runner Configured (FIXED)
```bash
# No pytest.ini, setup.cfg, pyproject.toml
# No test command in package.json
# No CI pipeline for tests
```

### Required Setup
```ini
# pytest.ini (needed)
[pytest]
DJANGO_SETTINGS_MODULE = backend.EchoFlow.settings
python_files = tests.py test_*.py *_tests.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

### Test Dependencies (Not in requirements)
```txt
pytest==7.4.0
pytest-django==4.5.0
pytest-cov==4.1.0
factory-boy==3.3.0
faker==20.0.0
responses==0.23.0
```

---

## Running Tests (Manual — pre-Group-A; see AGENTS.md for current)

```bash
# Current (no test runner)
python -m pytest backend/app/tests/ -v  # Won't work without config

# Manual test run
cd backend
python manage.py test app.tests.test_scraper  # Django test runner
```

---

## Observability

### Health Endpoints (`health.py`)

#### `/health/` — Liveness
```python
def health_check(request):
    return JsonResponse({
        "status": "healthy",
        "timestamp": time.time(),
    })
```
- **Purpose:** Process is alive
- **Used by:** Docker healthcheck, load balancer liveness
- **No dependencies** — returns immediately

#### `/ready/` — Readiness
```python
def readiness_check(request):
    try:
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({
            "status": "ready",
            "database": "connected",
            "timestamp": time.time(),
        })
    except Exception:
        logger.exception("Readiness check failed")
        return JsonResponse({
            "status": "not_ready",
            "database": "error",
            "timestamp": time.time(),
        }, status=503)
```
- **Purpose:** App can serve traffic (DB connected)
- **Used by:** Docker healthcheck, load balancer readiness

### Prometheus Metrics (`/metrics/`)
```python
# urls.py
from django_prometheus.exports import ExportToDjangoView
path('metrics/', ExportToDjangoView, name='prometheus_django_metrics')
```

**Provided by `django-prometheus`:**
- HTTP request counts/latencies
- DB query counts/latencies
- Cache hits/misses
- Custom metrics (none defined yet)

### Logging Configuration (`settings.py:341-378`)

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'fmt': '%(asctime)s %(name)s %(levelname)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.environ.get('LOG_LEVEL', 'INFO'),
    },
    'loggers': {
        'django': {'handlers': ['console'], 'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'), 'propagate': False},
        'backend.app': {'handlers': ['console'], 'level': os.environ.get('APP_LOG_LEVEL', 'INFO'), 'propagate': False},
        'celery': {'handlers': ['console'], 'level': os.environ.get('CELERY_LOG_LEVEL', 'INFO'), 'propagate': False},
    },
}
```

**Output:** JSON lines to stdout
```json
{"asctime": "2024-01-15T10:30:00", "name": "backend.app.tasks", "levelname": "INFO", "message": "Extracted acoustic vector and duration for clip abc-123"}
```

### Missing Observability

| Component | Status | Needed |
|-----------|--------|--------|
| Distributed Tracing | ❌ | OpenTelemetry + Jaeger/Tempo |
| Custom Business Metrics | ❌ | Feed latency, recommendation quality, upload success rate |
| Alerting | ❌ | PagerDuty/OpsGenie on error rates, queue depth |
| Log Aggregation | ❌ | Loki/ELK for structured log search |
| Dashboard | ❌ | Grafana dashboards for system health |
| Error Tracking | ❌ | Sentry/Rollbar for exception grouping |

---

## CI/CD (Missing)

### No GitHub Actions Workflows
```yaml
# .github/workflows/ (empty or missing)
# Needed:
# - test.yml: Run tests on PR
# - build.yml: Build Docker images
# - deploy.yml: Deploy to staging/prod
```

### Required Pipeline
```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: {POSTGRES_DB: test, POSTGRES_USER: test, POSTGRES_PASSWORD: test}
        ports: [5432:5432]
      redis:
        image: redis:7-alpine
        ports: [6379:6379]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: '3.11'}
      - run: pip install -r requirements.txt
      - run: python manage.py test
      - run: pytest --cov=backend/app
  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/build-push-action@v5
        with: {push: false, tags: echoflow-api:test}
```

---

## Verification Scripts (Manual)

### Scripts Directory
| Script | Purpose |
|--------|---------|
| `scripts/verify_minio_deployment.sh` | MinIO API, bucket, anonymous policy, CORS |
| `scripts/test_minio_edge_cases.py` | Concurrent reads, bad auth, timeout, large upload |
| `scripts/verify_clip_url.sh` | Quick 200-check for HLS URL |
| `scripts/verify_decoder_rootcause.sh` | Download .ts, check MPEG-TS magic (47401111) |
| `scripts/verify_hls_playback.html` | Browser HLS test with hls.js |

### Diagnostics Directory
| Script | Purpose |
|--------|---------|
| `diagnostics/check_pipeline.py` | Check AI pipeline |
| `diagnostics/test_hls_playback.py` | HLS playback test |
| `diagnostics/check_minio.sh` | MinIO health |

---

## Gap Summary

| Category | Current | Target |
|----------|---------|--------|
| Unit Tests | 1 file (scraper) | >80% coverage |
| Integration Tests | 0 | API + DB + Redis |
| E2E Tests | 0 | Critical user flows |
| Test Runner | None | pytest + Django |
| CI/CD | None | GitHub Actions |
| Health Checks | Basic (DB only) | Deep (Redis, S3, Celery) |
| Metrics | Basic (django-prometheus) | Custom business metrics |
| Tracing | None | OpenTelemetry |
| Logging | JSON stdout | Structured + aggregated |
| Alerting | None | PagerDuty on errors |
| Dashboards | None | Grafana system + business |

---

*Source: `backend/app/tests/test_scraper.py`, `backend/EchoFlow/health.py`, `backend/EchoFlow/settings.py`, `backend/EchoFlow/urls.py`, `scripts/`, `diagnostics/`*