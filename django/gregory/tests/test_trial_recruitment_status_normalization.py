"""Tests for gregory.utils.trial_field_normalizers.normalize_recruitment_status and the
Trials.save() hook that keeps recruitment_status_normalized in lockstep. Mirrors the
structure of test_trial_phase_normalization.py.

The generalized backfill_trial_normalized_fields command and the admin "Recompute
normalized fields" action are exercised in test_trial_phase_normalization.py (phase
coverage) plus the recruitment_status-scoped tests below.

See docs/trials-field-normalization.md for the design and the three judgment calls behind
the mapping table (WHO "Not recruiting" gets its own bucket, "Authorised" -> unknown,
expanded-access statuses -> other).
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
	TrialRecruitmentStatus,
	normalize_recruitment_status,
)

User = get_user_model()

# Every distinct raw value in gregory.utils.trial_field_normalizers.
# _RECRUITMENT_STATUS_EXACT_MATCHES, transcribed independently so this test actually
# catches drift in the module rather than restating it.
EXACT_MATCH_CASES = [
	# Not yet recruiting
	("not_yet_recruiting", TrialRecruitmentStatus.NOT_YET_RECRUITING),
	("authorised, recruitment pending", TrialRecruitmentStatus.NOT_YET_RECRUITING),
	# Recruiting
	("recruiting", TrialRecruitmentStatus.RECRUITING),
	("ongoing, recruiting", TrialRecruitmentStatus.RECRUITING),
	("authorised, recruiting", TrialRecruitmentStatus.RECRUITING),
	# Enrolling by invitation
	("enrolling_by_invitation", TrialRecruitmentStatus.ENROLLING_BY_INVITATION),
	# Active, not recruiting
	("active_not_recruiting", TrialRecruitmentStatus.ACTIVE_NOT_RECRUITING),
	("ongoing, recruitment ended", TrialRecruitmentStatus.ACTIVE_NOT_RECRUITING),
	# Not recruiting (WHO's generic status — its own bucket, see module docstring)
	("not recruiting", TrialRecruitmentStatus.NOT_RECRUITING),
	# Suspended
	("suspended", TrialRecruitmentStatus.SUSPENDED),
	("temporarily halted", TrialRecruitmentStatus.SUSPENDED),
	("temporarily_not_available", TrialRecruitmentStatus.SUSPENDED),
	# Completed
	("completed", TrialRecruitmentStatus.COMPLETED),
	("ended", TrialRecruitmentStatus.COMPLETED),
	# Terminated
	("terminated", TrialRecruitmentStatus.TERMINATED),
	# Withdrawn
	("withdrawn", TrialRecruitmentStatus.WITHDRAWN),
	# Unknown
	("unknown", TrialRecruitmentStatus.UNKNOWN),
	("authorised", TrialRecruitmentStatus.UNKNOWN),
	("not available", TrialRecruitmentStatus.UNKNOWN),
	# Other: expanded-access program statuses, not trial recruitment states
	("available", TrialRecruitmentStatus.OTHER),
	("no_longer_available", TrialRecruitmentStatus.OTHER),
	("approved_for_marketing", TrialRecruitmentStatus.OTHER),
]


@pytest.mark.parametrize("raw,expected", EXACT_MATCH_CASES)
def test_exact_match_table(raw, expected):
	assert normalize_recruitment_status(raw) == expected


@pytest.mark.parametrize("raw,expected", EXACT_MATCH_CASES)
def test_exact_match_table_is_case_and_whitespace_insensitive(raw, expected):
	"""Uppercasing and wrapping in stray whitespace must not change the result."""
	noisy = f"  {raw.upper()}  \n"
	assert normalize_recruitment_status(noisy) == expected


@pytest.mark.parametrize(
	"raw,expected",
	[
		# Original registry casings, transcribed verbatim rather than derived from the
		# table above, so this test independently exercises casefold() collapsing them.
		("Not Recruiting", TrialRecruitmentStatus.NOT_RECRUITING),
		("Not recruiting", TrialRecruitmentStatus.NOT_RECRUITING),
		("RECRUITING", TrialRecruitmentStatus.RECRUITING),
		("Recruiting", TrialRecruitmentStatus.RECRUITING),
		("Ongoing, recruitment ended", TrialRecruitmentStatus.ACTIVE_NOT_RECRUITING),
		("Authorised", TrialRecruitmentStatus.UNKNOWN),
		("TEMPORARILY_NOT_AVAILABLE", TrialRecruitmentStatus.SUSPENDED),
		("APPROVED_FOR_MARKETING", TrialRecruitmentStatus.OTHER),
	],
)
def test_original_registry_casings(raw, expected):
	assert normalize_recruitment_status(raw) == expected


def test_none_returns_none():
	assert normalize_recruitment_status(None) is None


def test_empty_string_returns_none():
	assert normalize_recruitment_status("") is None


def test_whitespace_only_returns_none():
	assert normalize_recruitment_status("   ") is None
	assert normalize_recruitment_status("\n\t ") is None


def test_double_space_variant_collapses_to_match_table():
	assert (
		normalize_recruitment_status("Ongoing,  recruitment ended")
		== TrialRecruitmentStatus.ACTIVE_NOT_RECRUITING
	)


# --- Unmapped values ------------------------------------------------------------------


def test_unmapped_value_returns_other():
	assert normalize_recruitment_status("Some future registry status") == TrialRecruitmentStatus.OTHER


def test_unmapped_value_is_logged(caplog):
	with caplog.at_level("INFO", logger="gregory.utils.trial_field_normalizers"):
		normalize_recruitment_status("Some future registry status")
	assert "Some future registry status" in caplog.text


def test_no_generic_token_fallback():
	"""Unlike normalize_phase, there is no regex fallback here — a near-miss spelling
	that isn't in the exact-match table must land in OTHER, not get guessed at."""
	assert normalize_recruitment_status("Recruiting now") == TrialRecruitmentStatus.OTHER
	assert normalize_recruitment_status("Status: Recruiting") == TrialRecruitmentStatus.OTHER


# --- Trials.save() hook ---------------------------------------------------------------


class TrialSaveHookTests(TestCase):
	def test_create_computes_recruitment_status_normalized(self):
		trial = Trials.objects.create(
			title="Recruiting trial",
			link="https://example.com/rs-hook-1",
			recruitment_status="Recruiting",
		)
		self.assertEqual(trial.recruitment_status_normalized, "recruiting")

	def test_changing_recruitment_status_and_saving_recomputes(self):
		trial = Trials.objects.create(
			title="Recruiting trial",
			link="https://example.com/rs-hook-2",
			recruitment_status="Recruiting",
		)
		self.assertEqual(trial.recruitment_status_normalized, "recruiting")

		trial.recruitment_status = "Completed"
		trial.save()

		trial.refresh_from_db()
		self.assertEqual(trial.recruitment_status_normalized, "completed")

	def test_save_update_fields_recruitment_status_only_still_persists_derived_field(self):
		trial = Trials.objects.create(
			title="Recruiting trial",
			link="https://example.com/rs-hook-3",
			recruitment_status="Recruiting",
		)

		trial.recruitment_status = "Withdrawn"
		trial.save(update_fields=["recruitment_status"])

		trial.refresh_from_db()
		self.assertEqual(trial.recruitment_status_normalized, "withdrawn")

	def test_save_update_fields_without_recruitment_status_leaves_stored_value_alone(self):
		trial = Trials.objects.create(
			title="Recruiting trial",
			link="https://example.com/rs-hook-4",
			recruitment_status="Recruiting",
		)
		# Simulate an out-of-band write that bypassed save(), then a save() that
		# doesn't touch recruitment_status: recruitment_status_normalized must not be
		# clobbered back.
		Trials.objects.filter(pk=trial.pk).update(recruitment_status="Withdrawn")
		trial.title = "Renamed"
		trial.save(update_fields=["title"])

		trial.refresh_from_db()
		self.assertEqual(trial.recruitment_status_normalized, "recruiting")  # unchanged

	def test_none_recruitment_status_leaves_normalized_none(self):
		trial = Trials.objects.create(
			title="No status", link="https://example.com/rs-hook-5"
		)
		self.assertIsNone(trial.recruitment_status_normalized)

	def test_save_recomputes_both_phase_and_recruitment_status_together(self):
		"""Sanity check that the NORMALIZED_TRIAL_FIELDS loop in Trials.save() handles
		both registered fields on the same write, not just whichever was touched."""
		trial = Trials.objects.create(
			title="Both fields",
			link="https://example.com/rs-hook-6",
			phase="Phase II",
			recruitment_status="Terminated",
		)
		self.assertEqual(trial.phase_normalized, "phase_2")
		self.assertEqual(trial.recruitment_status_normalized, "terminated")


# --- backfill_trial_normalized_fields command (recruitment_status coverage) ------------


class BackfillTrialRecruitmentStatusTest(TestCase):
	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_normalized_fields", stdout=out, stderr=err, **kwargs
		)
		return out.getvalue(), err.getvalue()

	def _make_stale(self, title, link, recruitment_status):
		"""Create a trial, then blank recruitment_status_normalized via bulk-style
		update() to simulate a row written before the save() hook existed (or by
		bulk_update)."""
		trial = Trials.objects.create(
			title=title, link=link, recruitment_status=recruitment_status
		)
		Trials.objects.filter(pk=trial.pk).update(recruitment_status_normalized=None)
		trial.refresh_from_db()
		return trial

	def test_backfill_restores_values_bulk_update_bypassed(self):
		t1 = self._make_stale("A", "https://example.com/rs-bf-1", "Recruiting")
		t2 = self._make_stale("B", "https://example.com/rs-bf-2", "Some future registry status")

		out, _ = self.run_command(field="recruitment_status")

		t1.refresh_from_db()
		t2.refresh_from_db()
		self.assertEqual(t1.recruitment_status_normalized, "recruiting")
		self.assertEqual(t2.recruitment_status_normalized, "other")
		self.assertIn("Some future registry status", out)

	def test_dry_run_changes_nothing(self):
		t1 = self._make_stale("A", "https://example.com/rs-bf-3", "Recruiting")

		out, _ = self.run_command(field="recruitment_status", dry_run=True)

		t1.refresh_from_db()
		self.assertIsNone(t1.recruitment_status_normalized)
		self.assertIn("Would update", out)

	def test_idempotent_rerun_updates_nothing(self):
		Trials.objects.create(
			title="A", link="https://example.com/rs-bf-4", recruitment_status="Recruiting"
		)

		out, _ = self.run_command(field="recruitment_status")

		self.assertIn("Updated 0 rows", out)

	def test_field_filter_scopes_to_recruitment_status_only(self):
		"""--field recruitment_status must not touch phase_normalized, even when it is
		stale — the counterpart to the phase-only test in
		test_trial_phase_normalization.py."""
		trial = Trials.objects.create(
			title="A",
			link="https://example.com/rs-bf-field",
			phase="Phase III",
			recruitment_status="Recruiting",
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, recruitment_status_normalized=None
		)
		trial.refresh_from_db()

		self.run_command(field="recruitment_status")

		trial.refresh_from_db()
		self.assertEqual(trial.recruitment_status_normalized, "recruiting")
		self.assertIsNone(trial.phase_normalized)  # untouched: not selected

	def test_default_scope_backfills_both_fields_in_one_pass(self):
		"""Omitting --field entirely covers every registered field, not just one."""
		trial = Trials.objects.create(
			title="A",
			link="https://example.com/rs-bf-both",
			phase="Phase III",
			recruitment_status="Recruiting",
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, recruitment_status_normalized=None
		)
		trial.refresh_from_db()

		self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.phase_normalized, "phase_3")
		self.assertEqual(trial.recruitment_status_normalized, "recruiting")

	def test_comma_separated_field_option_accepted(self):
		trial = Trials.objects.create(
			title="A",
			link="https://example.com/rs-bf-comma",
			phase="Phase III",
			recruitment_status="Recruiting",
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, recruitment_status_normalized=None
		)
		trial.refresh_from_db()

		self.run_command(field="phase,recruitment_status")

		trial.refresh_from_db()
		self.assertEqual(trial.phase_normalized, "phase_3")
		self.assertEqual(trial.recruitment_status_normalized, "recruiting")


# --- Admin "Recompute normalized fields" action (recruitment_status coverage) ----------


class TrialAdminRecomputeRecruitmentStatusNormalizedTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()
		self.trial_admin = TrialAdmin(Trials, self.site)
		self.superuser = User.objects.create_superuser(
			username="recruitment-status-admin-root", email="root2@example.com", password="pw"
		)

	def _request(self):
		request = self.factory.post("/admin/gregory/trials/")
		request.user = self.superuser
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def test_action_updates_stale_recruitment_status_normalized(self):
		trial = Trials.objects.create(
			title="Terminated trial",
			link="https://example.com/rs-admin-1",
			recruitment_status="Terminated",
		)
		Trials.objects.filter(pk=trial.pk).update(recruitment_status_normalized=None)
		trial.refresh_from_db()
		self.assertIsNone(trial.recruitment_status_normalized)

		request = self._request()
		queryset = Trials.objects.filter(pk=trial.pk)
		self.trial_admin.recompute_normalized_fields(request, queryset)

		trial.refresh_from_db()
		self.assertEqual(trial.recruitment_status_normalized, "terminated")
