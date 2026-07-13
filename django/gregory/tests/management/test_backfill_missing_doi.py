"""
Tests for the backfill_missing_doi management command (articles-missing-doi
fix, phase 2).

All network calls (PubMed E-utilities, CrossRef) are mocked -- these tests
never hit a real external API.

Run:
	docker exec gregory python manage.py test gregory.tests.management.test_backfill_missing_doi
"""

import os
from io import StringIO
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase

from gregory.models import Articles

GET_DOI_TARGET = "gregory.management.commands.backfill_missing_doi.greg.get_doi"
PUBMED_RESOLVE_TARGET = (
	"gregory.management.commands.backfill_missing_doi.resolve_doi_from_pubmed_url"
)


class BackfillMissingDoiCommandTest(TestCase):
	def setUp(self):
		# Bucket 1: DOI embedded in a Springer article URL.
		self.url_case = Articles.objects.create(
			title="Springer article with URL DOI",
			link="https://link.springer.com/article/10.1007/s10484-026-09800-x",
			kind="science paper",
		)
		# Bucket 1b: DOI embedded via a doi.org resolver link.
		self.doi_org_case = Articles.objects.create(
			title="DOI resolver link",
			link="https://doi.org/10.3109/some.identifier",
			kind="science paper",
		)
		# Bucket 2: PubMed link, resolved via PMID.
		self.pubmed_case = Articles.objects.create(
			title="PubMed article",
			link="https://pubmed.ncbi.nlm.nih.gov/38812345/",
			kind="science paper",
		)
		# Bucket 3: no URL/PubMed signal, resolved via CrossRef title search.
		self.crossref_case = Articles.objects.create(
			title="Repository entry resolvable via CrossRef",
			link="https://hdl.handle.net/10520/EJC-1234abcd",
			kind="science paper",
		)
		# Bucket 4: genuinely no DOI anywhere.
		self.not_found_case = Articles.objects.create(
			title="Genuinely DOI-less thesis",
			link="https://hdl.handle.net/10520/EJC-9999zzzz",
			kind="science paper",
		)
		# Control: already has a DOI, must never be touched.
		self.has_doi_case = Articles.objects.create(
			title="Already has a DOI",
			link="https://example.com/already-has-doi",
			doi="10.9999/already-has-doi",
			kind="science paper",
		)
		# Control: not a science paper, must be excluded from selection.
		self.news_case = Articles.objects.create(
			title="A news article",
			link="https://hdl.handle.net/10520/EJC-news",
			kind="news article",
		)

	def _crossref_side_effect(self, title):
		if title == self.crossref_case.title:
			return "10.5555/crossref-resolved"
		return None

	def run_command(self, *extra_args):
		out = StringIO()
		with patch(
			PUBMED_RESOLVE_TARGET,
			side_effect=lambda link: (
				"10.1234/pubmed-resolved"
				if "38812345" in (link or "")
				else None
			),
		), patch(GET_DOI_TARGET, side_effect=self._crossref_side_effect):
			call_command("backfill_missing_doi", *extra_args, stdout=out)
		return out.getvalue()

	def test_resolves_url_embedded_doi(self):
		self.run_command()
		self.url_case.refresh_from_db()
		self.assertEqual(
			self.url_case.doi, "10.1007/s10484-026-09800-x"
		)

	def test_resolves_doi_org_resolver_link(self):
		self.run_command()
		self.doi_org_case.refresh_from_db()
		self.assertEqual(self.doi_org_case.doi, "10.3109/some.identifier")

	def test_resolves_pubmed_via_pmid(self):
		self.run_command()
		self.pubmed_case.refresh_from_db()
		self.assertEqual(self.pubmed_case.doi, "10.1234/pubmed-resolved")

	def test_resolves_via_crossref_title_search_as_last_resort(self):
		self.run_command()
		self.crossref_case.refresh_from_db()
		self.assertEqual(self.crossref_case.doi, "10.5555/crossref-resolved")

	def test_leaves_genuinely_doi_less_article_untouched(self):
		self.run_command()
		self.not_found_case.refresh_from_db()
		self.assertFalse(self.not_found_case.doi)

	def test_does_not_touch_article_that_already_has_a_doi(self):
		self.run_command()
		self.has_doi_case.refresh_from_db()
		self.assertEqual(self.has_doi_case.doi, "10.9999/already-has-doi")

	def test_excludes_non_science_paper_kind(self):
		self.run_command()
		self.news_case.refresh_from_db()
		self.assertFalse(self.news_case.doi)

	def test_dry_run_does_not_write(self):
		self.run_command("--dry-run")
		self.url_case.refresh_from_db()
		self.doi_org_case.refresh_from_db()
		self.pubmed_case.refresh_from_db()
		self.crossref_case.refresh_from_db()
		self.assertFalse(self.url_case.doi)
		self.assertFalse(self.doi_org_case.doi)
		self.assertFalse(self.pubmed_case.doi)
		self.assertFalse(self.crossref_case.doi)

	def test_dry_run_reports_summary(self):
		output = self.run_command("--dry-run")
		self.assertIn("Resolved via URL: 2", output)
		self.assertIn("Resolved via PubMed: 1", output)
		self.assertIn("Resolved via CrossRef: 1", output)
		self.assertIn("Not found: 1", output)
		self.assertIn("Dry run", output)

	def test_summary_counts_on_real_run(self):
		output = self.run_command()
		self.assertIn("Resolved via URL: 2", output)
		self.assertIn("Resolved via PubMed: 1", output)
		self.assertIn("Resolved via CrossRef: 1", output)
		self.assertIn("Not found: 1", output)

	def test_limit_option_restricts_batch_size(self):
		out = StringIO()
		with patch(PUBMED_RESOLVE_TARGET, return_value=None), patch(
			GET_DOI_TARGET, return_value=None
		):
			call_command(
				"backfill_missing_doi", "--dry-run", "--limit", "1", stdout=out
			)
		output = out.getvalue()
		self.assertIn("Total processed: 1", output)

	def test_limit_zero_processes_no_rows(self):
		# --limit 0 must select zero rows, not fall through to the full queryset.
		out = StringIO()
		with patch(PUBMED_RESOLVE_TARGET, return_value=None), patch(
			GET_DOI_TARGET, return_value=None
		):
			call_command(
				"backfill_missing_doi", "--dry-run", "--limit", "0", stdout=out
			)
		output = out.getvalue()
		self.assertIn("Total processed: 0", output)
		# Nothing was written or even attempted.
		self.url_case.refresh_from_db()
		self.assertFalse(self.url_case.doi)

	def test_crossref_network_failure_is_caught_and_counted_as_not_found(self):
		out = StringIO()
		with patch(PUBMED_RESOLVE_TARGET, return_value=None), patch(
			GET_DOI_TARGET, side_effect=ConnectionError("network down")
		):
			call_command("backfill_missing_doi", "--dry-run", stdout=out)
		output = out.getvalue()
		self.assertIn("CrossRef lookup failed", output)

	def test_collision_merges_into_existing_article(self):
		"""If the resolved DOI already belongs to another article, the rows
		merge instead of tripping the unique constraint (assign_doi_or_merge)."""
		Articles.objects.create(
			title="Existing article with the same DOI",
			link="https://example.com/existing",
			doi="10.1007/s10484-026-09800-x",
			kind="science paper",
		)
		out = StringIO()
		with patch(PUBMED_RESOLVE_TARGET, return_value=None), patch(
			GET_DOI_TARGET, return_value=None
		):
			call_command(
				"backfill_missing_doi",
				"--limit",
				"1000",
				stdout=out,
			)
		# The url_case article should have been merged away (or itself survive
		# and hold the DOI) -- either way exactly one article holds the DOI.
		matches = Articles.objects.filter(doi__iexact="10.1007/s10484-026-09800-x")
		self.assertEqual(matches.count(), 1)
