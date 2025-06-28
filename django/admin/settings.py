import os
from pathlib import Path
from cryptography.fernet import Fernet  # Ensure this import is included

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
            print(f"Loaded environment variables from {env_path}")
            break
    else:
        print("No .env file found in the checked locations.")
except ImportError:
    print("python-dotenv not installed. Environment variables must be set manually.")

# SECURITY WARNING: keep the secret key used in production secret!
# Use environment variable if available, otherwise use a default value for development
SECRET_KEY = os.environ.get('SECRET_KEY', 'DEFAULT SECRET_KEY')

# Print warning if using default key
if SECRET_KEY == 'DEFAULT SECRET_KEY':
    print("Using default SECRET_KEY for development. DO NOT use in production!")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG= True
SITE_ID = 1
# FERNET SECRET KEY
FERNET_SECRET_KEY = os.environ.get('FERNET_SECRET_KEY', 'DEFAULT KEY GOES HERE')

# Print warning if using default key
if FERNET_SECRET_KEY == 'DEFAULT KEY GOES HERE':
    print("Using default FERNET_SECRET_KEY for development. DO NOT use in production!")
FORMS_URLFIELD_ASSUME_HTTPS = True

ALLOWED_HOSTS = [
	'0.0.0.0',
	'localhost',
	'127.0.0.1',
	'gregory',
	'api.' + os.environ.get('DOMAIN_NAME', ''),
	'manage.' + os.environ.get('DOMAIN_NAME', ''),
]

CSRF_TRUSTED_ORIGINS = [
	'https://' + os.environ.get('DOMAIN_NAME', ''),
	'https://api.' + os.environ.get('DOMAIN_NAME', ''),
	'https://manage.' + os.environ.get('DOMAIN_NAME', ''),
]

# Application definition
INSTALLED_APPS = [
	'gregory.apps.GregoryConfig',
	'subscriptions.apps.SubscriptionsConfig',
	'rest_framework',
	'rest_framework.authtoken',
	'rest_framework_simplejwt',
	'rest_framework_csv',  # Add CSV renderer support
	'django_filters',
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
]

MIDDLEWARE = [
	'django.middleware.security.SecurityMiddleware',
	'django.contrib.sessions.middleware.SessionMiddleware',
	'django.middleware.common.CommonMiddleware',
	'django.middleware.csrf.CsrfViewMiddleware',
	'django.contrib.auth.middleware.AuthenticationMiddleware',
	'django.contrib.messages.middleware.MessageMiddleware',
	'django.middleware.clickjacking.XFrameOptionsMiddleware',
	'django.middleware.gzip.GZipMiddleware',
	'django.contrib.sites.middleware.CurrentSiteMiddleware',
	'simple_history.middleware.HistoryRequestMiddleware',
	'api.middleware.StreamingCSVMiddleware',
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
	}
}

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

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Rest Framework
REST_FRAMEWORK = {
	'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
	'PAGE_SIZE': 10,
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
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS')
EMAIL_DOMAIN = os.environ.get('EMAIL_DOMAIN')

EMAIL_MAILGUN_API = os.environ.get('EMAIL_MAILGUN_API')
EMAIL_MAILGUN_API_URL = os.environ.get('EMAIL_MAILGUN_API_URL')

EMAIL_POSTMARK_API = os.environ.get('EMAIL_POSTMARK_API')
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

if not FERNET_SECRET_KEY:
		if DEBUG:  # Only generate a key in development
				FERNET_SECRET_KEY = Fernet.generate_key().decode()
				print("Temporary FERNET_SECRET_KEY generated for development.")
		else:
				raise ValueError("FERNET_SECRET_KEY is required in production.")