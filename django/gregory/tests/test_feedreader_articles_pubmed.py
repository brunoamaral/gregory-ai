"""feedreader_articles_pubmed against canned ESearch results and the real sample XML.

Never calls NCBI or CrossRef: NcbiClient is replaced and SciencePaper.refresh is patched.
"""

from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization

from gregory.classes import SciencePaper
from gregory.models import Articles, PublicationType, Sources, Subject, Team
from gregory.utils.ncbi import ESearchResult, NcbiError, NcbiResultCapError

SAMPLE_XML = (Path(__file__).parent / "fixtures" / "pubmed" / "efetch-sample-3-records.xml").read_bytes()
PMIDS = ["42830590", "42854181", "42834886"]
COMMAND = "gregory.management.commands.feedreader_articles_pubmed"


def crossref_failed(paper_self):
	return "DOI not found"


def fake_client(ids=PMIDS, xml=SAMPLE_XML):
	client = MagicMock()
	client.esearch.return_value = ESearchResult(count=len(ids), ids=list(ids), querytranslation="translated")
	client.efetch_pubmed.return_value = xml
	return client


class PubmedImporterBase(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Imp Org", slug="imp-org")
		self.team = Team.objects.create(organization=self.org, name="Imp Team", slug="imp-team")
		self.subject = Subject.objects.create(team=self.team, subject_name="MS", subject_slug="ms")
		self.source = self.make_source()
		patcher = patch.object(SciencePaper, "refresh", autospec=True, side_effect=crossref_failed)
		self.refresh = patcher.start()
		self.addCleanup(patcher.stop)

	def make_source(self, **kwargs):
		defaults = dict(
			name="PubMed | MS",
			method="pubmed_api",
			source_for="science paper",
			active=True,
			team=self.team,
			subject=self.subject,
			pubmed_query="multiple sclerosis",
		)
		defaults.update(kwargs)
		return Sources.objects.create(**defaults)

	def run_command(self, client=None, *args):
		client = client or fake_client()
		out = StringIO()
		with patch(f"{COMMAND}.NcbiClient") as klass:
			klass.return_value = client
			klass.for_organization.return_value = client
			call_command("feedreader_articles_pubmed", *args, stdout=out)
		self.client_class = klass
		return out.getvalue()


class ImportTests(PubmedImporterBase):
	def test_new_articles_are_created_with_everything_from_pubmed(self):
		output = self.run_command()
		self.assertEqual(Articles.objects.count(), 3)
		article = Articles.objects.get(pmid="42830590")
		self.assertEqual(article.link, "https://pubmed.ncbi.nlm.nih.gov/42830590/")
		self.assertTrue(article.summary.startswith("BACKGROUND: Peer support"))
		self.assertEqual(article.doi, "10.1071/ib25113")
		self.assertEqual(article.pubmed_status, "MEDLINE")
		self.assertGreater(article.authors.count(), 0)
		self.assertGreater(article.mesh_headings.count(), 0)
		self.assertEqual(list(article.sources.all()), [self.source])
		self.assertEqual(list(article.subjects.all()), [self.subject])
		self.assertEqual(list(article.teams.all()), [self.team])
		self.assertIn("3 new, 0 merged", output)

	def test_journal_falls_back_to_pubmeds_when_crossref_has_none(self):
		self.run_command()
		self.assertEqual(
			Articles.objects.get(pmid="42830590").container_title[:13], "Brain impairm"
		)
		self.assertEqual(Articles.objects.get(pmid="42830590").access, "unknown")
		self.assertIsNone(Articles.objects.get(pmid="42830590").crossref_check)

	def test_crossref_details_are_used_but_never_replace_pubmeds_title_or_abstract(self):
		def refresh(paper_self):
			paper_self.journal = "CrossRef Journal"
			paper_self.publisher = "CrossRef Publisher"
			paper_self.access = "open"
			paper_self.pdf_link = "https://example.org/x.pdf"
			paper_self.title = "A different CrossRef title"
			paper_self.abstract = "A different CrossRef abstract"
			return None

		self.refresh.side_effect = refresh
		self.run_command()
		article = Articles.objects.get(pmid="42830590")
		self.assertEqual(article.container_title, "CrossRef Journal")
		self.assertEqual(article.publisher, "CrossRef Publisher")
		self.assertEqual(article.access, "open")
		self.assertEqual(article.pdf_link, "https://example.org/x.pdf")
		self.assertIsNotNone(article.crossref_check)
		self.assertTrue(article.title.startswith("'Got to be the right fit"))
		self.assertTrue(article.summary.startswith("BACKGROUND:"))

	def test_pmids_already_on_the_source_are_not_fetched_again(self):
		first = Articles.objects.create(title="Known", link="https://x.org/1", pmid="42830590")
		first.sources.add(self.source)
		client = fake_client()
		output = self.run_command(client)
		fetched = client.efetch_pubmed.call_args.args[0]
		self.assertEqual(sorted(fetched), ["42834886", "42854181"])
		self.assertIn("1 skipped (already on the source)", output)

	def test_a_paper_already_stored_from_rss_is_merged_by_pmid(self):
		rss = Articles.objects.create(
			title="Rss title", link="https://pubmed.ncbi.nlm.nih.gov/42830590/",
			summary="A complete abstract from the RSS feed.", pmid="42830590",
		)
		output = self.run_command()
		self.assertEqual(Articles.objects.count(), 3)
		rss.refresh_from_db()
		self.assertEqual(rss.summary, "A complete abstract from the RSS feed.")  # full summary kept
		self.assertEqual(rss.title, "Rss title")
		self.assertEqual(rss.pubmed_status, "MEDLINE")
		self.assertGreater(rss.mesh_headings.count(), 0)
		self.assertIn(self.source, rss.sources.all())
		self.assertIn("2 new, 1 merged", output)

	def test_a_truncated_rss_summary_is_replaced(self):
		rss = Articles.objects.create(
			title="Rss title", link="https://x.org/r", summary="Peer support for people with...", pmid="42830590",
		)
		self.run_command()
		rss.refresh_from_db()
		self.assertTrue(rss.summary.startswith("BACKGROUND: Peer support"))

	def test_a_paper_matched_by_doi_gains_the_pmid(self):
		known = Articles.objects.create(title="By doi", link="https://x.org/d", doi="10.1071/IB25113")
		self.run_command()
		known.refresh_from_db()
		self.assertEqual(known.pmid, "42830590")
		self.assertEqual(Articles.objects.count(), 3)

	def test_an_excluded_type_is_in_the_search_and_dropped_by_the_safety_net(self):
		observational = PublicationType.objects.create(ui="D064888", name="Observational Study")
		self.source.excluded_publication_types.add(observational)
		client = fake_client()
		output = self.run_command(client)
		self.assertIn('NOT ("Observational Study"[pt])', client.esearch.call_args.args[0])
		self.assertFalse(Articles.objects.filter(pmid="42854181").exists())
		self.assertEqual(Articles.objects.count(), 2)
		self.assertIn("1 excluded", output)

	def test_rss_inactive_and_queryless_sources_are_left_alone(self):
		self.source.delete()
		Sources.objects.create(name="RSS", method="rss", source_for="science paper", active=True,
			link="https://pubmed.ncbi.nlm.nih.gov/rss/search/x", team=self.team, subject=self.subject)
		self.make_source(name="Inactive", active=False)
		self.make_source(name="Queryless", pubmed_query="")
		client = fake_client()
		self.run_command(client)
		client.esearch.assert_not_called()


class WindowTests(PubmedImporterBase):
	def term(self, client, call=0):
		return client.esearch.call_args_list[call].args[0]

	def window(self, term):
		span = term.rsplit(" AND ", 1)[1].replace("[crdt]", "")
		start, end = span.split(":")
		to_date = lambda s: date(*map(int, s.split("/")))
		return to_date(start), to_date(end)

	def test_first_run_covers_thirty_days(self):
		client = fake_client()
		self.run_command(client)
		start, end = self.window(self.term(client))
		self.assertEqual(end, timezone.now().date())
		self.assertEqual(end - start, timedelta(days=30))
		self.assertTrue(self.term(client).startswith("(multiple sclerosis) AND "))

	def test_days_option_sets_the_first_run_window(self):
		client = fake_client()
		self.run_command(client, "--days", "90")
		start, end = self.window(self.term(client))
		self.assertEqual(end - start, timedelta(days=90))

	def test_later_runs_start_two_days_before_the_anchor(self):
		anchor = timezone.now() - timedelta(days=5)
		Sources.objects.filter(pk=self.source.pk).update(last_successful_fetch_at=anchor)
		client = fake_client()
		self.run_command(client)
		start, _ = self.window(self.term(client))
		self.assertEqual(start, (anchor - timedelta(days=2)).date())

	def test_a_successful_run_moves_the_anchor(self):
		before = timezone.now()
		self.run_command()
		self.source.refresh_from_db()
		self.assertGreaterEqual(self.source.last_successful_fetch_at, before)

	def test_a_capped_window_is_halved_and_the_halves_are_merged_without_duplicates(self):
		calls = []

		def esearch(term, **kwargs):
			start, end = self.window(term)
			calls.append((start, end))
			if (end - start).days >= 8:  # anything wider than ~a week hits the cap
				raise NcbiResultCapError("capped")
			return ESearchResult(count=2, ids=["42830590", "42854181"] if len(calls) % 2 else ["42854181", "42834886"], querytranslation="t")

		client = fake_client()
		client.esearch.side_effect = esearch
		self.run_command(client)
		widths = [(e - s).days for s, e in calls]
		self.assertEqual(widths[0], 30)
		self.assertTrue(all(w <= 15 for w in widths[1:]))
		# windows tile the range with no gap or overlap
		leaves = sorted((s, e) for s, e in calls if (e - s).days < 8)
		for (_, previous_end), (next_start, _) in zip(leaves, leaves[1:]):
			self.assertEqual(next_start, previous_end + timedelta(days=1))
		self.assertEqual(Articles.objects.count(), 3)
		fetched = client.efetch_pubmed.call_args.args[0]
		self.assertEqual(len(fetched), len(set(fetched)))

	def test_a_single_day_at_the_cap_fails_the_source_and_keeps_the_anchor(self):
		client = fake_client()
		client.esearch.side_effect = NcbiResultCapError("capped")
		output = self.run_command(client)
		self.source.refresh_from_db()
		self.assertIsNone(self.source.last_successful_fetch_at)
		self.assertIn("failed", output)
		self.assertEqual(Articles.objects.count(), 0)

	def test_a_fetch_failure_keeps_the_anchor(self):
		anchor = timezone.now() - timedelta(days=3)
		Sources.objects.filter(pk=self.source.pk).update(last_successful_fetch_at=anchor)
		client = fake_client()
		client.efetch_pubmed.side_effect = NcbiError("down")
		self.run_command(client)
		self.source.refresh_from_db()
		self.assertEqual(self.source.last_successful_fetch_at, anchor)

	def test_a_failing_record_keeps_the_anchor_but_not_the_others(self):
		real = __import__("gregory.services.pubmed_records", fromlist=["x"]).apply_pubmed_record
		calls = []

		def flaky(article, record, is_new):
			calls.append(record.pmid)
			if len(calls) == 1:
				raise RuntimeError("bad record")
			return real(article, record, is_new=is_new)

		with patch(f"{COMMAND}.apply_pubmed_record", side_effect=flaky):
			output = self.run_command()
		self.source.refresh_from_db()
		self.assertIsNone(self.source.last_successful_fetch_at)
		self.assertEqual(Articles.objects.count(), 2)  # the failed record's placeholder row rolled back
		self.assertIn("1 failed", output)

	def test_one_failing_source_does_not_stop_the_next(self):
		self.source.delete()
		first = self.make_source(name="First", pubmed_query="first query")
		second = self.make_source(name="Second", pubmed_query="second query")

		def esearch(term, **kwargs):
			if "first query" in term:
				raise NcbiError("boom")
			return ESearchResult(count=3, ids=PMIDS, querytranslation="t")

		client = fake_client()
		client.esearch.side_effect = esearch
		self.run_command(client)
		first.refresh_from_db()
		second.refresh_from_db()
		self.assertIsNone(first.last_successful_fetch_at)
		self.assertIsNotNone(second.last_successful_fetch_at)
		self.assertEqual(Articles.objects.count(), 3)

	def test_dry_run_writes_nothing_and_keeps_the_anchor(self):
		client = fake_client()
		output = self.run_command(client, "--dry-run")
		client.efetch_pubmed.assert_not_called()
		self.assertEqual(Articles.objects.count(), 0)
		self.source.refresh_from_db()
		self.assertIsNone(self.source.last_successful_fetch_at)
		self.assertIn("would fetch 3 records", output)

	def test_source_id_limits_the_run(self):
		other = self.make_source(name="Other", pubmed_query="other")
		client = fake_client()
		self.run_command(client, "--source-id", str(other.pk))
		self.assertEqual(client.esearch.call_count, 1)
		self.assertIn("(other)", self.term(client))

	def test_the_organizations_ncbi_credentials_are_used(self):
		self.run_command()
		self.client_class.for_organization.assert_called_with(self.org)
