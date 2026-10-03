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
	'drf_spectacular',
	'django_filters',
	'django.contrib.postgres',
	'django.contrib.admin',
	'django.contrib.auth',
	'django.contrib.contenttypes',
	'django.contrib.sessions',
	'django.contrib.messages',
	'django.contrib.staticfiles',
	'django.contrib.sites',
	'django.contrib.sitemaps',
	'organizations',
	'simple_history',
	'sitesettings',
	'indexers',
	'api',
	# mcpauth before oauth2_provider: its authorize.html must win the template lookup.
	'mcpauth',
	'oauth2_provider',
	'django_ckeditor_5',
]

MIDDLEWARE = [
	'django.middleware.security.SecurityMiddleware',
	'mcpauth.middleware.EditorHeaderGuardMiddleware',
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
		'OPTIONS': {
			# Default is 300. HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 3 adds a
			# paginator-count cache entry per (endpoint path, visible orgs,
			# filter params) combination on top of the existing /stats/
			# entries — left at 300 that thrashes: DatabaseCache's cull runs
			# a COUNT(*) on the cache table on every set() once over the
			# limit, then deletes by cache_key < lexical order (sha256 keys,
			# so effectively arbitrary eviction, not LRU), producing a
			# steady evict/miss/recompute-the-expensive-count/set cycle.
			'MAX_ENTRIES': int(os.environ.get('CACHE_MAX_ENTRIES', '10000')),
		},
	}
}

# TTL (seconds) for the /stats/ response cache. Override via STATS_CACHE_TTL env var.
STATS_CACHE_TTL = int(os.environ.get('STATS_CACHE_TTL', '600'))

# TTL (seconds) for the paginator COUNT(*) cache (HOUSE-LOAD-SPIKE-P2-QUERY-COST.md
# item 3). Kept short and independently tunable from STATS_CACHE_TTL: a stale
# count moves total_pages/the next link, and combined with the pagination
# offset cap a stale-high count can advertise a page that then 400s — see
# api/pagination.py CachedCountMixin. Override via COUNT_CACHE_TTL env var.
COUNT_CACHE_TTL = int(os.environ.get('COUNT_CACHE_TTL', '60'))

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
	],
	'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
	# Delegates to DRF's own default handler for everything except
	# gregory.site_resolution.NoSiteResolvedError (Phase 3 of site-scoped
	# API visibility's fail-closed 400), whose int fields it restores after
	# DRF's default handling would otherwise stringify them -- see that
	# module's exception_handler and NoSiteResolvedError docstrings.
	'EXCEPTION_HANDLER': 'gregory.site_resolution.exception_handler',
}

# drf-spectacular — OpenAPI schema generation. See /api/schema/, /api/schema/swagger-ui/,
# /api/schema/redoc/ (admin/urls.py). Canonical prose reference is
# docs/03-api-and-rss-feeds.md; this schema is the machine-checkable counterpart.
SPECTACULAR_SETTINGS = {
	'POSTPROCESSING_HOOKS': [
		'drf_spectacular.hooks.postprocess_schema_enums',
		'api.schema_hooks.make_editorial_optional',
	],
	'TITLE': 'GregoryAI API',
	'DESCRIPTION': (
		'REST API for GregoryAI: articles, clinical trials, authors, sources, '
		'subjects, categories, sponsors and teams. See docs/03-api-and-rss-feeds.md '
		'for prose documentation and authentication details.'
	),
	'VERSION': '1.0.0',
	'SERVE_INCLUDE_SCHEMA': False,
	# ApiKeyMiddleware (api/middleware.py) reads a raw API key straight off the
	# Authorization header (no "Bearer"/"Basic" prefix, no DRF authentication
	# class) — drf-spectacular can't discover it automatically, so it's declared
	# by hand here and referenced via `security=` on the views that use it.
	'APPEND_COMPONENTS': {
		'securitySchemes': {
			'editorServiceAuth': {
				'type': 'http',
				'scheme': 'bearer',
				'description': (
					'The MCP server\'s service credential (GREGORY_MCP_SERVICE_KEY), '
					'sent with X-Gregory-Editor-User and X-Gregory-Editor-Site naming the '
					'verified editor and the one site. Only the MCP server holds it; '
					'/editor/ is not reachable from the public internet.'
				),
			},
			'apiKeyAuth': {
				'type': 'apiKey',
				'in': 'header',
				'name': 'Authorization',
				'description': (
					'Raw API key (no "Bearer"/"Basic" prefix) issued per '
					'organisation. Required for write endpoints '
					'(articles/post, articles/edit, trials/edit); optional '
					'on read endpoints, where it scopes org-visibility and '
					'does not unlock editorial content (use ?include=editorial).'
				),
			},
		},
	},
}

# --- MCP editor access: OAuth 2.1 authorization server (MCP-AUTH-PLAN.md) ---
#
# django-oauth-toolkit is the authorization server; mcpauth/ holds the pieces
# that are ours: the per-site editor grant, the access token that carries one
# site and one tier (swapped in below), the login and consent pages, and the
# introspection endpoint the MCP server calls. The MCP server never sees a
# user's password and never forwards a user's token to this API: it asks
# /o/introspect/ who a token belongs to, then calls /editor/ with
# GREGORY_MCP_SERVICE_KEY (a single-purpose credential, not an APIAccessScheme).
OAUTH2_PROVIDER_ACCESS_TOKEN_MODEL = 'mcpauth.AccessToken'
# DOT's migrations read every swappable model setting. The three token models
# are swapped together (they reference each other); application and grant stay
# DOT's own.
OAUTH2_PROVIDER_APPLICATION_MODEL = 'oauth2_provider.Application'
OAUTH2_PROVIDER_GRANT_MODEL = 'oauth2_provider.Grant'
OAUTH2_PROVIDER_REFRESH_TOKEN_MODEL = 'mcpauth.RefreshToken'
OAUTH2_PROVIDER_ID_TOKEN_MODEL = 'mcpauth.IDToken'
OAUTH2_PROVIDER_DEVICE_GRANT_MODEL = 'oauth2_provider.DeviceGrant'

# Shared secret between this API and the MCP server. Unset means the
# introspection and /editor/ endpoints refuse every request.
GREGORY_MCP_SERVICE_KEY = os.environ.get('GREGORY_MCP_SERVICE_KEY', '')

# The editor address of a site is https://<prefix>.<site domain>/mcp/editor.
MCP_EDITOR_HOST_PREFIX = os.environ.get('MCP_EDITOR_HOST_PREFIX', 'gregory-ai')

# MCP editor writes allowed per (user, site) per window (D21). Start here and
# tune from production logs.
MCP_EDITOR_RATE_LIMITS = {
	'hour': int(os.environ.get('MCP_EDITOR_WRITES_PER_HOUR', '60')),
	'day': int(os.environ.get('MCP_EDITOR_WRITES_PER_DAY', '500')),
}

# Failed sign-ins allowed per window on the OAuth login page, per client
# address and per username.
OAUTH_LOGIN_MAX_FAILURES = int(os.environ.get('OAUTH_LOGIN_MAX_FAILURES', '10'))
OAUTH_LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60

# Dynamic registrations accepted per client address per hour.
OAUTH_DCR_MAX_PER_HOUR = int(os.environ.get('OAUTH_DCR_MAX_PER_HOUR', '30'))

# Registered clients unused this long are deleted (prune_oauth_clients).
OAUTH_CLIENT_UNUSED_DAYS = 90

OAUTH2_PROVIDER = {
	# Authorization code with PKCE (S256) only: no implicit, password or
	# client-credentials grants. The validator enforces this at the token
	# endpoint; the flags below make DOT's own checks agree with it.
	'PKCE_REQUIRED': True,
	'OAUTH2_VALIDATOR_CLASS': 'mcpauth.validators.EditorOAuth2Validator',
	'SCOPES': {
		'articles:read': 'Read articles, trials and authors for this site',
		'articles:edit': "Edit this site's article takeaways, relevance and trial links",
	},
	'DEFAULT_SCOPES': ['articles:read'],
	'ACCESS_TOKEN_EXPIRE_SECONDS': 60 * 60,
	'REFRESH_TOKEN_EXPIRE_SECONDS': 30 * 24 * 60 * 60,
	'ROTATE_REFRESH_TOKEN': True,
	'REFRESH_TOKEN_REUSE_PROTECTION': True,
	'AUTHORIZATION_CODE_EXPIRE_SECONDS': 60,
	# Native clients (Claude Code, Claude Desktop) redirect to a loopback port
	# chosen at run time (RFC 8252); everything else must be https.
	'ALLOWED_REDIRECT_URI_SCHEMES': ['https', 'http'],
	'ALLOW_LOCALHOST_LOOPBACK': True,
	# Client registration: RFC 7591 and Client ID Metadata Documents (D20).
	# Registration is open, so it is rate limited and restricted to
	# authorization-code clients (mcpauth.registration).
	'DCR_ENABLED': True,
	'DCR_REGISTRATION_PERMISSION_CLASSES': ('oauth2_provider.dcr.AllowAllDCRPermission',),
	'CIMD_ENABLED': True,
	'CIMD_METADATA_FETCHER': 'mcpauth.registration.PolicyMetadataFetcher',
	'OAUTH2_RESPONSE_TYPES_SUPPORTED': ['code'],
	'OAUTH2_GRANT_TYPES_SUPPORTED': ['authorization_code', 'refresh_token'],
	'OAUTH2_TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED': ['none', 'client_secret_post', 'client_secret_basic'],
	# RFC 9700 hardening: adopt the compliant behaviour rather than warn.
	'COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT': True,
	'COMPLIANT_BCP_RFC9700_PASSWORD_GRANT': True,
	'COMPLIANT_BCP_RFC9700_PKCE_METHOD': True,
	'COMPLIANT_BCP_RFC9700_PKCE_REQUIRED': True,
	'COMPLIANT_BCP_RFC9700_ACCESS_TOKEN_TRANSPORT': True,
	'COMPLIANT_BCP_RFC9700_AUTHZ_RESPONSE_ISS': True,
	'COMPLIANT_BCP_RFC9700_TOKEN_STORAGE': True,
	'COMPLIANT_BCP_RFC9700_REFRESH_TOKEN': True,
	# The issuer is the API domain over https. Django sits behind a TLS-terminating
	# proxy here, so deriving it from the request would advertise http://.
	'OIDC_ISS_ENDPOINT': os.environ.get('OAUTH_ISSUER') or (f'https://api.{_domain}' if _domain else ''),
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

