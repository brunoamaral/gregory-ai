"""Tests for refresh_pubmed_records: selection rules, batching and error handling."""

from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.management.commands.refresh_pubmed_records import articles_due_for_refresh
from gregory.models import Articles
from gregory.utils.ncbi import NcbiError

SAMPLE_XML = (Path(__file__).parent / "fixtures" / "pubmed" / "efetch-sample-3-records.xml").read_bytes()
NOW = timezone.now()


def make(pmid, checked_days_ago=None, status=None, **kwargs):
	checked = None if checked_days_ago is None else NOW - timedelta(days=checked_days_ago)
	return Articles.objects.create(
		title=f"Article {pmid}",
		link=f"https://ex.org/{pmid}",
		pmid=pmid,
		pubmed_checked_at=checked,
		pubmed_status=status,
		**kwargs,
	)


def due_pmids():
	return [a.pmid for a in articles_due_for_refresh(now=NOW)]


class SelectionTests(TestCase):
	def test_articles_without_a_pmid_are_never_selected(self):
		Articles.objects.create(title="No pmid", link="https://ex.org/n")
		self.assertEqual(due_pmids(), [])

	def test_never_checked_is_due(self):
		make("1")
		self.assertEqual(due_pmids(), ["1"])

	def test_recently_checked_is_not_due(self):
		make("1", checked_days_ago=10, status="MEDLINE")
		self.assertEqual(due_pmids(), [])

	def test_in_progress_statuses_are_due_after_30_days(self):
		for pmid, status in (("1", "In-Process"), ("2", "In-Data-Review"), ("3", "Publisher")):
			make(pmid, checked_days_ago=31, status=status)
		make("4", checked_days_ago=29, status="In-Process")
		make("5", checked_days_ago=31, status="MEDLINE")
		self.assertEqual(sorted(due_pmids()), ["1", "2", "3"])

	def test_anything_is_due_after_a_year(self):
		make("1", checked_days_ago=366, status="MEDLINE")
		make("2", checked_days_ago=364, status="MEDLINE")
		self.assertEqual(due_pmids(), ["1"])

	def test_never_checked_go_first_then_oldest_check(self):
		make("old", checked_days_ago=400)
		make("older", checked_days_ago=800)
		make("never")
		self.assertEqual(due_pmids(), ["never", "older", "old"])


def echo_xml(pmids):
	"""A minimal PubmedArticleSet with one record per requested PMID."""
	records = "".join(
		f"<PubmedArticle><MedlineCitation Status='MEDLINE'><PMID>{p}</PMID>"
		"<Article><ArticleTitle>t</ArticleTitle></Article></MedlineCitation></PubmedArticle>"
		for p in pmids
	)
	return f"<PubmedArticleSet>{records}</PubmedArticleSet>".encode()


def fake_client(xml=SAMPLE_XML, side_effect=None):
	client = MagicMock()
	if side_effect is not None:
		client.efetch_pubmed.side_effect = side_effect
	else:
		client.efetch_pubmed.return_value = xml
	return client


def run(client, *args):
	out = StringIO()
	with patch("gregory.management.commands.refresh_pubmed_records.NcbiClient", return_value=client):
		call_command("refresh_pubmed_records", *args, stdout=out)
	return out.getvalue()


class RefreshCommandTests(TestCase):
	def test_applies_returned_records_and_reports(self):
		a = make("42830590", summary="")
		b = make("42854181", summary="A full summary kept.")
		output = run(fake_client())
		a.refresh_from_db()
		b.refresh_from_db()
		self.assertTrue(a.summary.startswith("BACKGROUND: Peer support"))
		self.assertEqual(b.summary, "A full summary kept.")
		self.assertIsNotNone(a.pubmed_checked_at)
		self.assertEqual(a.pubmed_status, "MEDLINE")
		self.assertIn("2 fetched, 2 updated, 1 summaries replaced, 0 retracted, 0 missing", output)

	def test_a_pmid_ncbi_does_not_return_is_marked_checked_and_logged(self):
		gone = make("11111111")
		output = run(fake_client())
		gone.refresh_from_db()
		self.assertIsNotNone(gone.pubmed_checked_at)
		self.assertIn("PMID 11111111", output)
		self.assertIn("1 missing", output)
		self.assertEqual(due_pmids(), [])

	def test_a_failed_batch_does_not_stop_the_run(self):
		make("42830590")
		with patch("gregory.management.commands.refresh_pubmed_records.BATCH_SIZE", 1):
			make("42854181")
			client = fake_client(side_effect=[NcbiError("boom"), SAMPLE_XML])
			output = run(client)
		self.assertEqual(client.efetch_pubmed.call_count, 2)
		self.assertIn("1 failed batches", output)
		self.assertEqual(Articles.objects.filter(pubmed_checked_at__isnull=False).count(), 1)

	def test_unparseable_xml_counts_as_a_failed_batch(self):
		make("42830590")
		output = run(fake_client(xml=b"<not xml"))
		self.assertIn("1 failed batches", output)

	def test_batches_hold_at_most_200_pmids(self):
		for n in range(205):
			make(str(50000000 + n))
		client = fake_client()
		client.efetch_pubmed.side_effect = lambda pmids: echo_xml(pmids)
		run(client, "--limit", "1000")
		sizes = [len(c.args[0]) for c in client.efetch_pubmed.call_args_list]
		self.assertEqual(sizes, [200, 5])

	def test_limit_takes_never_checked_first(self):
		make("old", checked_days_ago=400)
		never = make("42830590")
		client = fake_client()
		run(client, "--limit", "1")
		self.assertEqual(client.efetch_pubmed.call_args.args[0], [never.pmid])

	def test_dry_run_does_not_call_ncbi(self):
		make("42830590")
		client = fake_client()
		output = run(client, "--dry-run")
		client.efetch_pubmed.assert_not_called()
		self.assertIn("1 articles due", output)

	def test_nothing_due_makes_no_calls(self):
		client = fake_client()
		run(client)
		client.efetch_pubmed.assert_not_called()

	def test_one_bad_article_does_not_lose_the_rest(self):
		make("42830590")
		make("42854181")
		real = __import__("gregory.services.pubmed_records", fromlist=["x"]).apply_pubmed_record
		calls = []

		def flaky(article, record, is_new):
			calls.append(record.pmid)
			if len(calls) == 1:
				raise RuntimeError("bad article")
			return real(article, record, is_new=is_new)

		with patch("gregory.management.commands.refresh_pubmed_records.apply_pubmed_record", side_effect=flaky):
			output = run(fake_client())
		self.assertIn("1 updated", output)

	def test_an_error_body_is_halved_down_to_single_failures_and_nothing_is_marked_missing(self):
		for n in range(4):
			make(str(60000000 + n))
		client = fake_client(xml=b"<eFetchResult><ERROR>Unable to obtain query</ERROR></eFetchResult>")
		output = run(client)
		self.assertIn("4 failed batches", output)
		self.assertIn("0 missing", output)
		self.assertEqual(Articles.objects.filter(pubmed_checked_at__isnull=False).count(), 0)
		self.assertEqual(sorted(len(c.args[0]) for c in client.efetch_pubmed.call_args_list), [1, 1, 1, 1, 2, 2, 4])

	def test_an_empty_response_is_only_believed_for_single_pmids(self):
		for n in range(3):
			make(str(61000000 + n))
		client = fake_client(xml=b"<PubmedArticleSet/>")
		output = run(client)
		self.assertIn("3 missing", output)
		self.assertEqual(Articles.objects.filter(pubmed_checked_at__isnull=False).count(), 3)

	def test_a_good_record_in_a_batch_saves_it_from_being_split(self):
		a, b = make("42830590"), make("62000000")
		client = fake_client()
		output = run(client)
		self.assertIn("1 fetched", output)
		self.assertIn("1 missing", output)
		# first call for both, then one confirming call for the straggler alone
		self.assertEqual(
			[sorted(c.args[0]) for c in client.efetch_pubmed.call_args_list],
			[["42830590", "62000000"], ["62000000"]],
		)
		b.refresh_from_db()
		self.assertIsNotNone(b.pubmed_checked_at)

	def test_a_straggler_found_on_the_second_ask_is_applied_not_missed(self):
		a, b = make("42830590"), make("42854181")
		first = echo_xml(["42830590"])
		client = fake_client(side_effect=[first, SAMPLE_XML])
		output = run(client)
		self.assertIn("2 fetched", output)
		self.assertIn("0 missing", output)

	def test_a_second_run_does_not_start_while_one_is_running(self):
		import fcntl

		from gregory.management.commands import refresh_pubmed_records as module

		make("42830590")
		client = fake_client()
		with open(module.LOCK_PATH, "a") as held:
			fcntl.flock(held, fcntl.LOCK_EX)
			output = run(client)
		client.efetch_pubmed.assert_not_called()
		self.assertIn("Another refresh_pubmed_records run is in progress", output)

	def test_a_non_positive_limit_is_an_error(self):
		from django.core.management import CommandError

		with self.assertRaises(CommandError):
			run(fake_client(), "--limit", "0")
