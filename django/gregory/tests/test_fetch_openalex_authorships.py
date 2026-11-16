"""Tests for fetch_openalex_authorships: selection, back-off, errors and options.

Never calls OpenAlex: OpenAlexClient.works_by_doi is mocked at the client boundary.
"""

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.management.commands.fetch_openalex_authorships import articles_to_fetch
from gregory.models import ArticleAuthorship, Articles, Authors
from gregory.utils.openalex import OpenAlexBudgetError, OpenAlexError

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "openalex" / "works-sample.json").read_text())
WORKS = {w["doi"].replace("https://doi.org/", ""): w for w in SAMPLE["results"]}
DOI_A = "10.1186/s12974-023-02861-3"
DOI_B = "10.1016/j.crmeth.2023.100533"
NOW = timezone.now()

PATH = "gregory.management.commands.fetch_openalex_authorships"


def make(doi, title=None, authors=True, **kwargs):
	article = Articles.objects.create(
		title=title or f"Article {doi}", link=f"https://ex.org/{doi}", doi=doi, **kwargs
	)
	if authors:
		author, _ = Authors.objects.get_or_create(given_name="Ruth", family_name="Harvey")
		article.authors.add(author)
	return article


def run(*args, works=None, side_effect=None):
	"""Run the command against a mocked client. Returns (stdout, mock of works_by_doi)."""
	out = StringIO()
	with patch(f"{PATH}.OpenAlexClient.works_by_doi") as fetch:
		if side_effect is not None:
			fetch.side_effect = side_effect
		else:
			fetch.side_effect = lambda dois: {d: w for d, w in (works or WORKS).items() if d in dois}
		call_command("fetch_openalex_authorships", *args, stdout=out)
	return out.getvalue(), fetch


class SelectionTests(TestCase):
	def due(self, **kwargs):
		return [a.doi for a in articles_to_fetch(**kwargs)]

	def test_an_article_with_a_doi_and_authors_is_selected(self):
		make("10.1/due")
		self.assertEqual(self.due(), ["10.1/due"])

	def test_no_doi_no_authors_already_checked_and_not_yet_due_are_skipped(self):
		make("10.1/due", title="due")
		Articles.objects.create(title="no doi", link="https://ex.org/n")
		Articles.objects.create(title="empty doi", link="https://ex.org/e", doi="")
		make("10.1/noauthors", title="noauthors", authors=False)
		make("10.1/checked", title="checked", openalex_checked_at=NOW)
		make("10.1/later", title="later", openalex_next_check=NOW + timedelta(days=3))
		self.assertEqual(self.due(), ["10.1/due"])

	def test_a_marker_that_is_due_is_selected(self):
		make("10.1/past", openalex_next_check=NOW - timedelta(days=1))
		self.assertEqual(self.due(), ["10.1/past"])

	def test_all_ignores_the_backoff(self):
		make("10.1/later", openalex_next_check=NOW + timedelta(days=3))
		self.assertEqual(self.due(ignore_backoff=True), ["10.1/later"])

	def test_refresh_takes_checked_articles_oldest_first(self):
		make("10.1/new", title="new", openalex_checked_at=NOW)
		make("10.1/old", title="old", openalex_checked_at=NOW - timedelta(days=30))
		make("10.1/never", title="never")
		self.assertEqual(self.due(refresh=True), ["10.1/old", "10.1/new"])

	def test_newest_first(self):
		make("10.1/a", title="a", published_date=datetime(2020, 1, 1, tzinfo=dt_timezone.utc))
		make("10.1/b", title="b", published_date=datetime(2024, 1, 1, tzinfo=dt_timezone.utc))
		make("10.1/c", title="c")
		self.assertEqual(self.due(), ["10.1/b", "10.1/a", "10.1/c"])

	def test_an_article_id_selects_that_article_whatever_its_state(self):
		article = make("10.1/checked", openalex_checked_at=NOW)
		self.assertEqual(self.due(article_id=article.pk), ["10.1/checked"])


class RunTests(TestCase):
	def setUp(self):
		patcher = patch("gregory.utils.openalex._sleep")
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_fetches_applies_and_reports(self):
		article = make(DOI_A)
		harvey = article.authors.get()
		out, fetch = run()
		article.refresh_from_db()
		self.assertEqual(article.openalex_id, "W4385549237")
		self.assertIsNotNone(article.openalex_checked_at)
		row = ArticleAuthorship.objects.get(article=article)
		self.assertEqual(row.author_id, harvey.pk)
		harvey.refresh_from_db()
		self.assertEqual(harvey.openalex_id, row.openalex_author_id)
		self.assertIn("1 articles fetched", out)
		self.assertIn("1 author links matched", out)
		self.assertIn("5 authorships unmatched", out)
		self.assertIn("1 institutions created", out)
		self.assertIn("OpenAlex cost this run", out)
		self.assertEqual(sorted(fetch.call_args.args[0]), [DOI_A])

	def test_a_missing_doi_records_a_fruitless_attempt(self):
		article = make("10.1/unknown")
		history_before = article.history.count()
		out, _ = run()
		article.refresh_from_db()
		self.assertIsNone(article.openalex_checked_at)
		self.assertEqual(article.openalex_attempts, 1)
		self.assertGreater(article.openalex_next_check, timezone.now() + timedelta(days=1))
		self.assertIn("1 DOIs not found", out)
		self.assertEqual(article.history.count(), history_before)
		# Not due again until the marker is.
		self.assertEqual([a.pk for a in articles_to_fetch()], [])

	def test_backoff_grows_with_each_attempt(self):
		article = make("10.1/unknown")
		run()
		Articles.objects.filter(pk=article.pk).update(openalex_next_check=timezone.now() - timedelta(minutes=1))
		run()
		article.refresh_from_db()
		self.assertEqual(article.openalex_attempts, 2)
		self.assertGreater(article.openalex_next_check, timezone.now() + timedelta(days=3))

	def test_a_budget_error_stops_the_run_without_marking(self):
		first = make(DOI_A, title="first", published_date=datetime(2024, 1, 1, tzinfo=dt_timezone.utc))
		second = make("10.1/other", title="second", published_date=datetime(2023, 1, 1, tzinfo=dt_timezone.utc))
		out, fetch = run(side_effect=OpenAlexBudgetError("HTTP 429"))
		for article in (first, second):
			article.refresh_from_db()
			self.assertIsNone(article.openalex_checked_at)
			self.assertEqual(article.openalex_attempts, 0)
			self.assertIsNone(article.openalex_next_check)
		self.assertEqual(fetch.call_count, 1)
		self.assertIn("Stopped", out)

	def test_a_budget_error_in_a_later_batch_keeps_the_earlier_work(self):
		with patch(f"{PATH}.MAX_DOIS_PER_REQUEST", 1):
			first = make(DOI_A, title="first", published_date=datetime(2024, 1, 1, tzinfo=dt_timezone.utc))
			second = make(DOI_B, title="second", published_date=datetime(2023, 1, 1, tzinfo=dt_timezone.utc))
			calls = []

			def fetch(dois):
				calls.append(dois)
				if len(calls) == 2:
					raise OpenAlexBudgetError("HTTP 429")
				return {d: w for d, w in WORKS.items() if d in dois}

			out, _ = run(side_effect=fetch)
		first.refresh_from_db()
		second.refresh_from_db()
		self.assertIsNotNone(first.openalex_checked_at)
		self.assertIsNone(second.openalex_checked_at)
		self.assertEqual(second.openalex_attempts, 0)

	def test_a_network_failure_skips_one_batch_and_carries_on(self):
		with patch(f"{PATH}.MAX_DOIS_PER_REQUEST", 1):
			first = make(DOI_A, title="first", published_date=datetime(2024, 1, 1, tzinfo=dt_timezone.utc))
			second = make(DOI_B, title="second", published_date=datetime(2023, 1, 1, tzinfo=dt_timezone.utc))
			calls = []

			def fetch(dois):
				calls.append(dois)
				if len(calls) == 1:
					raise OpenAlexError("unreachable")
				return {d: w for d, w in WORKS.items() if d in dois}

			out, _ = run(side_effect=fetch)
		first.refresh_from_db()
		second.refresh_from_db()
		self.assertIsNone(first.openalex_checked_at)
		self.assertEqual(first.openalex_attempts, 0)
		self.assertIsNotNone(second.openalex_checked_at)
		self.assertIn("1 failed batches", out)

	def test_an_unusable_doi_is_skipped_with_a_backoff_attempt(self):
		bad = make("10.1/a|b", title="bad")
		good = make(DOI_A, title="good")
		_, fetch = run()
		self.assertEqual(sorted(fetch.call_args.args[0]), [DOI_A])
		bad.refresh_from_db()
		good.refresh_from_db()
		self.assertIsNone(bad.openalex_checked_at)
		self.assertEqual(bad.openalex_attempts, 1)
		self.assertIsNotNone(good.openalex_checked_at)

	def test_refresh_refetches_and_replaces(self):
		article = make(DOI_A, openalex_checked_at=NOW - timedelta(days=60))
		stale_author = Authors.objects.create(given_name="Old", family_name="Person")
		article.authors.add(stale_author)
		ArticleAuthorship.objects.create(
			article=article, author=stale_author, position="middle", author_index=9, openalex_author_id="A9"
		)
		run()
		self.assertTrue(ArticleAuthorship.objects.filter(article=article, author=stale_author).exists())
		run("--refresh")
		self.assertFalse(ArticleAuthorship.objects.filter(article=article, author=stale_author).exists())
		article.refresh_from_db()
		self.assertGreater(article.openalex_checked_at, NOW - timedelta(minutes=1))

	def test_a_checked_article_missing_on_refresh_is_not_given_a_marker(self):
		article = make("10.1/gone", openalex_checked_at=NOW - timedelta(days=60))
		run("--refresh")
		article.refresh_from_db()
		self.assertEqual(article.openalex_attempts, 0)
		self.assertEqual(article.openalex_checked_at, NOW - timedelta(days=60))

	def test_dry_run_calls_nothing_and_writes_nothing(self):
		article = make(DOI_A)
		out, fetch = run("--dry-run")
		fetch.assert_not_called()
		article.refresh_from_db()
		self.assertIsNone(article.openalex_checked_at)
		self.assertFalse(ArticleAuthorship.objects.exists())
		self.assertIn("1 articles due", out)

	def test_limit_caps_the_run(self):
		make(DOI_A, title="a")
		make(DOI_B, title="b")
		_, fetch = run("--limit", "1")
		self.assertEqual(len(fetch.call_args.args[0]), 1)

	def test_article_id_fetches_just_that_article(self):
		make(DOI_A, title="a")
		other = make(DOI_B, title="b")
		_, fetch = run("--article-id", str(other.pk))
		self.assertEqual(list(fetch.call_args.args[0]), [DOI_B])

	def test_the_pipeline_runs_it_after_the_pubmed_refresh(self):
		from gregory.management.commands import pipeline

		source = Path(pipeline.__file__).read_text()
		self.assertLess(source.index('"refresh_pubmed_records"'), source.index('"fetch_openalex_authorships"'))
