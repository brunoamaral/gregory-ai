"""Pytest configuration shared across the Django test suite.

``--no-migrations`` (pytest.ini) skips all migrations, including hand-written
``RunSQL`` operations that aren't derivable from current model state:

- ``CREATE EXTENSION pg_trgm`` (gregory/migrations/0019): several models
  declare GIN trigram indexes in their ``Meta.indexes`` (e.g.
  ``authors_ufull_name_gin_idx``), which sync_apps still creates directly
  from the model state — but that CREATE INDEX fails without the extension
  in place first. Created on the ``pre_migrate`` signal, which Django fires
  before sync_apps regardless of migration mode.

- Hand-named performance indexes (gregory/migrations/0022, net of the
  columns 0050 later superseded with ``db_index=True``) that live only as
  raw SQL, not as ``Meta.indexes`` entries. Created on ``post_migrate``,
  once sync_apps has built the tables they apply to.

The historical migrations that only repair stale FK constraints (0005,
0009, 0037, 0047, 0073, subscriptions/0012) are omitted here on purpose:
they patch up databases restored from old dumps, and a syncdb-built test
database never has the wrong constraint to begin with.
"""

import pytest
from django.core.cache import cache
from django.db import connections
from django.db.models.signals import post_migrate, pre_migrate


@pytest.fixture(autouse=True)
def _reset_cache_between_tests():
	"""Clear the Django cache before every test.

	Django rolls back the database between tests but never touches the cache,
	so anything cache-backed leaks from one test into the next. Two things in
	this codebase are:

	- DRF's ScopedRateThrottle. ``bulk_export`` is 4/hour keyed on client IP,
	  and every test request comes from the same anonymous 127.0.0.1 — so the
	  5th ``all_results=true`` request in a worker process got a 429 no matter
	  which test issued it. The victim was whichever test happened to run 5th,
	  which under ``--dist loadfile -n auto`` depends on how files land on
	  workers: green locally, red in CI, different failures each run. It hit
	  test_trial_site_api (429 instead of the expected 400) and the CSV export
	  tests (throttle body ``{"detail": ...}`` rendered as a CSV with a single
	  ``detail`` column, so the row assertions saw an empty set).
	- The stats endpoints' ``STATS_CACHE_TTL`` responses, where a stale entry
	  from a previous test's fixtures could be served to the next one.

	A handful of tests already called ``cache.clear()`` by hand; this makes it
	universal so a new cache-touching test can't reintroduce the same flake.
	"""
	cache.clear()
	yield
	cache.clear()

# Both signals fire once per installed app (see
# django.core.management.sql.emit_{pre,post}_migrate_signal); gate on a
# single app label so the SQL below runs exactly once per test DB setup
# instead of once per app.
_GATE_APP_LABEL = "gregory"


def _create_pg_trgm_extension(sender, **kwargs):
	using = kwargs.get("using", "default")
	connection = connections[using]
	if sender.label != _GATE_APP_LABEL or connection.vendor != "postgresql":
		return
	with connection.cursor() as cursor:
		cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


_CUSTOM_INDEXES = (
	"CREATE INDEX IF NOT EXISTS idx_articles_team_categories_category_id ON articles_team_categories (teamcategory_id);",
	"CREATE INDEX IF NOT EXISTS idx_articles_team_categories_article_id ON articles_team_categories (articles_id);",
	"CREATE INDEX IF NOT EXISTS idx_trials_team_categories_category_id ON trials_team_categories (teamcategory_id);",
	"CREATE INDEX IF NOT EXISTS idx_trials_team_categories_trial_id ON trials_team_categories (trials_id);",
	"CREATE INDEX IF NOT EXISTS idx_articles_authors_article_id ON articles_authors (articles_id);",
	"CREATE INDEX IF NOT EXISTS idx_articles_authors_author_id ON articles_authors (authors_id);",
	"CREATE INDEX IF NOT EXISTS idx_trials_discovery_date ON trials (discovery_date);",
	"CREATE INDEX IF NOT EXISTS idx_team_categories_team_subject ON team_categories (team_id, id);",
	"CREATE INDEX IF NOT EXISTS idx_team_categories_slug ON team_categories (category_slug);",
	"CREATE INDEX IF NOT EXISTS idx_team_categories_team_id ON team_categories (team_id);",
	"CREATE INDEX IF NOT EXISTS idx_articles_covering ON articles (article_id, title, published_date, discovery_date);",
	"CREATE INDEX IF NOT EXISTS idx_trials_covering ON trials (trial_id, title, published_date, discovery_date);",
)


def _create_custom_perf_indexes(sender, **kwargs):
	using = kwargs.get("using", "default")
	connection = connections[using]
	if sender.label != _GATE_APP_LABEL or connection.vendor != "postgresql":
		return
	with connection.cursor() as cursor:
		for sql in _CUSTOM_INDEXES:
			cursor.execute(sql)


pre_migrate.connect(_create_pg_trgm_extension, dispatch_uid="conftest_create_pg_trgm_extension")
post_migrate.connect(_create_custom_perf_indexes, dispatch_uid="conftest_create_custom_perf_indexes")
