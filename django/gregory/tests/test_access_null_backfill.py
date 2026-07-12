"""
Regression tests for migration 0076_backfill_access_null_to_unknown:
NULL and "unknown" are the same semantic state for Articles.access, and
existing NULL rows should be backfilled to "unknown" for consistency.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_access_null_backfill
"""

import importlib

from django.apps import apps as global_apps
from django.test import TestCase

from gregory.models import Articles

# Migration module names start with digits, so they can't be imported with a
# plain dotted `import` statement.
_migration_module = importlib.import_module(
	"gregory.migrations.0076_backfill_access_null_to_unknown"
)
backfill_access_null_to_unknown = _migration_module.backfill_access_null_to_unknown


class AccessNullBackfillTests(TestCase):
	def test_null_access_rows_backfilled_to_unknown(self):
		null_access_article = Articles.objects.create(
			title="Null Access Article",
			link="https://example.com/null-access-article",
			access=None,
		)
		unknown_access_article = Articles.objects.create(
			title="Already Unknown Article",
			link="https://example.com/already-unknown-article",
			access="unknown",
		)
		open_access_article = Articles.objects.create(
			title="Open Access Article",
			link="https://example.com/open-access-article",
			access="open",
		)

		backfill_access_null_to_unknown(global_apps, None)

		null_access_article.refresh_from_db()
		unknown_access_article.refresh_from_db()
		open_access_article.refresh_from_db()

		self.assertEqual(null_access_article.access, "unknown")
		self.assertEqual(unknown_access_article.access, "unknown")
		self.assertEqual(open_access_article.access, "open")
