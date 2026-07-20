"""Tests for ClinicalTrialsGovAPI.extract_sites (TRIAL-GEOGRAPHY-PLAN.md PR G2),
which reads per-site rows (facility/city/state/zip/country/geoPoint) out of a
CTGov study's contactsLocationsModule.locations[] — the same subtree
extract_countries already reads, but preserving per-site detail instead of
collapsing to one country string.

Run:
  docker exec gregory python manage.py test gregory.tests.test_ctgov_extract_sites
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase

from gregory.classes import ClinicalTrialsGovAPI


def _study(locations):
	return {
		"protocolSection": {"contactsLocationsModule": {"locations": locations}}
	}


class ExtractSitesTests(TestCase):
	def test_full_location_extracts_all_fields_including_coordinates(self):
		study = _study(
			[
				{
					"facility": "Mayo Clinic",
					"city": "Phoenix",
					"state": "Arizona",
					"zip": "85054",
					"country": "United States",
					"geoPoint": {"lat": 33.44838, "lon": -112.07404},
				}
			]
		)
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		self.assertEqual(len(sites), 1)
		site = sites[0]
		self.assertEqual(site["name"], "Mayo Clinic")
		self.assertEqual(site["city"], "Phoenix")
		self.assertEqual(site["state"], "Arizona")
		self.assertEqual(site["postcode"], "85054")
		self.assertEqual(site["country"], "US")
		self.assertEqual(site["latitude"], 33.44838)
		self.assertEqual(site["longitude"], -112.07404)

	def test_missing_geo_point_yields_null_coordinates_but_keeps_row(self):
		study = _study([{"facility": "Research Site", "city": "Lisbon"}])
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		self.assertEqual(len(sites), 1)
		self.assertIsNone(sites[0]["latitude"])
		self.assertIsNone(sites[0]["longitude"])

	def test_non_numeric_geo_point_yields_null_coordinates(self):
		study = _study(
			[
				{
					"facility": "Research Site",
					"city": "Lisbon",
					"geoPoint": {"lat": "not-a-number", "lon": None},
				}
			]
		)
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		self.assertIsNone(sites[0]["latitude"])
		self.assertIsNone(sites[0]["longitude"])

	def test_missing_state_and_zip_are_null(self):
		study = _study([{"facility": "Research Site", "city": "Lisbon"}])
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		self.assertIsNone(sites[0]["state"])
		self.assertIsNone(sites[0]["postcode"])

	def test_non_dict_location_entries_are_skipped(self):
		study = _study(["not-a-dict", None, {"facility": "Valid Site", "city": "Porto"}])
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		self.assertEqual(len(sites), 1)
		self.assertEqual(sites[0]["name"], "Valid Site")

	def test_location_with_neither_facility_nor_city_is_skipped(self):
		study = _study([{"state": "Arizona", "country": "United States"}])
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		self.assertEqual(sites, [])

	def test_location_with_only_city_is_kept(self):
		study = _study([{"city": "Lisbon", "country": "Portugal"}])
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		self.assertEqual(len(sites), 1)
		self.assertIsNone(sites[0]["name"])
		self.assertEqual(sites[0]["city"], "Lisbon")

	def test_empty_locations_returns_empty_list(self):
		self.assertEqual(ClinicalTrialsGovAPI.extract_sites(_study([])), [])

	def test_missing_locations_key_returns_empty_list(self):
		self.assertEqual(
			ClinicalTrialsGovAPI.extract_sites({"protocolSection": {}}), []
		)

	def test_missing_contacts_locations_module_returns_empty_list(self):
		self.assertEqual(ClinicalTrialsGovAPI.extract_sites({}), [])

	def test_over_length_values_are_truncated_to_column_limits(self):
		study = _study(
			[
				{
					"facility": "F" * 600,
					"city": "C" * 300,
					"state": "S" * 300,
					"zip": "Z" * 100,
				}
			]
		)
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		site = sites[0]
		self.assertEqual(len(site["name"]), 500)
		self.assertEqual(len(site["city"]), 200)
		self.assertEqual(len(site["state"]), 200)
		self.assertEqual(len(site["postcode"]), 50)

	def test_unmappable_country_name_keeps_row_with_null_country(self):
		study = _study(
			[{"facility": "Research Site", "city": "Nowhere", "country": "Wakanda"}]
		)
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		self.assertEqual(len(sites), 1)
		self.assertIsNone(sites[0]["country"])

	def test_non_string_country_does_not_raise_and_yields_null(self):
		"""Regression guard (Copilot review on PR #790): _map_token expects a str
		and raises TypeError on other JSON types — a malformed/unexpected CTGov
		payload with a non-string `country` (e.g. a list or number) must not crash
		extraction, just leave country=None."""
		for bad_country in (123, ["Portugal"], {"name": "Portugal"}, True):
			with self.subTest(bad_country=bad_country):
				study = _study(
					[{"facility": "Research Site", "city": "Lisbon", "country": bad_country}]
				)
				sites = ClinicalTrialsGovAPI.extract_sites(study)
				self.assertEqual(len(sites), 1)
				self.assertIsNone(sites[0]["country"])

	def test_multiple_locations_all_collected(self):
		study = _study(
			[
				{"facility": "Site A", "city": "Lisbon", "country": "Portugal"},
				{"facility": "Site B", "city": "Porto", "country": "Portugal"},
			]
		)
		sites = ClinicalTrialsGovAPI.extract_sites(study)
		self.assertEqual(len(sites), 2)
		self.assertEqual({s["name"] for s in sites}, {"Site A", "Site B"})
