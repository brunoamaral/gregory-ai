"""
Tests for the migrate_legacy_takeaways management command.

Covers spec §10.5:
  - 2 articles (one with takeaways, one without) → exactly 1 ArticleOrgContent created
  - Re-run is idempotent (no duplicate rows created)
  - --dry-run reports counts and writes nothing
  - --org-id <bad-id> exits non-zero with clear error
  - --org-id <id> --noinput runs without prompting
  - Articles outside the chosen org are not migrated.

Run with:
    docker exec gregory python manage.py test gregory.tests.management.test_migrate_legacy_takeaways
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from organizations.models import Organization

from gregory.models import Articles, ArticleOrgContent, Team, OrganizationApiSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_org(name, slug=''):
	slug = slug or name.lower().replace(' ', '-')
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=False)
	return org


def _make_team(org, name):
	return Team.objects.create(organization=org, name=name, slug=name.lower().replace(' ', '-'))


def _make_article(team, title, link, takeaways=None):
	article = Articles.objects.create(title=title, link=link, takeaways=takeaways)
	article.teams.add(team)
	return article


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class MigrateLegacyTakeawaysCommandTest(TestCase):
	"""Core migration behaviour."""

	def setUp(self):
		self.org = _make_org('Migration Org')
		self.team = _make_team(self.org, 'Migration Team')
		# One article with takeaways, one without
		self.article_with = _make_article(
			self.team, 'Has Takeaways', 'https://example.com/with', takeaways='Some takeaways'
		)
		self.article_without = _make_article(
			self.team, 'No Takeaways', 'https://example.com/without', takeaways=None
		)

	def test_creates_exactly_one_org_content_row(self):
		"""Only the article with takeaways should produce an ArticleOrgContent row."""
		out = StringIO()
		call_command(
			'migrate_legacy_takeaways',
			org_id=self.org.pk,
			noinput=True,
			stdout=out,
		)
		self.assertEqual(ArticleOrgContent.objects.count(), 1)
		content = ArticleOrgContent.objects.get()
		self.assertEqual(content.article_id, self.article_with.article_id)
		self.assertEqual(content.takeaways, 'Some takeaways')
		self.assertEqual(content.organization_id, self.org.pk)

	def test_idempotent_on_rerun(self):
		"""Running the command twice does not create duplicate rows."""
		for _ in range(2):
			call_command(
				'migrate_legacy_takeaways',
				org_id=self.org.pk,
				noinput=True,
				stdout=StringIO(),
			)
		self.assertEqual(ArticleOrgContent.objects.count(), 1)

	def test_dry_run_writes_nothing(self):
		"""--dry-run reports counts but creates no database rows."""
		out = StringIO()
		call_command(
			'migrate_legacy_takeaways',
			org_id=self.org.pk,
			noinput=True,
			dry_run=True,
			stdout=out,
		)
		self.assertEqual(ArticleOrgContent.objects.count(), 0)
		output = out.getvalue()
		# Should mention at least 1 article would be migrated
		self.assertIn('1', output)

	def test_invalid_org_id_raises_error(self):
		"""--org-id with a nonexistent org should raise CommandError."""
		with self.assertRaises((CommandError, SystemExit)):
			call_command(
				'migrate_legacy_takeaways',
				org_id=99999,
				noinput=True,
				stdout=StringIO(),
			)

	def test_noinput_runs_without_prompting(self):
		"""--org-id <id> --noinput should complete without stdin interaction."""
		out = StringIO()
		# Should not raise EOFError or similar (which would happen if it
		# tried to read from stdin)
		call_command(
			'migrate_legacy_takeaways',
			org_id=self.org.pk,
			noinput=True,
			stdout=out,
		)
		self.assertEqual(ArticleOrgContent.objects.count(), 1)


class MigrateLegacyTakeawaysMultiOrgTest(TestCase):
	"""Only articles belonging to the specified org are migrated."""

	def setUp(self):
		self.org_a = _make_org('Multi Org A')
		self.org_b = _make_org('Multi Org B', 'multi-org-b')
		self.team_a = _make_team(self.org_a, 'Multi Team A')
		self.team_b = _make_team(self.org_b, 'Multi Team B')
		self.article_a = _make_article(
			self.team_a, 'Org A Article', 'https://example.com/a', takeaways='Org A takeaway'
		)
		self.article_b = _make_article(
			self.team_b, 'Org B Article', 'https://example.com/b', takeaways='Org B takeaway'
		)

	def test_only_migrates_chosen_org_articles(self):
		"""Migrating for Org A should not create content for Org B articles."""
		call_command(
			'migrate_legacy_takeaways',
			org_id=self.org_a.pk,
			noinput=True,
			stdout=StringIO(),
		)
		self.assertEqual(ArticleOrgContent.objects.count(), 1)
		content = ArticleOrgContent.objects.get()
		self.assertEqual(content.organization_id, self.org_a.pk)
