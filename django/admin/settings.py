import os
from pathlib import Path
from cryptography.fernet import Fernet  # Ensure this import is included

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'False').lower() in ('true', '1', 'yes')
SITE_ID = 1
# FERNET SECRET KEY
FERNET_SECRET_KEY = os.environ.get('FERNET_SECRET_KEY')


ALLOWED_HOSTS = [
	'0.0.0.0',
	'localhost',
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
	)
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