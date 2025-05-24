"""
Test settings for Gregory AI Django tests.

These settings override the base settings to create a lightweight
test environment that avoids Django migrations.
"""
from admin.settings import *

# Use in-memory SQLite database for fast tests
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {
            'NAME': ':memory:',
        },
    }
}

# Skip migrations during testing to avoid conflicts
# This will create all tables directly from the model definitions
MIGRATION_MODULES = {app: None for app in INSTALLED_APPS}

# Disable timezone-aware datetimes for testing simplicity
USE_TZ = False
