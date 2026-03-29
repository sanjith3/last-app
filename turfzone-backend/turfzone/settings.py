"""
Django settings for turfzone project.
"""

from pathlib import Path
import os
from datetime import timedelta
from dotenv import load_dotenv
import dj_database_url

# Load environment variables from .env file
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# ── Firebase Admin SDK ──────────────────────────────────────────────────────
FIREBASE_SERVICE_ACCOUNT_PATH = BASE_DIR / 'firebase-service-account.json'
if not FIREBASE_SERVICE_ACCOUNT_PATH.exists():
    import warnings
    warnings.warn(
        f'Firebase service account not found at {FIREBASE_SERVICE_ACCOUNT_PATH}. '
        'Push notifications will not work.',
        stacklevel=2,
    )
# ───────────────────────────────────────────────────────────────────────────

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-turfzone-dev-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*').split(',')

# Application definition
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    'rest_framework',
    'corsheaders',
    'axes',
    
    'users',
    'turfs',
    'bookings',
    'finance',
    'truff_admin_panel',
    'payments',
    'growth',
    'support',
    'channels',
]

ASGI_APPLICATION = 'turfzone.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [('127.0.0.1', 6379)],
        },
    },
}

# Add fallback for development when Redis is not available
if os.environ.get('REDIS_URL') is None:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

MIDDLEWARE = [
    'core.middleware.RequestLogMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'axes.middleware.AxesMiddleware',  # BUG-04: brute-force protection (must be last)
]

ROOT_URLCONF = 'turfzone.urls'

# Landing page directory (may not exist on cloud deployments)
_LANDING_DIR = BASE_DIR.parent.parent / 'trufspot-landing'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
        ] + ([_LANDING_DIR] if _LANDING_DIR.exists() else []),
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'truff_admin_panel.context_processors.pending_turf_count',
            ],
        },
    },
]

WSGI_APPLICATION = 'turfzone.wsgi.application'

# Database — Render provides DATABASE_URL for PostgreSQL;
# falls back to SQLite for local development.
DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        conn_max_age=600,
    )
}

# Cache — Redis for production locking & caching
_REDIS_URL = os.environ.get('REDIS_URL', '')
if _REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _REDIS_URL,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'unique-snowflake',
        }
    }


# Password validation
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

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',  # Landing page CSS/JS/assets (bundled in repo)
] + [
    d for d in [_LANDING_DIR] if d.exists()  # Also check external landing dir
]
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    # BUG-04 FIX (additional layer): DRF rate limiting on top of django-axes
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/minute',   # Unauthenticated callers (login, register, OTP)
        'user': '300/minute',  # Authenticated users (normal app usage)
    },
}

# JWT Configuration
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=7),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
}

# PERF-2: Owner dashboard stats cache timeout (seconds). Set to 0 to disable.
OWNER_DASHBOARD_CACHE_TTL = int(os.environ.get('OWNER_DASHBOARD_CACHE_TTL', 300))

CORS_ALLOW_ALL_ORIGINS = True  # Dev only — restrict in production
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Production Security (only when DEBUG is off)
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True') == 'True'

# Custom User Model
AUTH_USER_MODEL = 'users.CustomUser'

# ---------------------------------------------------------------------------
# django-axes: Brute-force login protection (BUG-04)
# ---------------------------------------------------------------------------
AXES_ENABLED = True
AXES_FAILURE_LIMIT = 5          # Lock after 5 failures
AXES_COOLOFF_TIME = 0.25        # 15 minutes (in hours)
AXES_LOCK_OUT_AT_FAILURE = True
AXES_RESET_ON_SUCCESS = True    # Reset failure count on successful login
AXES_LOCKOUT_PARAMETERS = ['ip_address', 'username']  # Lock per IP+username combo
AXES_HANDLER = 'axes.handlers.database.AxesDatabaseHandler'

# Required by django-axes to intercept failed authentication attempts
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# Logging — force every request to console
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django.server': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ---------------------------------------------------------------------------
# Razorpay Configuration (Test mode by default)
# ---------------------------------------------------------------------------
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')
RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')

# ---------------------------------------------------------------------------
# WhatsApp Cloud API (Meta) — OTP via WhatsApp
# ---------------------------------------------------------------------------
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', '')
WHATSAPP_PHONE_NUMBER_ID = os.environ.get('WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_TEMPLATE_NAME = os.environ.get('WHATSAPP_TEMPLATE_NAME', 'trufspot_otp_20260319122659')
WHATSAPP_API_URL = os.environ.get('WHATSAPP_API_URL', 'https://graph.facebook.com/v21.0')
WHATSAPP_VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN', 'turfspot_verify_2026')
WHATSAPP_OTP_RATE_LIMIT = int(os.environ.get('WHATSAPP_OTP_RATE_LIMIT', 3))  # per phone per hour


# Tuning knobs (override in .env if needed)
OTP_EXPIRY_MINUTES = int(os.environ.get('OTP_EXPIRY_MINUTES', 5))
OTP_MAX_ATTEMPTS  = int(os.environ.get('OTP_MAX_ATTEMPTS', 5))
OTP_RATE_LIMIT_PER_HOUR = int(os.environ.get('OTP_RATE_LIMIT_PER_HOUR', 5))

# Ensure debug toolbar doesn't interfere
DEBUG_PROPAGATE_EXCEPTIONS = True

# Configure logging to console
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.server': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
