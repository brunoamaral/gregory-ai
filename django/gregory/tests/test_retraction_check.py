"""Tests for the retraction_check management command."""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.management.commands.retraction_check import Command
from gregory.models import Articles

_article_counter = 0


def _make_article(
	doi="10.1234/test",
	title=None,
	kind="science paper",
	retracted=False,
	published_date=None,
	crossref_retraction_check=None,
):
	"""Create an Articles instance with sensible defaults for retraction tests."""
	global _article_counter
	_article_counter += 1
	if title is None:
		title = f"Test Article {_article_counter}"
	if published_date is None:
		# 3 years ago — safely older than the 2-year threshold
		published_date = timezone.now() - timedelta(days=1095)
	return Articles.objects.create(
		title=title,
		link=f"http://example.com/article/{_article_counter}",
		doi=doi,
		kind=kind,
		retracted=retracted,
		published_date=published_date,
		crossref_retraction_check=crossref_retraction_check,
	)


class IsCrossrefFailedTest(TestCase):
	"""Unit tests for Command.is_crossref_failed."""

	def setUp(self):
		self.cmd = Command()

	def test_error_keyword_returns_true(self):
		self.assertTrue(self.cmd.is_crossref_failed("CrossRef HTTP error: 500"))

	def test_not_found_keyword_returns_true(self):
		self.assertTrue(self.cmd.is_crossref_failed("DOI not found"))

	def test_json_decode_keyword_returns_true(self):
		self.assertTrue(self.cmd.is_crossref_failed("JSON decode error"))

	def test_matching_is_case_insensitive(self):
		self.assertTrue(self.cmd.is_crossref_failed("ERROR: something bad"))
		self.assertTrue(self.cmd.is_crossref_failed("Not Found in registry"))
		self.assertTrue(self.cmd.is_crossref_failed("Json Decode issue"))

	def test_returns_false_for_none(self):
		self.assertFalse(self.cmd.is_crossref_failed(None))

	def test_returns_false_for_non_string_types(self):
		self.assertFalse(self.cmd.is_crossref_failed(404))
		self.assertFalse(self.cmd.is_crossref_failed({"status": "error"}))
		self.assertFalse(self.cmd.is_crossref_failed(["error"]))

	def test_returns_false_for_unrelated_string(self):
		self.assertFalse(self.cmd.is_crossref_failed("ok"))
		self.assertFalse(self.cmd.is_crossref_failed(""))


class CompareRetractionTest(TestCase):
	"""Unit tests for Command.compare_retraction."""

	def setUp(self):
		self.cmd = Command()
		self.cmd.stdout = StringIO()

	def _paper(self, retracted=False, doi="10.1234/test"):
		p = MagicMock()
		p.retracted = retracted
		p.doi = doi
		p.title = "Paper Title"
		return p

	def test_marks_article_retracted_when_paper_is_retracted(self):
		article = _make_article(doi="10.0001/a", retracted=False)
		self.cmd.compare_retraction(article, self._paper(retracted=True))
		article.refresh_from_db()
		self.assertTrue(article.retracted)

	def test_updates_check_timestamp_when_retracted(self):
		article = _make_article(doi="10.0001/b", retracted=False)
		self.cmd.compare_retraction(article, self._paper(retracted=True))
		article.refresh_from_db()
		self.assertIsNotNone(article.crossref_retraction_check)

	def test_does_not_change_retracted_status_when_paper_not_retracted(self):
		article = _make_article(doi="10.0001/c", retracted=False)
		self.cmd.compare_retraction(article, self._paper(retracted=False))
		article.refresh_from_db()
		self.assertFalse(article.retracted)

	def test_updates_timestamp_when_paper_not_retracted(self):
		article = _make_article(doi="10.0001/d", retracted=False)
		self.cmd.compare_retraction(article, self._paper(retracted=False))
		article.refresh_from_db()
		self.assertIsNotNone(article.crossref_retraction_check)

	def test_does_not_change_retracted_when_paper_retracted_is_none(self):
		# paper.retracted == None → None != True → goes to else branch
		article = _make_article(doi="10.0001/e", retracted=False)
		self.cmd.compare_retraction(article, self._paper(retracted=None))
		article.refresh_from_db()
		self.assertFalse(article.retracted)

	def test_already_retracted_article_stays_retracted(self):
		# paper.retracted == True but article.retracted == True → else branch
		article = _make_article(doi="10.0001/f", retracted=True)
		self.cmd.compare_retraction(article, self._paper(retracted=True))
		article.refresh_from_db()
		self.assertTrue(article.retracted)
		self.assertIsNotNone(article.crossref_retraction_check)

	def test_stdout_mentions_article_when_retracted(self):
		article = _make_article(doi="10.0001/g", retracted=False)
		paper = self._paper(retracted=True, doi="10.0001/g")
		paper.title = article.title
		self.cmd.compare_retraction(article, paper)
		out = self.cmd.stdout.getvalue()
		self.assertIn("Updated retraction status", out)

	def test_stdout_mentions_no_change_when_not_retracted(self):
		article = _make_article(doi="10.0001/h", retracted=False)
		self.cmd.compare_retraction(article, self._paper(retracted=False))
		out = self.cmd.stdout.getvalue()
		self.assertIn("No change", out)


class ArticleSelectionFilterTest(TestCase):
	"""
	Tests for the article selection query in check_retraction_status (no DOI arg).

	Current filter logic includes articles where:
	  - doi is not null/empty
	  - kind == "science paper"
	  - retracted == False
	  - published_date <= 2 years ago
	  - crossref_retraction_check is NULL  OR  within the last 30 days (crossref_retraction_check__gt=thirty_days_ago)

	Note: the crossref_retraction_check condition uses __gt (checked within 30 days
	OR never). Articles checked more than 30 days ago are excluded by this query.
	"""

	def setUp(self):
		self.cmd = Command()
		self.cmd.stdout = StringIO()

	def _run(self):
		mock_paper = MagicMock()
		mock_paper.refresh.return_value = None
		mock_paper.retracted = False
		with patch(
			"gregory.management.commands.retraction_check.SciencePaper",
			return_value=mock_paper,
		):
			self.cmd.check_retraction_status()
		return mock_paper

	def test_article_without_doi_is_excluded(self):
		_make_article(doi=None)
		paper = self._run()
		paper.refresh.assert_not_called()

	def test_article_with_empty_doi_is_excluded(self):
		_make_article(doi="")
		paper = self._run()
		paper.refresh.assert_not_called()

	def test_news_article_kind_is_excluded(self):
		_make_article(kind="news article")
		paper = self._run()
		paper.refresh.assert_not_called()

	def test_already_retracted_article_is_excluded(self):
		_make_article(retracted=True)
		paper = self._run()
		paper.refresh.assert_not_called()

	def test_article_published_within_two_years_is_excluded(self):
		recent = timezone.now() - timedelta(days=365)
		_make_article(published_date=recent)
		paper = self._run()
		paper.refresh.assert_not_called()

	def test_article_checked_more_than_30_days_ago_is_excluded(self):
		# Checked 60 days ago — outside the __gt=thirty_days_ago window
		sixty_days_ago = timezone.now() - timedelta(days=60)
		_make_article(crossref_retraction_check=sixty_days_ago)
		paper = self._run()
		paper.refresh.assert_not_called()

	def test_article_never_checked_is_included(self):
		_make_article(crossref_retraction_check=None, doi="10.2000/never-checked")
		paper = self._run()
		paper.refresh.assert_called_once()

	def test_article_checked_within_30_days_is_included(self):
		five_days_ago = timezone.now() - timedelta(days=5)
		_make_article(
			crossref_retraction_check=five_days_ago,
			doi="10.2000/checked-recently",
		)
		paper = self._run()
		paper.refresh.assert_called_once()

	def test_multiple_eligible_articles_all_processed(self):
		_make_article(doi="10.3000/first", crossref_retraction_check=None)
		_make_article(doi="10.3000/second", crossref_retraction_check=None)
		paper = self._run()
		self.assertEqual(paper.refresh.call_count, 2)

	def test_article_with_null_published_date_is_excluded(self):
		# NULL <= two_years_ago evaluates to NULL (falsy) in SQL,
		# so undated articles are silently skipped.
		_make_article(doi="10.3000/nodatearticle", published_date=None)
		# Override the default published_date by updating directly
		Articles.objects.filter(doi="10.3000/nodatearticle").update(published_date=None)
		paper = self._run()
		paper.refresh.assert_not_called()

	def test_stdout_reports_article_count_before_processing(self):
		_make_article(doi="10.3000/count-a", crossref_retraction_check=None)
		_make_article(doi="10.3000/count-b", crossref_retraction_check=None)
		self._run()
		self.assertIn("Found 2 articles to update", self.cmd.stdout.getvalue())

	def test_crossref_failure_writes_warning_to_stdout(self):
		_make_article(doi="10.3000/warn-doi", crossref_retraction_check=None)
		mock_paper = MagicMock()
		mock_paper.refresh.return_value = "CrossRef HTTP error: 503"
		with patch(
			"gregory.management.commands.retraction_check.SciencePaper",
			return_value=mock_paper,
		):
			self.cmd.check_retraction_status()
		out = self.cmd.stdout.getvalue()
		self.assertIn("CrossRef lookup failed", out)
		self.assertIn("10.3000/warn-doi", out)


class CheckRetractionStatusWithDOITest(TestCase):
	"""Tests for check_retraction_status when a specific DOI is supplied."""

	def setUp(self):
		self.cmd = Command()
		self.cmd.stdout = StringIO()

	def _run(self, doi, retracted_result=False, crossref_result=None):
		mock_paper = MagicMock()
		mock_paper.refresh.return_value = crossref_result
		mock_paper.retracted = retracted_result
		with patch(
			"gregory.management.commands.retraction_check.SciencePaper",
			return_value=mock_paper,
		) as MockSP:
			self.cmd.check_retraction_status(doi=doi)
		return mock_paper, MockSP

	def test_doi_arg_bypasses_publication_date_filter(self):
		"""A recently published article is still checked when DOI is explicit."""
		recent = timezone.now() - timedelta(days=30)
		_make_article(doi="10.4000/recent", published_date=recent)
		paper, _ = self._run("10.4000/recent")
		paper.refresh.assert_called_once()

	def test_doi_arg_does_not_bypass_kind_filter(self):
		"""kind='science paper' is enforced even when DOI is explicit."""
		_make_article(doi="10.4000/news", kind="news article")
		paper, _ = self._run("10.4000/news")
		paper.refresh.assert_not_called()

	def test_doi_arg_bypasses_old_check_timestamp_filter(self):
		"""An article last checked 60 days ago is re-checked when DOI is explicit."""
		sixty_days_ago = timezone.now() - timedelta(days=60)
		_make_article(doi="10.4000/oldcheck", crossref_retraction_check=sixty_days_ago)
		paper, _ = self._run("10.4000/oldcheck")
		paper.refresh.assert_called_once()

	def test_unknown_doi_processes_no_articles(self):
		"""No article is processed if none matches the given DOI."""
		paper, _ = self._run("10.9999/nonexistent")
		paper.refresh.assert_not_called()

	def test_crossref_failure_skips_article_without_exception(self):
		"""CrossRef error causes the article to be skipped; no exception is raised."""
		_make_article(doi="10.4000/broken")
		paper, _ = self._run("10.4000/broken", crossref_result="CrossRef HTTP error: 503")
		# refresh was called but compare_retraction should not have been called;
		# the article's crossref_retraction_check must remain None
		article = Articles.objects.get(doi="10.4000/broken")
		self.assertIsNone(article.crossref_retraction_check)

	def test_retracted_doi_updates_article_in_db(self):
		"""When CrossRef confirms retraction, the DB record is updated."""
		_make_article(doi="10.4000/retracted-yes", retracted=False)
		self._run("10.4000/retracted-yes", retracted_result=True)
		article = Articles.objects.get(doi="10.4000/retracted-yes")
		self.assertTrue(article.retracted)
		self.assertIsNotNone(article.crossref_retraction_check)

	def test_sciencepaper_instantiated_with_correct_doi(self):
		"""SciencePaper is constructed with the article's DOI."""
		_make_article(doi="10.4000/specific")
		_, MockSP = self._run("10.4000/specific")
		MockSP.assert_called_once_with(doi="10.4000/specific")


class HandleCommandTest(TestCase):
	"""End-to-end tests via call_command (the handle() method)."""

	def _call(self, mock_retracted=False, mock_crossref_result=None, **kwargs):
		out = StringIO()
		with patch(
			"gregory.management.commands.retraction_check.SciencePaper"
		) as MockSP:
			mock_paper = MagicMock()
			mock_paper.refresh.return_value = mock_crossref_result
			mock_paper.retracted = mock_retracted
			MockSP.return_value = mock_paper
			call_command("retraction_check", stdout=out, **kwargs)
		return out.getvalue(), MockSP

	def test_outputs_start_message(self):
		out, _ = self._call()
		self.assertIn("Starting retraction status check", out)

	def test_outputs_success_message(self):
		out, _ = self._call()
		self.assertIn("Successfully updated articles retraction status", out)

	def test_doi_argument_scopes_check_to_that_article(self):
		_make_article(doi="10.5000/only-this")
		_make_article(doi="10.5000/other", crossref_retraction_check=None)
		_, MockSP = self._call(doi="10.5000/only-this")
		# SciencePaper should only be instantiated once, for the requested DOI
		self.assertEqual(MockSP.call_count, 1)
		MockSP.assert_called_with(doi="10.5000/only-this")

	def test_crossref_error_does_not_abort_command(self):
		"""Command completes successfully even if CrossRef fails for an article."""
		_make_article(doi="10.5000/flaky")
		out, _ = self._call(mock_crossref_result="CrossRef HTTP error: 502")
		self.assertIn("Successfully updated", out)

	def test_eligible_article_gets_retraction_check_timestamp_updated(self):
		"""Running the command without --doi updates the check timestamp of an eligible article."""
		article = _make_article(doi="10.5000/eligible")
		self._call()
		article.refresh_from_db()
		self.assertIsNotNone(article.crossref_retraction_check)
