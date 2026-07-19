"""Tests for the backfill_trial_sponsors_from_ctgov management command.

Mirrors gregory/tests/test_backfill_trial_countries.py's structure and FakeAPI pattern —
no network calls.

Run:
	docker exec gregory python manage.py test gregory.tests.management.test_backfill_trial_sponsors_from_ctgov
"""

import os
from io import StringIO
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase

from gregory.management.commands.backfill_trial_sponsors_from_ctgov import (
	CHANGE_REASON,
	FIELDS,
)
from gregory.models import Sponsor, Trials


def _study(nct_id, name=None, sponsor_class=None, collaborators=None):
	protocol = {"identificationModule": {"nctId": nct_id}}
	sponsor_module = {}
	if name is not None or sponsor_class is not None:
		lead = {}
		if name is not None:
			lead["name"] = name
		if sponsor_class is not None:
			lead["class"] = sponsor_class
		sponsor_module["leadSponsor"] = lead
	if collaborators is not None:
		sponsor_module["collaborators"] = [{"name": c} for c in collaborators]
	if sponsor_module:
		protocol["sponsorCollaboratorsModule"] = sponsor_module
	return {"protocolSection": protocol}


class FakeAPI:
	"""Stand-in for ClinicalTrialsGovAPI returning canned studies (no network)."""

	studies_by_nct = {}
	calls = []
	fields_seen = []

	def __init__(self):
		pass

	def search(self, filter_ids=None, fields=None, **kwargs):
		type(self).calls.append(list(filter_ids))
		type(self).fields_seen.append(fields)
		return {
			"studies": [
				self.studies_by_nct[nct]
				for nct in filter_ids
				if nct in self.studies_by_nct
			]
		}


@patch(
	"gregory.management.commands.backfill_trial_sponsors_from_ctgov.ClinicalTrialsGovAPI",
	FakeAPI,
)
class BackfillTrialSponsorsFromCtgovTest(TestCase):
	def setUp(self):
		FakeAPI.studies_by_nct = {}
		FakeAPI.calls = []
		FakeAPI.fields_seen = []

	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_sponsors_from_ctgov", sleep=0, stdout=out, stderr=err, **kwargs
		)
		return out.getvalue(), err.getvalue()

	def make_trial(self, nct, n=None, **extra):
		n = n if n is not None else (nct or "X")
		return Trials.objects.create(
			title=f"Trial {n}",
			link=f"https://clinicaltrials.gov/study/{n}",
			identifiers={"nct": nct},
			**extra,
		)

	def test_requests_expected_ctgov_fields(self):
		self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001", "Acme Corp")}

		self.run_command()

		self.assertEqual(FakeAPI.fields_seen, [FIELDS])
		self.assertIn("protocolSection.identificationModule.nctId", FIELDS)
		self.assertIn("protocolSection.sponsorCollaboratorsModule", FIELDS)

	def test_fills_empty_primary_sponsor_and_class_and_resolves(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", "Acme Corp", "INDUSTRY", ["Collab A"])
		}

		out, _ = self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.primary_sponsor, "Acme Corp")
		self.assertEqual(trial.secondary_sponsor, "Collab A")
		self.assertEqual(trial.lead_sponsor_class, "INDUSTRY")
		self.assertIsInstance(trial.primary_sponsor_normalized, Sponsor)
		self.assertEqual(trial.primary_sponsor_normalized.name, "Acme Corp")
		self.assertEqual(trial.history.first().history_change_reason, CHANGE_REASON)
		self.assertIn("Filled primary_sponsor on 1 trial row(s)", out)

	def test_selection_requires_empty_sponsor_or_null_class(self):
		# Has a sponsor name AND a class already -> not selected at all.
		fully_populated = self.make_trial(
			"NCT00000001", primary_sponsor="Existing Corp", lead_sponsor_class="INDUSTRY"
		)
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", "New Corp", "NIH")
		}

		out, _ = self.run_command()

		fully_populated.refresh_from_db()
		self.assertEqual(FakeAPI.calls, [])
		self.assertEqual(fully_populated.primary_sponsor, "Existing Corp")
		self.assertEqual(fully_populated.lead_sponsor_class, "INDUSTRY")
		self.assertIn("0 NCT ids missing sponsor data", out)

	def test_selects_trial_with_sponsor_but_null_class(self):
		trial = self.make_trial("NCT00000001", primary_sponsor="Existing Corp")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", "Ignored New Name", "NIH")
		}

		self.run_command()

		trial.refresh_from_db()
		# primary_sponsor was already non-empty -> never overwritten (fill-only-when-empty).
		self.assertEqual(trial.primary_sponsor, "Existing Corp")
		# lead_sponsor_class was null -> filled.
		self.assertEqual(trial.lead_sponsor_class, "NIH")

	def test_never_overwrites_existing_secondary_sponsor(self):
		trial = self.make_trial(
			"NCT00000001", secondary_sponsor="Pre-existing Collaborator"
		)
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", "Acme Corp", "INDUSTRY", ["New Collab"])
		}

		self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.secondary_sponsor, "Pre-existing Collaborator")

	def test_dry_run_saves_nothing(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", "Acme Corp", "INDUSTRY")
		}

		out, _ = self.run_command(dry_run=True)

		trial.refresh_from_db()
		self.assertIsNone(trial.primary_sponsor)
		self.assertIsNone(trial.lead_sponsor_class)
		self.assertFalse(Sponsor.objects.exists())
		self.assertIn("Would fill primary_sponsor on 1 trial row(s)", out)

	def test_limit_caps_candidates(self):
		for i in range(1, 4):
			self.make_trial(f"NCT0000000{i}")
		FakeAPI.studies_by_nct = {
			f"NCT0000000{i}": _study(f"NCT0000000{i}", f"Corp {i}", "INDUSTRY")
			for i in range(1, 4)
		}

		out, _ = self.run_command(limit=2)

		self.assertIn("2 NCT ids missing sponsor data", out)

	def test_invalid_nct_ids_are_skipped(self):
		self.make_trial("not-an-id", n="BAD1")

		out, _ = self.run_command()

		self.assertEqual(FakeAPI.calls, [])
		self.assertIn("1 skipped as invalid", out)
