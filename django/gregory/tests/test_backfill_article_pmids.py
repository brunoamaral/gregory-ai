"""Tests for backfill_article_pmids: PMIDs parsed from link/links, no API calls."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from gregory.management.commands.backfill_article_pmids import pmids_in_article_urls
from gregory.models import Articles


def make(n, link=None, links=None, pmid=None):
	return Articles.objects.create(
		title=f"Article {n}",
		link=link or f"https://example.org/{n}",
		links=links,
		pmid=pmid,
	)


def run(*args):
	out = StringIO()
	call_command("backfill_article_pmids", *args, stdout=out)
	return out.getvalue()


class PmidsInArticleUrlsTests(TestCase):
	def test_link_and_links_values_are_scanned(self):
		found = pmids_in_article_urls(
			"https://pubmed.ncbi.nlm.nih.gov/111/",
			{"pubmed.ncbi.nlm.nih.gov": "https://pubmed.ncbi.nlm.nih.gov/111/", "doi.org": "https://doi.org/10.1/x"},
		)
		self.assertEqual(found, {"111"})

	def test_non_dict_links_are_ignored(self):
		self.assertEqual(pmids_in_article_urls("https://example.org/1", None), set())
		self.assertEqual(pmids_in_article_urls("https://example.org/1", ["x"]), set())


class BackfillArticlePmidsTests(TestCase):
	def test_sets_a_single_pmid_from_link(self):
		a = make(1, link="https://pubmed.ncbi.nlm.nih.gov/38812345/")
		run()
		a.refresh_from_db()
		self.assertEqual(a.pmid, "38812345")

	def test_sets_a_pmid_found_only_in_links(self):
		a = make(2, links={"pubmed.ncbi.nlm.nih.gov": "https://pubmed.ncbi.nlm.nih.gov/222/"})
		run()
		a.refresh_from_db()
		self.assertEqual(a.pmid, "222")

	def test_article_without_a_pubmed_url_is_untouched(self):
		a = make(3)
		run()
		a.refresh_from_db()
		self.assertIsNone(a.pmid)

	def test_existing_pmid_is_never_overwritten(self):
		a = make(4, link="https://pubmed.ncbi.nlm.nih.gov/444/", pmid="999")
		run()
		a.refresh_from_db()
		self.assertEqual(a.pmid, "999")

	def test_pmid_on_two_articles_is_set_on_neither_and_reported(self):
		a = make(5, link="https://pubmed.ncbi.nlm.nih.gov/555/")
		b = make(6, links={"pubmed.ncbi.nlm.nih.gov": "https://pubmed.ncbi.nlm.nih.gov/555/"})
		output = run()
		a.refresh_from_db()
		b.refresh_from_db()
		self.assertIsNone(a.pmid)
		self.assertIsNone(b.pmid)
		self.assertIn("duplicate PMID 555", output)
		self.assertIn(str(a.article_id), output)
		self.assertIn(str(b.article_id), output)

	def test_pmid_already_held_by_another_article_is_not_reused(self):
		held = make(7, pmid="777")
		b = make(8, link="https://pubmed.ncbi.nlm.nih.gov/777/")
		output = run()
		b.refresh_from_db()
		self.assertIsNone(b.pmid)
		self.assertIn("duplicate PMID 777", output)
		self.assertIn(f"{held.article_id} already holds it", output)

	def test_article_naming_two_pmids_is_skipped(self):
		a = make(
			9,
			link="https://pubmed.ncbi.nlm.nih.gov/901/",
			links={"x": "https://pubmed.ncbi.nlm.nih.gov/902/"},
		)
		output = run()
		a.refresh_from_db()
		self.assertIsNone(a.pmid)
		self.assertIn("ambiguous", output)

	def test_dry_run_writes_nothing_but_reports(self):
		a = make(10, link="https://pubmed.ncbi.nlm.nih.gov/1010/")
		output = run("--dry-run")
		a.refresh_from_db()
		self.assertIsNone(a.pmid)
		self.assertIn("Would set 1 PMIDs", output)

	def test_ambiguous_article_keeps_other_articles_naming_its_pmids_out(self):
		x = make(
			11,
			link="https://pubmed.ncbi.nlm.nih.gov/1101/",
			links={"x": "https://pubmed.ncbi.nlm.nih.gov/1102/"},
		)
		y = make(12, link="https://pubmed.ncbi.nlm.nih.gov/1101/")
		output = run()
		y.refresh_from_db()
		self.assertIsNone(y.pmid)
		self.assertIn(f"duplicate PMID 1101: articles [{x.article_id}, {y.article_id}]", output)

	def test_rows_that_changed_since_the_scan_are_skipped_not_overwritten(self):
		from gregory.management.commands.backfill_article_pmids import Command

		taken = make(13, pmid="1301")
		changed = make(14, pmid="999")
		free = make(15)
		skipped = Command()._write({"1301": free.article_id, "1400": changed.article_id, "1500": free.article_id})
		changed.refresh_from_db()
		free.refresh_from_db()
		self.assertEqual(changed.pmid, "999")
		self.assertEqual(free.pmid, "1500")
		self.assertIn(("1301", free.article_id), skipped)
		self.assertIn(("1400", changed.article_id), skipped)
		self.assertEqual(taken.pmid, "1301")
