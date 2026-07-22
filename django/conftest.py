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

from django.db import connections
from django.db.models.signals import post_migrate, pre_migrate


def _create_pg_trgm_extension(sender, **kwargs):
	using = kwargs.get("using", "default")
	with connections[using].cursor() as cursor:
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
	with connections[using].cursor() as cursor:
		for sql in _CUSTOM_INDEXES:
			cursor.execute(sql)


pre_migrate.connect(_create_pg_trgm_extension)
post_migrate.connect(_create_custom_perf_indexes)
