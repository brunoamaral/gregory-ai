"""Tests for gregory.utils.trial_field_normalizers.normalize_study_type and the
Trials.save() hook that keeps study_type_normalized in lockstep. Mirrors the structure of
test_trial_recruitment_status_normalization.py.

See docs/trials-field-normalization.md and STUDY-TYPE-NORMALIZATION-PLAN.md for the design
and the judgment calls behind the mapping table (Diagnostic test/Screening -> observational,
BA/BE -> interventional, Basic Science keeping its own bucket rather than falling into
"other").
"""

from io import StringIO

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.management import call_command
from django.test import RequestFactory, TestCase

from gregory.admin import TrialAdmin
from gregory.models import Trials
from gregory.utils.trial_field_normalizers import (
	TrialStudyType,
	normalize_study_type,
)

User = get_user_model()

# Every distinct raw value from the 27-row inventory in STUDY-TYPE-NORMALIZATION-PLAN.md,
# transcribed independently so this test actually catches drift in the module rather than
# restating it.
EXACT_MATCH_CASES = [
	# Interventional
	("INTERVENTIONAL", TrialStudyType.INTERVENTIONAL),
	("Interventional", TrialStudyType.INTERVENTIONAL),
	("interventional", TrialStudyType.INTERVENTIONAL),
	("Intervention", TrialStudyType.INTERVENTIONAL),
	("Interventional study", TrialStudyType.INTERVENTIONAL),
	(
		"Interventional clinical trial of medicinal product",
		TrialStudyType.INTERVENTIONAL,
	),
	("Treatment study", TrialStudyType.INTERVENTIONAL),
	("BA/BE", TrialStudyType.INTERVENTIONAL),
	# Observational
	("OBSERVATIONAL", TrialStudyType.OBSERVATIONAL),
	("Observational", TrialStudyType.OBSERVATIONAL),
	("observational", TrialStudyType.OBSERVATIONAL),
	("Observational study", TrialStudyType.OBSERVATIONAL),
	("Observational non invasive", TrialStudyType.OBSERVATIONAL),
	("Observational invasive", TrialStudyType.OBSERVATIONAL),
	("Diagnostic test", TrialStudyType.OBSERVATIONAL),
	("Screening", TrialStudyType.OBSERVATIONAL),
	("Cause", TrialStudyType.OBSERVATIONAL),
	("Cause/Relative factors study", TrialStudyType.OBSERVATIONAL),
	("Relative factors research", TrialStudyType.OBSERVATIONAL),
	("Epidemilogical research", TrialStudyType.OBSERVATIONAL),  # sic, registry typo
	("Prognosis study", TrialStudyType.OBSERVATIONAL),
	# Expanded access
	("EXPANDED_ACCESS", TrialStudyType.EXPANDED_ACCESS),
	("Expanded Access", TrialStudyType.EXPANDED_ACCESS),
	# Basic science
	("Basic Science", TrialStudyType.BASIC_SCIENCE),
	("Basic science", TrialStudyType.BASIC_SCIENCE),
	# Other
	("Other", TrialStudyType.OTHER),
]


@pytest.mark.parametrize("raw,expected", EXACT_MATCH_CASES)
def test_exact_match_table(raw, expected):
	assert normalize_study_type(raw) == expected


@pytest.mark.parametrize("raw,expected", EXACT_MATCH_CASES)
def test_exact_match_table_is_case_and_whitespace_insensitive(raw, expected):
	"""Uppercasing and wrapping in stray whitespace must not change the result."""
	noisy = f"  {raw.upper()}  \n"
	assert normalize_study_type(noisy) == expected


def test_none_returns_none():
	assert normalize_study_type(None) is None


def test_empty_string_returns_none():
	assert normalize_study_type("") is None


def test_whitespace_only_returns_none():
	assert normalize_study_type("   ") is None
	assert normalize_study_type("\n\t ") is None


def test_double_space_variant_collapses_to_match_table():
	assert (
		normalize_study_type("Interventional  study")
		== TrialStudyType.INTERVENTIONAL
	)


# --- Unmapped values ------------------------------------------------------------------


def test_unmapped_value_returns_other():
	assert normalize_study_type("Some future registry study type") == TrialStudyType.OTHER


def test_unmapped_value_is_logged(caplog):
	with caplog.at_level("INFO", logger="gregory.utils.trial_field_normalizers"):
		normalize_study_type("Some future registry study type")
	assert "Some future registry study type" in caplog.text


def test_no_generic_token_fallback():
	"""Like normalize_recruitment_status, there is no regex fallback here — a near-miss
	spelling that isn't in the exact-match table must land in OTHER, not get guessed at."""
	assert normalize_study_type("Interventional-ish") == TrialStudyType.OTHER
	assert normalize_study_type("Type: Interventional") == TrialStudyType.OTHER


# --- Trials.save() hook ---------------------------------------------------------------


class TrialSaveHookTests(TestCase):
	def test_create_computes_study_type_normalized(self):
		trial = Trials.objects.create(
			title="Interventional trial",
			link="https://example.com/st-hook-1",
			study_type="INTERVENTIONAL",
		)
		self.assertEqual(trial.study_type_normalized, "interventional")

	def test_changing_study_type_and_saving_recomputes(self):
		trial = Trials.objects.create(
			title="Study type trial",
			link="https://example.com/st-hook-2",
			study_type="INTERVENTIONAL",
		)
		self.assertEqual(trial.study_type_normalized, "interventional")

		trial.study_type = "OBSERVATIONAL"
		trial.save()

		trial.refresh_from_db()
		self.assertEqual(trial.study_type_normalized, "observational")

	def test_save_update_fields_study_type_only_still_persists_derived_field(self):
		trial = Trials.objects.create(
			title="Study type trial",
			link="https://example.com/st-hook-3",
			study_type="INTERVENTIONAL",
		)

		trial.study_type = "Basic Science"
		trial.save(update_fields=["study_type"])

		trial.refresh_from_db()
		self.assertEqual(trial.study_type_normalized, "basic_science")

	def test_save_update_fields_without_study_type_leaves_stored_value_alone(self):
		trial = Trials.objects.create(
			title="Study type trial",
			link="https://example.com/st-hook-4",
			study_type="INTERVENTIONAL",
		)
		# Simulate an out-of-band write that bypassed save(), then a save() that
		# doesn't touch study_type: study_type_normalized must not be clobbered back.
		Trials.objects.filter(pk=trial.pk).update(study_type="OBSERVATIONAL")
		trial.title = "Renamed"
		trial.save(update_fields=["title"])

		trial.refresh_from_db()
		self.assertEqual(trial.study_type_normalized, "interventional")  # unchanged

	def test_none_study_type_leaves_normalized_none(self):
		trial = Trials.objects.create(
			title="No study type", link="https://example.com/st-hook-5"
		)
		self.assertIsNone(trial.study_type_normalized)

	def test_save_recomputes_phase_and_study_type_together(self):
		"""Sanity check that the NORMALIZED_TRIAL_FIELDS loop in Trials.save() handles
		every registered field on the same write, not just whichever was touched."""
		trial = Trials.objects.create(
			title="Both fields",
			link="https://example.com/st-hook-6",
			phase="Phase II",
			study_type="OBSERVATIONAL",
		)
		self.assertEqual(trial.phase_normalized, "phase_2")
		self.assertEqual(trial.study_type_normalized, "observational")


# --- backfill_trial_normalized_fields command (study_type coverage) -------------------


class BackfillTrialStudyTypeTest(TestCase):
	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_normalized_fields", stdout=out, stderr=err, **kwargs
		)
		return out.getvalue(), err.getvalue()

	def _make_stale(self, title, link, study_type):
		"""Create a trial, then blank study_type_normalized via bulk-style update() to
		simulate a row written before the save() hook existed (or by bulk_update)."""
		trial = Trials.objects.create(title=title, link=link, study_type=study_type)
		Trials.objects.filter(pk=trial.pk).update(study_type_normalized=None)
		trial.refresh_from_db()
		return trial

	def test_backfill_restores_values_bulk_update_bypassed(self):
		t1 = self._make_stale("A", "https://example.com/st-bf-1", "INTERVENTIONAL")
		t2 = self._make_stale(
			"B", "https://example.com/st-bf-2", "Some future registry study type"
		)

		out, _ = self.run_command(field="study_type")

		t1.refresh_from_db()
		t2.refresh_from_db()
		self.assertEqual(t1.study_type_normalized, "interventional")
		self.assertEqual(t2.study_type_normalized, "other")
		self.assertIn("Some future registry study type", out)

	def test_dry_run_changes_nothing(self):
		t1 = self._make_stale("A", "https://example.com/st-bf-3", "INTERVENTIONAL")

		out, _ = self.run_command(field="study_type", dry_run=True)

		t1.refresh_from_db()
		self.assertIsNone(t1.study_type_normalized)
		self.assertIn("Would update", out)

	def test_idempotent_rerun_updates_nothing(self):
		Trials.objects.create(
			title="A", link="https://example.com/st-bf-4", study_type="INTERVENTIONAL"
		)

		out, _ = self.run_command(field="study_type")

		self.assertIn("Updated 0 rows", out)

	def test_field_filter_scopes_to_study_type_only(self):
		"""--field study_type must not touch phase_normalized, even when it is stale —
		the counterpart to the phase-only test in test_trial_phase_normalization.py."""
		trial = Trials.objects.create(
			title="A",
			link="https://example.com/st-bf-field",
			phase="Phase III",
			study_type="INTERVENTIONAL",
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, study_type_normalized=None
		)
		trial.refresh_from_db()

		self.run_command(field="study_type")

		trial.refresh_from_db()
		self.assertEqual(trial.study_type_normalized, "interventional")
		self.assertIsNone(trial.phase_normalized)  # untouched: not selected

	def test_default_scope_backfills_study_type_alongside_other_fields(self):
		"""Omitting --field entirely covers every registered field, not just one."""
		trial = Trials.objects.create(
			title="A",
			link="https://example.com/st-bf-both",
			phase="Phase III",
			study_type="INTERVENTIONAL",
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, study_type_normalized=None
		)
		trial.refresh_from_db()

		self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.phase_normalized, "phase_3")
		self.assertEqual(trial.study_type_normalized, "interventional")


# --- Admin "Recompute normalized fields" action (study_type coverage) -----------------


class TrialAdminRecomputeStudyTypeNormalizedTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()
		self.trial_admin = TrialAdmin(Trials, self.site)
		self.superuser = User.objects.create_superuser(
			username="study-type-admin-root", email="root3@example.com", password="pw"
		)

	def _request(self):
		request = self.factory.post("/admin/gregory/trials/")
		request.user = self.superuser
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def test_action_updates_stale_study_type_normalized(self):
		trial = Trials.objects.create(
			title="Observational trial",
			link="https://example.com/st-admin-1",
			study_type="OBSERVATIONAL",
		)
		Trials.objects.filter(pk=trial.pk).update(study_type_normalized=None)
		trial.refresh_from_db()
		self.assertIsNone(trial.study_type_normalized)

		request = self._request()
		queryset = Trials.objects.filter(pk=trial.pk)
		self.trial_admin.recompute_normalized_fields(request, queryset)

		trial.refresh_from_db()
		self.assertEqual(trial.study_type_normalized, "observational")
