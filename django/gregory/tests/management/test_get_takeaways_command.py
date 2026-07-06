"""
Tests for the get_takeaways management command.

The command summarises article abstracts and writes the result into
ArticleOrgContent for every organisation the article belongs to via teams.

Run with:
    docker exec gregory python manage.py test gregory.tests.management.test_get_takeaways_command
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from io import StringIO
from unittest.mock import patch, MagicMock

from django.core.management import call_command
from django.test import TestCase
from organizations.models import Organization

from gregory.management.commands.get_takeaways import Command
from gregory.models import Articles, ArticleOrgContent, OrganizationApiSettings, Team


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org(name, slug=""):
	slug = slug or name.lower().replace(" ", "-")
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=False
	)
	return org


def _make_team(org, name):
	return Team.objects.create(
		organization=org, name=name, slug=name.lower().replace(" ", "-")
	)


def _make_article(teams, title, link, summary):
	article = Articles.objects.create(
		title=title,
		link=link,
		summary=summary,
		kind="science paper",
	)
	for team in teams:
		article.teams.add(team)
	return article


# A summary long enough for `get_summary_max_length` to return >25
_LONG_ABSTRACT = ("word " * 80).strip()


# ---------------------------------------------------------------------------
# Pure unit tests for static helpers
# ---------------------------------------------------------------------------


class GetTakeawaysHelpersTest(TestCase):
	def test_clean_html(self):
		self.assertEqual(Command.clean_html("<b>hi</b>"), "hi")

	def test_get_summary_max_length(self):
		text = "word " * 50
		self.assertEqual(Command.get_summary_max_length(text), 25)

	def test_summarize_abstract(self):
		mock_summarizer = MagicMock(return_value=[{"summary_text": "ok"}])
		result = Command.summarize_abstract(
			1, "word " * 60, mock_summarizer, min_length=10
		)
		mock_summarizer.assert_called_once()
		self.assertEqual(result, "ok")


# ---------------------------------------------------------------------------
# Integration-style tests against the DB
# ---------------------------------------------------------------------------


class GetTakeawaysFanOutTest(TestCase):
	"""Generating once per article should fan out to every linked org."""

	def setUp(self):
		self.org_a = _make_org("Takeaways Org A")
		self.org_b = _make_org("Takeaways Org B", "takeaways-org-b")
		self.team_a = _make_team(self.org_a, "Takeaways Team A")
		self.team_b = _make_team(self.org_b, "Takeaways Team B")
		self.article = _make_article(
			[self.team_a, self.team_b],
			"Multi-org article",
			"https://example.com/multi",
			_LONG_ABSTRACT,
		)

	@patch("gregory.management.commands.get_takeaways.pipeline")
	def test_fans_out_to_all_orgs_with_single_inference(self, mock_pipeline):
		summarizer = MagicMock(return_value=[{"summary_text": "GENERATED"}])
		summarizer.tokenizer = MagicMock()
		mock_pipeline.return_value = summarizer

		call_command("get_takeaways", stdout=StringIO())

		# Both orgs should now have rows with the generated takeaway.
		self.assertEqual(ArticleOrgContent.objects.count(), 2)
		for org in (self.org_a, self.org_b):
			row = ArticleOrgContent.objects.get(article=self.article, organization=org)
			self.assertEqual(row.takeaways, "GENERATED")

		# Crucially, the summariser ran exactly once even though we wrote two rows.
		self.assertEqual(summarizer.call_count, 1)
		# Model was loaded exactly once for the run.
		mock_pipeline.assert_called_once_with(
			"summarization", model="philschmid/bart-large-cnn-samsum"
		)

	@patch("gregory.management.commands.get_takeaways.pipeline")
	def test_org_id_scopes_generation(self, mock_pipeline):
		summarizer = MagicMock(return_value=[{"summary_text": "SCOPED"}])
		summarizer.tokenizer = MagicMock()
		mock_pipeline.return_value = summarizer

		call_command("get_takeaways", org_id=self.org_a.pk, stdout=StringIO())

		self.assertEqual(ArticleOrgContent.objects.count(), 1)
		row = ArticleOrgContent.objects.get()
		self.assertEqual(row.organization_id, self.org_a.pk)
		self.assertEqual(row.takeaways, "SCOPED")

	@patch("gregory.management.commands.get_takeaways.pipeline")
	def test_skips_orgs_that_already_have_takeaways(self, mock_pipeline):
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org_a,
			takeaways="Editor wrote this",
		)
		summarizer = MagicMock(return_value=[{"summary_text": "GENERATED"}])
		summarizer.tokenizer = MagicMock()
		mock_pipeline.return_value = summarizer

		call_command("get_takeaways", stdout=StringIO())

		# Org A row stays untouched, Org B gets a new row.
		row_a = ArticleOrgContent.objects.get(
			article=self.article, organization=self.org_a
		)
		row_b = ArticleOrgContent.objects.get(
			article=self.article, organization=self.org_b
		)
		self.assertEqual(row_a.takeaways, "Editor wrote this")
		self.assertEqual(row_b.takeaways, "GENERATED")

	@patch("gregory.management.commands.get_takeaways.pipeline")
	def test_fills_existing_row_with_empty_takeaways(self, mock_pipeline):
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org_a,
			takeaways="",
			summary_plain_english="Pre-existing plain english",
		)
		summarizer = MagicMock(return_value=[{"summary_text": "GENERATED"}])
		summarizer.tokenizer = MagicMock()
		mock_pipeline.return_value = summarizer

		call_command("get_takeaways", stdout=StringIO())

		row_a = ArticleOrgContent.objects.get(
			article=self.article, organization=self.org_a
		)
		self.assertEqual(row_a.takeaways, "GENERATED")
		# The previously-set plain English text must not be wiped.
		self.assertEqual(row_a.summary_plain_english, "Pre-existing plain english")


class GetTakeawaysOrphanTest(TestCase):
	"""Articles with no linked organisation are excluded by the upfront filter."""

	def setUp(self):
		self.article = Articles.objects.create(
			title="Orphan",
			link="https://example.com/orphan",
			summary=_LONG_ABSTRACT,
			kind="science paper",
		)

	@patch("gregory.management.commands.get_takeaways.pipeline")
	def test_orphan_is_excluded_upfront_and_never_processed(self, mock_pipeline):
		summarizer = MagicMock(return_value=[{"summary_text": "X"}])
		summarizer.tokenizer = MagicMock()
		mock_pipeline.return_value = summarizer

		err = StringIO()
		call_command("get_takeaways", stdout=StringIO(), stderr=err)

		self.assertEqual(ArticleOrgContent.objects.count(), 0)
		# Model is never loaded because no work was found: the upfront
		# queryset filter excludes the orphan entirely (it has zero orgs
		# linked via teams, so there's nothing it could fill), so it's never
		# scanned and no "orphan" warning is emitted for it. The warning path
		# in the command itself is retained only as a defensive guard for the
		# rare race between the queryset annotation and the per-article write.
		mock_pipeline.assert_not_called()
		self.assertNotIn("orphan", err.getvalue().lower())


class GetTakeawaysOrderingTest(TestCase):
	"""Oldest eligible articles must be processed first when --limit caps the run."""

	def setUp(self):
		self.org = _make_org("Ordering Org")
		self.team = _make_team(self.org, "Ordering Team")
		# Created in order, so `older` gets the lower article_id.
		self.older = _make_article(
			[self.team], "Older article", "https://example.com/older", _LONG_ABSTRACT
		)
		self.newer = _make_article(
			[self.team], "Newer article", "https://example.com/newer", _LONG_ABSTRACT
		)

	@patch("gregory.management.commands.get_takeaways.pipeline")
	def test_oldest_article_processed_first(self, mock_pipeline):
		summarizer = MagicMock(return_value=[{"summary_text": "GENERATED"}])
		summarizer.tokenizer = MagicMock()
		mock_pipeline.return_value = summarizer

		call_command("get_takeaways", limit=1, stdout=StringIO())

		# Only the older article should have been summarised.
		self.assertEqual(ArticleOrgContent.objects.count(), 1)
		row = ArticleOrgContent.objects.get()
		self.assertEqual(row.article_id, self.older.article_id)
		self.assertFalse(
			ArticleOrgContent.objects.filter(article=self.newer).exists()
		)


class GetTakeawaysUpfrontFilterTest(TestCase):
	"""Already-filled articles must not be fetched or consume --limit."""

	def setUp(self):
		self.org = _make_org("Upfront Org")
		self.team = _make_team(self.org, "Upfront Team")
		# Older article, already fully filled for its only org.
		self.filled = _make_article(
			[self.team], "Already filled", "https://example.com/filled", _LONG_ABSTRACT
		)
		# Newer article, still eligible.
		self.pending = _make_article(
			[self.team], "Still pending", "https://example.com/pending", _LONG_ABSTRACT
		)
		ArticleOrgContent.objects.create(
			article=self.filled, organization=self.org, takeaways="Pre-existing"
		)

	@patch("gregory.management.commands.get_takeaways.pipeline")
	def test_filled_article_excluded_does_not_consume_limit(self, mock_pipeline):
		summarizer = MagicMock(return_value=[{"summary_text": "GENERATED"}])
		summarizer.tokenizer = MagicMock()
		mock_pipeline.return_value = summarizer

		call_command("get_takeaways", limit=1, stdout=StringIO())

		# The already-filled (older) article is untouched...
		row_filled = ArticleOrgContent.objects.get(
			article=self.filled, organization=self.org
		)
		self.assertEqual(row_filled.takeaways, "Pre-existing")

		# ...and the newer, eligible article behind it still gets processed
		# even though --limit=1, because the filled article never consumed
		# the budget in the first place.
		row_pending = ArticleOrgContent.objects.get(
			article=self.pending, organization=self.org
		)
		self.assertEqual(row_pending.takeaways, "GENERATED")


class GetTakeawaysPartialFillTest(TestCase):
	"""An article linked to two orgs, one already filled, only fills the empty one."""

	def setUp(self):
		self.org_filled = _make_org("Partial Filled Org", "partial-filled-org")
		self.org_empty = _make_org("Partial Empty Org", "partial-empty-org")
		self.team_filled = _make_team(self.org_filled, "Partial Filled Team")
		self.team_empty = _make_team(self.org_empty, "Partial Empty Team")
		self.article = _make_article(
			[self.team_filled, self.team_empty],
			"Partial fill article",
			"https://example.com/partial",
			_LONG_ABSTRACT,
		)
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org_filled,
			takeaways="Already have this",
		)

	@patch("gregory.management.commands.get_takeaways.pipeline")
	def test_only_empty_org_row_is_written(self, mock_pipeline):
		summarizer = MagicMock(return_value=[{"summary_text": "GENERATED"}])
		summarizer.tokenizer = MagicMock()
		mock_pipeline.return_value = summarizer

		call_command("get_takeaways", stdout=StringIO())

		self.assertEqual(ArticleOrgContent.objects.count(), 2)
		row_filled = ArticleOrgContent.objects.get(
			article=self.article, organization=self.org_filled
		)
		row_empty = ArticleOrgContent.objects.get(
			article=self.article, organization=self.org_empty
		)
		self.assertEqual(row_filled.takeaways, "Already have this")
		self.assertEqual(row_empty.takeaways, "GENERATED")
		# One summariser call still covers both orgs.
		self.assertEqual(summarizer.call_count, 1)


class GetTakeawaysDryRunTest(TestCase):
	"""--dry-run must not load the model or write any rows."""

	def setUp(self):
		self.org = _make_org("Dry Run Org")
		self.team = _make_team(self.org, "Dry Run Team")
		self.article = _make_article(
			[self.team], "Dry article", "https://example.com/dry", _LONG_ABSTRACT
		)

	@patch("gregory.management.commands.get_takeaways.pipeline")
	def test_dry_run_writes_nothing_and_skips_model(self, mock_pipeline):
		call_command("get_takeaways", dry_run=True, stdout=StringIO())

		self.assertEqual(ArticleOrgContent.objects.count(), 0)
		mock_pipeline.assert_not_called()
