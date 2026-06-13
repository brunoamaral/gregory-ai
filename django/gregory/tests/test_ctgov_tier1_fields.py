"""
Tests for Tier-1 field gap fixes in the ClinicalTrials.gov parser.

Covers parse_study_to_clinical_trial producing:
  - study_design (interventional, observational, absent designInfo → None)
  - results_ipd_plan (truncated to ≤10 chars), results_ipd_description, absent module → None
  - secondary_sponsor joined from collaborators; none → None
  - last_refreshed_on and date_enrollement as date objects (including partial date)
  - contact_affiliation from first overall official; empty/absent → None
  - countries string is deterministically sorted

Run:
  docker exec gregory python manage.py test gregory.tests.test_ctgov_tier1_fields
"""

import datetime
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import SimpleTestCase

from gregory.classes import ClinicalTrialsGovAPI


def _base_study(**overrides):
	"""Minimal study dict; caller can override any top-level protocolSection key."""
	study = {
		"protocolSection": {
			"identificationModule": {
				"nctId": "NCT99999999",
				"officialTitle": "Tier-1 Test Study",
			},
			"statusModule": {
				"overallStatus": "RECRUITING",
			},
			"designModule": {},
			"sponsorCollaboratorsModule": {"leadSponsor": {"name": "Lead Corp"}},
			"conditionsModule": {},
			"eligibilityModule": {},
			"outcomesModule": {},
			"contactsLocationsModule": {},
			"descriptionModule": {},
			"armsInterventionsModule": {},
		}
	}
	study["protocolSection"].update(overrides)
	return study


class StudyDesignTest(SimpleTestCase):
	def setUp(self):
		self.api = ClinicalTrialsGovAPI()

	def _parse(self, design_module):
		study = _base_study(designModule=design_module)
		trial = self.api.parse_study_to_clinical_trial(study)
		return trial.extra_fields.get("study_design")

	def test_interventional_full(self):
		result = self._parse(
			{
				"studyType": "INTERVENTIONAL",
				"designInfo": {
					"allocation": "RANDOMIZED",
					"interventionModel": "PARALLEL",
					"maskingInfo": {
						"masking": "QUADRUPLE",
						"whoMasked": [
							"PARTICIPANT",
							"INVESTIGATOR",
							"CARE_PROVIDER",
							"OUTCOMES_ASSESSOR",
						],
					},
					"primaryPurpose": "TREATMENT",
				},
			}
		)
		self.assertIn("Allocation: RANDOMIZED", result)
		self.assertIn("Intervention model: PARALLEL", result)
		self.assertIn("Masking: QUADRUPLE", result)
		self.assertIn("Primary purpose: TREATMENT", result)
		# whoMasked must be sorted for determinism
		who_section = result[result.index("Masking:") :]
		paren_content = who_section[who_section.index("(") + 1 : who_section.index(")")]
		names = [n.strip() for n in paren_content.split(",")]
		self.assertEqual(names, sorted(names))

	def test_observational(self):
		result = self._parse(
			{
				"studyType": "OBSERVATIONAL",
				"designInfo": {
					"observationalModel": "COHORT",
					"timePerspective": "PROSPECTIVE",
				},
			}
		)
		self.assertIn("Observational model: COHORT", result)
		self.assertIn("Time perspective: PROSPECTIVE", result)

	def test_absent_design_info_returns_none(self):
		result = self._parse({"studyType": "INTERVENTIONAL"})
		self.assertIsNone(result)

	def test_empty_design_info_returns_none(self):
		result = self._parse({"studyType": "INTERVENTIONAL", "designInfo": {}})
		self.assertIsNone(result)

	def test_deterministic_repeated_call(self):
		dm = {
			"studyType": "INTERVENTIONAL",
			"designInfo": {
				"allocation": "RANDOMIZED",
				"maskingInfo": {
					"masking": "DOUBLE",
					"whoMasked": ["PARTICIPANT", "CARE_PROVIDER"],
				},
				"primaryPurpose": "PREVENTION",
			},
		}
		first = self._parse(dm)
		second = self._parse(dm)
		self.assertEqual(first, second)


class IpdFieldsTest(SimpleTestCase):
	def setUp(self):
		self.api = ClinicalTrialsGovAPI()

	def _parse(self, ipd_module=None):
		overrides = {}
		if ipd_module is not None:
			overrides["ipdSharingStatementModule"] = ipd_module
		study = _base_study(**overrides)
		trial = self.api.parse_study_to_clinical_trial(study)
		return trial.extra_fields

	def test_yes_plan_and_description(self):
		ef = self._parse(
			{"ipdSharing": "YES", "description": "Available after 2 years"}
		)
		self.assertEqual(ef["results_ipd_plan"], "YES")
		self.assertEqual(ef["results_ipd_description"], "Available after 2 years")

	def test_plan_truncated_to_10_chars(self):
		ef = self._parse({"ipdSharing": "UNDECIDEDX_TOOLONG"})
		self.assertLessEqual(len(ef["results_ipd_plan"]), 10)

	def test_absent_module_both_none(self):
		ef = self._parse()
		self.assertIsNone(ef["results_ipd_plan"])
		self.assertIsNone(ef["results_ipd_description"])

	def test_empty_ipd_sharing_is_none(self):
		ef = self._parse({"ipdSharing": ""})
		self.assertIsNone(ef["results_ipd_plan"])


class SecondarySponsorTest(SimpleTestCase):
	def setUp(self):
		self.api = ClinicalTrialsGovAPI()

	def _parse(self, collaborators):
		study = _base_study(
			sponsorCollaboratorsModule={
				"leadSponsor": {"name": "Lead Corp"},
				"collaborators": [{"name": c} for c in collaborators],
			}
		)
		return self.api.parse_study_to_clinical_trial(study).extra_fields.get(
			"secondary_sponsor"
		)

	def test_multiple_collaborators_joined(self):
		result = self._parse(["Org A", "Org B", "Org C"])
		self.assertEqual(result, "Org A; Org B; Org C")

	def test_no_collaborators_is_none(self):
		self.assertIsNone(self._parse([]))

	def test_api_order_preserved(self):
		result = self._parse(["Z Corp", "A Corp"])
		self.assertEqual(result, "Z Corp; A Corp")


class LastRefreshedAndEnrollementTest(SimpleTestCase):
	def setUp(self):
		self.api = ClinicalTrialsGovAPI()

	def _parse(self, last_update_date=None, start_date=None):
		status = {"overallStatus": "RECRUITING"}
		if last_update_date:
			status["lastUpdatePostDateStruct"] = {"date": last_update_date}
		if start_date:
			status["startDateStruct"] = {"date": start_date}
		study = _base_study(statusModule=status)
		return self.api.parse_study_to_clinical_trial(study).extra_fields

	def test_full_date(self):
		ef = self._parse(last_update_date="2024-03-15", start_date="2023-06-01")
		self.assertEqual(ef["last_refreshed_on"], datetime.date(2024, 3, 15))
		self.assertEqual(ef["date_enrollement"], datetime.date(2023, 6, 1))

	def test_partial_year_month(self):
		ef = self._parse(start_date="2022-09")
		self.assertEqual(ef["date_enrollement"], datetime.date(2022, 9, 1))

	def test_partial_year_only(self):
		ef = self._parse(start_date="2021")
		self.assertEqual(ef["date_enrollement"], datetime.date(2021, 1, 1))

	def test_absent_dates_are_none(self):
		ef = self._parse()
		self.assertIsNone(ef["last_refreshed_on"])
		self.assertIsNone(ef["date_enrollement"])


class ContactAffiliationTest(SimpleTestCase):
	def setUp(self):
		self.api = ClinicalTrialsGovAPI()

	def _parse(self, officials):
		study = _base_study(contactsLocationsModule={"overallOfficials": officials})
		return self.api.parse_study_to_clinical_trial(study).extra_fields.get(
			"contact_affiliation"
		)

	def test_first_official_affiliation(self):
		result = self._parse(
			[
				{"affiliation": "University Hospital", "name": "Dr. Smith"},
				{"affiliation": "Other Institute", "name": "Dr. Jones"},
			]
		)
		self.assertEqual(result, "University Hospital")

	def test_empty_officials_list_is_none(self):
		self.assertIsNone(self._parse([]))

	def test_absent_officials_key_is_none(self):
		study = _base_study(contactsLocationsModule={})
		result = self.api.parse_study_to_clinical_trial(study).extra_fields.get(
			"contact_affiliation"
		)
		self.assertIsNone(result)


class CountriesDeterminismTest(SimpleTestCase):
	def setUp(self):
		self.api = ClinicalTrialsGovAPI()

	def test_countries_sorted(self):
		study = _base_study(
			contactsLocationsModule={
				"locations": [
					{"country": "Zimbabwe"},
					{"country": "Argentina"},
					{"country": "France"},
				]
			}
		)
		ef = self.api.parse_study_to_clinical_trial(study).extra_fields
		countries = ef["countries"]
		parts = [c.strip() for c in countries.split(",")]
		self.assertEqual(parts, sorted(parts))
