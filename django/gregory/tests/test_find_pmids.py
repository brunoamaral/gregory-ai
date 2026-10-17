"""find_pmids: batching, DOI-equality matching, back-off and selection. No network."""

from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from gregory.management.commands.find_pmids import articles_needing_pmid, build_doi_term
from gregory.models import Articles
from gregory.utils.ncbi import ESearchResult, NcbiError, NcbiQueryError

COMMAND = "gregory.management.commands.find_pmids"
NOW = timezone.now()


def record_xml(*pairs, abstract="A complete abstract from PubMed."):
	"""PubmedArticleSet with one record per (pmid, doi)."""
	records = ""
	for pmid, doi in pairs:
		doi_id = f'<ArticleId IdType="doi">{doi}</ArticleId>' if doi else ""
		records += (
			f"<PubmedArticle><MedlineCitation Status='MEDLINE'><PMID>{pmid}</PMID>"
			f"<Article><ArticleTitle>Title {pmid}</ArticleTitle>"
			f"<Abstract><AbstractText>{abstract}</AbstractText></Abstract></Article>"
			f"</MedlineCitation><PubmedData><ArticleIdList>{doi_id}</ArticleIdList>"
			f"</PubmedData></PubmedArticle>"
		)
	return f"<PubmedArticleSet>{records}</PubmedArticleSet>".encode()


def make(doi, discovered_days_ago=1, **kwargs):
	article = Articles.objects.create(
		title=f"Article {doi}", link=f"https://x.org/{doi}", doi=doi, **kwargs
	)
	Articles.objects.filter(pk=article.pk).update(
		discovery_date=NOW - timedelta(days=discovered_days_ago)
	)
	article.refresh_from_db()
	return article


def fake_client(ids=(), xml=None):
	client = MagicMock()
	client.esearch.return_value = ESearchResult(count=len(ids), ids=list(ids))
	client.efetch_pubmed.return_value = xml if xml is not None else b"<PubmedArticleSet/>"
	return client


def run(client, *args):
	out = StringIO()
	with patch(f"{COMMAND}.NcbiClient") as klass:
		klass.for_site.return_value = client
		klass.for_organization.return_value = client
		call_command("find_pmids", *args, stdout=out)
	return out.getvalue()


class BuildDoiTermTests(TestCase):
	def test_dois_are_quoted_field_searched_and_joined(self):
		self.assertEqual(
			build_doi_term(["10.1/a", "10.2/b"]), '"10.1/a"[doi] OR "10.2/b"[doi]'
		)

	def test_quotes_in_a_doi_cannot_break_the_term(self):
		self.assertEqual(build_doi_term(['10.1/a"b']), '"10.1/ab"[doi]')


class SelectionTests(TestCase):
	def pks(self, **kwargs):
		return [a.pk for a in articles_needing_pmid(now=NOW, **kwargs)]

	def test_recent_articles_with_a_doi_and_no_pmid_are_selected(self):
		a = make("10.1/a", discovered_days_ago=5)
		self.assertEqual(self.pks(), [a.pk])

	def test_days_limits_by_discovery_date_and_all_does_not(self):
		recent = make("10.1/recent", discovered_days_ago=5)
		old = make("10.1/old", discovered_days_ago=200)
		self.assertEqual(self.pks(days=30), [recent.pk])
		self.assertEqual(self.pks(days=300), [recent.pk, old.pk])
		self.assertEqual(self.pks(everything=True), [recent.pk, old.pk])

	def test_articles_with_a_pmid_or_without_a_doi_are_not_selected(self):
		make("10.1/has", pmid="1")
		Articles.objects.create(title="No doi", link="https://x.org/n")
		Articles.objects.create(title="Empty doi", link="https://x.org/e", doi="")
		self.assertEqual(self.pks(everything=True), [])

	def test_a_backed_off_article_is_selected_only_when_due(self):
		waiting = make("10.1/wait", pmid_lookup_next_check=NOW + timedelta(days=2))
		due = make("10.1/due", pmid_lookup_next_check=NOW - timedelta(days=1))
		self.assertEqual(self.pks(), [due.pk])
		self.assertNotIn(waiting.pk, self.pks(everything=True))

	def test_newest_discoveries_come_first(self):
		old = make("10.1/o", discovered_days_ago=10)
		new = make("10.1/n", discovered_days_ago=1)
		self.assertEqual(self.pks(), [new.pk, old.pk])


class MatchingTests(TestCase):
	def test_a_matching_record_is_applied(self):
		a = make("10.1/ABC", summary="Truncated summary...")
		client = fake_client(ids=["111"], xml=record_xml(("111", "10.1/abc")))
		output = run(client)
		a.refresh_from_db()
		self.assertEqual(a.pmid, "111")
		self.assertEqual(a.pubmed_status, "MEDLINE")
		self.assertIsNotNone(a.pubmed_checked_at)
		self.assertEqual(a.summary, "A complete abstract from PubMed.")
		self.assertEqual(a.pmid_lookup_attempts, 0)
		self.assertIsNone(a.pmid_lookup_next_check)
		self.assertIn("1 matched", output)
		self.assertIn("1 summaries replaced", output)
		self.assertEqual(client.esearch.call_args.args[0], '"10.1/abc"[doi]')

	def test_a_full_summary_is_kept(self):
		a = make("10.1/abc", summary="A complete CrossRef abstract.")
		run(fake_client(ids=["111"], xml=record_xml(("111", "10.1/abc"))))
		a.refresh_from_db()
		self.assertEqual(a.summary, "A complete CrossRef abstract.")
		self.assertEqual(a.pmid, "111")

	def test_a_near_miss_doi_is_rejected_and_backed_off(self):
		a = make("10.1/abc")
		client = fake_client(ids=["111"], xml=record_xml(("111", "10.1/abc.1")))
		output = run(client)
		a.refresh_from_db()
		self.assertIsNone(a.pmid)
		self.assertIn("1 near-miss DOIs rejected", output)
		self.assertEqual(a.pmid_lookup_attempts, 1)
		self.assertGreater(a.pmid_lookup_next_check, timezone.now() + timedelta(days=1))

	def test_a_record_without_a_doi_is_rejected(self):
		a = make("10.1/abc")
		run(fake_client(ids=["111"], xml=record_xml(("111", None))))
		a.refresh_from_db()
		self.assertIsNone(a.pmid)

	def test_each_record_goes_to_its_own_article(self):
		a, b = make("10.1/a"), make("10.1/b")
		client = fake_client(ids=["1", "2"], xml=record_xml(("2", "10.1/b"), ("1", "10.1/a")))
		run(client)
		a.refresh_from_db()
		b.refresh_from_db()
		self.assertEqual((a.pmid, b.pmid), ("1", "2"))

	def test_a_pmid_already_on_another_article_is_a_reported_conflict_not_a_match(self):
		Articles.objects.create(title="Holder", link="https://x.org/h", pmid="111")
		a = make("10.1/abc")
		output = run(fake_client(ids=["111"], xml=record_xml(("111", "10.1/abc"))))
		a.refresh_from_db()
		self.assertIsNone(a.pmid)
		self.assertIn("1 PMIDs already on another article", output)
		self.assertEqual(a.pmid_lookup_attempts, 1)  # not retried every run

	def test_the_run_writes_no_article_history_for_the_markers(self):
		a = make("10.1/abc")
		before = a.history.count()
		run(fake_client(ids=[]))
		self.assertEqual(a.history.count(), before)


class BackOffTests(TestCase):
	def test_no_record_moves_the_marker_forward_and_the_next_run_skips_it(self):
		a = make("10.1/abc")
		client = fake_client(ids=[])
		output = run(client)
		a.refresh_from_db()
		self.assertIn("1 with no PubMed record", output)
		self.assertEqual(a.pmid_lookup_attempts, 1)
		self.assertAlmostEqual(
			(a.pmid_lookup_next_check - timezone.now()).total_seconds(), 2 * 86400, delta=60
		)
		again = fake_client(ids=[])
		run(again)
		again.esearch.assert_not_called()

	def test_the_delay_doubles_with_each_fruitless_attempt(self):
		a = make("10.1/abc", pmid_lookup_attempts=2, pmid_lookup_next_check=NOW - timedelta(days=1))
		run(fake_client(ids=[]))
		a.refresh_from_db()
		self.assertEqual(a.pmid_lookup_attempts, 3)
		self.assertAlmostEqual(
			(a.pmid_lookup_next_check - timezone.now()).total_seconds(), 8 * 86400, delta=60
		)

	def test_a_network_failure_on_the_search_moves_nothing(self):
		a = make("10.1/abc")
		client = fake_client()
		client.esearch.side_effect = NcbiError("down")
		output = run(client)
		a.refresh_from_db()
		self.assertEqual(a.pmid_lookup_attempts, 0)
		self.assertIsNone(a.pmid_lookup_next_check)
		self.assertIn("1 failed", output)

	def test_a_fetch_failure_moves_nothing(self):
		a = make("10.1/abc")
		client = fake_client(ids=["111"])
		client.efetch_pubmed.side_effect = NcbiError("down")
		run(client)
		a.refresh_from_db()
		self.assertEqual(a.pmid_lookup_attempts, 0)
		self.assertIsNone(a.pmid)

	def test_unparseable_records_move_nothing(self):
		a = make("10.1/abc")
		run(fake_client(ids=["111"], xml=b"<eFetchResult><ERROR/></eFetchResult>"))
		a.refresh_from_db()
		self.assertEqual(a.pmid_lookup_attempts, 0)

	def test_a_doi_with_nothing_to_search_backs_off_instead_of_being_retried_forever(self):
		a = make("/")  # normalises to nothing
		client = fake_client()
		run(client)
		a.refresh_from_db()
		client.esearch.assert_not_called()
		self.assertEqual(a.pmid_lookup_attempts, 1)


class BatchingTests(TestCase):
	def test_searches_hold_at_most_fifty_dois(self):
		for n in range(120):
			make(f"10.1/{n:03d}")
		client = fake_client(ids=[])
		run(client, "--limit", "1000")
		sizes = [c.args[0].count("[doi]") for c in client.esearch.call_args_list]
		self.assertEqual(sorted(sizes), [20, 50, 50])

	def test_a_doi_pubmed_cannot_read_is_isolated_by_halving(self):
		good_one, bad, good_two = make("10.1/good1"), make("10.1/bad"), make("10.1/good2")
		pmid_of = {"10.1/good1": "1", "10.1/good2": "2"}

		def esearch(term, **kwargs):
			if "10.1/bad" in term:
				raise NcbiQueryError("cannot parse")
			return ESearchResult(count=1, ids=[p for d, p in pmid_of.items() if d in term])

		def efetch(pmids):
			return record_xml(*[(p, d) for d, p in pmid_of.items() if p in pmids])

		client = fake_client()
		client.esearch.side_effect = esearch
		client.efetch_pubmed.side_effect = efetch
		run(client)
		for article in (good_one, bad, good_two):
			article.refresh_from_db()
		self.assertEqual((good_one.pmid, good_two.pmid), ("1", "2"))
		self.assertIsNone(bad.pmid)
		self.assertEqual(bad.pmid_lookup_attempts, 1)  # a completed, fruitless attempt
		self.assertEqual(good_one.pmid_lookup_attempts, 0)


class OptionTests(TestCase):
	def test_days_and_all_decide_which_articles_are_looked_at(self):
		recent = make("10.1/recent", discovered_days_ago=2)
		old = make("10.1/old", discovered_days_ago=400)
		client = fake_client(ids=[])
		run(client)
		self.assertIn('"10.1/recent"[doi]', client.esearch.call_args.args[0])
		self.assertNotIn("10.1/old", client.esearch.call_args.args[0])
		Articles.objects.update(pmid_lookup_next_check=None, pmid_lookup_attempts=0)
		everything = fake_client(ids=[])
		run(everything, "--all")
		terms = " ".join(c.args[0] for c in everything.esearch.call_args_list)
		self.assertIn("10.1/old", terms)
		self.assertIn("10.1/recent", terms)
		self.assertTrue(recent.pk and old.pk)

	def test_limit_takes_the_newest_first(self):
		make("10.1/old", discovered_days_ago=20)
		make("10.1/new", discovered_days_ago=1)
		client = fake_client(ids=[])
		run(client, "--limit", "1")
		self.assertEqual(client.esearch.call_args.args[0], '"10.1/new"[doi]')

	def test_dry_run_calls_nothing_and_changes_nothing(self):
		a = make("10.1/abc")
		client = fake_client(ids=["1"])
		output = run(client, "--dry-run")
		client.esearch.assert_not_called()
		a.refresh_from_db()
		self.assertEqual(a.pmid_lookup_attempts, 0)
		self.assertIn("1 articles with a DOI and no PMID are due", output)

	def test_a_non_positive_limit_is_an_error(self):
		with self.assertRaises(CommandError):
			run(fake_client(), "--limit", "0")

	def test_an_unknown_organization_is_a_clean_error(self):
		make("10.1/abc")
		with self.assertRaises(CommandError):
			run(fake_client(), "--organization", "999999")

	def test_a_second_run_does_not_start_while_one_is_running(self):
		import fcntl

		from gregory.management.commands import find_pmids as module

		make("10.1/abc")
		client = fake_client(ids=[])
		with open(module.LOCK_PATH, "a") as held:
			fcntl.flock(held, fcntl.LOCK_EX)
			output = run(client)
		client.esearch.assert_not_called()
		self.assertIn("Another find_pmids run is in progress", output)
