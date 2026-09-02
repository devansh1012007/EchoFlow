# Backend Authentication & Permissions

## Authentication Stack

**JWT via SimpleJWT** (`djangorestframework-simplejwt==5.5.1`)

### Configuration (`backend/EchoFlow/settings.py`)

```python
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    # ... throttling, pagination
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}
```

**Token lifetimes:**
- Access token: 15 minutes (short — limits exposure if stolen)
- Refresh token: 7 days (long — "remember me" duration)

### Token Structure

**Access token payload:**
```json
{
  "token_type": "access",
  "exp": 1699999999,
  "iat": 1699999099,
  "jti": "uuid",
  "user_id": 123
}
```

**Refresh token payload:**
```json
{
  "token_type": "refresh",
  "exp": 1700603899,
  "iat": 1699999099,
  "jti": "uuid",
  "user_id": 123
}
```

### Authentication Flow

```
1. POST /auth/login/ {username, password}
   │
   ▼
2. TokenObtainPairView → validates credentials
   │
   ▼
3. Returns: {access, refresh, user}
   │
   ├── access: Bearer token for API requests
   └── refresh: Stored securely, used to obtain new access tokens

4. Client includes: Authorization: Bearer <access_token>
   │
   ▼
5. JWTAuthentication validates:
   ├── Signature (HS256 with DJANGO_SECRET_KEY)
   ├── Expiration (exp claim)
   ├── User exists and is_active
   │
   ▼
6. Request.user populated
```

### Token Refresh Flow

```
1. Access token expires (401 on API call)
   │
   ▼
2. Client POST /auth/token/refresh/ {refresh}
   │
   ▼
3. TokenRefreshView validates refresh token:
   ├── Signature valid
   ├── Not expired (7 days)
   ├── Not blacklisted (no blacklist implemented)
   │
   ▼
4. Returns new {access, refresh?}
   │
   ▼
5. Client retries original request with new access token
```

**Frontend implementation:** `frontend/sample_frontend/src/api/client.ts:50-78` auto-refreshes on 401.

---

## Permission Classes

### Global Default
```python
'DEFAULT_PERMISSION_CLASSES': [
    'rest_framework.permissions.IsAuthenticated',
]
```
All endpoints require authentication unless explicitly overridden.

### View-Level Overrides

| View | Permission | Reason |
|------|------------|--------|
| `RegisterView` | `AllowAny` | Public registration |
| `TokenObtainPairView` | `AllowAny` (via SimpleJWT) | Public login |
| `TokenRefreshView` | `AllowAny` (via SimpleJWT) | Public refresh |
| All other ViewSets | `IsAuthenticated` | Protected resources |

### Object-Level Permissions

Implemented via `get_queryset()` filtering:

```python
# AudioUploadViewSet - users only see their own clips
def get_queryset(self):
    return AudioClip.objects.filter(creator=self.request.user)

# ShareViewSet - users only see shares where they're receiver
def get_queryset(self):
    return ShareEvent.objects.filter(receiver=self.request.user)

# CommentViewSet - all comments visible (filterable by clip/parent)
def get_queryset(self):
    return Comment.objects.select_related('author').all()
```

**No custom permission classes** — all object-level control via queryset filtering.

---

## User Registration

**Endpoint:** `POST /auth/register/`

```python
class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[UniqueValidator(queryset=User.objects.all())]
    )
    
    class Meta:
        model = User
        fields = ('username', 'password', 'email')
        extra_kwargs = {'password': {'write_only': True}, 'email': {'write_only': True}}

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password']
        )
        return user
```

**Flow:**
1. Validates username unique, email unique, password provided
2. `User.objects.create_user()` → hashes password
3. User.save() → encrypts email to `encrypted_email` (Fernet)
4. Returns tokens via `dj-rest-auth` integration

**No email verification** — user immediately active.

---

## Email Encryption (PII Protection)

### Implementation (`backend/app/models.py`)

```python
FERNET_KEY = os.environ.get('FIELD_ENCRYPTION_KEY')
if not FERNET_KEY:
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY is missing...")
cipher_suite = Fernet(FERNET_KEY.encode())

class User(AbstractUser):
    encrypted_email = models.TextField(unique=True, null=True, blank=True)
    
    def save(self, *args, **kwargs):
        if self.email and cipher_suite:
            self.encrypted_email = cipher_suite.encrypt(self.email.encode()).decode()
        elif self.email and not cipher_suite:
            logger.warning(f"Saving email in plaintext for user {self.username}")
            self.encrypted_email = self.email
        super().save(*args, **kwargs)
```

**Properties:**
- `email` field (AbstractUser) used for authentication
- `encrypted_email` stores Fernet-encrypted version (unique)
- Fail-fast if `FIELD_ENCRYPTION_KEY` missing at startup
- **No key rotation** — single key for all users (architecture audit gap)

**Decryption:** Not implemented in codebase — encrypted_email write-only.

---

## CORS Configuration

### Settings (`backend/EchoFlow/settings.py`)

```python
CORS_ALLOWED_ORIGINS = os.environ.get('DJANGO_CORS_ALLOWED_ORIGINS', 'http://localhost:3000,http://localhost:5173').split(',')
CORS_ALLOW_ALL_ORIGINS = False  # Explicit origins only

CORS_ALLOW_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
CORS_ALLOW_HEADERS = [
    'accept', 'authorization', 'content-type', 'origin', 'range',  # range critical for HLS
]
CORS_EXPOSE_HEADERS = [
    'Content-Range', 'Accept-Ranges',  # browser needs for HLS segment seeking
]
CORS_URLS_REGEX = r'^.*$'  # All URLs (or narrow to r'^/media/.*$' for HLS only)
```

**Key headers for HLS:**
- `Range` — browsers send for partial segment content
- `Content-Range` / `Accept-Ranges` — exposed for segment seeking

**DISCREPANCY:** README.md:25 claims `CORS_ALLOW_ALL_ORIGINS = True` hardcoded, but settings.py:63 explicitly sets `False`.

---

## CSRF Protection

**Middleware order:**
```python
MIDDLEWARE = [
    'django_prometheus.middleware.PrometheusBeforeMiddleware',
    'corsheaders.middleware.CorsMiddleware',  # Before CSRF
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',  # CSRF after session
    'allauth.account.middleware.AccountMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    ...
]
```

**JWT + CSRF:**
- JWT in Authorization header → **CSRF not required** (stateless)
- Session authentication (admin, browsable API) → CSRF required
- `CsrfViewMiddleware` active but bypassed for JWT endpoints

---

## Rate Limiting (Throttling)

### Configuration
```python
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
    },
}
```

### Current Limits
| Tier | Limit | Scope |
|------|-------|-------|
| Anonymous | 100/hour | Per IP |
| Authenticated | 1000/hour | Per user |

**Gaps (Architecture Audit):**
- No per-endpoint overrides (e.g., `log_telemetry` can be spammed 1000x/hour)
- No Redis-backed distributed throttling (uses Django cache → local memory in dev)
- No stricter limits on write-heavy endpoints

---

## Security Headers

**Middleware provides:**
- `SecurityMiddleware` → HSTS, SSL redirect, content type nosniff
- `XFrameOptionsMiddleware` → `X-Frame-Options: DENY`
- `CorsMiddleware` → CORS headers

**Missing (recommended):**
- `Content-Security-Policy`
- `Referrer-Policy`
- `Permissions-Policy`

---

## Session Authentication (Admin/Browsable API)

**Enabled apps:**
```python
INSTALLED_APPS = [
    'django.contrib.sessions',
    'django.contrib.auth',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'dj_rest_auth',
    'dj_rest_auth.registration',
]
```

**Used for:**
- Django admin (`/admin/`)
- DRF browsable API (when `DEBUG=True`)
- Social login (Google OAuth via allauth)

**Not used for:** API endpoints (JWT only)

---

## Social Authentication (Google)

**Configured but not fully implemented:**
```python
INSTALLED_APPS += [
    'allauth.socialaccount.providers.google',
]
```
Requires `SOCIALACCOUNT_PROVIDERS` config with Google client ID/secret.

---

## Missing Security Features

| Feature | Status | Risk |
|---------|--------|------|
| Token blacklist/logout invalidation | ❌ Not implemented | Stolen access token valid 15min |
| Refresh token rotation | ❌ Not implemented | Long-lived refresh token theft |
| Email verification | ❌ Not implemented | Fake emails possible |
| Password strength validation | ❌ Not in serializer | Weak passwords allowed |
| Magic byte file validation | ❌ Not in serializer | Executable upload risk |
| Per-endpoint rate limits | ❌ Global only | Telemetry spam possible |
| PgBouncer connection pooling | ❌ Direct connections | DB exhaustion at scale |

---

*Source: `backend/EchoFlow/settings.py`, `backend/app/models.py`, `backend/app/serializers.py`, `backend/app/views.py`, `frontend/sample_frontend/src/api/client.ts`*