"""
Tests for the CTIS public search API client and record mapper
(CTIS-API-FEEDREADER-PLAN.md).

Covers:
  - CTISPublicAPI.search / iter_search: pagination, incremental stop only when a
    whole page is stale on both date fields, sleep is honoured, typed exception on
    a non-JSON / unexpected-shape response
  - parse_ctis_search_record: sponsorType dedupe, ageGroup split, day-first dates,
    results_posted None-guard, unknown status/region code handling (log-and-skip,
    never write a bare numeric code)
  - RSS-parity invariant: EUTrialParser.parse_summary (RSS path) and
    CTISPublicAPI.parse_ctis_search_record (API path) produce byte-identical
    extra_fields for the same real trial (2025-523726-40-00), captured live from
    both channels on 2026-07-18 (see docs/ctis-public-api-schema.md)

Run:
  docker exec gregory python manage.py test gregory.tests.test_ctis_public_api
"""

import datetime
import os
from unittest.mock import MagicMock

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import SimpleTestCase

from gregory.classes import CTISPublicAPI, CTISPublicAPIError, EUTrialParser


def _mock_response(payload, status_ok=True, json_error=False):
	response = MagicMock()
	if not status_ok:
		import requests

		response.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
	else:
		response.raise_for_status.return_value = None
	if json_error:
		response.json.side_effect = ValueError("no JSON object could be decoded")
	else:
		response.json.return_value = payload
	return response


def _page(records, current_page=1, total_pages=1, next_page=None):
	if next_page is None:
		next_page = current_page < total_pages
	return {
		"showWarning": False,
		"pagination": {
			"totalRecords": len(records),
			"currentPage": current_page,
			"totalPages": total_pages,
			"nextPage": next_page,
			"prevPage": current_page > 1,
		},
		"data": records,
	}


def _record(ct_number="2026-000000-00-00", last_updated="01/01/2026", last_pub="01/01/2026", **overrides):
	record = {
		"ctNumber": ct_number,
		"ctStatus": 4,
		"ctTitle": f"Trial {ct_number}",
		"conditions": "Multiple Sclerosis",
		"trialCountries": ["Spain:4"],
		"decisionDateOverall": "01/01/2026",
		"decisionDate": "ES: 01/01/2026",
		"therapeuticAreas": ["Diseases [C] - Nervous System Diseases [C10]"],
		"sponsor": "Test Sponsor",
		"sponsorType": "Pharmaceutical company",
		"trialPhase": "Therapeutic exploratory (Phase II)",
		"endPoint": "Secondary endpoint text",
		"product": "Test Product",
		"ageGroup": "18-64 years",
		"gender": "Female, Male",
		"trialRegion": 3,
		"totalNumberEnrolled": "10",
		"primaryEndPoint": "Primary endpoint text",
		"resultsFirstReceived": "No",
		"lastUpdated": last_updated,
		"lastPublicationUpdate": last_pub,
	}
	record.update(overrides)
	return record


class SearchTests(SimpleTestCase):
	def setUp(self):
		self.api = CTISPublicAPI()
		self.api.session = MagicMock()

	def test_search_posts_expected_payload(self):
		self.api.session.post.return_value = _mock_response(_page([_record()]))
		self.api.search({"medicalCondition": "Multiple Sclerosis"}, page=2, size=25)

		args, kwargs = self.api.session.post.call_args
		self.assertEqual(args[0], f"{CTISPublicAPI.BASE_URL}/search")
		self.assertEqual(
			kwargs["json"],
			{
				"searchCriteria": {"medicalCondition": "Multiple Sclerosis"},
				"pagination": {"page": 2, "size": 25},
				"sort": {"property": "lastPublicationUpdate", "direction": "DESC"},
			},
		)
		self.assertEqual(kwargs["timeout"], 30)

	def test_search_raises_typed_error_on_non_json_response(self):
		self.api.session.post.return_value = _mock_response({}, json_error=True)
		with self.assertRaises(CTISPublicAPIError):
			self.api.search({"medicalCondition": "X"})

	def test_search_raises_typed_error_on_missing_data_key(self):
		self.api.session.post.return_value = _mock_response({"pagination": {}})
		with self.assertRaises(CTISPublicAPIError):
			self.api.search({"medicalCondition": "X"})

	def test_search_propagates_http_error(self):
		import requests

		self.api.session.post.return_value = _mock_response({}, status_ok=False)
		with self.assertRaises(requests.exceptions.HTTPError):
			self.api.search({"medicalCondition": "X"})


class IterSearchTests(SimpleTestCase):
	def setUp(self):
		self.api = CTISPublicAPI()
		self.api.session = MagicMock()

	def test_paginates_until_no_next_page(self):
		page1 = _page([_record("2026-000000-00-01"), _record("2026-000000-00-02")], current_page=1, total_pages=2)
		page2 = _page([_record("2026-000000-00-03")], current_page=2, total_pages=2)
		self.api.session.post.side_effect = [
			_mock_response(page1),
			_mock_response(page2),
		]

		records = list(self.api.iter_search({"medicalCondition": "X"}, sleep=0))

		self.assertEqual(len(records), 3)
		self.assertEqual(self.api.session.post.call_count, 2)

	def test_stops_when_no_records_returned(self):
		self.api.session.post.return_value = _mock_response(_page([]))
		records = list(self.api.iter_search({"medicalCondition": "X"}, sleep=0))
		self.assertEqual(records, [])
		self.assertEqual(self.api.session.post.call_count, 1)

	def test_sleep_is_invoked_between_pages(self):
		page1 = _page([_record("2026-000000-00-01")], current_page=1, total_pages=2)
		page2 = _page([_record("2026-000000-00-02")], current_page=2, total_pages=2)
		self.api.session.post.side_effect = [_mock_response(page1), _mock_response(page2)]

		with __import__("unittest.mock", fromlist=["patch"]).patch("time.sleep") as mock_sleep:
			list(self.api.iter_search({"medicalCondition": "X"}, sleep=0.75))
			mock_sleep.assert_called_once_with(0.75)

	def test_incremental_mode_does_not_stop_on_single_stale_record_mid_page(self):
		"""One stale record inside an otherwise-fresh page must not stop paging —
		the sort is not strictly monotonic (see docs/ctis-public-api-schema.md)."""
		fresh = _record("2026-000000-00-01", last_updated="15/07/2026", last_pub="15/07/2026")
		stale = _record("2026-000000-00-02", last_updated="01/01/2020", last_pub="01/01/2020")
		page1 = _page([stale, fresh], current_page=1, total_pages=2)
		page2 = _page([_record("2026-000000-00-03", last_updated="14/07/2026", last_pub="14/07/2026")], current_page=2, total_pages=2)
		self.api.session.post.side_effect = [_mock_response(page1), _mock_response(page2)]

		records = list(
			self.api.iter_search(
				{"medicalCondition": "X"}, since=datetime.date(2026, 7, 1), sleep=0
			)
		)

		self.assertEqual(self.api.session.post.call_count, 2)
		self.assertEqual(len(records), 3)

	def test_incremental_mode_stops_after_a_wholly_stale_page(self):
		stale_page = _page(
			[
				_record("2026-000000-00-01", last_updated="01/01/2020", last_pub="01/01/2020"),
				_record("2026-000000-00-02", last_updated="02/01/2020", last_pub="02/01/2020"),
			],
			current_page=1,
			total_pages=3,
		)
		self.api.session.post.return_value = _mock_response(stale_page)

		list(self.api.iter_search({"medicalCondition": "X"}, since=datetime.date(2026, 7, 1), sleep=0))

		self.assertEqual(self.api.session.post.call_count, 1)


class ParseSearchRecordTests(SimpleTestCase):
	def setUp(self):
		self.api = CTISPublicAPI()

	def test_sponsor_type_comma_duplicates_are_deduped(self):
		record = _record(sponsorType="Hospital/Clinic, Hospital/Clinic")
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(trial.extra_fields["sponsor_type"], "Hospital/Clinic")

	def test_sponsor_type_distinct_values_are_kept_joined(self):
		record = _record(sponsorType="Hospital/Clinic, Pharmaceutical company")
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(
			trial.extra_fields["sponsor_type"], "Hospital/Clinic, Pharmaceutical company"
		)

	def test_age_group_splits_into_min_max(self):
		record = _record(ageGroup="18-64 years")
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(trial.extra_fields["inclusion_agemin"], "18")
		self.assertEqual(trial.extra_fields["inclusion_agemax"], "64")

	def test_day_first_dates_parsed_correctly(self):
		# 08/12/2025 is 8 December, not 12 August.
		record = _record(decisionDateOverall="08/12/2025")
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(
			trial.extra_fields["overall_decision_date"], datetime.date(2025, 12, 8)
		)

	def test_results_posted_none_when_field_absent(self):
		record = _record()
		del record["resultsFirstReceived"]
		trial = self.api.parse_ctis_search_record(record)
		self.assertIsNone(trial.extra_fields["results_posted"])

	def test_results_posted_false_for_explicit_no(self):
		record = _record(resultsFirstReceived="No")
		trial = self.api.parse_ctis_search_record(record)
		self.assertIs(trial.extra_fields["results_posted"], False)

	def test_unknown_status_code_leaves_recruitment_status_unset(self):
		record = _record(ctStatus=99)
		trial = self.api.parse_ctis_search_record(record)
		self.assertIsNone(trial.extra_fields["recruitment_status"])

	def test_unknown_country_status_code_omits_country(self):
		record = _record(trialCountries=["Spain:4", "France:99"])
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(trial.extra_fields["country_status"], "Spain:Ongoing, recruiting")

	def test_status_code_6_is_temporarily_halted(self):
		"""Regression test for a live trial (2024-512914-16-00) that surfaced this gap
		in production: Sweden/Lithuania/Belgium carried status code 6, which wasn't in
		the original empirically-derived table. Confirmed 2026-07-19 against the
		portal's own frontend status enum (see ctis_codes.py docstring) rather than
		guessed."""
		record = _record(trialCountries=["Sweden:6", "Lithuania:6", "Belgium:6", "Germany:5"])
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(
			trial.extra_fields["country_status"],
			"Sweden:Temporarily halted, Lithuania:Temporarily halted, "
			"Belgium:Temporarily halted, Germany:Ongoing, recruitment ended",
		)

	def test_all_confirmed_status_codes_map_to_expected_labels(self):
		expected = {
			1: "Under evaluation",
			2: "Authorised, recruitment pending",
			3: "Authorised, recruiting",
			4: "Ongoing, recruiting",
			5: "Ongoing, recruitment ended",
			6: "Temporarily halted",
			7: "Suspended",
			8: "Ended",
			9: "Expired",
			10: "Revoked",
			11: "Not authorised",
			12: "Cancelled",
		}
		for code, label in expected.items():
			with self.subTest(code=code):
				record = _record(ctStatus=code)
				trial = self.api.parse_ctis_search_record(record)
				self.assertEqual(trial.extra_fields["recruitment_status"], label)

	def test_region_code_eea_only(self):
		record = _record(trialRegion=1)
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(trial.extra_fields["trial_region"], "EEA only")

	def test_region_code_non_eea_only(self):
		record = _record(trialRegion=2)
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(trial.extra_fields["trial_region"], "Non-EEA only")

	def test_unmapped_region_code_leaves_trial_region_unset(self):
		record = _record(trialRegion=99)
		trial = self.api.parse_ctis_search_record(record)
		self.assertIsNone(trial.extra_fields["trial_region"])

	def test_identifiers_mirror_euttrialparser_key_and_bare_value(self):
		record = _record(ct_number="2025-523726-40-00")
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(
			trial.identifiers, {"eudract": None, "nct": None, "euct": "2025-523726-40-00"}
		)

	def test_link_matches_rss_url_format(self):
		record = _record(ct_number="2025-523726-40-00")
		trial = self.api.parse_ctis_search_record(record)
		self.assertEqual(
			trial.link,
			"https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&EUCT=2025-523726-40-00",
		)

	def test_summary_composed_only_when_fields_present(self):
		record = _record()
		trial = self.api.parse_ctis_search_record(record)
		self.assertIn("<b>Sponsor</b>: Test Sponsor<br/>", trial.summary)


class RssParityTests(SimpleTestCase):
	"""Real trial 2025-523726-40-00, captured live from both the RSS feed and the
	/search API on 2026-07-18 (see docs/ctis-public-api-schema.md). Both parsers
	must produce identical extra_fields for the same trial."""

	RSS_SUMMARY_HTML = (
		"<b>Trial number</b>: 2025-523726-40-00<br />"
		"<b>Overall trial status</b>: Ongoing, recruiting<br />"
		"<b>Trial title</b>: A PHASE III, OPEN-LABEL, SINGLE-ARM,  MULTI-CENTER STUDY TO "
		"EVALUATE THE  SAFETY AND USE OF AN ON-BODY DELIVERY SYSTEM FOR THE SUBCUTANEOUS "
		"HOME  ADMINISTRATION OF OCRELIZUMAB IN  PATIENTS WITH MULTIPLE SCLEROSIS<br />"
		"<b>Medical conditions</b>: Multiple Sclerosis<br />"
		"<b>Status in each country</b>: Spain:Ongoing, recruiting, France:Authorised, "
		"recruitment pending, Poland:Authorised, recruiting, Italy:Ongoing, recruiting<br />"
		"<b>Trial phase</b>: Therapeutic confirmatory  (Phase III)<br />"
		"<b>Therapeutic Areas</b>: Diseases [C] - Immune System Diseases [C20], Diseases "
		"[C] - Nervous System Diseases [C10]<br />"
		"<b>Primary end point</b>: Occurrence of successful self/lay caregiver "
		"administration of the Week 1 dose<br />"
		"<b>Secondary end point</b>: Occurrence of successful self/lay caregiver "
		"administration at home of the Week 24 and Week 48 doses, Occurrence and nature "
		"of injection reactions (severity, treatment, need for intervention by a "
		"healthcare professional, outcome), Occurrence and severity of adverse device "
		"effect (ADEs) and anticipated serious adverse device effect (ASADEs), "
		"Occurrence of unanticipated serious adverse device effect (USADEs), Occurrence "
		"of device deficiency (DDs) that could lead to serious adverse device effect "
		"serious adverse device effect (SADEs)<br />"
		"<b>Age of participants</b>: 18-64 years<br />"
		"<b>Gender of participants</b>: Female, Male<br />"
		"<b>Trial region</b>: In both EEA and non-EEA<br />"
		"<b>Planned number of participants</b>: 54<br />"
		"<b>Sponsor</b>: F. Hoffmann-La Roche AG<br />"
		"<b>Sponsor type</b>: Pharmaceutical company<br />"
		"<b>Trial product</b>: Ocrevus, Ocrelizumab<br />"
		"<b>Results posted</b>: No<br />"
		"<b>Overall decision date</b>: 24/06/2026<br />"
		"<b>Countries decision date</b>: IT: 24/06/2026, ES: 26/06/2026, PL: 29/06/2026, "
		"FR: 26/06/2026<br />"
		"<b>Last updated date</b>: 09/07/2026"
	)

	API_RECORD = {
		"ctNumber": "2025-523726-40-00",
		"ctStatus": 4,
		"ctTitle": (
			"A PHASE III, OPEN-LABEL, SINGLE-ARM,  MULTI-CENTER STUDY TO EVALUATE THE  "
			"SAFETY AND USE OF AN ON-BODY DELIVERY SYSTEM FOR THE SUBCUTANEOUS HOME  "
			"ADMINISTRATION OF OCRELIZUMAB IN  PATIENTS WITH MULTIPLE SCLEROSIS"
		),
		"shortTitle": "CN46182",
		"startDateEU": "24/06/2026",
		"conditions": "Multiple Sclerosis",
		"trialCountries": ["Spain:4", "France:2", "Poland:3", "Italy:4"],
		"decisionDateOverall": "24/06/2026",
		"decisionDate": "IT: 24/06/2026, ES: 26/06/2026, PL: 29/06/2026, FR: 26/06/2026",
		"therapeuticAreas": [
			"Diseases [C] - Immune System Diseases [C20]",
			"Diseases [C] - Nervous System Diseases [C10]",
		],
		"sponsor": "F. Hoffmann-La Roche AG",
		"sponsorType": "Pharmaceutical company",
		"trialPhase": "Therapeutic confirmatory  (Phase III)",
		"endPoint": (
			"Occurrence of successful self/lay caregiver administration at home of the "
			"Week 24 and Week 48 doses, Occurrence and nature of injection reactions "
			"(severity, treatment, need for intervention by a healthcare professional, "
			"outcome), Occurrence and severity of adverse device effect (ADEs) and "
			"anticipated serious adverse device effect (ASADEs), Occurrence of "
			"unanticipated serious adverse device effect (USADEs), Occurrence of device "
			"deficiency (DDs) that could lead to serious adverse device effect serious "
			"adverse device effect (SADEs)"
		),
		"product": "Ocrevus, Ocrelizumab",
		"ageRangeSecondary": [""],
		"ageGroup": "18-64 years",
		"gender": "Female, Male",
		"trialRegion": 3,
		"totalNumberEnrolled": "54",
		"primaryEndPoint": "Occurrence of successful self/lay caregiver administration of the Week 1 dose",
		"resultsFirstReceived": "No",
		"lastUpdated": "09/07/2026",
		"lastPublicationUpdate": "15/07/2026",
	}

	def test_extra_fields_match_between_rss_and_api_channels(self):
		rss_fields = EUTrialParser().parse_summary(self.RSS_SUMMARY_HTML)
		api_fields = CTISPublicAPI().parse_ctis_search_record(self.API_RECORD).extra_fields

		self.assertEqual(set(rss_fields.keys()), set(api_fields.keys()))
		# last_refreshed_on is the one intentional divergence: the RSS feed only
		# exposes a single "Last updated date" line (-> lastUpdated), while the API
		# additionally exposes lastPublicationUpdate. Per the mapping table
		# (CTIS-API-FEEDREADER-PLAN.md section 3) the API mapper takes
		# max(lastUpdated, lastPublicationUpdate) — a strictly fresher recency
		# signal than the RSS channel can produce, not a parity bug.
		for key in rss_fields:
			if key == "last_refreshed_on":
				continue
			self.assertEqual(
				rss_fields[key], api_fields[key], f"mismatch on field '{key}'"
			)
		self.assertEqual(api_fields["last_refreshed_on"], datetime.date(2026, 7, 15))
		self.assertEqual(rss_fields["last_refreshed_on"], datetime.date(2026, 7, 9))

	def test_identifiers_match_rss_extract_identifiers(self):
		rss_link = (
			"https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en"
			"&EUCT=2025-523726-40-00"
		)
		rss_identifiers = EUTrialParser().extract_identifiers(rss_link, guid=None)
		api_identifiers = CTISPublicAPI().parse_ctis_search_record(self.API_RECORD).identifiers
		self.assertEqual(rss_identifiers, api_identifiers)
