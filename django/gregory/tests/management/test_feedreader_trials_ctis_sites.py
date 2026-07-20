"""
Tests for feedreader_trials_ctis's TrialSite capture (CTIS-API-PHASE-2-PLAN.md PR 2b):
authorizedApplication.authorizedPartsII[].trialSites[] -> TrialSite, wholesale replaced
on every enrichment run.

Covers:
  - Wholesale replace: re-enriching with a changed fixture drops stale rows and adds
    the new set; count matches the fixture
  - Optional-leaf tolerance: a site missing address/person fields is still stored;
    a site missing the organisation name is dropped
  - Country mapping: countryName -> alpha-2 code; an unmappable name keeps the row
    with a null country
  - personInfo/organisationAddressInfo phone/email are never persisted anywhere on
    the model
  - Malformed/missing authorizedPartsII skips the replace entirely (never wipes a
    previously-captured site set on a degraded payload)
  - Query bound: one DELETE + one bulk_create per enrichment

Run:
  docker exec gregory python manage.py test gregory.tests.management.test_feedreader_trials_ctis_sites
"""

import os
from unittest.mock import MagicMock

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase

from gregory.management.commands.feedreader_trials_ctis import (
	Command,
	_extract_trial_sites,
)
from gregory.models import Trials, TrialSite


def _make_command():
	cmd = Command()
	cmd.verbosity = 0
	cmd.api = MagicMock()
	return cmd


def _make_trial(**overrides):
	defaults = dict(title="CTIS Site Test Trial", identifiers={"euct": "2025-523726-40-00"})
	defaults.update(overrides)
	return Trials.objects.create(**defaults)


def _site_entry(
	name="Test Hospital",
	site_type="Hospital/Clinic/Other health care facility",
	one_line="Viale Oxford 81",
	city="Rome",
	postcode="00133",
	country_name="Italy",
	first_name="Girolama Alessandra",
	last_name="Marfia",
	phone="0039000000",
	email="doc@example.com",
):
	organisation = {}
	if name is not None:
		organisation["name"] = name
	if site_type is not None:
		organisation["type"] = site_type

	address = {}
	if one_line is not None:
		address["oneLine"] = one_line
	if city is not None:
		address["city"] = city
	if postcode is not None:
		address["postcode"] = postcode
	if country_name is not None:
		address["countryName"] = country_name

	org_info = {"organisation": organisation, "address": address}
	if phone is not None:
		org_info["phone"] = phone
	if email is not None:
		org_info["email"] = email

	person_info = {}
	if first_name is not None:
		person_info["firstName"] = first_name
	if last_name is not None:
		person_info["lastName"] = last_name
	if phone is not None:
		person_info["telephone"] = phone
	if email is not None:
		person_info["email"] = email

	return {"organisationAddressInfo": org_info, "personInfo": person_info}


def _payload(sites=None):
	if sites is None:
		sites = [_site_entry()]
	return {
		"authorizedApplication": {
			"authorizedPartsII": [{"trialSites": sites}],
		}
	}


class ExtractTrialSitesTests(TestCase):
	def test_extracts_full_shape(self):
		result = _extract_trial_sites(_payload())
		self.assertEqual(len(result), 1)
		site = result[0]
		self.assertEqual(site["name"], "Test Hospital")
		self.assertEqual(site["site_type"], "Hospital/Clinic/Other health care facility")
		self.assertEqual(site["address"], "Viale Oxford 81")
		self.assertEqual(site["city"], "Rome")
		self.assertEqual(site["postcode"], "00133")
		self.assertEqual(site["country"], "IT")
		self.assertEqual(site["investigator_name"], "Girolama Alessandra Marfia")
		self.assertEqual(site["sources"], ["ctis"])
		self.assertNotIn("phone", site)
		self.assertNotIn("email", site)
		self.assertNotIn("telephone", site)

	def test_site_missing_organisation_name_is_dropped(self):
		result = _extract_trial_sites(_payload(sites=[_site_entry(name=None)]))
		self.assertEqual(result, [])

	def test_site_missing_optional_leaves_is_still_stored(self):
		result = _extract_trial_sites(
			_payload(
				sites=[
					_site_entry(
						site_type=None,
						one_line=None,
						city=None,
						postcode=None,
						country_name=None,
						first_name=None,
						last_name=None,
						phone=None,
						email=None,
					)
				]
			)
		)
		self.assertEqual(len(result), 1)
		site = result[0]
		self.assertEqual(site["name"], "Test Hospital")
		self.assertIsNone(site["site_type"])
		self.assertIsNone(site["address"])
		self.assertIsNone(site["city"])
		self.assertIsNone(site["postcode"])
		self.assertIsNone(site["country"])
		self.assertIsNone(site["investigator_name"])

	def test_unmappable_country_name_keeps_row_with_null_country(self):
		result = _extract_trial_sites(_payload(sites=[_site_entry(country_name="Nonexistria")]))
		self.assertEqual(len(result), 1)
		self.assertIsNone(result[0]["country"])

	def test_missing_authorized_parts_ii_returns_none(self):
		self.assertIsNone(_extract_trial_sites({}))
		self.assertIsNone(_extract_trial_sites({"authorizedApplication": {}}))

	def test_non_list_trial_sites_within_a_part_is_skipped(self):
		payload = {
			"authorizedApplication": {"authorizedPartsII": [{"trialSites": "oops"}]}
		}
		self.assertEqual(_extract_trial_sites(payload), [])

	def test_multiple_parts_ii_are_all_collected(self):
		payload = {
			"authorizedApplication": {
				"authorizedPartsII": [
					{"trialSites": [_site_entry(name="Site A")]},
					{"trialSites": [_site_entry(name="Site B")]},
				]
			}
		}
		result = _extract_trial_sites(payload)
		self.assertEqual(sorted(s["name"] for s in result), ["Site A", "Site B"])


class TrialSiteEnrichmentHookTests(TestCase):
	def setUp(self):
		self.cmd = _make_command()

	def test_wholesale_replace_on_changed_fixture(self):
		trial = _make_trial()
		self.cmd._enrich_trial_sites(trial, _payload(sites=[_site_entry(name="Old Site")]))
		self.assertEqual(list(trial.trial_sites.values_list("name", flat=True)), ["Old Site"])

		self.cmd._enrich_trial_sites(
			trial,
			_payload(
				sites=[_site_entry(name="New Site A"), _site_entry(name="New Site B")]
			),
		)
		names = sorted(trial.trial_sites.values_list("name", flat=True))
		self.assertEqual(names, ["New Site A", "New Site B"])

	def test_count_matches_fixture(self):
		trial = _make_trial()
		sites = [_site_entry(name=f"Site {i}") for i in range(4)]
		self.cmd._enrich_trial_sites(trial, _payload(sites=sites))
		self.assertEqual(trial.trial_sites.count(), 4)

	def test_no_replace_when_authorized_parts_ii_missing(self):
		"""A degraded/malformed payload must never wipe a previously-captured set."""
		trial = _make_trial()
		self.cmd._enrich_trial_sites(trial, _payload())
		self.assertEqual(trial.trial_sites.count(), 1)

		self.cmd._enrich_trial_sites(trial, {"ctNumber": "2025-523726-40-00"})
		self.assertEqual(trial.trial_sites.count(), 1)

	def test_empty_but_present_parts_ii_replaces_with_zero_rows(self):
		trial = _make_trial()
		self.cmd._enrich_trial_sites(trial, _payload())
		self.assertEqual(trial.trial_sites.count(), 1)

		self.cmd._enrich_trial_sites(
			trial, {"authorizedApplication": {"authorizedPartsII": []}}
		)
		self.assertEqual(trial.trial_sites.count(), 0)

	def test_no_phone_or_email_persisted_anywhere_on_the_model(self):
		trial = _make_trial()
		self.cmd._enrich_trial_sites(trial, _payload())
		site = trial.trial_sites.get()
		field_names = {f.name for f in TrialSite._meta.get_fields()}
		self.assertNotIn("phone", field_names)
		self.assertNotIn("email", field_names)
		self.assertNotIn("telephone", field_names)
		# Investigator name is the only personInfo-derived value stored.
		self.assertEqual(site.investigator_name, "Girolama Alessandra Marfia")

	def test_query_bound_one_delete_one_bulk_create(self):
		"""Exactly one DELETE and one bulk_create INSERT per enrichment — the two
		extra queries are the atomic() savepoint bracket, not per-row overhead."""
		trial = _make_trial()
		self.cmd._enrich_trial_sites(trial, _payload(sites=[_site_entry(), _site_entry(name="Second")]))
		with self.assertNumQueries(4):
			self.cmd._enrich_trial_sites(
				trial, _payload(sites=[_site_entry(name="Replacement")])
			)
		self.assertEqual(trial.trial_sites.count(), 1)

	def test_enrich_from_retrieve_calls_site_enrichment_and_isolates_failures(self):
		"""_enrich_from_retrieve (the combined hook) must also capture sites, and a
		site-parsing failure must not block or be blocked by the fields enrichment."""
		trial = _make_trial()
		self.cmd._enrich_from_retrieve(trial, _payload())
		self.assertEqual(trial.trial_sites.count(), 1)

	def test_sites_are_still_captured_when_fields_enrichment_raises(self):
		"""A regression guard: a failure in the countries/dates/eligibility step
		must not prevent site capture from running afterwards."""
		from unittest.mock import patch

		from gregory.management.commands.feedreader_trials_ctis import Command

		trial = _make_trial()
		with patch.object(
			Command, "_enrich_countries_by_source", side_effect=Exception("boom")
		):
			self.cmd._enrich_from_retrieve(trial, _payload())

		self.assertEqual(trial.trial_sites.count(), 1)

	def test_fields_enrichment_still_saves_when_site_enrichment_raises(self):
		"""The inverse regression guard: a site-parsing failure must not block the
		countries/dates/eligibility save."""
		from unittest.mock import patch

		trial = _make_trial()
		with patch.object(
			Command, "_enrich_trial_sites", side_effect=Exception("boom")
		):
			self.cmd._enrich_from_retrieve(
				trial,
				{
					"authorizedApplication": {
						"authorizedPartI": {
							"rowCountriesInfo": [{"name": "Brazil", "isoAlpha2Code": "BR"}]
						}
					}
				},
			)

		trial.refresh_from_db()
		self.assertEqual(trial.countries_by_source, {"ctis": "Brazil"})
