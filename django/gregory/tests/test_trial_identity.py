"""
Integration-style tests for the title-fallback guard in the three trial importers.

These tests verify that:
  - same title + conflicting nct  ⇒ find_existing_trial returns None (new row)
  - same title + nct vs euctr     ⇒ find_existing_trial returns the existing row
  - re-presenting the exact same trial ⇒ same row returned (idempotent)
  - WHO identifiers__contains fallback is also guarded

The tests use the SQLite in-memory database set up by test_settings so they
run in CI without PostgreSQL.  The partial unique-constraint enforcement
(migration 0054) requires PostgreSQL and is therefore NOT covered here —
run audit_trial_merges + the migration against a staging/prod-clone to
validate that side.

Run:
  docker exec gregory python manage.py test gregory.tests.test_trial_identity
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from organizations.models import Organization

from gregory.models import Trials, Sources, Subject, Team
from gregory.classes import ClinicalTrial
from gregory.management.commands.feedreader_trials import Command as EUCommand
from gregory.management.commands.feedreader_trials_ctgov import Command as CTGovCommand
from gregory.management.commands.importWHOXML import Command as WHOCommand


def _make_source(org):
	"""Create a minimal Source + Team + Subject for testing."""
	team = Team.objects.create(organization=org, name="Test Team", slug="test-team")
	subject = Subject.objects.create(subject_name="MS", subject_slug="ms")
	source = Sources.objects.create(
		name="Test Source",
		source_for="trials",
		method="rss",
		subject=subject,
		team=team,
	)
	return source, team, subject


def _make_trial(title, identifiers, link="https://example.com/trial"):
	"""Create a minimal Trials row."""
	return Trials.objects.create(
		title=title,
		identifiers=identifiers,
		link=link,
	)


def _make_clinical_trial(title, identifiers, link="https://example.com/trial"):
	"""Return a ClinicalTrial stub with the given identifiers."""
	ct = ClinicalTrial(
		title=title,
		summary="",
		link=link,
		published_date=None,
		identifiers=identifiers,
	)
	return ct


# ---------------------------------------------------------------------------
# Helper: a Command instance that doesn't need a real handle() invocation
# ---------------------------------------------------------------------------


def _eu_cmd():
	cmd = EUCommand()
	cmd.verbosity = 0
	cmd.setup()
	return cmd


def _ctgov_cmd():
	cmd = CTGovCommand()
	cmd.verbosity = 0
	return cmd


# ---------------------------------------------------------------------------
# EU CTIS feedreader_trials tests
# ---------------------------------------------------------------------------


class EUImporterTitleGuardTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Test Org")

	def test_same_title_conflicting_nct_returns_none(self):
		"""Same title but different NCTs → should NOT match (different trials)."""
		_make_trial("Trial Alpha", {"nct": "NCT05529498"})
		incoming = _make_clinical_trial("Trial Alpha", {"nct": "NCT05560880"})
		result = _eu_cmd().find_existing_trial(incoming)
		self.assertIsNone(result)

	def test_same_title_disjoint_keys_returns_existing(self):
		"""Same title, nct vs euctr → cross-registry merge is safe."""
		existing = _make_trial("Trial Beta", {"nct": "NCT03268902"})
		incoming = _make_clinical_trial("Trial Beta", {"euctr": "EUCTR2018-000123-42"})
		result = _eu_cmd().find_existing_trial(incoming)
		self.assertEqual(result.pk, existing.pk)

	def test_exact_same_trial_returns_existing(self):
		"""Re-ingesting the same record must return the existing row."""
		existing = _make_trial("Trial Gamma", {"nct": "NCT00000001"})
		incoming = _make_clinical_trial("Trial Gamma", {"nct": "NCT00000001"})
		result = _eu_cmd().find_existing_trial(incoming)
		self.assertEqual(result.pk, existing.pk)

	def test_identifier_match_takes_priority_over_title(self):
		"""Direct identifier match bypasses title comparison entirely."""
		existing = _make_trial("Old Title", {"nct": "NCT00000099"})
		incoming = _make_clinical_trial("New Different Title", {"nct": "NCT00000099"})
		result = _eu_cmd().find_existing_trial(incoming)
		self.assertEqual(result.pk, existing.pk)


# ---------------------------------------------------------------------------
# CT.gov feedreader_trials_ctgov tests
# ---------------------------------------------------------------------------


class CTGovImporterTitleGuardTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Test Org")

	def test_same_title_conflicting_nct_returns_none(self):
		_make_trial(
			"Study One",
			{"nct": "NCT05560880"},
			link="https://clinicaltrials.gov/study/NCT05560880",
		)
		# Use a different link so the link-exact-match step doesn't fire
		incoming = _make_clinical_trial(
			"Study One",
			{"nct": "NCT05529498"},
			link="https://clinicaltrials.gov/study/NCT05529498",
		)
		result = _ctgov_cmd().find_existing_trial(incoming)
		self.assertIsNone(result)

	def test_same_title_disjoint_keys_returns_existing(self):
		existing = _make_trial(
			"Study Two",
			{"nct": "NCT03268902"},
			link="https://clinicaltrials.gov/study/NCT03268902",
		)
		incoming = _make_clinical_trial(
			"Study Two",
			{"euctr": "EUCTR2018-000456-12"},
			link="https://euclinicaltrials.eu/EUCTR2018-000456-12",
		)
		result = _ctgov_cmd().find_existing_trial(incoming)
		self.assertEqual(result.pk, existing.pk)

	def test_exact_same_trial_returns_existing(self):
		existing = _make_trial(
			"Study Three",
			{"nct": "NCT00000002"},
			link="https://clinicaltrials.gov/study/NCT00000002",
		)
		incoming = _make_clinical_trial(
			"Study Three",
			{"nct": "NCT00000002"},
			link="https://clinicaltrials.gov/study/NCT00000002",
		)
		result = _ctgov_cmd().find_existing_trial(incoming)
		self.assertEqual(result.pk, existing.pk)


# ---------------------------------------------------------------------------
# WHO importWHOXML conservative identifier merge test
# ---------------------------------------------------------------------------


class WHOConservativeMergeTest(TestCase):
	"""Verify that WHO's update_existing_trial no longer overwrites stored IDs."""

	def setUp(self):
		self.org = Organization.objects.create(name="WHO Org")
		self.team = Team.objects.create(
			organization=self.org, name="WHO Team", slug="who-team"
		)
		self.subject = Subject.objects.create(
			subject_name="WHO S", subject_slug="who-s"
		)
		self.source = Sources.objects.create(
			name="WHO Source",
			source_for="trials",
			method="rss",
			subject=self.subject,
			team=self.team,
		)

	def test_existing_nct_not_overwritten_by_incoming(self):
		"""If a trial already has nct=NCT001, re-ingesting with nct=NCT002
		(via {**current, **value}) would have overwritten it.  After the fix,
		the stored value must be preserved."""
		trial = _make_trial(
			"WHO Trial", {"nct": "NCT00000010"}, link="https://who.int/trial/1"
		)
		cmd = WHOCommand()
		import io

		cmd.stdout = io.StringIO()

		# Simulate a re-ingest where the XML carries a *different* NCT
		# (pathological case — shouldn't happen, but validates conservative merge)
		trial_data = {"identifiers": {"nct": "NCT99999999"}}
		cmd.update_existing_trial(
			trial, trial_data, source=self.source, subject=self.subject
		)
		trial.refresh_from_db()
		self.assertEqual(
			trial.identifiers["nct"],
			"NCT00000010",
			"Conservative merge must preserve the existing NCT ID",
		)

	def test_new_key_is_added_when_absent(self):
		"""A key not yet in the stored identifiers should be added."""
		trial = _make_trial(
			"WHO Trial 2", {"nct": "NCT00000011"}, link="https://who.int/trial/2"
		)
		cmd = WHOCommand()
		import io

		cmd.stdout = io.StringIO()

		trial_data = {"identifiers": {"euctr": "EUCTR2024-000001-01"}}
		cmd.update_existing_trial(
			trial, trial_data, source=self.source, subject=self.subject
		)
		trial.refresh_from_db()
		self.assertEqual(trial.identifiers["nct"], "NCT00000011")
		self.assertEqual(trial.identifiers["euctr"], "EUCTR2024-000001-01")


# ---------------------------------------------------------------------------
# WHO title fallback guard
# ---------------------------------------------------------------------------


class WHOImporterTitleGuardTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org")
		self.team = Team.objects.create(organization=self.org, name="T", slug="t")
		self.subject = Subject.objects.create(subject_name="S", subject_slug="s")
		self.source = Sources.objects.create(
			name="WHO",
			source_for="trials",
			method="rss",
			subject=self.subject,
			team=self.team,
		)

	def _check_for(self, trial_data):
		import io

		cmd = WHOCommand()
		cmd.stdout = io.StringIO()
		cmd.check_for_existing_trial(trial_data, self.source, self.subject)

	def test_same_title_conflicting_nct_creates_new_row(self):
		"""Two different WHO records with the same title but different NCTs
		must NOT be merged."""
		_make_trial("WHO Study Alpha", {"nct": "NCT05529498"})
		initial_count = Trials.objects.count()

		self._check_for(
			{
				"title": "WHO Study Alpha",
				"identifiers": {"nct": "NCT05560880"},
				"link": "https://who.int/trial/new",
			}
		)

		# A new row should have been created
		self.assertEqual(Trials.objects.count(), initial_count + 1)

	def test_same_title_disjoint_keys_merges(self):
		"""Same title + nct vs euctr → merge into existing row."""
		existing = _make_trial("WHO Study Beta", {"nct": "NCT03268902"})
		initial_count = Trials.objects.count()

		self._check_for(
			{
				"title": "WHO Study Beta",
				"identifiers": {"euctr": "EUCTR2018-000123-42"},
				"link": "https://who.int/trial/beta",
			}
		)

		# No new row — update only
		self.assertEqual(Trials.objects.count(), initial_count)
