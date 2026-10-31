"""Tests for gregory.utils.fda: reading Drugs@FDA applications and the Purple Book, and
downloading them.

The rows in fixtures/fda/drugsfda-base.json are real applications cut from the Drugs@FDA
bulk file of 2026-10-23 (trimmed to the applications the tests need, plus NDA017581 cut
down to three submissions). fixtures/fda/purplebook-base.csv holds real rows from the
Purple Book file for September 2026. Nothing here calls the FDA: downloads are mocked at
``requests.get``.
"""

import json
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import requests

from gregory.utils import fda

FIXTURES = Path(__file__).parent / "fixtures" / "fda"


def load_drugsfda():
	with open(FIXTURES / "drugsfda-base.json") as handle:
		return json.load(handle)


RECORDS = {r["application_number"]: r for r in load_drugsfda()["results"]}
PURPLEBOOK = (FIXTURES / "purplebook-base.csv").read_text()


def parse(number):
	return fda.parse_application(RECORDS[number])


def event_keys(parsed):
	return {(e["kind"], e["event_date"]): e["to_value"] for e in parsed.events}


class ParseFdaDateTests(TestCase):
	def test_valid(self):
		self.assertEqual(fda.parse_fda_date("20170328"), date(2017, 3, 28))

	def test_empty_is_none_without_a_warning(self):
		with self.assertNoLogs("gregory.utils.fda", level="WARNING"):
			self.assertIsNone(fda.parse_fda_date(""))
			self.assertIsNone(fda.parse_fda_date(None))

	def test_invalid_is_none_with_a_warning(self):
		with self.assertLogs("gregory.utils.fda", level="WARNING"):
			self.assertIsNone(fda.parse_fda_date("2017-03-28"))
		with self.assertLogs("gregory.utils.fda", level="WARNING"):
			self.assertIsNone(fda.parse_fda_date("20170231"))


class ParsePurplebookDateTests(TestCase):
	def test_valid(self):
		self.assertEqual(fda.parse_purplebook_date("August 30, 2017"), date(2017, 8, 30))

	def test_empty_is_none_without_a_warning(self):
		with self.assertNoLogs("gregory.utils.fda", level="WARNING"):
			self.assertIsNone(fda.parse_purplebook_date(""))
			self.assertIsNone(fda.parse_purplebook_date(None))

	def test_invalid_is_none_with_a_warning(self):
		with self.assertLogs("gregory.utils.fda", level="WARNING"):
			self.assertIsNone(fda.parse_purplebook_date("30/08/2017"))


class NormaliseSubstanceTests(TestCase):
	def test_strips_the_four_letter_suffix(self):
		self.assertEqual(fda.normalise_substance("NATALIZUMAB-SZTN"), "natalizumab")

	def test_keeps_a_salt(self):
		self.assertEqual(fda.normalise_substance("FINGOLIMOD HYDROCHLORIDE"), "fingolimod hydrochloride")

	def test_collapses_whitespace(self):
		self.assertEqual(fda.normalise_substance("  Ocrelizumab   and hyaluronidase-ocsq "), "ocrelizumab and hyaluronidase")

	def test_leaves_a_longer_hyphenated_ending_alone(self):
		self.assertEqual(fda.normalise_substance("ESTROGENS-CONJUGATED"), "estrogens-conjugated")
		self.assertEqual(fda.normalise_substance("FERROUS-CITRATE"), "ferrous-citrate")

	def test_a_suffix_must_be_exactly_four_letters(self):
		self.assertEqual(fda.normalise_substance("ABC-DEF"), "abc-def")
		self.assertEqual(fda.normalise_substance("KR-81M"), "kr-81m")
		self.assertEqual(fda.normalise_substance("ABC-DEFGH"), "abc-defgh")

	def test_empty(self):
		self.assertEqual(fda.normalise_substance(""), "")
		self.assertEqual(fda.normalise_substance(None), "")


class ParseApplicationTests(TestCase):
	def test_anda_is_skipped(self):
		self.assertEqual(fda.application_type(RECORDS["ANDA076192"]), "ANDA")
		self.assertIsNone(parse("ANDA076192"))

	def test_ocrevus(self):
		ocrevus = parse("BLA761053")
		self.assertEqual(ocrevus.fda_application_number, "BLA761053")
		self.assertEqual((ocrevus.fda_application_type, ocrevus.fda_center), ("BLA", "CDER"))
		self.assertEqual(ocrevus.name, "OCREVUS")
		self.assertEqual(ocrevus.brand_names, ["OCREVUS"])
		self.assertEqual(ocrevus.substances, ["ocrelizumab"])
		self.assertEqual(ocrevus.inn, "OCRELIZUMAB")
		self.assertFalse(ocrevus.is_combination)
		self.assertEqual(ocrevus.status, "Approved")
		self.assertEqual(ocrevus.holder, "GENENTECH INC")
		self.assertEqual(ocrevus.authorisation_date, date(2017, 3, 28))
		self.assertEqual(ocrevus.fda_original_class, "TYPE 1")
		self.assertIs(ocrevus.fda_priority_review, True)
		self.assertEqual(ocrevus.fda_pharm_classes, ["CD20-directed Cytolytic Antibody [EPC]"])
		self.assertEqual(ocrevus.pharmacotherapeutic_group, "CD20-directed Cytolytic Antibody [EPC]")
		self.assertIn("ApplNo=761053", ocrevus.url)
		self.assertEqual(ocrevus.raw["application_number"], "BLA761053")
		self.assertEqual(ocrevus.bad_dates, [])

	def test_ocrevus_events_are_the_approval_and_the_efficacy_supplements(self):
		self.assertEqual(
			event_keys(parse("BLA761053")),
			{
				(fda.FDA_APPROVED, date(2017, 3, 28)): "",
				(fda.FDA_EFFICACY_SUPPLEMENT, date(2020, 12, 14)): "S-22",
				(fda.FDA_EFFICACY_SUPPLEMENT, date(2026, 5, 8)): "S-38",
			},
		)

	def test_labeling_supplements_are_not_events(self):
		kinds = {e["kind"] for e in parse("BLA761053").events}
		self.assertEqual(kinds, {fda.FDA_APPROVED, fda.FDA_EFFICACY_SUPPLEMENT})

	def test_ocrevus_zunovo_has_no_openfda_block(self):
		zunovo = parse("BLA761371")
		self.assertEqual(zunovo.fda_pharm_classes, [])
		self.assertEqual(zunovo.pharmacotherapeutic_group, "")
		# The FDA lists the pair as one ingredient name, "OCRELIZUMAB AND HYALURONIDASE-OCSQ":
		# it is split on the word AND, so the row is a combination of two substances.
		self.assertEqual(zunovo.substances, ["ocrelizumab", "hyaluronidase"])
		self.assertTrue(zunovo.is_combination)
		# The names stay as the FDA published them.
		self.assertEqual(zunovo.inn, "OCRELIZUMAB AND HYALURONIDASE-OCSQ")
		self.assertEqual(zunovo.active_substance, "OCRELIZUMAB AND HYALURONIDASE-OCSQ")

	def test_kesimpta_and_arzerra_are_one_application_with_two_brands(self):
		kesimpta = parse("BLA125326")
		self.assertEqual(kesimpta.brand_names, ["KESIMPTA", "ARZERRA"])
		self.assertEqual(kesimpta.name, "KESIMPTA / ARZERRA")
		self.assertEqual(kesimpta.substances, ["ofatumumab"])

	def test_tysabri_has_no_openfda_block(self):
		tysabri = parse("BLA125104")
		self.assertEqual(tysabri.fda_pharm_classes, [])
		self.assertEqual(tysabri.authorisation_date, date(2004, 11, 23))
		self.assertEqual(tysabri.status, "Approved")

	def test_tyruko_loses_the_suffix_from_its_substance(self):
		tyruko = parse("BLA761322")
		self.assertEqual(tyruko.inn, "NATALIZUMAB-SZTN")
		self.assertEqual(tyruko.substances, ["natalizumab"])

	def test_mavenclad_and_leustatin_share_a_substance_and_differ_in_status(self):
		mavenclad, leustatin = parse("NDA022561"), parse("NDA020229")
		self.assertEqual(mavenclad.substances, leustatin.substances)
		self.assertEqual(mavenclad.status, "Approved")
		self.assertEqual(leustatin.status, "Discontinued")

	def test_viagra_has_no_pharmacologic_class(self):
		viagra = parse("NDA020895")
		self.assertEqual(viagra.fda_pharm_classes, [])
		self.assertEqual(viagra.brand_names, ["VIAGRA"])

	def test_synjardy_is_a_combination(self):
		synjardy = parse("NDA206111")
		self.assertTrue(synjardy.is_combination)
		self.assertEqual(sorted(synjardy.substances), ["empagliflozin", "metformin hydrochloride"])
		self.assertEqual(synjardy.inn, "EMPAGLIFLOZIN; METFORMIN HYDROCHLORIDE")

	def test_a_tentative_approval(self):
		tentative = parse("NDA202172")
		self.assertEqual(tentative.status, "Tentatively approved")
		self.assertIsNone(tentative.authorisation_date)
		self.assertEqual(
			event_keys(tentative), {(fda.FDA_TENTATIVE_APPROVAL, date(2016, 2, 12)): ""}
		)
		# The class still comes from the original submission.
		self.assertEqual(tentative.fda_original_class, "TYPE 2")

	def test_two_efficacy_approvals_on_one_day_are_one_event(self):
		naprosyn = parse("NDA017581")
		efficacy = [e for e in naprosyn.events if e["kind"] == fda.FDA_EFFICACY_SUPPLEMENT]
		self.assertEqual(len(efficacy), 1)
		self.assertEqual(efficacy[0]["event_date"], date(1987, 3, 23))
		self.assertEqual(efficacy[0]["to_value"], "S-73, S-65")
		self.assertEqual(naprosyn.brand_names, ["NAPROSYN"])
		self.assertEqual(naprosyn.status, "Approved")

	def test_a_tentative_cmc_or_labeling_supplement_is_not_an_event(self):
		record = json.loads(json.dumps(RECORDS["BLA761053"]))
		for number, code in (("90", "MANUF (CMC)"), ("91", "LABELING"), ("92", "EFFICACY")):
			record["submissions"].append(
				{
					"submission_type": "SUPPL",
					"submission_number": number,
					"submission_status": "TA",
					"submission_status_date": "20250101",
					"submission_class_code": code,
				}
			)
		keys = event_keys(fda.parse_application(record))
		# Only the efficacy supplement counts, as a tentative approval.
		self.assertEqual(keys[(fda.FDA_TENTATIVE_APPROVAL, date(2025, 1, 1))], "S-92")
		self.assertEqual(
			len([k for k in keys if k[0] == fda.FDA_TENTATIVE_APPROVAL]), 1
		)
		record["submissions"] = [s for s in record["submissions"] if s["submission_number"] != "92"]
		self.assertNotIn(
			fda.FDA_TENTATIVE_APPROVAL, {k[0] for k in event_keys(fda.parse_application(record))}
		)

	def test_the_raw_digest_follows_the_record(self):
		parsed = parse("BLA761053")
		self.assertEqual(len(parsed.raw_digest), 64)
		self.assertEqual(parsed.raw_digest, fda.raw_digest(json.loads(json.dumps(RECORDS["BLA761053"]))))
		self.assertEqual(fda.raw_digest({"a": 1, "b": 2}), fda.raw_digest({"b": 2, "a": 1}))
		self.assertNotEqual(parsed.raw_digest, parse("BLA761371").raw_digest)
		self.assertIn("raw_digest", parsed.model_values())

	def test_a_contrast_agent_carries_its_class(self):
		agent = parse("NDA205764")
		self.assertTrue(any("Contrast" in c for c in agent.fda_pharm_classes))

	def test_an_application_without_products_submissions_or_openfda(self):
		parsed = fda.parse_application({"application_number": "NDA000001", "sponsor_name": "ACME"})
		self.assertEqual(parsed.status, "Discontinued")
		self.assertEqual(parsed.name, "NDA000001")
		self.assertEqual(parsed.substances, [])
		self.assertEqual(parsed.events, [])
		self.assertIsNone(parsed.authorisation_date)
		self.assertIsNone(parsed.fda_priority_review)

	def test_a_bad_submission_date_is_skipped_and_listed(self):
		record = json.loads(json.dumps(RECORDS["BLA761053"]))
		original = next(s for s in record["submissions"] if s["submission_type"] == "ORIG")
		original["submission_status_date"] = "not a date"
		with self.assertLogs("gregory.utils.fda", level="WARNING"):
			parsed = fda.parse_application(record)
		self.assertEqual(parsed.bad_dates, ["submission_status_date"])
		# The original had the bad date, so there is no approval date or event for it.
		self.assertIsNone(parsed.authorisation_date)
		self.assertNotIn(fda.FDA_APPROVED, {e["kind"] for e in parsed.events})

	def test_the_earliest_approved_original_wins(self):
		record = json.loads(json.dumps(RECORDS["BLA761053"]))
		original = next(s for s in record["submissions"] if s["submission_type"] == "ORIG")
		record["submissions"].append(
			dict(original, submission_number="2", submission_status_date="20300101", submission_class_code="TYPE 3")
		)
		parsed = fda.parse_application(record)
		self.assertEqual(parsed.authorisation_date, date(2017, 3, 28))
		self.assertEqual(parsed.fda_original_class, "TYPE 1")

	def test_model_values_leave_out_events_and_bad_dates(self):
		values = parse("BLA761053").model_values()
		self.assertNotIn("events", values)
		self.assertNotIn("bad_dates", values)
		self.assertEqual(values["regulator"], "fda")


class DrugsFdaFileTests(TestCase):
	def test_reads_meta_and_results_one_record_at_a_time(self):
		text = (FIXTURES / "drugsfda-base.json").read_text()
		meta, results = fda.read_drugsfda(text)
		self.assertEqual(meta["results"]["total"], 13)
		self.assertEqual([r["application_number"] for r in results][:2], ["BLA761053", "BLA761371"])

	def test_reads_a_zip(self):
		buffer = BytesIO()
		with zipfile.ZipFile(buffer, "w") as archive:
			archive.writestr("drug-drugsfda-0001-of-0001.json", (FIXTURES / "drugsfda-base.json").read_bytes())
		meta, results = fda.read_drugsfda(fda.drugsfda_text(buffer.getvalue()))
		self.assertEqual(len(list(results)), meta["results"]["total"])

	def test_a_truncated_file_raises(self):
		text = (FIXTURES / "drugsfda-base.json").read_text()
		_, results = fda.read_drugsfda(text[: len(text) // 2])
		with self.assertRaises(ValueError):
			list(results)

	def test_results_before_meta_still_parse(self):
		payload = load_drugsfda()
		text = json.dumps({"results": payload["results"], "meta": payload["meta"]})
		meta, results = fda.read_drugsfda(text)
		self.assertEqual(len(list(results)), meta["results"]["total"])

	def test_text_without_results_raises(self):
		with self.assertRaises(ValueError):
			fda.read_drugsfda('{"meta": {}}')


class ParsePurplebookTests(TestCase):
	def setUp(self):
		self.cber, self.cder_flags = fda.parse_purplebook(PURPLEBOOK)
		self.by_number = {p.fda_application_number: p for p in self.cber}

	def test_only_the_rows_after_the_last_header_are_read(self):
		# The two change rows above the second header (Tudriqev, Fayuvi) are not in the list.
		self.assertEqual(
			sorted(self.by_number),
			["BLA125586", "BLA125643", "BLA125646", "BLA125773", "BLA125789"],
		)

	def test_kymriah(self):
		kymriah = self.by_number["BLA125646"]
		self.assertEqual((kymriah.fda_application_type, kymriah.fda_center), ("BLA", "CBER"))
		self.assertEqual(kymriah.name, "Kymriah")
		self.assertEqual(kymriah.brand_names, ["Kymriah"])
		self.assertEqual(kymriah.substances, ["tisagenlecleucel"])
		self.assertEqual(kymriah.holder, "Novartis Pharmaceuticals Corporation")
		self.assertEqual(kymriah.status, "Approved")
		self.assertEqual(kymriah.authorisation_date, date(2017, 8, 30))
		self.assertEqual(
			event_keys(kymriah), {(fda.FDA_APPROVED, date(2017, 8, 30)): ""}
		)
		self.assertEqual(kymriah.url, "")
		self.assertFalse(kymriah.biosimilar)
		self.assertEqual(kymriah.raw["rows"][0]["BLA Number"], "125646")

	def test_the_bla_number_is_padded_to_six_digits(self):
		self.assertEqual(fda.bla_key("125646"), "BLA125646")
		self.assertEqual(fda.bla_key("5"), "BLA000005")
		self.assertEqual(fda.bla_key(""), "")

	def test_amtagvi_is_a_leucel_that_is_not_car_t(self):
		self.assertEqual(self.by_number["BLA125773"].substances, ["lifileucel"])

	def test_tecelra_takes_the_earliest_of_two_original_dates(self):
		tecelra = self.by_number["BLA125789"]
		self.assertEqual(len(tecelra.raw["rows"]), 2)
		self.assertEqual(tecelra.authorisation_date, date(2024, 8, 1))
		self.assertEqual(tecelra.brand_names, ["tecelra"])

	def test_a_revoked_licence_is_a_revoked_status(self):
		andexxa = self.by_number["BLA125586"]
		self.assertEqual(andexxa.status, "Revoked")

	def test_cder_rows_give_flags_not_medicines(self):
		self.assertEqual(
			set(self.cder_flags), {"BLA761322", "BLA761053", "BLA761371"}
		)
		self.assertEqual(
			self.cder_flags["BLA761322"],
			{"biosimilar": True, "interchangeable": False, "licensure": "Licensed"},
		)
		self.assertEqual(
			self.cder_flags["BLA761053"],
			{"biosimilar": False, "interchangeable": False, "licensure": "Licensed"},
		)

	def test_a_cber_proper_name_is_one_substance(self):
		rows = [r for r in PURPLEBOOK.splitlines() if "tisagenlecleucel" in r]
		self.assertTrue(rows)
		text = PURPLEBOOK.replace("tisagenlecleucel", "alpha and beta-xioi")
		cber, _ = fda.parse_purplebook(text)
		kymriah = next(p for p in cber if p.fda_application_number == "BLA125646")
		self.assertEqual(kymriah.substances, ["alpha and beta"])
		self.assertFalse(kymriah.is_combination)

	def test_an_interchangeable_biosimilar(self):
		text = PURPLEBOOK.replace("351(k) Biosimilar", "351(k) Interchangeable")
		_, flags = fda.parse_purplebook(text)
		self.assertEqual(
			(flags["BLA761322"]["biosimilar"], flags["BLA761322"]["interchangeable"]), (True, True)
		)

	def test_a_missing_brand_falls_back_to_the_proper_name(self):
		text = PURPLEBOOK.replace(",Kymriah,tisagenlecleucel,", ",,tisagenlecleucel,")
		cber, _ = fda.parse_purplebook(text)
		kymriah = next(p for p in cber if p.fda_application_number == "BLA125646")
		self.assertEqual(kymriah.brand_names, [])
		self.assertEqual(kymriah.name, "tisagenlecleucel")

	def test_a_bad_approval_date_is_listed(self):
		text = PURPLEBOOK.replace('"August 30, 2017"', "30/08/2017")
		with self.assertLogs("gregory.utils.fda", level="WARNING"):
			cber, _ = fda.parse_purplebook(text)
		kymriah = next(p for p in cber if p.fda_application_number == "BLA125646")
		self.assertEqual(kymriah.bad_dates, ["Approval Date"])
		self.assertIsNone(kymriah.authorisation_date)
		self.assertEqual(kymriah.events, [])

	def test_a_different_header_set_is_refused(self):
		text = PURPLEBOOK.replace("Proper Name", "Proper Title")
		with self.assertRaisesRegex(ValueError, "columns changed"):
			fda.parse_purplebook(text)

	def test_a_file_without_a_header_is_refused(self):
		with self.assertRaises(ValueError):
			fda.parse_purplebook("just,some,text\n1,2,3\n")


class FindPurplebookUrlTests(TestCase):
	def test_takes_the_newest_by_year_and_month_whatever_the_case(self):
		base = "https://www.accessdata.fda.gov/drugsatfda_docs/PurpleBook"
		html = "".join(
			f'<a href="{base}/{year}/purplebook-search-{month}-data-download.csv">x</a>'
			for year, month in [(2026, "january"), (2026, "February"), (2026, "October"), (2025, "december")]
		)
		self.assertEqual(
			fda.find_purplebook_url(html),
			f"{base}/2026/purplebook-search-October-data-download.csv",
		)

	def test_no_link_raises(self):
		with self.assertRaises(ValueError):
			fda.find_purplebook_url("<html></html>")


def response(status, body=b"", headers=None, text=""):
	mock = MagicMock()
	mock.status_code = status
	mock.content = body
	mock.text = text
	mock.headers = headers or {}
	return mock


class State:
	def __init__(self, last_modified="", etag="", source_url=""):
		self.last_modified, self.etag, self.source_url = last_modified, etag, source_url


INDEX = {
	"results": {
		"drug": {"drugsfda": {"partitions": [{"file": "https://download.open.fda.gov/drug/drugsfda/x.json.zip"}]}}
	}
}


@patch("gregory.utils.regulator_http.time.sleep")
class DownloadDrugsFdaTests(TestCase):
	def index_response(self):
		mock = response(200)
		mock.json.return_value = INDEX
		return mock

	@patch("gregory.utils.regulator_http.requests.get")
	def test_reads_the_path_from_the_index_and_returns_the_validators(self, get, sleep):
		get.side_effect = [
			self.index_response(),
			response(200, b"zip", {"Last-Modified": "Fri, 23 Oct 2026 12:00:00 GMT", "ETag": '"abc"'}),
		]
		status, body, headers = fda.download_drugsfda()
		self.assertEqual((status, body), (200, b"zip"))
		self.assertEqual(headers["Last-Modified"], "Fri, 23 Oct 2026 12:00:00 GMT")
		self.assertEqual(headers["ETag"], '"abc"')
		self.assertEqual(headers["url"], "https://download.open.fda.gov/drug/drugsfda/x.json.zip")
		self.assertEqual(get.call_args_list[0].args[0], fda.DOWNLOAD_INDEX_URL)
		file_headers = get.call_args_list[1].kwargs["headers"]
		self.assertIn("gregory-ai", file_headers["User-Agent"])
		self.assertNotIn("If-Modified-Since", file_headers)
		self.assertEqual(get.call_args_list[1].kwargs["timeout"], 60)

	@patch("gregory.utils.regulator_http.requests.get")
	def test_sends_both_validators_and_passes_a_304_through(self, get, sleep):
		get.side_effect = [self.index_response(), response(304)]
		state = State("Fri, 23 Oct 2026 12:00:00 GMT", '"abc"')
		status, body, _ = fda.download_drugsfda(state)
		self.assertEqual((status, body), (304, b""))
		sent = get.call_args_list[1].kwargs["headers"]
		self.assertEqual(sent["If-Modified-Since"], "Fri, 23 Oct 2026 12:00:00 GMT")
		self.assertEqual(sent["If-None-Match"], '"abc"')

	@patch("gregory.utils.regulator_http.requests.get")
	def test_retries_a_server_error_twice_then_succeeds(self, get, sleep):
		get.side_effect = [self.index_response(), response(503), response(502), response(200, b"ok")]
		self.assertEqual(fda.download_drugsfda()[:2], (200, b"ok"))

	@patch("gregory.utils.regulator_http.requests.get")
	def test_gives_up_after_the_retries(self, get, sleep):
		get.side_effect = [self.index_response(), response(500), response(500), response(500)]
		with self.assertRaises(requests.RequestException):
			fda.download_drugsfda()


@patch("gregory.utils.regulator_http.time.sleep")
class DownloadPurplebookTests(TestCase):
	URL = "https://www.accessdata.fda.gov/drugsatfda_docs/PurpleBook/2026/purplebook-search-October-data-download.csv"

	@patch("gregory.utils.regulator_http.requests.get")
	def test_finds_the_newest_file_on_the_downloads_page(self, get, sleep):
		get.side_effect = [response(200, text=f'<a href="{self.URL}">x</a>'), response(200, b"csv")]
		status, body, headers = fda.download_purplebook()
		self.assertEqual((status, body, headers["url"]), (200, b"csv", self.URL))
		self.assertEqual(get.call_args_list[0].args[0], fda.PURPLEBOOK_DOWNLOADS_URL)

	@patch("gregory.utils.regulator_http.requests.get")
	def test_an_explicit_url_skips_the_page(self, get, sleep):
		get.return_value = response(200, b"csv")
		fda.download_purplebook(url=self.URL)
		self.assertEqual(get.call_count, 1)
		self.assertEqual(get.call_args.args[0], self.URL)

	@patch("gregory.utils.regulator_http.requests.get")
	def test_validators_are_sent_only_for_the_same_file(self, get, sleep):
		get.return_value = response(304)
		same = State("Wed, 28 Oct 2026 12:00:00 GMT", '"x"', source_url=self.URL)
		self.assertEqual(fda.download_purplebook(same, url=self.URL)[0], 304)
		self.assertEqual(get.call_args.kwargs["headers"]["If-None-Match"], '"x"')
		older = State("Wed, 28 Oct 2026 12:00:00 GMT", '"x"', source_url=self.URL.replace("October", "September"))
		fda.download_purplebook(older, url=self.URL)
		self.assertNotIn("If-None-Match", get.call_args.kwargs["headers"])
		self.assertNotIn("If-Modified-Since", get.call_args.kwargs["headers"])


class IngredientSplitTests(TestCase):
	"""Drugs@FDA sometimes writes a combination as one ingredient name."""

	def record(self, *names, per_product=1):
		ingredients = [{"name": name, "strength": "1 MG"} for name in names]
		return {
			"application_number": "NDA999990",
			"sponsor_name": "ACME",
			"products": [
				{"brand_name": "ACME", "marketing_status": "Prescription", "active_ingredients": ingredients}
			][:per_product],
			"submissions": [],
		}

	def test_and_splits_one_ingredient_into_two_substances(self):
		parsed = fda.parse_application(self.record("OCRELIZUMAB AND HYALURONIDASE-OCSQ"))
		self.assertEqual(parsed.substances, ["ocrelizumab", "hyaluronidase"])
		self.assertTrue(parsed.is_combination)

	def test_the_split_is_case_insensitive_and_whole_word(self):
		parsed = fda.parse_application(self.record("Amlodipine and Benazepril", "BRANDON SANDOZ"))
		self.assertEqual(parsed.substances, ["amlodipine", "benazepril", "brandon sandoz"])

	def test_a_name_containing_and_inside_a_word_is_not_split(self):
		parsed = fda.parse_application(self.record("ANDROGEN", "LAVENDER"))
		self.assertEqual(parsed.substances, ["androgen", "lavender"])
		single = fda.parse_application(self.record("ANDROGEN"))
		self.assertEqual(single.substances, ["androgen"])
		self.assertFalse(single.is_combination)

	def test_a_single_ordinary_ingredient_is_not_a_combination(self):
		parsed = fda.parse_application(self.record("FINGOLIMOD HYDROCHLORIDE"))
		self.assertEqual(parsed.substances, ["fingolimod hydrochloride"])
		self.assertFalse(parsed.is_combination)

	def test_an_enumeration_with_and_is_split_on_its_commas_too(self):
		parsed = fda.parse_application(self.record("LAMIVUDINE, NEVIRAPINE, AND STAVUDINE"))
		self.assertEqual(parsed.substances, ["lamivudine", "nevirapine", "stavudine"])
		parsed = fda.parse_application(self.record("DOLUTEGRAVIR, EMTRICITABINE and TENOFOVIR ALAFENAMIDE"))
		self.assertEqual(parsed.substances, ["dolutegravir", "emtricitabine", "tenofovir alafenamide"])

	def test_a_name_with_a_comma_but_no_and_is_not_split(self):
		parsed = fda.parse_application(self.record("ESTROGENS, CONJUGATED SYNTHETIC A"))
		self.assertEqual(parsed.substances, ["estrogens, conjugated synthetic a"])
		self.assertFalse(parsed.is_combination)

	def test_a_repeated_substance_is_kept_once(self):
		parsed = fda.parse_application(self.record("A AND B", "B AND A"))
		self.assertEqual(parsed.substances, ["a", "b"])

	def test_inn_and_active_substance_stay_as_published(self):
		parsed = fda.parse_application(self.record("A AND B-ABCD"))
		self.assertEqual(parsed.inn, "A AND B-ABCD")
		self.assertEqual(parsed.active_substance, "A AND B-ABCD")

	def test_two_listed_ingredients_still_make_a_combination(self):
		parsed = fda.parse_application(self.record("EMPAGLIFLOZIN", "METFORMIN HYDROCHLORIDE"))
		self.assertTrue(parsed.is_combination)

	def test_a_cber_proper_name_is_not_split(self):
		text = (Path(__file__).parent / "fixtures" / "fda" / "purplebook-base.csv").read_text()
		cber, _ = fda.parse_purplebook(text)
		for parsed in cber:
			self.assertEqual(len(parsed.substances), 1, parsed.name)
