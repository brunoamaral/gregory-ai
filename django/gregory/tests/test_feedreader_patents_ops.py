"""End-to-end tests for the feedreader_patents_ops command with the OPS request layer
replaced by a scripted fake. The parsers run for real against the fixtures."""

import datetime
import re
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization

from gregory.management.commands.feedreader_patents_ops import Command
from gregory.models import (
	OrganizationCredentials,
	PatentApplicant,
	PatentPublication,
	Patents,
	Sources,
	Subject,
	Team,
)
from gregory.utils.epo_ops import (
	OpsQuotaExceeded,
	SearchHit,
	SearchPage,
	parse_biblio,
	parse_claims,
	parse_family,
	parse_search,
)

FIXTURES = Path(__file__).parent / "fixtures" / "epo_ops"


def fixture(name):
	return (FIXTURES / name).read_bytes()


class FakeOps:
	"""Stands in for OpsClient: serves parsed fixtures and records calls."""

	def __init__(self):
		self.bytes_received = 0
		self.request_count = 0
		self.throttle_states = set()
		self.search_calls = []
		self.family_calls = []
		self.biblio_calls = []
		self.claims_calls = []
		self.search_handler = lambda cql, begin, end: parse_search(fixture("search_page.xml"))
		self.fail_search_with = None

	def search(self, cql, begin=1, end=100):
		self.search_calls.append((cql, begin, end))
		if self.fail_search_with:
			raise self.fail_search_with
		return self.search_handler(cql, begin, end)

	def family(self, country, doc_number, kind, family_id=None):
		self.family_calls.append((country, doc_number, kind, family_id))
		name = "family_us_only.xml" if country == "US" else "family_900001.xml"
		return parse_family(fixture(name), family_id)

	def biblio_bulk(self, publications):
		publications = list(publications)
		self.biblio_calls.append(publications)
		wanted = {f"{c}{n}{k}" for c, n, k in publications}
		return [b for b in parse_biblio(fixture("biblio_bulk.xml")) if b.publication_number in wanted]

	def claims(self, country, doc_number, kind):
		self.claims_calls.append((country, doc_number, kind))
		return parse_claims(fixture("claims_ep.xml"))


class PatentImporterTestCase(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org", slug="pat-org")
		OrganizationCredentials.objects.create(
			organization=self.org, epo_ops_consumer_key="key", epo_ops_consumer_secret="secret"
		)
		self.team = Team.objects.create(organization=self.org, name="Team", slug="pat-team")
		self.subject = Subject.objects.create(subject_name="MS", subject_slug="ms-pat", team=self.team)
		self.source = Sources.objects.create(
			name="MS patents",
			source_for="patents",
			method="epo_ops",
			team=self.team,
			subject=self.subject,
			ops_cql_query='ta="multiple sclerosis" and cpc=/low A61P25/00',
		)
		self.ops = FakeOps()
		patcher = patch.object(Command, "get_ops_client", return_value=self.ops)
		patcher.start()
		self.addCleanup(patcher.stop)

	def run_command(self, **kwargs):
		out = StringIO()
		call_command("feedreader_patents_ops", stdout=out, stderr=StringIO(), **kwargs)
		return out.getvalue()


class NewFamilyTests(PatentImporterTestCase):
	def test_creates_families_with_members_applicants_and_claims(self):
		self.run_command(since="2024-01-01")

		self.assertEqual(Patents.objects.count(), 2)
		ms = Patents.objects.get(family_id="900001")
		self.assertEqual(ms.title, "METHODS OF TREATING MULTIPLE SCLEROSIS")
		self.assertIn("remyelination-promoting", ms.summary)
		self.assertTrue(ms.claims.startswith("1. A compound for use"))
		self.assertEqual(ms.representative_publication, "EP4100001A1")
		self.assertEqual(ms.earliest_priority_date, datetime.date(2021, 6, 1))
		self.assertEqual(ms.earliest_publication_date, datetime.date(2022, 12, 7))
		self.assertTrue(ms.has_grant)  # the EP B1 is a member
		self.assertEqual(ms.cpc_classes, ["A61P25/28", "A61K31/4045"])
		self.assertEqual(ms.inventors, ["GARCIA, MARIA", "LINDQVIST, ERIK"])
		self.assertIn("espacenet.com", ms.link)
		self.assertEqual(ms.links, {"espacenet": ms.link})
		self.assertEqual(
			set(ms.publications.values_list("publication_number", flat=True)),
			{"EP4100001A1", "EP4100001B1", "WO2022200001A1"},
		)
		# the other simple family in the INPADOC response was filtered out
		self.assertFalse(PatentPublication.objects.filter(publication_number="US2020100999A1").exists())
		self.assertEqual(list(ms.sources.all()), [self.source])
		self.assertEqual(list(ms.teams.all()), [self.team])
		self.assertEqual(list(ms.subjects.all()), [self.subject])

		applicants = list(PatentApplicant.objects.filter(patent=ms).order_by("sequence"))
		self.assertEqual([a.raw_name for a in applicants], ["NEURONOVA THERAPEUTICS INC.", "Garcia, Maria"])
		self.assertEqual([a.is_individual for a in applicants], [False, True])
		self.assertIsNotNone(applicants[0].sponsor)

	def test_us_only_family_gets_no_claims_request(self):
		self.run_command(since="2024-01-01")

		us = Patents.objects.get(family_id="900002")
		self.assertIsNone(us.claims)
		self.assertIsNone(us.summary)  # no English abstract anywhere in the family
		self.assertEqual(us.title, "DOPAMINE AGONIST FORMULATION")
		self.assertEqual([c[0] for c in self.ops.claims_calls], ["EP"])

	def test_biblio_is_fetched_in_one_bulk_request(self):
		self.run_command(since="2024-01-01")
		self.assertEqual(len(self.ops.biblio_calls), 1)
		self.assertEqual(len(self.ops.biblio_calls[0]), 2)

	def test_family_is_fetched_once_per_new_family(self):
		self.run_command(since="2024-01-01")
		# EP4100001A1 and WO2022200001A1 share family 900001
		self.assertEqual(len(self.ops.family_calls), 2)

	def test_dry_run_writes_nothing(self):
		out = self.run_command(since="2024-01-01", dry_run=True)
		self.assertEqual(Patents.objects.count(), 0)
		self.assertEqual(self.ops.family_calls, [])
		self.assertIn("Would create 2", out)
		self.source.refresh_from_db()
		self.assertIsNone(self.source.last_successful_fetch_at)

	def test_query_gets_the_date_window(self):
		self.run_command(since="2024-01-01")
		cql = self.ops.search_calls[0][0]
		self.assertTrue(cql.startswith('(ta="multiple sclerosis" and cpc=/low A61P25/00) and pd within "20240101 20240131"'))


class KnownRecordTests(PatentImporterTestCase):
	def test_second_run_skips_known_publications(self):
		self.run_command(since="2024-01-01")
		self.ops.family_calls.clear()
		self.ops.biblio_calls.clear()

		self.run_command(since="2024-01-01")

		self.assertEqual(Patents.objects.count(), 2)
		self.assertEqual(self.ops.biblio_calls, [])

	def test_new_publication_of_known_family_is_attached(self):
		patent = Patents.objects.create(family_id="900001", title="Known", earliest_priority_date=datetime.date(2021, 6, 1))
		PatentPublication.objects.create(patent=patent, publication_number="EP4100001A1")

		self.run_command(since="2024-01-01")

		self.assertEqual(Patents.objects.filter(family_id="900001").count(), 1)
		patent.refresh_from_db()
		self.assertEqual(
			set(patent.publications.values_list("publication_number", flat=True)),
			{"EP4100001A1", "EP4100001B1", "WO2022200001A1"},
		)
		self.assertTrue(patent.has_grant)
		self.assertEqual(list(patent.sources.all()), [self.source])
		self.assertEqual(patent.title, "Known")  # existing family is not rewritten

	def test_family_conflict_is_logged_not_moved(self):
		other = Patents.objects.create(family_id="777777", title="Other family")
		PatentPublication.objects.create(patent=other, publication_number="EP4100001A1")

		out = self.run_command(since="2024-01-01")

		self.assertIn("Family conflict", out)
		self.assertEqual(PatentPublication.objects.get(publication_number="EP4100001A1").patent, other)
		self.assertEqual(other.sources.count(), 0)

	def test_back_off_marker_prevents_refetching_family(self):
		self.run_command(since="2024-01-01")
		self.ops.family_calls.clear()
		self.run_command(since="2024-01-01")
		# family_next_check was set on creation, so the known publications are not re-synced
		self.assertEqual(self.ops.family_calls, [])


class SearchPagingTests(PatentImporterTestCase):
	def hit(self, n):
		return SearchHit(country="EP", doc_number=str(5000000 + n), kind="A1", family_id=str(700000 + n))

	def test_pages_through_results(self):
		hits = [self.hit(n) for n in range(250)]

		def handler(cql, begin, end):
			return SearchPage(total=250, hits=hits[begin - 1 : end])

		self.ops.search_handler = handler
		hits_found, capped = Command.search_slice(self._command(), "q", datetime.date(2024, 1, 1), datetime.date(2024, 1, 31))
		self.assertEqual(len(hits_found), 250)
		self.assertFalse(capped)
		self.assertEqual([(c[1], c[2]) for c in self.ops.search_calls], [(1, 100), (101, 200), (201, 300)])

	def _command(self):
		command = Command()
		command.ops = self.ops
		return command

	def test_slice_over_cap_is_halved(self):
		def handler(cql, begin, end):
			# any window longer than 8 days has too many results
			start = datetime.datetime.strptime(cql.split('"')[1].split()[0], "%Y%m%d").date()
			stop = datetime.datetime.strptime(cql.split('"')[1].split()[1], "%Y%m%d").date()
			if (stop - start).days >= 8:
				return SearchPage(total=2500, hits=[self.hit(1)])
			return SearchPage(total=1, hits=[self.hit((start.day) + 100)])

		self.ops.search_handler = handler
		hits_found, capped = Command.search_slice(self._command(), "q", datetime.date(2024, 1, 1), datetime.date(2024, 1, 16))
		self.assertFalse(capped)
		self.assertEqual(len(hits_found), 2)  # two halves of 8 days each
		windows = [c[0].split('"')[1] for c in self.ops.search_calls]
		self.assertEqual(windows[0], "20240101 20240116")
		self.assertIn("20240101 20240108", windows)
		self.assertIn("20240109 20240116", windows)

	def test_single_day_over_cap_is_capped(self):
		self.ops.search_handler = lambda cql, begin, end: SearchPage(total=5000, hits=[self.hit(begin)])
		day = datetime.date(2024, 1, 5)
		hits_found, capped = Command.search_slice(self._command(), "q", day, day)
		self.assertTrue(capped)
		self.assertEqual(len(hits_found), 20)  # 2000 results in pages of 100 (one hit each here)


class AnchorTests(PatentImporterTestCase):
	def test_complete_run_advances_the_anchor(self):
		self.run_command(since="2024-01-01")
		self.source.refresh_from_db()
		self.assertIsNotNone(self.source.last_successful_fetch_at)

	def test_capped_run_does_not_advance_the_anchor(self):
		self.ops.search_handler = lambda cql, begin, end: SearchPage(total=5000, hits=[])
		# a one-day window that is still over the cap
		today = timezone.now().date().isoformat()
		out = self.run_command(since=today)
		self.assertIn("not advancing", out)
		self.source.refresh_from_db()
		self.assertIsNone(self.source.last_successful_fetch_at)

	def test_quota_error_stops_and_does_not_advance(self):
		self.ops.fail_search_with = OpsQuotaExceeded("weekly")
		out = self.run_command(since="2024-01-01")
		self.assertIn("quota exhausted", out)
		self.source.refresh_from_db()
		self.assertIsNone(self.source.last_successful_fetch_at)

	def test_max_families_stops_early_without_advancing(self):
		self.run_command(since="2024-01-01", max_families=1)
		self.assertEqual(Patents.objects.count(), 1)
		self.source.refresh_from_db()
		self.assertIsNone(self.source.last_successful_fetch_at)

	def test_incremental_window_starts_before_the_anchor(self):
		self.source.last_successful_fetch_at = timezone.now() - datetime.timedelta(days=3)
		self.source.save()
		self.run_command()
		first_window = re.search(r'pd within "(\d+) ', self.ops.search_calls[0][0]).group(1)
		expected = (self.source.last_successful_fetch_at - datetime.timedelta(days=14)).date()
		self.assertEqual(first_window, expected.strftime("%Y%m%d"))

	def test_missing_biblio_keeps_the_anchor_back(self):
		self.ops.biblio_bulk = lambda publications: []
		self.run_command(since="2024-01-01")
		self.assertEqual(Patents.objects.count(), 0)
		self.source.refresh_from_db()
		self.assertIsNone(self.source.last_successful_fetch_at)


class SkippedSourceTests(PatentImporterTestCase):
	def test_missing_credentials_skip_with_warning(self):
		OrganizationCredentials.objects.all().delete()
		out = self.run_command(since="2024-01-01")
		self.assertIn("No EPO OPS credentials", out)
		self.assertEqual(self.ops.search_calls, [])

	def test_source_without_query_is_skipped(self):
		self.source.ops_cql_query = ""
		self.source.save()
		out = self.run_command(since="2024-01-01")
		self.assertIn("no CQL query", out)

	def test_inactive_source_is_not_processed(self):
		self.source.active = False
		self.source.save()
		out = self.run_command(since="2024-01-01")
		self.assertIn("No active epo_ops patent Sources", out)

	def test_invalid_since_is_rejected(self):
		from django.core.management.base import CommandError

		with self.assertRaises(CommandError):
			self.run_command(since="yesterday")
