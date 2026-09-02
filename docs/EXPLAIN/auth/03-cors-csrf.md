# CORS & CSRF Configuration

## CORS Configuration (`settings.py:31-63`)

### Settings
```python
# Allowed origins (comma-separated)
CORS_ALLOWED_ORIGINS = os.environ.get('DJANGO_CORS_ALLOWED_ORIGINS', 'http://localhost:3000,http://localhost:5173').split(',')

# Explicit deny-all (overrides any True setting)
CORS_ALLOW_ALL_ORIGINS = False

# Methods
CORS_ALLOW_METHODS = [
    'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS',
]

# Headers — CRITICAL for HLS
CORS_ALLOW_HEADERS = [
    'accept', 'authorization', 'content-type', 'origin', 'range',  # 'range' for HLS
]

# Exposed headers — for HLS Range responses
CORS_EXPOSE_HEADERS = [
    'Content-Range',   # Browser needs for segment seeking
    'Accept-Ranges',   # Advertises range support
]

# All URLs (could narrow to r'^/media/.*$' for HLS only)
CORS_URLS_REGEX = r'^.*$'
```

---

## Why These Headers?

### `range` (Request Header)
- **HLS.js** requests segments with `Range: bytes=0-` for seeking
- Without CORS `range` allowance → preflight fails → playback breaks

### `Content-Range` & `Accept-Ranges` (Response Headers)
- Browser needs `Content-Range` to know segment boundaries
- `Accept-Ranges: bytes` advertises partial content support
- Must be **exposed** via `CORS_EXPOSE_HEADERS` for JavaScript access

---

## CORS Middleware Order (`settings.py:100-113`)

```python
MIDDLEWARE = [
    'django_prometheus.middleware.PrometheusBeforeMiddleware',
    'corsheaders.middleware.CorsMiddleware',  # MUST be early
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_prometheus.middleware.PrometheusAfterMiddleware',
]
```

**Rule:** `CorsMiddleware` before `CommonMiddleware` and `CsrfViewMiddleware`.

---

## CSRF Configuration

### Middleware
```python
MIDDLEWARE = [
    ...
    'django.middleware.csrf.CsrfViewMiddleware',  # After SessionMiddleware
    ...
]
```

### JWT + CSRF
- **JWT in Authorization header** → CSRF **not required** (stateless)
- **Session authentication** (admin, browsable API) → CSRF **required**
- `CsrfViewMiddleware` active but bypassed for JWT endpoints

### Settings (Defaults)
```python
CSRF_COOKIE_SECURE = True      # HTTPS only (prod)
CSRF_COOKIE_HTTPONLY = True    # No JS access
CSRF_COOKIE_SAMESITE = 'Lax'   # CSRF protection
CSRF_TRUSTED_ORIGINS = []      # Configure for subdomains
```

---

## HLS-Specific CORS

### MinIO CORS (`docker-compose.yml`)
```yaml
environment:
  MINIO_CORS_ALLOW_ORIGIN: "*"
  MINIO_CORS_ALLOW_METHODS: "GET,PUT,POST,DELETE,OPTIONS"
  MINIO_CORS_ALLOW_HEADERS: "Origin,Accept,Content-Type,Authorization,Range"
  MINIO_CORS_EXPOSE_HEADERS: "Date,Content-Length,Content-Range,Accept-Ranges"
  MINIO_CORS_MAX_AGE: "3600"
```

### Django CORS for Media (Not Used)
```python
# Not needed — media served directly from MinIO/S3
# CORS handled at storage layer
```

---

## Preflight Flow (HLS Segment Request)

```
Browser: OPTIONS /hls/clip/segment_000.ts
         Origin: http://localhost:5173
         Access-Control-Request-Method: GET
         Access-Control-Request-Headers: Range

MinIO:   204 No Content
         Access-Control-Allow-Origin: *
         Access-Control-Allow-Methods: GET, OPTIONS
         Access-Control-Allow-Headers: Range
         Access-Control-Expose-Headers: Content-Range, Accept-Ranges
         Access-Control-Max-Age: 3600

Browser: GET /hls/clip/segment_000.ts
         Range: bytes=0-
         Origin: http://localhost:5173

MinIO:   206 Partial Content
         Content-Range: bytes 0-1023/65536
         Accept-Ranges: bytes
         Access-Control-Expose-Headers: Content-Range, Accept-Ranges
```

---

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| HLS playback fails | `Range` not in `CORS_ALLOW_HEADERS` | Add `'range'` |
| Segment seeking broken | `Content-Range` not exposed | Add to `CORS_EXPOSE_HEADERS` |
| Preflight 403 | Origin not in `CORS_ALLOWED_ORIGINS` | Add frontend origin |
| CSRF 403 on login | Session auth + missing CSRF token | Use JWT or include CSRF token |

---

## Discrepancy: README vs Implementation

| README Claim | Actual |
|--------------|--------|
| "CORS_ALLOW_ALL_ORIGINS = True hardcoded" (line 25) | **Explicitly `False`** at line 63 |
| "Hardcoded to allow all" (audit) | **Fixed** — explicit origins |

---

## Production Hardening

### Restrict Origins
```bash
# .env.prod
DJANGO_CORS_ALLOWED_ORIGINS=https://app.echoflow.com,https://www.echoflow.com
DJANGO_CORS_ALL=False
```

### CSRF for Subdomains
```python
CSRF_TRUSTED_ORIGINS = [
    'https://app.echoflow.com',
    'https://api.echoflow.com',
]
CSRF_COOKIE_DOMAIN = '.echoflow.com'  # Share across subdomains
```

### Security Headers
```python
# Add to SecurityMiddleware
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
```

---

*Source: `backend/EchoFlow/settings.py:31-63, 100-113`, `docker-compose.yml:78-83`*