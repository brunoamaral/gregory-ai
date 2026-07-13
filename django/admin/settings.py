import os
import hashlib
import base64
import logging
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    
    # Try multiple locations for .env file
    potential_paths = [
        Path(BASE_DIR).parent / '.env',  # Project root
        Path(BASE_DIR) / '.env',         # Django directory
    ]
    
    for env_path in potential_paths:
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
            logging.info(f"Loaded environment variables from {env_path}")
            break
    else:
        logging.warning(f"No .env file found in ${env_path}")
except ImportError:
    logging.warning("python-dotenv not installed. Environment variables must be set manually.")

# SECURITY WARNING: don't run with debug turned on in production!
# Secure by default: production is safe even if DJANGO_DEBUG is unset.
# For local development, set DJANGO_DEBUG=True in your .env file.
DEBUG = os.environ.get('DJANGO_DEBUG', 'False').lower() in ('true', '1', 'yes')

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY', 'DEFAULT SECRET_KEY')
if SECRET_KEY == 'DEFAULT SECRET_KEY':
    if not DEBUG:
        raise ValueError("SECRET_KEY environment variable must be set in production.")
    logging.warning("Using default SECRET_KEY for development. DO NOT use in production!")

SITE_ID = 1

# FERNET SECRET KEY — used to encrypt sensitive database fields.
_fernet_raw = os.environ.get('FERNET_SECRET_KEY', '')
if not _fernet_raw:
    if DEBUG:
        # Derive a stable dev key from a fixed, non-secret seed so encrypted DB
        # values survive container restarts. Not a secret — never use in production.
        _fernet_raw = base64.urlsafe_b64encode(
            hashlib.sha256(b'gregory-dev-only-fernet-key').digest()
        ).decode()
        logging.warning("Using dev-only FERNET_SECRET_KEY fallback. Set FERNET_SECRET_KEY in .env for a stable value.")
    else:
        raise ValueError("FERNET_SECRET_KEY environment variable must be set in production.")
FERNET_SECRET_KEY = _fernet_raw
FORMS_URLFIELD_ASSUME_HTTPS = True

# Django admin handles bulk actions on large datasets; raise the POST field limit
# accordingly. The default of 1000 is too low when selecting hundreds of subscribers.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

_domain = os.environ.get('DOMAIN_NAME', '')
# Comma-separated list of extra hostnames/IPs for ALLOWED_HOSTS (e.g. a server IP,
# a legacy domain, or a staging hostname). Set via EXTRA_ALLOWED_HOSTS in .env.
_extra_hosts = [h.strip() for h in os.environ.get('EXTRA_ALLOWED_HOSTS', '').split(',') if h.strip()]

ALLOWED_HOSTS = ['localhost', '127.0.0.1', '0.0.0.0', 'gregory'] + _extra_hosts
if _domain:
	ALLOWED_HOSTS += [_domain, f'api.{_domain}']

CSRF_TRUSTED_ORIGINS = [f'https://{h}' for h in _extra_hosts if not h.startswith('http')]
if _domain:
	CSRF_TRUSTED_ORIGINS += [f'https://{_domain}', f'https://api.{_domain}']

# CORS — in debug mode allow all origins (local dev convenience).
# In production, only explicitly listed origins are accepted.
if DEBUG:
	CORS_ALLOW_ALL_ORIGINS = True
else:
	_cors_origins = [o.strip() for o in os.environ.get('CORS_ALLOWED_ORIGINS', '').split(',') if o.strip()]
	if _domain:
		_cors_origins += [f'https://{_domain}', f'https://www.{_domain}']
	CORS_ALLOWED_ORIGINS = _cors_origins

# Application definition
INSTALLED_APPS = [
	'corsheaders',
	'gregory.apps.GregoryConfig',
	'subscriptions.apps.SubscriptionsConfig',
	'rest_framework',
	'rest_framework_simplejwt',
	'rest_framework_csv',  # Add CSV renderer support
	'django_filters',
	'django.contrib.postgres',
	'django.contrib.admin',
	'django.contrib.auth',
	'django.contrib.contenttypes',
	'django.contrib.sessions',
	'django.contrib.messages',
	'django.contrib.staticfiles',
	'django.contrib.sites',
	'organizations',
	'simple_history',
	'sitesettings',
	'indexers',
	'api',
	'django_ckeditor_5',
]

MIDDLEWARE = [
	'django.middleware.security.SecurityMiddleware',
	'corsheaders.middleware.CorsMiddleware',
	'django.contrib.sessions.middleware.SessionMiddleware',
	'django.middleware.common.CommonMiddleware',
	'django.middleware.csrf.CsrfViewMiddleware',
	'django.contrib.auth.middleware.AuthenticationMiddleware',
	'django.contrib.messages.middleware.MessageMiddleware',
	'django.middleware.clickjacking.XFrameOptionsMiddleware',
	'django.middleware.gzip.GZipMiddleware',
	'django.contrib.sites.middleware.CurrentSiteMiddleware',
	'simple_history.middleware.HistoryRequestMiddleware',
	'gregory.middleware.visibility.VisibleOrgMiddleware',
	'api.middleware.ApiKeyMiddleware',
]

ROOT_URLCONF = 'admin.urls'

TEMPLATES = [
	{
		'BACKEND': 'django.template.backends.django.DjangoTemplates',
		'DIRS': [os.path.join(BASE_DIR, 'templates')],
		'APP_DIRS': True,
		'OPTIONS': {
			'context_processors': [
				'django.template.context_processors.debug',
				'django.template.context_processors.request',
				'django.contrib.auth.context_processors.auth',
				'django.contrib.messages.context_processors.messages',
			],
		},
	},
]

WSGI_APPLICATION = 'admin.wsgi.application'

# Database
DATABASES = {
	'default': {
		'ENGINE': 'django.db.backends.postgresql',
		'NAME': os.environ.get('POSTGRES_DB'),
		'USER': os.environ.get('POSTGRES_USER'),
		'PASSWORD': os.environ.get('POSTGRES_PASSWORD'),
		'HOST': os.environ.get('DB_HOST'),
		'PORT': 5432,
		# Reuse connections across requests instead of opening/closing one per
		# request. gunicorn runs 4 workers x 2 threads (Dockerfile), so worst
		# case is ~8 persistent connections -- well within Postgres defaults.
		'CONN_MAX_AGE': int(os.environ.get('CONN_MAX_AGE', '60')),
		'CONN_HEALTH_CHECKS': True,
	}
}

# Cache backend — shared across gunicorn workers via the existing Postgres DB.
# Run `python manage.py createcachetable gregory_cache` once after deploy to create the table.
CACHES = {
	'default': {
		'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
		'LOCATION': 'gregory_cache',
	}
}

# TTL (seconds) for the /stats/ response cache. Override via STATS_CACHE_TTL env var.
STATS_CACHE_TTL = int(os.environ.get('STATS_CACHE_TTL', '600'))

# Password validation
AUTH_PASSWORD_VALIDATORS = [
	{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
	{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
	{'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
	{'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'
STATIC_ROOT = '/code/static'

# Media files (for CKEditor uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = '/code/media'

# CKEditor 5 configuration
CKEDITOR_5_CONFIGS = {
	'default': {
		'toolbar': [
			'heading', '|',
			'bold', 'italic', 'underline', 'strikethrough', '|',
			'bulletedList', 'numberedList', '|',
			'link', 'blockQuote', '|',
			'horizontalLine', '|',
			'imageUpload', 'insertImage', '|',
			'undo', 'redo',
		],
		'language': 'en',
		'image': {
			'toolbar': [
				'imageTextAlternative', '|',
				'imageStyle:full', 'imageStyle:side',
			],
		},
		# General HTML Support — allow <a class="btn-cta"> to be preserved
		# in the CKEditor model and output so the button plugin can insert it.
		'htmlSupport': {
			'allow': [
				{'name': 'a', 'classes': ['btn-cta']},
			],
		},
		# NOTE: button_plugin.js is NOT listed here as an extraPlugin.
		# CKEditor 5's extraPlugins expects constructor references, not file
		# paths — loading it that way throws plugincollection-plugin-not-found.
		# The script is instead loaded as a regular Django Media JS on the
		# AnnouncementAdmin page (see subscriptions/admin.py).
	},
}
CKEDITOR_5_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'

# Point the CKEditor 5 widget's upload URL at our hardened view
# (django/subscriptions/views.py::ckeditor_upload).
CK_EDITOR_5_UPLOAD_FILE_VIEW_NAME = 'subscriptions_ckeditor_upload'

# When True, the announcement send-validation helper probes each /media/ image
# URL with a HEAD request to confirm the file is reachable before sending.
# Off by default because it makes an outbound HTTP call during an admin request;
# useful in staging to catch missing files.
ANNOUNCEMENT_PROBE_MEDIA = False

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Rest Framework
REST_FRAMEWORK = {
	'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
	'PAGE_SIZE': 10,
	'DEFAULT_THROTTLE_RATES': {
		'bulk_export': '4/hour',
	},
	'DEFAULT_AUTHENTICATION_CLASSES': (
		'rest_framework.authentication.BasicAuthentication',
		'rest_framework.authentication.SessionAuthentication',
		'rest_framework_simplejwt.authentication.JWTAuthentication',
	),
	'DEFAULT_RENDERER_CLASSES': (
		'rest_framework.renderers.JSONRenderer',
		'rest_framework.renderers.BrowsableAPIRenderer',
		'api.direct_streaming.DirectStreamingCSVRenderer',
	),
	'DEFAULT_FILTER_BACKENDS': [
		'django_filters.rest_framework.DjangoFilterBackend',
		'rest_framework.filters.SearchFilter',
		'rest_framework.filters.OrderingFilter',
	]
}

# Email Settings
EMAIL_HOST = os.environ.get('EMAIL_HOST')
EMAIL_PORT = os.environ.get('EMAIL_PORT')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
EMAIL_DOMAIN = os.environ.get('EMAIL_DOMAIN')

EMAIL_POSTMARK_API_KEY = os.environ.get('EMAIL_POSTMARK_API_KEY')
EMAIL_POSTMARK_API_URL = os.environ.get('EMAIL_POSTMARK_API_URL')

# Logging
LOGGING = {
	'version': 1,
	'disable_existing_loggers': False,
	'handlers': {
		'console': {
			'class': 'logging.StreamHandler',
		},
	},
	'root': {
		'handlers': ['console'],
		'level': 'INFO',
	},
}

