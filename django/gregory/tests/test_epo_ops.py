"""Tests for gregory.utils.epo_ops: the pure XML parsers (run against hand-made
fixtures in gregory/tests/fixtures/epo_ops/) and the OpsClient error mapping. Nothing
here calls OPS."""

import datetime
from pathlib import Path
from unittest.mock import MagicMock

import requests
from django.test import SimpleTestCase

from gregory.management.commands.feedreader_patents_ops import (
	choose_representative,
	is_grant_kind,
	month_slices,
)
from gregory.utils.epo_ops import (
	FULLTEXT_COUNTRIES,
	FamilyMember,
	OpsClient,
	OpsError,
	OpsQueryError,
	OpsQuotaExceeded,
	parse_biblio,
	parse_claims,
	parse_family,
	parse_search,
)

FIXTURES = Path(__file__).parent / "fixtures" / "epo_ops"


def fixture(name):
	return (FIXTURES / name).read_bytes()


class ParseSearchTests(SimpleTestCase):
	def test_hits_and_total(self):
		page = parse_search(fixture("search_page.xml"))
		self.assertEqual(page.total, 3)
		self.assertEqual(
			[(h.publication_number, h.family_id) for h in page.hits],
			[("EP4100001A1", "900001"), ("WO2022200001A1", "900001"), ("US2023300001A1", "900002")],
		)

	def test_no_search_element_means_zero(self):
		page = parse_search(b'<ops:world-patent-data xmlns:ops="http://ops.epo.org"/>')
		self.assertEqual((page.total, page.hits), (0, []))


class ParseFamilyTests(SimpleTestCase):
	def test_extended_family_filtered_to_one_simple_family(self):
		members = parse_family(fixture("family_900001.xml"), family_id="900001")
		self.assertEqual(
			[m.publication_number for m in members],
			["EP4100001A1", "EP4100001B1", "WO2022200001A1"],
		)

	def test_unfiltered_returns_every_member(self):
		self.assertEqual(len(parse_family(fixture("family_900001.xml"))), 4)

	def test_member_details(self):
		members = {m.publication_number: m for m in parse_family(fixture("family_900001.xml"), "900001")}
		ep = members["EP4100001A1"]
		self.assertEqual(ep.publication_date, datetime.date(2022, 12, 7))
		self.assertEqual(ep.application_number, "EP21177001")
		self.assertEqual(ep.application_date, datetime.date(2021, 6, 1))
		self.assertEqual(ep.priority_dates, [datetime.date(2021, 6, 1)])

	def test_is_representative_flag(self):
		members = {m.publication_number: m for m in parse_family(fixture("family_900001.xml"), "900001")}
		self.assertTrue(members["EP4100001A1"].is_representative)
		self.assertTrue(members["EP4100001B1"].is_representative)
		self.assertFalse(members["WO2022200001A1"].is_representative)


class ParseBiblioTests(SimpleTestCase):
	def setUp(self):
		self.docs = {b.publication_number: b for b in parse_biblio(fixture("biblio_bulk.xml"))}

	def test_two_documents(self):
		self.assertEqual(set(self.docs), {"EP4100001A1", "US2023300001A1"})

	def test_title_abstract_family(self):
		ep = self.docs["EP4100001A1"]
		self.assertEqual(ep.title, "METHODS OF TREATING MULTIPLE SCLEROSIS")
		self.assertIn("remyelination-promoting compound", ep.abstract)
		self.assertEqual(ep.family_id, "900001")

	def test_missing_english_abstract_is_none(self):
		self.assertIsNone(self.docs["US2023300001A1"].abstract)

	def test_applicants_deduplicated_by_case_keeping_both_formats(self):
		ep = self.docs["EP4100001A1"]
		self.assertEqual(ep.applicants_original, ["NEURONOVA THERAPEUTICS INC.", "Garcia, Maria"])
		self.assertEqual(ep.applicants_epodoc, ["NEURONOVA THERAPEUTICS INC [US]"])

	def test_inventors_deduplicated(self):
		self.assertEqual(self.docs["EP4100001A1"].inventors, ["GARCIA, MARIA", "LINDQVIST, ERIK"])

	def test_inventors_fall_back_to_epodoc(self):
		self.assertEqual(self.docs["US2023300001A1"].inventors, ["OKAFOR CHINEDU [GB]"])

	def test_classifications(self):
		ep = self.docs["EP4100001A1"]
		self.assertEqual(ep.cpc, ["A61P25/28", "A61K31/4045"])
		self.assertEqual(ep.ipc, ["A61K31/4045", "A61P25/28"])

	def test_priority_dates(self):
		self.assertEqual(self.docs["EP4100001A1"].priority_dates, [datetime.date(2021, 6, 1)])

	def test_literature_citations(self):
		citations = self.docs["EP4100001A1"].npl_citations
		self.assertEqual(len(citations), 2)  # the patent citation is not an nplcit
		first, second = citations
		self.assertEqual(
			first.title,
			"Remyelination after oligodendrocyte precursor transplantation in demyelinated lesions",
		)
		self.assertEqual(first.doi, "10.1523/JNEUROSCI.0001-21.2021")
		self.assertEqual(first.pmid, "34567890")
		self.assertEqual(first.cited_by, "applicant")
		self.assertIsNone(second.title)
		self.assertIsNone(second.doi)


class ParseClaimsTests(SimpleTestCase):
	def test_prefers_english(self):
		claims = parse_claims(fixture("claims_ep.xml"))
		self.assertEqual(claims.lang, "EN")
		self.assertTrue(claims.text.startswith("1. A compound for use in treating multiple sclerosis."))
		self.assertIn("2. The compound of claim 1", claims.text)

	def test_no_claims(self):
		self.assertIsNone(parse_claims(b'<ops:world-patent-data xmlns:ops="http://ops.epo.org"/>'))


class RepresentativeTests(SimpleTestCase):
	def member(self, country, kind="A1", number="1", date=None, flagged=False):
		return FamilyMember(
			family_id="1",
			country=country,
			doc_number=number,
			kind=kind,
			publication_date=date,
			is_representative=flagged,
		)

	def test_flagged_member_wins(self):
		members = parse_family(fixture("family_900001.xml"), "900001")
		self.assertEqual(choose_representative(members).publication_number, "EP4100001A1")

	def test_us_only_family_keeps_us_representative(self):
		members = parse_family(fixture("family_us_only.xml"), "900002")
		self.assertEqual(choose_representative(members).publication_number, "US2023300001A1")
		self.assertNotIn("US", FULLTEXT_COUNTRIES)

	def test_prefers_ep_over_flagged_office_without_full_text(self):
		us = self.member("US", flagged=True, date=datetime.date(2022, 1, 1))
		wo = self.member("WO", number="2", date=datetime.date(2022, 2, 1))
		ep = self.member("EP", number="3", date=datetime.date(2022, 3, 1))
		self.assertEqual(choose_representative([us, wo, ep]), ep)

	def test_then_wo(self):
		us = self.member("US", flagged=True)
		wo = self.member("WO", number="2")
		self.assertEqual(choose_representative([us, wo]), wo)

	def test_no_flag_uses_earliest_publication(self):
		later = self.member("EP", number="2", date=datetime.date(2023, 1, 1))
		earlier = self.member("EP", number="1", date=datetime.date(2022, 1, 1))
		self.assertEqual(choose_representative([later, earlier]), earlier)


class HelperTests(SimpleTestCase):
	def test_grant_kinds(self):
		self.assertTrue(is_grant_kind("EP", "B1"))
		self.assertTrue(is_grant_kind("CA", "C"))
		self.assertTrue(is_grant_kind("ES", "T3"))
		self.assertFalse(is_grant_kind("EP", "A1"))
		self.assertFalse(is_grant_kind("WO", "A1"))

	def test_month_slices_clip_to_bounds(self):
		slices = list(month_slices(datetime.date(2024, 1, 20), datetime.date(2024, 3, 5)))
		self.assertEqual(
			slices,
			[
				(datetime.date(2024, 1, 20), datetime.date(2024, 1, 31)),
				(datetime.date(2024, 2, 1), datetime.date(2024, 2, 29)),
				(datetime.date(2024, 3, 1), datetime.date(2024, 3, 5)),
			],
		)

	def test_month_slices_single_day(self):
		day = datetime.date(2024, 12, 31)
		self.assertEqual(list(month_slices(day, day)), [(day, day)])


def http_error(status, headers=None, content=b""):
	response = requests.Response()
	response.status_code = status
	response._content = content
	response.headers.update(headers or {})
	return requests.HTTPError(f"{status}", response=response)


def ok_response(content=b"", headers=None):
	response = requests.Response()
	response.status_code = 200
	response._content = content
	response.headers.update(headers or {})
	return response


class OpsClientErrorMappingTests(SimpleTestCase):
	def client_raising(self, *errors):
		library = MagicMock()
		library.published_data_search.side_effect = list(errors)
		return OpsClient(library, sleep=lambda s: None), library

	def test_429_is_quota(self):
		ops, _ = self.client_raising(http_error(429, content=fixture("fault_429.xml")))
		with self.assertRaises(OpsQuotaExceeded):
			ops.search("ta=x")

	def test_403_with_quota_reason(self):
		ops, _ = self.client_raising(http_error(403, {"X-Rejection-Reason": "RegisteredQuotaPerWeek"}))
		with self.assertRaises(OpsQuotaExceeded):
			ops.search("ta=x")

	def test_403_other_reason_is_plain_error(self):
		ops, _ = self.client_raising(http_error(403, {"X-Rejection-Reason": "RobotDetected"}))
		with self.assertRaises(OpsError) as ctx:
			ops.search("ta=x")
		self.assertNotIsInstance(ctx.exception, OpsQuotaExceeded)

	def test_library_quota_exceptions_are_mapped(self):
		from epo_ops import exceptions

		response = requests.Response()
		response.status_code = 403
		ops, _ = self.client_raising(
			exceptions.IndividualQuotaPerHourExceeded("hourly", response=response)
		)
		with self.assertRaises(OpsQuotaExceeded):
			ops.search("ta=x")

	def test_400_is_query_error_with_message(self):
		body = b'<fault xmlns="http://ops.epo.org"><code>CLIENT.CQL</code><message>bad query</message></fault>'
		ops, _ = self.client_raising(http_error(400, content=body))
		with self.assertRaisesMessage(OpsQueryError, "CLIENT.CQL bad query"):
			ops.search("ta=")

	def test_404_search_is_zero_results(self):
		ops, _ = self.client_raising(http_error(404))
		page = ops.search("ta=nothing")
		self.assertEqual((page.total, page.hits), (0, []))

	def test_503_is_retried(self):
		ops, library = self.client_raising(http_error(503), ok_response(fixture("search_page.xml")))
		self.assertEqual(ops.search("ta=x").total, 3)
		self.assertEqual(library.published_data_search.call_count, 2)

	def test_503_gives_up_after_max_attempts(self):
		ops, _ = self.client_raising(http_error(503), http_error(503), http_error(503))
		with self.assertRaises(OpsError):
			ops.search("ta=x")

	def test_usage_accounting(self):
		headers = {
			"X-Throttling-Control": "idle (retrieval=green:200, search=green:30)",
			"X-RegisteredQuotaPerWeek-Used": "1234",
		}
		ops, _ = self.client_raising(ok_response(fixture("search_page.xml"), headers))
		ops.search("ta=x")
		self.assertEqual(ops.request_count, 1)
		self.assertEqual(ops.bytes_received, len(fixture("search_page.xml")))
		self.assertEqual(ops.throttle_states, {headers["X-Throttling-Control"]})
		self.assertEqual(ops.last_quota_headers, {"X-RegisteredQuotaPerWeek-Used": "1234"})

	def test_bulk_biblio_rejects_oversized_batches(self):
		ops = OpsClient(MagicMock())
		with self.assertRaises(ValueError):
			ops.biblio_bulk([("EP", str(n), "A1") for n in range(101)])
