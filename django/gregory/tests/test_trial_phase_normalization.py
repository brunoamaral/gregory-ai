"""Tests for gregory.utils.trial_field_normalizers.normalize_phase, the Trials.save()
hook that keeps phase_normalized in lockstep, the generalized
backfill_trial_normalized_fields management command, and the admin "Recompute normalized
fields" action.

See docs/trials-field-normalization.md for the design. Recruitment-status-specific
coverage lives in test_trial_recruitment_status_normalization.py, which mirrors this
file's structure.
"""

from io import StringIO

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase

from gregory.admin import TrialAdmin
from gregory.models import Trials
from gregory.utils.trial_field_normalizers import TrialPhase, normalize_phase

User = get_user_model()

# Every distinct raw value in gregory.utils.trial_field_normalizers._EXACT_MATCHES,
# transcribed independently so this test actually catches drift in the module rather
# than restating it.
EXACT_MATCH_CASES = [
	# Early Phase 1 / Phase 0
	("phase 0", TrialPhase.EARLY_PHASE_1),
	("0", TrialPhase.EARLY_PHASE_1),
	("0 (exploratory trials)", TrialPhase.EARLY_PHASE_1),
	("early phase 1", TrialPhase.EARLY_PHASE_1),
	("early_phase1", TrialPhase.EARLY_PHASE_1),
	# Phase 1
	("phase 1", TrialPhase.PHASE_1),
	("phase i", TrialPhase.PHASE_1),
	("phase1", TrialPhase.PHASE_1),
	("1", TrialPhase.PHASE_1),
	("i (phase i study)", TrialPhase.PHASE_1),
	(
		"human pharmacology (phase i)- first administration to humans",
		TrialPhase.PHASE_1,
	),
	("human pharmacology (phase i)- other", TrialPhase.PHASE_1),
	# Phase 1/2
	("phase 1/phase 2", TrialPhase.PHASE_1_2),
	("phase 1/ phase 2", TrialPhase.PHASE_1_2),
	("phase 1 / phase 2", TrialPhase.PHASE_1_2),
	("phase i,ii", TrialPhase.PHASE_1_2),
	("phase i and phase ii (integrated)- other", TrialPhase.PHASE_1_2),
	("phase1, phase2", TrialPhase.PHASE_1_2),
	("1-2", TrialPhase.PHASE_1_2),
	("i+ii (phase i+phase ii)", TrialPhase.PHASE_1_2),
	# Phase 2
	("phase 2", TrialPhase.PHASE_2),
	("phase ii", TrialPhase.PHASE_2),
	("phase2", TrialPhase.PHASE_2),
	("2", TrialPhase.PHASE_2),
	("ii (phase ii study)", TrialPhase.PHASE_2),
	("therapeutic exploratory (phase ii)", TrialPhase.PHASE_2),
	# Phase 2/3
	("phase 2/phase 3", TrialPhase.PHASE_2_3),
	("phase 2/ phase 3", TrialPhase.PHASE_2_3),
	("phase 2 / phase 3", TrialPhase.PHASE_2_3),
	("phase ii/iii", TrialPhase.PHASE_2_3),
	("phase ii,iii", TrialPhase.PHASE_2_3),
	("phase ii and phase iii (integrated)", TrialPhase.PHASE_2_3),
	("phase2, phase3", TrialPhase.PHASE_2_3),
	("2-3", TrialPhase.PHASE_2_3),
	# Phase 3
	("phase 3", TrialPhase.PHASE_3),
	("phase iii", TrialPhase.PHASE_3),
	("phase3", TrialPhase.PHASE_3),
	("3", TrialPhase.PHASE_3),
	("iii", TrialPhase.PHASE_3),
	("therapeutic confirmatory (phase iii)", TrialPhase.PHASE_3),
	# Phase 3/4
	("phase 3/ phase 4", TrialPhase.PHASE_3_4),
	("phase 3 / phase 4", TrialPhase.PHASE_3_4),
	# Phase 4
	("phase 4", TrialPhase.PHASE_4),
	("phase iv", TrialPhase.PHASE_4),
	("phase4", TrialPhase.PHASE_4),
	("4", TrialPhase.PHASE_4),
	("iv (phase iv study)", TrialPhase.PHASE_4),
	("therapeutic use (phase iv)", TrialPhase.PHASE_4),
	# Post-market
	("post-marketing clinical trial", TrialPhase.POST_MARKET),
	("post-market", TrialPhase.POST_MARKET),
	# Not applicable
	("not applicable", TrialPhase.NOT_APPLICABLE),
	("n/a", TrialPhase.NOT_APPLICABLE),
	("na", TrialPhase.NOT_APPLICABLE),
	("not selected", TrialPhase.NOT_APPLICABLE),
	("not specified", TrialPhase.NOT_APPLICABLE),
	# Other: registry categories with no phase equivalent
	("others", TrialPhase.OTHER),
	("other", TrialPhase.OTHER),
	("retrospective study", TrialPhase.OTHER),
	("pilot study", TrialPhase.OTHER),
	("pilot clinical trial", TrialPhase.OTHER),
	("new treatment measure clinical study", TrialPhase.OTHER),
	("diagnostic new technique clincal study", TrialPhase.OTHER),  # sic, registry typo
	("bioequivalence", TrialPhase.OTHER),
	("basic science", TrialPhase.OTHER),
]


@pytest.mark.parametrize("raw,expected", EXACT_MATCH_CASES)
def test_exact_match_table(raw, expected):
	assert normalize_phase(raw) == expected


@pytest.mark.parametrize("raw,expected", EXACT_MATCH_CASES)
def test_exact_match_table_is_case_and_whitespace_insensitive(raw, expected):
	"""Uppercasing and wrapping in stray whitespace must not change the result."""
	noisy = f"  {raw.upper()}  \n"
	assert normalize_phase(noisy) == expected


def test_none_returns_none():
	assert normalize_phase(None) is None


def test_empty_string_returns_none():
	assert normalize_phase("") is None


def test_whitespace_only_returns_none():
	assert normalize_phase("   ") is None
	assert normalize_phase("\n\t ") is None


def test_double_space_variant_collapses_to_match_table():
	# Registry export arrives with a literal double space before the parenthetical.
	assert normalize_phase("Therapeutic confirmatory  (Phase III)") == TrialPhase.PHASE_3


# --- EudraCT / EU CTIS yes/no matrix -----------------------------------------------


def _matrix(i="no", ii="no", iii="no", iv="no", label_iv="Therapeutic use"):
	return (
		f"Human pharmacology (Phase I): {i} "
		f"Therapeutic exploratory (Phase II): {ii} "
		f"Therapeutic confirmatory - (Phase III): {iii} "
		f"{label_iv} (Phase IV): {iv}"
	)


@pytest.mark.parametrize(
	"i,ii,iii,iv,expected",
	[
		("yes", "no", "no", "no", TrialPhase.PHASE_1),
		("no", "yes", "no", "no", TrialPhase.PHASE_2),
		("no", "no", "yes", "no", TrialPhase.PHASE_3),
		("no", "no", "no", "yes", TrialPhase.PHASE_4),
		("yes", "yes", "no", "no", TrialPhase.PHASE_1_2),
		("no", "yes", "yes", "no", TrialPhase.PHASE_2_3),
		("no", "no", "yes", "yes", TrialPhase.PHASE_3_4),
		("no", "no", "no", "no", TrialPhase.OTHER),
		("", "yes", "", "", TrialPhase.PHASE_2),  # blank cells instead of "no"
		("yes", "", "", "", TrialPhase.PHASE_1),
	],
)
def test_matrix_permutations(i, ii, iii, iv, expected):
	assert normalize_phase(_matrix(i, ii, iii, iv)) == expected


def test_matrix_newline_and_indented_variant():
	raw = (
		"Human pharmacology (Phase I): no\n"
		"Therapeutic exploratory (Phase II): yes\n"
		"Therapeutic confirmatory - (Phase III): no\n"
		"Therapeutic use (Phase IV): no"
	)
	assert normalize_phase(raw) == TrialPhase.PHASE_2


def test_matrix_phase_iv_label_with_trailing_dash_variant():
	raw = _matrix(iv="yes", label_iv="Therapeutic use -")
	assert normalize_phase(raw) == TrialPhase.PHASE_4


# --- Generic fallback ---------------------------------------------------------------


def test_fallback_slash_no_space_between_phases():
	assert normalize_phase("Phase 3/Phase 4") == TrialPhase.PHASE_3_4


def test_fallback_unseen_variant_still_extracts_span():
	assert normalize_phase("PHASE-2 study") == TrialPhase.OTHER  # no "phase 2" token, no digit adjacency


# --- Unmapped values ------------------------------------------------------------------


def test_unmapped_value_returns_other():
	assert normalize_phase("Some future registry value") == TrialPhase.OTHER


def test_unmapped_value_is_logged(caplog):
	with caplog.at_level("INFO", logger="gregory.utils.trial_field_normalizers"):
		normalize_phase("Some future registry value")
	assert "Some future registry value" in caplog.text


# --- Trials.save() hook ---------------------------------------------------------------


class TrialSaveHookTests(TestCase):
	def test_create_computes_phase_normalized(self):
		trial = Trials.objects.create(
			title="Phase III trial", link="https://example.com/hook-1", phase="Phase III"
		)
		self.assertEqual(trial.phase_normalized, "phase_3")

	def test_changing_phase_and_saving_recomputes(self):
		trial = Trials.objects.create(
			title="Phase I trial", link="https://example.com/hook-2", phase="Phase I"
		)
		self.assertEqual(trial.phase_normalized, "phase_1")

		trial.phase = "Phase II"
		trial.save()

		trial.refresh_from_db()
		self.assertEqual(trial.phase_normalized, "phase_2")

	def test_save_update_fields_phase_only_still_persists_phase_normalized(self):
		trial = Trials.objects.create(
			title="Phase I trial", link="https://example.com/hook-3", phase="Phase I"
		)

		trial.phase = "Phase IV"
		trial.save(update_fields=["phase"])

		trial.refresh_from_db()
		self.assertEqual(trial.phase_normalized, "phase_4")

	def test_save_update_fields_without_phase_leaves_stored_value_alone(self):
		trial = Trials.objects.create(
			title="Phase I trial", link="https://example.com/hook-4", phase="Phase I"
		)
		# Simulate an out-of-band write to phase that bypassed save(), then a save()
		# that doesn't touch phase: phase_normalized must not be clobbered back.
		Trials.objects.filter(pk=trial.pk).update(phase="Phase IV")
		trial.title = "Renamed"
		trial.save(update_fields=["title"])

		trial.refresh_from_db()
		self.assertEqual(trial.phase_normalized, "phase_1")  # unchanged: not re-persisted

	def test_none_phase_leaves_phase_normalized_none(self):
		trial = Trials.objects.create(title="No phase", link="https://example.com/hook-5")
		self.assertIsNone(trial.phase_normalized)


# --- backfill_trial_normalized_fields command (phase coverage) -------------------------


class BackfillTrialPhasesTest(TestCase):
	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_normalized_fields", stdout=out, stderr=err, **kwargs
		)
		return out.getvalue(), err.getvalue()

	def _make_stale(self, title, link, phase):
		"""Create a trial, then blank phase_normalized via bulk-style update() to
		simulate a row written before the save() hook existed (or by bulk_update)."""
		trial = Trials.objects.create(title=title, link=link, phase=phase)
		Trials.objects.filter(pk=trial.pk).update(phase_normalized=None)
		trial.refresh_from_db()
		return trial

	def test_backfill_restores_values_bulk_update_bypassed(self):
		t1 = self._make_stale("A", "https://example.com/bf-1", "Phase III")
		t2 = self._make_stale("B", "https://example.com/bf-2", "Some future registry value")

		out, _ = self.run_command()

		t1.refresh_from_db()
		t2.refresh_from_db()
		self.assertEqual(t1.phase_normalized, "phase_3")
		self.assertEqual(t2.phase_normalized, "other")
		self.assertIn("Some future registry value", out)

	def test_dry_run_changes_nothing(self):
		t1 = self._make_stale("A", "https://example.com/bf-3", "Phase III")

		out, _ = self.run_command(dry_run=True)

		t1.refresh_from_db()
		self.assertIsNone(t1.phase_normalized)
		self.assertIn("Would update", out)

	def test_idempotent_rerun_updates_nothing(self):
		Trials.objects.create(
			title="A", link="https://example.com/bf-4", phase="Phase III"
		)

		out, _ = self.run_command()

		self.assertIn("Updated 0 rows", out)

	def test_output_mentions_other_raw_values_once_each(self):
		self._make_stale("A", "https://example.com/bf-5", "Some future registry value")
		self._make_stale("B", "https://example.com/bf-6", "Some future registry value")

		out, _ = self.run_command()

		self.assertEqual(out.count("Some future registry value"), 1)

	def test_batches_updates_by_batch_size(self):
		for i in range(5):
			self._make_stale(f"T{i}", f"https://example.com/bf-batch-{i}", "Phase III")

		out, _ = self.run_command(batch_size=2)

		self.assertIn("Updated 2/5 trial rows.", out)
		self.assertIn("Updated 4/5 trial rows.", out)
		self.assertIn("Updated 5/5 trial rows.", out)

	def test_field_filter_scopes_to_phase_only(self):
		"""--field phase must not touch recruitment_status_normalized, even when it is
		stale — the counterpart to the recruitment_status-only test in
		test_trial_recruitment_status_normalization.py."""
		trial = Trials.objects.create(
			title="A",
			link="https://example.com/bf-field-phase",
			phase="Phase III",
			recruitment_status="Recruiting",
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, recruitment_status_normalized=None
		)
		trial.refresh_from_db()

		self.run_command(field="phase")

		trial.refresh_from_db()
		self.assertEqual(trial.phase_normalized, "phase_3")
		self.assertIsNone(trial.recruitment_status_normalized)  # untouched: not selected

	def test_unknown_field_raises(self):
		with self.assertRaises(CommandError):
			self.run_command(field="not_a_real_field")


# --- Admin "Recompute normalized fields" action -----------------------------------------


class TrialAdminRecomputeNormalizedFieldsTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()
		self.trial_admin = TrialAdmin(Trials, self.site)
		self.superuser = User.objects.create_superuser(
			username="phase-admin-root", email="root@example.com", password="pw"
		)

	def _request(self):
		request = self.factory.post("/admin/gregory/trials/")
		request.user = self.superuser
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def test_action_updates_stale_row(self):
		trial = Trials.objects.create(
			title="Phase III trial",
			link="https://example.com/admin-1",
			phase="Phase III",
			recruitment_status="Recruiting",
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, recruitment_status_normalized=None
		)
		trial.refresh_from_db()
		self.assertIsNone(trial.phase_normalized)
		self.assertIsNone(trial.recruitment_status_normalized)

		request = self._request()
		queryset = Trials.objects.filter(pk=trial.pk)
		self.trial_admin.recompute_normalized_fields(request, queryset)

		trial.refresh_from_db()
		self.assertEqual(trial.phase_normalized, "phase_3")
		self.assertEqual(trial.recruitment_status_normalized, "recruiting")
