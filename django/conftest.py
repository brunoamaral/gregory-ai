"""Pytest configuration shared across the Django test suite.

The default cache backend (admin.settings) is Django's DatabaseCache, whose
table (`gregory_cache`) is created out-of-band via `manage.py createcachetable`
rather than by a migration. Freshly built test databases therefore lack the
table, so any test exercising the cache fails with
``ProgrammingError: relation "gregory_cache" does not exist``.

Create the cache table once, right after the test database is set up, so the
real DatabaseCache backend behaves exactly as it does in production.
"""

import pytest
from django.core.management import call_command


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
	with django_db_blocker.unblock():
		# createcachetable is idempotent — a no-op if the table already exists
		# (relevant when --reuse-db keeps the database between runs).
		call_command("createcachetable")
