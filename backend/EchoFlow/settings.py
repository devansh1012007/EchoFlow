import os
from pathlib import Path
import dj_database_url
from django.core.management.utils import get_random_secret_key
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

from django.core.management.utils import get_random_secret_key

# SECURITY WARNING: keep the secret key used in production secret!
# If DJANGO_SECRET_KEY is not set, generate a random key per process so no
# predictable fallback is ever used for signing sessions/CSRF/password resets.
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY') or get_random_secret_key()

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() == 'true'

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost').split(',')
CORS_ALLOW_ALL_ORIGINS = os.environ.get('DJANGO_CORS_ALL', 'False').lower() == 'true'
CORS_ALLOWED_ORIGINS = os.environ.get('DJANGO_CORS_ALLOWED_ORIGINS', 'http://localhost:3000,http://localhost:5173').split(',')

# Allow HLS media files specifically
CORS_URLS_REGEX = r'^.*$'  # all URLs, or narrow to r'^/media/.*$' for HLS only

CORS_ALLOW_METHODS = [
    'GET',
    'POST',
    'PUT',
    'PATCH',
    'DELETE',
    'OPTIONS',  # required for preflight requests
]
CELERY_BROKER_HEARTBEAT = 120 # Increase to 2 minutes
CELERY_BROKER_HEARTBEAT_CHECKRATE = 2

CORS_ALLOW_HEADERS = [
    'accept',
    'authorization',
    'content-type',
    'origin',
    'range',   # ← critical for HLS: browsers send Range headers for partial content
]

CORS_EXPOSE_HEADERS = [
    'Content-Range',   # ← browser needs this to know segment boundaries
    'Accept-Ranges',
]

CORS_ALLOWED_ORIGINS = os.environ.get('DJANGO_CORS_ALLOWED_ORIGINS', 'http://localhost:3000,http://localhost:5173').split(',')

# Only set True in dev — in prod use CORS_ALLOWED_ORIGINS
# DECISION: Disabled CORS_ALLOW_ALL_ORIGINS; using explicit allowed origins
# instead. Tradeoff: Less convenient for unknown clients, but prevents CSRF-style
# attacks from malicious domains using JWT auth.
CORS_ALLOW_ALL_ORIGINS = False
# Application definition
SITE_ID = 1
INSTALLED_APPS = [
    'django_prometheus',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.postgres',  # required for pgvector HnswIndex system checks
    'django_filters',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'rest_framework_simplejwt', 
    'corsheaders',#for frontend
    'storages',#S3-compatible MEDIA storage — see STORAGES["default"] below
    ##
    
    # Local Apps
    'backend.app',
    ##
    'django_redis',
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'dj_rest_auth',
    #'rest_framework_simplejwt',
    'dj_rest_auth.registration',
    'django_celery_beat',
    
    
]

MIDDLEWARE = [
    'django_prometheus.middleware.PrometheusBeforeMiddleware',
    'corsheaders.middleware.CorsMiddleware',##
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'allauth.account.middleware.AccountMiddleware',##
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_prometheus.middleware.PrometheusAfterMiddleware',
]

ROOT_URLCONF = 'backend.EchoFlow.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'backend.EchoFlow.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': dj_database_url.config(
            default=os.environ.get('DATABASE_URL', ''),
            conn_max_age=600
        )
}

REDIS_URL_DEFAULT = 'redis://localhost:6379/1'
REDIS_URL = os.getenv("REDIS_URL", REDIS_URL_DEFAULT)

# This is how you connect Redis to Django
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
   }
}
CELERY_TASK_ROUTES = {
    'backend.app.tasks.process_audio_to_hls': {'queue': 'heavy_media'},
    'backend.app.tasks.refill_user_feed': {'queue': 'fast_feed'},
}
# 3. CELERY CONFIGURATION
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_WORKER_STATE_DB = None
CELERY_WORKER_POOL = 'prefork'
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKNOWLEDGE_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# 4. MEDIA FILES (uploaded originals + generated HLS segments)
#
# DIAGNOSIS: every media bug this week (dead /media/ route under DEBUG=False,
# celery_media unable to see files web wrote, the wrong image entirely being
# served to a worker) traced back to one root assumption: that every
# container processing a clip shares one filesystem with the container that
# received the upload. That's only true in this specific docker-compose
# setup, and only true today because of a dev-convenience bind mount — it is
# NOT true of a real deployment (separate machines/nodes for web vs workers,
# autoscaled worker pools, no shared disk). Rather than keep patching volume
# paths to make the shared-filesystem assumption hold a little longer, we're
# removing the assumption: media now lives in S3-compatible object storage
# that every container reaches over the network, identically, in every
# environment. No shared volume, no bind-mount coincidence, no "works on my
# machine" — MEDIA_ROOT / FileSystemStorage no longer used for user content
# at all.
MEDIA_URL = '/media/'  # unused by S3Storage (which generates its own URLs);
                       # kept only because a few Django internals reference it
SCRAPER_SCRATCH_DIR = os.path.join(BASE_DIR, 'scratch')  # LOCAL, ephemeral,
    # per-container working space for downloading/decoding before upload to
    # object storage — never shared, never durable, never assumed to be
    # visible to any other container. tempfile-backed in code; this is the
    # equivalent of /tmp, just kept off the root filesystem for size reasons.

# Scraper defaults #############
SCRAPER_SOURCES = ['wikimedia', 'internet_archive', 'freesound', 'kaggle']
SCRAPER_USER_AGENT = os.getenv('SCRAPER_USER_AGENT', 'EchoFlowScraper/1.0')
SCRAPER_CONTACT_EMAIL = os.getenv('SCRAPER_CONTACT_EMAIL', '')
SCRAPER_TARGET_DIR = os.path.join(SCRAPER_SCRATCH_DIR, 'audio_scraper')  # local
    # scratch space for raw downloads before scrapers upload to object
    # storage via the model's FileField .save(), same as everything else
SCRAPER_DEFAULT_CLIP_SECONDS = int(os.getenv('SCRAPER_DEFAULT_CLIP_SECONDS', '300'))
SCRAPER_MAX_DOWNLOADS_PER_MIN = int(os.getenv('SCRAPER_MAX_DOWNLOADS_PER_MIN', '30'))
SCRAPER_ALLOW_LICENSES = os.getenv('SCRAPER_ALLOW_LICENSES', 'CC0,CC-BY,CC-BY-SA,CC-BY-NC').split(',')
FREESOUND_API_KEY = os.getenv('FREESOUND_API_KEY', '')
SCRAPER_KAGGLE_LOCAL_PATH = os.getenv('SCRAPER_KAGGLE_LOCAL_PATH', '')

# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Celery Beat — periodic task schedule
CELERY_BEAT_SCHEDULE = {
    'update-global-metrics': {
        'task': 'backend.app.tasks.update_global_metrics',
        'schedule': 300.0,  # every 5 minutes
    },
    'evolve-user-baselines': {
        'task': 'backend.app.tasks.evolve_long_term_user_baselines',
        'schedule': 3600.0,  # every hour
    },
}
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Enable WhiteNoise's compression and caching features.
# DECISION: STORAGES dict, not STATICFILES_STORAGE — that setting was removed
# in Django 5.1 and is silently ignored (no manifest would ever be generated).
STORAGES = {
    # Object storage, not the local disk — every service (web, every celery
    # worker, beat) reaches this over the network identically, so nothing
    # depends on which container wrote a file or which container reads it
    # back. Works against real S3, Cloudflare R2, or (for local dev) MinIO —
    # anything speaking the S3 API. See docker-compose.yml's `minio` service
    # for the local equivalent, and .env.example for the required vars.
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["AWS_STORAGE_BUCKET_NAME"],
            "region_name": os.getenv("AWS_S3_REGION_NAME", "auto"),
            # None -> talks to real AWS S3. Set to MinIO/R2's endpoint for
            # anything else. This one var is the entire prod/dev difference —
            # there is no separate code path for "local mode".
            "endpoint_url": os.getenv("AWS_S3_ENDPOINT_URL") or None,
            "access_key": os.environ["AWS_ACCESS_KEY_ID"],
            "secret_key": os.environ["AWS_SECRET_ACCESS_KEY"],
            # Bucket is private. We hand out short-lived signed URLs instead
            # of a public bucket + permanent links, so a leaked/scraped URL
            # stops working after AWS_S3_QUERYSTRING_EXPIRE seconds.
            "default_acl": None,
            "querystring_auth": True,
            "querystring_expire": int(os.getenv("AWS_S3_QUERYSTRING_EXPIRE", "3600")),
            "file_overwrite": False,
            # path-style addressing (bucket.region.host/bucket/key style off)
            # is required for MinIO and most non-AWS S3-compatible endpoints;
            # real AWS accepts it too, so one setting covers both.
            "addressing_style": "path",
        },
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# PUBLIC_MEDIA_ENDPOINT_URL: the endpoint a BROWSER can actually reach, as
# opposed to AWS_S3_ENDPOINT_URL above (which is what containers use to talk
# to the bucket over the Docker-internal network). These are frequently
# different hosts even in production — e.g. app servers reaching storage over
# a private/VPC endpoint while users need a public-facing URL — so this
# isn't a dev-only hack, it's the general shape of the problem.
#
# Locally: AWS_S3_ENDPOINT_URL=http://minio:9000 (container DNS name),
# PUBLIC_MEDIA_ENDPOINT_URL=http://localhost:9000 (host-published port) —
# same MinIO instance, reached two different ways depending on who's asking.
# In prod against real S3 these are typically identical (or you leave
# PUBLIC_MEDIA_ENDPOINT_URL unset and it falls back to AWS_S3_ENDPOINT_URL).
PUBLIC_MEDIA_ENDPOINT_URL = os.getenv("PUBLIC_MEDIA_ENDPOINT_URL") or os.getenv("AWS_S3_ENDPOINT_URL") or None
AUTH_USER_MODEL = 'app.User' # for Custom user model
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
    },
}
# lets set lifetimes for tokens
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}

# Structured logging configuration
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
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'backend.app': {
            'handlers': ['console'],
            'level': os.environ.get('APP_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'celery': {
            'handlers': ['console'],
            'level': os.environ.get('CELERY_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
    },
}

VERSION = '1.0.0'