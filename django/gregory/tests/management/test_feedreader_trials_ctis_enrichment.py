"""
Tests for feedreader_trials_ctis's /retrieve enrichment hook (CTIS-API-PHASE-2-PLAN.md
PR 2a): all-countries via countries_by_source["ctis"], per-country recruitment start
dates, and fill-if-empty eligibility criteria.

Fixtures below are trimmed-but-faithful subtrees of a real retrieve/{ctNumber} payload
(2025-523726-40-00, fetched live 2026-07-19 to verify shapes against
docs/ctis-public-api-schema.md) — not the full ~85 KB dossier.

Covers:
  - Self-verifying country round-trip: TrialCountry codes end up matching the
    fixture's rowCountriesInfo isoAlpha2Code set, sources include "ctis"
  - Merge semantics: enrichment unions with (never drops) countries known only from
    country_status; re-running enrichment is idempotent
  - Recruitment dates: earliest trialRecruitmentPeriod wins, unmappable countryName
    is skipped+logged, TrialCountry.recruitment_start_date populates and updates
  - Eligibility: fills empty inclusion/exclusion_criteria only, joined "N. text" lines
  - Hook: exactly one trial.save() per enrichment; a raising sub-step is caught and
    doesn't call save(); --enrich-all selects trials by euct/ctis identifier keys

Run:
  docker exec gregory python manage.py test gregory.tests.management.test_feedreader_trials_ctis_enrichment
"""

import os
from unittest.mock import MagicMock, patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from organizations.models import Organization

from gregory.management.commands.feedreader_trials_ctis import (
	Command,
	_extract_eligibility_criteria,
	_extract_recruitment_dates,
	_extract_row_countries,
)
from gregory.models import Team, Trials


def _make_command(api=None):
	cmd = Command()
	cmd.verbosity = 0
	cmd.api = api if api is not None else MagicMock()
	return cmd


def _make_trial(**overrides):
	defaults = dict(
		title="CTIS Enrichment Test Trial",
		identifiers={"euct": "2025-523726-40-00"},
	)
	defaults.update(overrides)
	return Trials.objects.create(**defaults)


def _retrieve_payload(
	row_countries=None,
	inclusion_entries=None,
	exclusion_entries=None,
	parts_ii=None,
):
	if row_countries is None:
		row_countries = [
			{"name": "Brazil", "isoAlpha2Code": "BR", "isoAlpha3Code": "BRA"},
			{"name": "Ukraine", "isoAlpha2Code": "UA", "isoAlpha3Code": "UKR"},
		]
	if inclusion_entries is None:
		inclusion_entries = [
			{"number": 1, "principalInclusionCriteria": "Age 18-65 years"},
			{"number": 2, "principalInclusionCriteria": "Confirmed diagnosis"},
		]
	if exclusion_entries is None:
		exclusion_entries = [
			{"number": 1, "principalExclusionCriteria": "Prior treatment with drug X"},
		]
	if parts_ii is None:
		parts_ii = [
			{
				"mscInfo": {
					"countryName": "Italy",
					"trialRecruitmentPeriod": [
						{"recruitmentStartDate": "2026-06-26"},
						{"recruitmentStartDate": "2026-08-01"},
					],
				}
			}
		]
	return {
		"ctNumber": "2025-523726-40-00",
		"authorizedApplication": {
			"authorizedPartI": {
				"rowCountriesInfo": row_countries,
				"trialDetails": {
					"trialInformation": {
						"eligibilityCriteria": {
							"principalInclusionCriteria": inclusion_entries,
							"principalExclusionCriteria": exclusion_entries,
						}
					}
				},
			},
			"authorizedPartsII": parts_ii,
		},
	}


class ExtractRowCountriesTests(TestCase):
	def test_extracts_name_and_code_pairs(self):
		payload = _retrieve_payload()
		result = _extract_row_countries(payload)
		self.assertEqual(
			[(c["name"], c["isoAlpha2Code"]) for c in result],
			[("Brazil", "BR"), ("Ukraine", "UA")],
		)

	def test_missing_subtree_returns_empty_list(self):
		self.assertEqual(_extract_row_countries({}), [])
		self.assertEqual(_extract_row_countries({"authorizedApplication": None}), [])

	def test_non_list_subtree_returns_empty_list(self):
		payload = _retrieve_payload()
		payload["authorizedApplication"]["authorizedPartI"]["rowCountriesInfo"] = "oops"
		self.assertEqual(_extract_row_countries(payload), [])

	def test_entries_missing_name_are_dropped(self):
		payload = _retrieve_payload(row_countries=[{"isoAlpha2Code": "BR"}, {"name": "Ukraine", "isoAlpha2Code": "UA"}])
		result = _extract_row_countries(payload)
		self.assertEqual([c["name"] for c in result], ["Ukraine"])


class ExtractEligibilityCriteriaTests(TestCase):
	def test_joins_numbered_lines_sorted(self):
		payload = _retrieve_payload(
			inclusion_entries=[
				{"number": 2, "principalInclusionCriteria": "Second"},
				{"number": 1, "principalInclusionCriteria": "First"},
			]
		)
		inclusion, _ = _extract_eligibility_criteria(payload)
		self.assertEqual(inclusion, "1. First\n2. Second")

	def test_missing_subtree_returns_none_none(self):
		self.assertEqual(_extract_eligibility_criteria({}), (None, None))

	def test_empty_entry_list_returns_none(self):
		payload = _retrieve_payload(inclusion_entries=[], exclusion_entries=[])
		self.assertEqual(_extract_eligibility_criteria(payload), (None, None))

	def test_entries_missing_text_are_skipped(self):
		payload = _retrieve_payload(
			inclusion_entries=[{"number": 1}, {"number": 2, "principalInclusionCriteria": "Kept"}]
		)
		inclusion, _ = _extract_eligibility_criteria(payload)
		self.assertEqual(inclusion, "2. Kept")


class ExtractRecruitmentDatesTests(TestCase):
	def test_earliest_period_wins(self):
		payload = _retrieve_payload()
		dates = _extract_recruitment_dates(payload)
		self.assertEqual(dates, {"IT": "2026-06-26"})

	def test_unmappable_country_name_is_skipped(self):
		payload = _retrieve_payload(
			parts_ii=[
				{
					"mscInfo": {
						"countryName": "Nonexistria",
						"trialRecruitmentPeriod": [{"recruitmentStartDate": "2026-01-01"}],
					}
				}
			]
		)
		self.assertEqual(_extract_recruitment_dates(payload), {})

	def test_part_with_no_recruitment_period_is_skipped(self):
		payload = _retrieve_payload(
			parts_ii=[{"mscInfo": {"countryName": "Italy", "trialRecruitmentPeriod": []}}]
		)
		self.assertEqual(_extract_recruitment_dates(payload), {})

	def test_multiple_countries(self):
		payload = _retrieve_payload(
			parts_ii=[
				{
					"mscInfo": {
						"countryName": "Italy",
						"trialRecruitmentPeriod": [{"recruitmentStartDate": "2026-06-26"}],
					}
				},
				{
					"mscInfo": {
						"countryName": "Spain",
						"trialRecruitmentPeriod": [{"recruitmentStartDate": "2026-07-08"}],
					}
				},
			]
		)
		self.assertEqual(
			_extract_recruitment_dates(payload), {"IT": "2026-06-26", "ES": "2026-07-08"}
		)


class EnrichmentHookTests(TestCase):
	"""DB-level tests for Command._enrich_from_retrieve and its three sub-steps."""

	def setUp(self):
		self.org = Organization.objects.create(name="Enrichment Org")
		self.team = Team.objects.create(organization=self.org, name="Enrichment Team", slug="enrichment-team")
		self.cmd = _make_command()

	def test_self_verifying_country_round_trip(self):
		"""The key test: TrialCountry codes after enrichment equal the fixture's
		rowCountriesInfo isoAlpha2Code set, and each row's sources include "ctis".
		parts_ii is emptied so item 2 (recruitment dates) can't also contribute a
		country code here — that union behavior is covered separately below."""
		trial = _make_trial()
		payload = _retrieve_payload(parts_ii=[])

		self.cmd._enrich_from_retrieve(trial, payload)

		codes = sorted(tc.country.code for tc in trial.trial_countries.all())
		expected = sorted(c["isoAlpha2Code"] for c in payload["authorizedApplication"]["authorizedPartI"]["rowCountriesInfo"])
		self.assertEqual(codes, expected)
		for tc in trial.trial_countries.all():
			self.assertIn("ctis", tc.sources)

	def test_countries_by_source_written_under_ctis_key(self):
		trial = _make_trial()
		payload = _retrieve_payload()
		self.cmd._enrich_from_retrieve(trial, payload)
		trial.refresh_from_db()
		self.assertEqual(trial.countries_by_source, {"ctis": "Brazil; Ukraine"})

	def test_union_never_drops_countries_from_other_sources(self):
		"""A country already known via country_status (e.g. France, EEA-only) must
		survive enrichment even though it's absent from rowCountriesInfo."""
		trial = _make_trial(country_status="France:Authorised, recruiting")
		trial.refresh_from_db()
		self.assertIn("FR", [tc.country.code for tc in trial.trial_countries.all()])

		payload = _retrieve_payload(parts_ii=[])
		self.cmd._enrich_from_retrieve(trial, payload)

		codes = {tc.country.code for tc in trial.trial_countries.all()}
		self.assertEqual(codes, {"FR", "BR", "UA"})

	def test_reenrichment_is_idempotent(self):
		trial = _make_trial()
		payload = _retrieve_payload()
		self.cmd._enrich_from_retrieve(trial, payload)
		first_codes = sorted(tc.country.code for tc in trial.trial_countries.all())
		first_cbs = trial.countries_by_source

		self.cmd._enrich_from_retrieve(trial, payload)
		trial.refresh_from_db()
		second_codes = sorted(tc.country.code for tc in trial.trial_countries.all())

		self.assertEqual(first_codes, second_codes)
		self.assertEqual(first_cbs, trial.countries_by_source)

	def test_reenrichment_with_unchanged_payload_does_not_save_again(self):
		"""Regression guard (Copilot review on PR 2a): _enrich_countries_by_source
		and _enrich_recruitment_dates must report "no change" — not just
		"non-empty" — once a payload's countries/dates already match what's
		stored, otherwise every re-run writes a pointless save()/history row even
		when nothing changed."""
		trial = _make_trial()
		payload = _retrieve_payload()
		self.cmd._enrich_from_retrieve(trial, payload)

		save_calls = []
		trial.save = lambda *a, **k: save_calls.append(1)
		self.cmd._enrich_from_retrieve(trial, payload)

		self.assertEqual(save_calls, [])

	def test_recruitment_start_date_populated_and_serialized(self):
		trial = _make_trial()
		payload = _retrieve_payload()
		self.cmd._enrich_from_retrieve(trial, payload)

		trial.refresh_from_db()
		self.assertEqual(trial.countries_recruitment_date, {"IT": "2026-06-26"})
		it_row = trial.trial_countries.get(country="IT")
		self.assertEqual(str(it_row.recruitment_start_date), "2026-06-26")

	def test_recruitment_start_date_updates_on_change(self):
		trial = _make_trial()
		self.cmd._enrich_from_retrieve(trial, _retrieve_payload())
		trial.refresh_from_db()
		self.assertEqual(str(trial.trial_countries.get(country="IT").recruitment_start_date), "2026-06-26")

		later_payload = _retrieve_payload(
			parts_ii=[
				{
					"mscInfo": {
						"countryName": "Italy",
						"trialRecruitmentPeriod": [{"recruitmentStartDate": "2026-09-01"}],
					}
				}
			]
		)
		self.cmd._enrich_from_retrieve(trial, later_payload)
		trial.refresh_from_db()
		self.assertEqual(str(trial.trial_countries.get(country="IT").recruitment_start_date), "2026-09-01")

	def test_eligibility_fills_empty_columns(self):
		trial = _make_trial()
		self.cmd._enrich_from_retrieve(trial, _retrieve_payload())
		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_criteria, "1. Age 18-65 years\n2. Confirmed diagnosis")
		self.assertEqual(trial.exclusion_criteria, "1. Prior treatment with drug X")

	def test_eligibility_never_overwrites_existing_values(self):
		trial = _make_trial(
			inclusion_criteria="WHO ICTRP inclusion text",
			exclusion_criteria="WHO ICTRP exclusion text",
		)
		self.cmd._enrich_from_retrieve(trial, _retrieve_payload())
		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_criteria, "WHO ICTRP inclusion text")
		self.assertEqual(trial.exclusion_criteria, "WHO ICTRP exclusion text")

	def test_exactly_one_save_per_enrichment(self):
		trial = _make_trial()
		save_calls = []
		original_save = trial.save

		def counting_save(*args, **kwargs):
			save_calls.append(1)
			return original_save(*args, **kwargs)

		trial.save = counting_save
		self.cmd._enrich_from_retrieve(trial, _retrieve_payload())
		self.assertEqual(len(save_calls), 1)

	def test_no_save_when_payload_yields_nothing(self):
		trial = _make_trial()
		save_calls = []
		trial.save = lambda *a, **k: save_calls.append(1)
		self.cmd._enrich_from_retrieve(trial, {"ctNumber": "2025-523726-40-00"})
		self.assertEqual(save_calls, [])

	def test_raising_substep_is_caught_and_does_not_save(self):
		trial = _make_trial()
		save_calls = []
		trial.save = lambda *a, **k: save_calls.append(1)

		with patch.object(Command, "_enrich_countries_by_source", side_effect=Exception("boom")):
			self.cmd._enrich_from_retrieve(trial, _retrieve_payload())

		self.assertEqual(save_calls, [])


class EnrichAllTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Enrich All Org")
		self.team = Team.objects.create(organization=self.org, name="Enrich All Team", slug="enrich-all-team")

	def test_selects_trials_by_euct_or_ctis_identifier(self):
		euct_trial = _make_trial(identifiers={"euct": "2025-000000-00-01"})
		ctis_trial = Trials.objects.create(
			title="WHO-created CTIS trial", identifiers={"ctis": "CTIS2025-000000-00-02"}
		)
		Trials.objects.create(title="Unrelated trial", identifiers={"nct": "NCT00000000"})

		api = MagicMock()
		api.retrieve.return_value = _retrieve_payload()
		cmd = _make_command(api)

		cmd.enrich_all_trials(sleep=0)

		called_with = sorted(call.args[0] for call in api.retrieve.call_args_list)
		self.assertEqual(called_with, ["2025-000000-00-01", "2025-000000-00-02"])
		euct_trial.refresh_from_db()
		ctis_trial.refresh_from_db()
		self.assertEqual(euct_trial.countries_by_source, {"ctis": "Brazil; Ukraine"})
		self.assertEqual(ctis_trial.countries_by_source, {"ctis": "Brazil; Ukraine"})

	def test_404_is_skipped_without_error(self):
		_make_trial()
		api = MagicMock()
		api.retrieve.return_value = None
		cmd = _make_command(api)
		cmd.enrich_all_trials(sleep=0)  # must not raise


class ProcessSourcesEnrichmentIntegrationTests(TestCase):
	"""End-to-end: process_sources shares the one retrieve() GET between the disk
	backup and the DB enrichment for the same created/updated trial."""

	def setUp(self):
		self.org = Organization.objects.create(name="Integration Org")
		self.team = Team.objects.create(organization=self.org, name="Integration Team", slug="integration-team")
		from gregory.models import Sources, Subject

		self.subject = Subject.objects.create(subject_name="MS Integration", subject_slug="ms-integration")
		self.source = Sources.objects.create(
			name="CTIS Integration Test",
			source_for="trials",
			method="ctis_api",
			active=True,
			subject=self.subject,
			team=self.team,
			ctis_search_criteria={"medicalCondition": "Multiple Sclerosis"},
		)

	def test_created_trial_is_enriched_from_the_shared_retrieve_call(self):
		import tempfile

		from gregory.classes import ClinicalTrial

		api = MagicMock()
		api.iter_search.return_value = iter([{"ctNumber": "2025-523726-40-00"}])
		api.parse_ctis_search_record.return_value = ClinicalTrial(
			title="Integration Trial",
			summary="Composed summary.",
			link="https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&EUCT=2025-523726-40-00",
			published_date=None,
			identifiers={"eudract": None, "nct": None, "euct": "2025-523726-40-00"},
			extra_fields={},
		)
		api.retrieve.return_value = _retrieve_payload()
		cmd = _make_command(api)

		cmd.process_sources(backup_dir=tempfile.mkdtemp())

		api.retrieve.assert_called_once_with("2025-523726-40-00")
		trial = Trials.objects.get(identifiers__euct="2025-523726-40-00")
		self.assertEqual(trial.countries_by_source, {"ctis": "Brazil; Ukraine"})
		self.assertEqual(trial.inclusion_criteria, "1. Age 18-65 years\n2. Confirmed diagnosis")
