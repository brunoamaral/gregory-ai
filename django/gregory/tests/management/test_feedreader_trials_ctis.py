"""
Command-level tests for feedreader_trials_ctis (CTIS-API-FEEDREADER-PLAN.md).

Covers:
  - source validation: empty/missing/non-dict ctis_search_criteria is skipped
    loudly and never fetched (an empty dict would pull the entire registry)
  - incremental anchor: no anchor -> full pull; anchor -> since = anchor - 2 days;
    advanced only after a fully successful run (never on fetch error, item error,
    or hitting --limit)
  - find_existing_trial: matches CTIS-API/RSS-created rows via the "euct" key,
    AND bridges to WHO ICTRP XML-created rows via the differently-keyed/prefixed
    "ctis" identifier (the single most important correctness point of the PR)
  - create/update: non-destructive guards, and the summary fill-once guard
  - command-level error isolation: one source failing doesn't stop the others

Run:
  docker exec gregory python manage.py test gregory.tests.management.test_feedreader_trials_ctis
"""

import datetime
import os
import tempfile
from unittest.mock import MagicMock

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization

from gregory.classes import ClinicalTrial
from gregory.management.commands.feedreader_trials_ctis import Command
from gregory.models import Sources, Subject, Team, Trials


_UNSET = object()


def _source(criteria=_UNSET, **overrides):
	org = Organization.objects.create(name=f"Test Org {overrides.get('name', 'CTIS')}")
	team = Team.objects.create(
		organization=org,
		name=f"Test Team {overrides.get('name', 'CTIS')}",
		slug=f"test-team-{overrides.get('name', 'ctis').lower().replace(' ', '-')}",
	)
	subject = Subject.objects.create(
		subject_name=f"MS {overrides.get('name', 'CTIS')}",
		subject_slug=f"ms-{overrides.get('name', 'ctis').lower().replace(' ', '-')}",
	)
	defaults = dict(
		name="CTIS API Test",
		source_for="trials",
		method="ctis_api",
		active=True,
		subject=subject,
		team=team,
		ctis_search_criteria=(
			{"medicalCondition": "Multiple Sclerosis"} if criteria is _UNSET else criteria
		),
	)
	defaults.update(overrides)
	return Sources.objects.create(**defaults)


def _trial(ct_number="2026-000000-00-01", title="CTIS Command Test Trial", **extra):
	return ClinicalTrial(
		title=title,
		summary=extra.pop("summary", "Composed summary text."),
		link=f"https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&EUCT={ct_number}",
		published_date=None,
		identifiers={"eudract": None, "nct": None, "euct": ct_number},
		extra_fields=extra,
	)


def _make_command(api=None):
	cmd = Command()
	cmd.verbosity = 0
	cmd.api = api if api is not None else MagicMock()
	return cmd


class SourceValidationTests(TestCase):
	def test_missing_criteria_is_skipped(self):
		_source(criteria=None)
		cmd = _make_command()
		cmd.process_sources()
		cmd.api.iter_search.assert_not_called()

	def test_non_dict_criteria_is_skipped(self):
		source = _source()
		source.ctis_search_criteria = "not a dict"
		source.save(update_fields=["ctis_search_criteria"])
		cmd = _make_command()
		cmd.process_sources()
		cmd.api.iter_search.assert_not_called()

	def test_empty_dict_criteria_is_skipped(self):
		_source(criteria={})
		cmd = _make_command()
		cmd.process_sources()
		cmd.api.iter_search.assert_not_called()

	def test_valid_criteria_is_fetched(self):
		_source(criteria={"medicalCondition": "Multiple Sclerosis"})
		api = MagicMock()
		api.iter_search.return_value = iter([])
		cmd = _make_command(api)
		cmd.process_sources()
		api.iter_search.assert_called_once()


class IncrementalAnchorTests(TestCase):
	def setUp(self):
		self.source = _source()

	def refreshed_anchor(self):
		self.source.refresh_from_db()
		return self.source.last_successful_fetch_at

	def test_no_anchor_means_full_pull(self):
		api = MagicMock()
		api.iter_search.return_value = iter([])
		cmd = _make_command(api)
		cmd.process_sources()
		_, kwargs = api.iter_search.call_args
		self.assertIsNone(kwargs.get("since"))

	def test_anchor_produces_since_two_days_before(self):
		anchor = timezone.now()
		self.source.last_successful_fetch_at = anchor
		self.source.save(update_fields=["last_successful_fetch_at"])

		api = MagicMock()
		api.iter_search.return_value = iter([])
		cmd = _make_command(api)
		cmd.process_sources()

		_, kwargs = api.iter_search.call_args
		expected = (anchor - datetime.timedelta(days=2)).date()
		self.assertEqual(kwargs.get("since"), expected)

	def test_successful_fetch_advances_anchor(self):
		api = MagicMock()
		api.iter_search.return_value = iter([{"ctNumber": "2026-000000-00-01"}])
		api.parse_ctis_search_record.return_value = _trial()
		cmd = _make_command(api)

		before = timezone.now()
		cmd.process_sources()
		anchor = self.refreshed_anchor()

		self.assertIsNotNone(anchor)
		self.assertGreaterEqual(anchor, before)
		self.assertEqual(Trials.objects.count(), 1)

	def test_fetch_error_does_not_advance_anchor(self):
		api = MagicMock()
		api.iter_search.side_effect = Exception("connection dropped mid-pagination")
		cmd = _make_command(api)
		cmd.process_sources()
		self.assertIsNone(self.refreshed_anchor())

	def test_hitting_limit_does_not_advance_anchor(self):
		api = MagicMock()
		api.iter_search.return_value = iter(
			[{"ctNumber": "2026-000000-00-01"}, {"ctNumber": "2026-000000-00-02"}]
		)
		api.parse_ctis_search_record.side_effect = [
			_trial("2026-000000-00-01"),
			_trial("2026-000000-00-02"),
		]
		cmd = _make_command(api)
		cmd.process_sources(limit=1)
		self.assertIsNone(self.refreshed_anchor())
		self.assertEqual(Trials.objects.count(), 1)

	def test_item_error_does_not_advance_anchor(self):
		api = MagicMock()
		api.iter_search.return_value = iter([{"ctNumber": "2026-000000-00-01"}])
		api.parse_ctis_search_record.side_effect = Exception("bad record payload")
		cmd = _make_command(api)
		cmd.process_sources()
		self.assertIsNone(self.refreshed_anchor())

	def test_parse_failure_on_first_record_does_not_abort_source(self):
		api = MagicMock()
		api.iter_search.return_value = iter(
			[{"ctNumber": "2026-000000-00-01"}, {"ctNumber": "2026-000000-00-02"}]
		)
		api.parse_ctis_search_record.side_effect = [
			Exception("bad record payload"),
			_trial("2026-000000-00-02", title="Second trial"),
		]
		cmd = _make_command(api)
		cmd.process_sources()
		self.assertEqual(Trials.objects.count(), 1)
		self.assertIsNone(self.refreshed_anchor())


class FindExistingTrialTests(TestCase):
	def setUp(self):
		self.cmd = _make_command()

	def test_matches_by_euct_key(self):
		existing = Trials.objects.create(
			title="Existing CTIS trial",
			link="https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&EUCT=2025-523726-40-00",
			identifiers={"eudract": None, "nct": None, "euct": "2025-523726-40-00"},
		)
		found = self.cmd.find_existing_trial(_trial("2025-523726-40-00"))
		self.assertEqual(found, existing)

	def test_bridges_to_who_ictrp_ctis_key(self):
		"""A WHO XML-imported trial keys its identifier as "ctis" with a "CTIS"-
		prefixed value (see importWHOXML.py). Without bridging this, the command
		would create a duplicate for every WHO-originated CTIS trial instead of
		enriching it."""
		existing = Trials.objects.create(
			title="A completely different title from WHO XML",
			link="https://trialsearch.who.int/Trial2.aspx?TrialID=CTIS2025-523726-40-00",
			identifiers={"ctis": "CTIS2025-523726-40-00"},
		)
		found = self.cmd.find_existing_trial(_trial("2025-523726-40-00"))
		self.assertEqual(found, existing)

	def test_no_match_returns_none(self):
		found = self.cmd.find_existing_trial(_trial("2025-523726-40-00"))
		self.assertIsNone(found)

	def test_title_fallback_respects_conflict_guard(self):
		Trials.objects.create(
			title="Shared Title Trial",
			link="https://clinicaltrials.gov/study/NCT99999999",
			identifiers={"nct": "NCT99999999", "euct": "2099-999999-99-99"},
		)
		incoming = _trial("2025-523726-40-00", title="Shared Title Trial")
		found = self.cmd.find_existing_trial(incoming)
		self.assertIsNone(found)


class CreateAndUpdateTests(TestCase):
	def setUp(self):
		self.source = _source()
		self.cmd = _make_command()

	def test_create_stores_ctis_fields(self):
		self.cmd.create_new_trial(
			_trial(
				"2026-000000-00-10",
				sponsor_type="Pharmaceutical company",
				country_status="Spain:Ongoing, recruiting",
				recruitment_status="Ongoing, recruiting",
			),
			self.source,
		)
		t = Trials.objects.get(identifiers__euct="2026-000000-00-10")
		self.assertEqual(t.sponsor_type, "Pharmaceutical company")
		self.assertEqual(t.country_status, "Spain:Ongoing, recruiting")
		self.assertEqual(t.recruitment_status, "Ongoing, recruiting")
		self.assertEqual(t.source_register, None)  # not set unless present in extras

	def test_update_summary_fill_once_never_overwrites_existing(self):
		t = self.cmd.create_new_trial(
			_trial("2026-000000-00-11", summary="Original summary"), self.source
		)
		self.cmd.update_existing_trial(
			t, _trial("2026-000000-00-11", summary="A different composed summary"), self.source
		)
		t.refresh_from_db()
		self.assertEqual(t.summary, "Original summary")

	def test_update_fills_summary_when_originally_empty(self):
		t = Trials.objects.create(
			title="No summary yet",
			link="https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&EUCT=2026-000000-00-12",
			identifiers={"eudract": None, "nct": None, "euct": "2026-000000-00-12"},
			summary="",
		)
		self.cmd.update_existing_trial(
			t, _trial("2026-000000-00-12", summary="Composed summary"), self.source
		)
		t.refresh_from_db()
		self.assertEqual(t.summary, "Composed summary")

	def test_update_does_not_blank_field_ctgov_already_set(self):
		t = self.cmd.create_new_trial(
			_trial("2026-000000-00-13", inclusion_agemin="18", inclusion_agemax="64"),
			self.source,
		)
		# Simulate CTGov having since overwritten agemin/agemax more precisely —
		# the CTIS record now has None for the same fields (e.g. missing ageGroup).
		self.cmd.update_existing_trial(
			t,
			_trial("2026-000000-00-13", inclusion_agemin=None, inclusion_agemax=None),
			self.source,
		)
		t.refresh_from_db()
		self.assertEqual(t.inclusion_agemin, "18")
		self.assertEqual(t.inclusion_agemax, "64")

	def test_update_replaces_field_with_new_non_empty_value(self):
		t = self.cmd.create_new_trial(
			_trial("2026-000000-00-14", recruitment_status="Authorised, recruitment pending"),
			self.source,
		)
		self.cmd.update_existing_trial(
			t,
			_trial("2026-000000-00-14", recruitment_status="Ongoing, recruiting"),
			self.source,
		)
		t.refresh_from_db()
		self.assertEqual(t.recruitment_status, "Ongoing, recruiting")

	def test_update_enriches_who_created_trial_without_duplicating(self):
		existing = Trials.objects.create(
			title="WHO originated CTIS trial",
			link="https://trialsearch.who.int/Trial2.aspx?TrialID=CTIS2026-000000-00-15",
			identifiers={"ctis": "CTIS2026-000000-00-15"},
		)
		incoming = _trial("2026-000000-00-15", sponsor_type="Pharmaceutical company")
		found = self.cmd.find_existing_trial(incoming)
		self.assertEqual(found, existing)

		self.cmd.update_existing_trial(found, incoming, self.source)
		self.assertEqual(Trials.objects.count(), 1)
		existing.refresh_from_db()
		self.assertEqual(existing.sponsor_type, "Pharmaceutical company")
		# The pre-existing "ctis" identifier is preserved, "euct" is added.
		self.assertEqual(existing.identifiers.get("ctis"), "CTIS2026-000000-00-15")
		self.assertEqual(existing.identifiers.get("euct"), "2026-000000-00-15")


class ErrorIsolationTests(TestCase):
	def test_one_source_failing_does_not_stop_the_others(self):
		bad_source = _source(name="Bad Source", criteria={"medicalCondition": "Bad"})
		_source(name="Good Source", criteria={"medicalCondition": "Good"})

		api = MagicMock()

		def iter_search_side_effect(criteria, since=None, sleep=0.5):
			if criteria == bad_source.ctis_search_criteria:
				raise Exception("CTIS API is down")
			return iter([{"ctNumber": "2026-000000-00-20"}])

		api.iter_search.side_effect = iter_search_side_effect
		api.parse_ctis_search_record.return_value = _trial("2026-000000-00-20")

		cmd = _make_command(api)
		cmd.process_sources()

		self.assertEqual(len(cmd.fetch_errors), 1)
		self.assertIn("Bad Source", cmd.fetch_errors[0])
		self.assertEqual(Trials.objects.count(), 1)


class RetrieveBackupTests(TestCase):
	"""Archiving the raw /retrieve dossier is a side effect: it must never block or
	fail the actual trial create/update, which comes entirely from /search."""

	def setUp(self):
		self.source = _source()
		self.tmp_dir = tempfile.mkdtemp()

	def test_backup_written_when_retrieve_returns_a_dict(self):
		api = MagicMock()
		api.iter_search.return_value = iter([{"ctNumber": "2026-000000-00-30"}])
		api.parse_ctis_search_record.return_value = _trial("2026-000000-00-30")
		api.retrieve.return_value = {"ctNumber": "2026-000000-00-30", "deep": "dossier"}
		cmd = _make_command(api)

		cmd.process_sources(backup_dir=self.tmp_dir)

		api.retrieve.assert_called_once_with("2026-000000-00-30")
		files = os.listdir(self.tmp_dir)
		self.assertEqual(len(files), 1)
		self.assertTrue(files[0].startswith("2026-000000-00-30-"))
		self.assertEqual(Trials.objects.count(), 1)

	def test_non_dict_retrieve_result_is_skipped_without_error(self):
		api = MagicMock()
		api.iter_search.return_value = iter([{"ctNumber": "2026-000000-00-31"}])
		api.parse_ctis_search_record.return_value = _trial("2026-000000-00-31")
		# api.retrieve default MagicMock() return value is not a dict.
		cmd = _make_command(api)

		cmd.process_sources(backup_dir=self.tmp_dir)

		self.assertEqual(os.listdir(self.tmp_dir), [])
		self.assertEqual(Trials.objects.count(), 1)

	def test_retrieve_exception_does_not_block_trial_processing_or_anchor(self):
		api = MagicMock()
		api.iter_search.return_value = iter([{"ctNumber": "2026-000000-00-32"}])
		api.parse_ctis_search_record.return_value = _trial("2026-000000-00-32")
		api.retrieve.side_effect = Exception("CTIS retrieve endpoint is down")
		cmd = _make_command(api)

		cmd.process_sources(backup_dir=self.tmp_dir)

		self.assertEqual(os.listdir(self.tmp_dir), [])
		self.assertEqual(Trials.objects.count(), 1)
		self.source.refresh_from_db()
		self.assertIsNotNone(self.source.last_successful_fetch_at)

	def test_default_backup_dir_is_used_when_not_specified(self):
		"""Omitting --backup-dir must fall through to the module's BACKUPS_DIR
		constant, not silently pass None to save_retrieve_backup."""
		from unittest.mock import patch

		import gregory.management.commands.feedreader_trials_ctis as cmd_module

		api = MagicMock()
		api.iter_search.return_value = iter([{"ctNumber": "2026-000000-00-33"}])
		api.parse_ctis_search_record.return_value = _trial("2026-000000-00-33")
		api.retrieve.return_value = {"ctNumber": "2026-000000-00-33"}
		cmd = _make_command(api)

		with patch.object(cmd_module, "save_retrieve_backup") as mock_save:
			cmd.process_sources()

		mock_save.assert_called_once_with(
			cmd_module.BACKUPS_DIR,
			"2026-000000-00-33",
			{"ctNumber": "2026-000000-00-33"},
		)
