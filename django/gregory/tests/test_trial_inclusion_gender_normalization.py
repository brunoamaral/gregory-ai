"""Tests for gregory.utils.trial_field_normalizers.normalize_inclusion_gender and the
Trials.save() hook that keeps inclusion_gender_normalized in lockstep. Mirrors the
structure of test_trial_study_type_normalization.py.

See docs/trials-field-normalization.md and INCLUSION-GENDER-NORMALIZATION-PLAN.md for the
design and the judgment calls behind the mapping table (no "other" bucket, placeholders vs.
the contradictory "Female: no Male: no" case, and the "Female, Male" -> all regression
guard that is the whole point of this normalization).
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
	TrialSexEligibility,
	normalize_inclusion_gender,
)

User = get_user_model()

# Every distinct raw value from the 25-row inventory (18 keys after whitespace-collapse +
# casefold) in INCLUSION-GENDER-NORMALIZATION-PLAN.md, transcribed independently so this
# test actually catches drift in the module rather than restating it.
EXACT_MATCH_CASES = [
	# All sexes
	("ALL", TrialSexEligibility.ALL),
	("Both", TrialSexEligibility.ALL),
	("Both males and females", TrialSexEligibility.ALL),
	("Female, Male", TrialSexEligibility.ALL),
	("Male and Female", TrialSexEligibility.ALL),
	("Male/Female", TrialSexEligibility.ALL),
	("Female: yes Male: yes", TrialSexEligibility.ALL),
	# Female only
	("Female", TrialSexEligibility.FEMALE),
	("Females", TrialSexEligibility.FEMALE),
	("F", TrialSexEligibility.FEMALE),
	("Female: yes Male: no", TrialSexEligibility.FEMALE),
	# Male only
	("Male", TrialSexEligibility.MALE),
	("Males", TrialSexEligibility.MALE),
	("Female: no Male: yes", TrialSexEligibility.MALE),
	# Placeholders -> None
	("-", None),
	("--", None),
	("Not Specified", None),
]


@pytest.mark.parametrize("raw,expected", EXACT_MATCH_CASES)
def test_exact_match_table(raw, expected):
	assert normalize_inclusion_gender(raw) == expected


@pytest.mark.parametrize("raw,expected", EXACT_MATCH_CASES)
def test_exact_match_table_is_case_and_whitespace_insensitive(raw, expected):
	"""Uppercasing and wrapping in stray whitespace must not change the result."""
	noisy = f"  {raw.upper()}  \n"
	assert normalize_inclusion_gender(noisy) == expected


# --- HTML variants (EU Clinical Trials Register) ---------------------------------------
# No stripping happens in the normalizer itself (WHO-HTML-CLEANUP-PLAN.md handles that at
# ingest) -- these are expected-dead post-cleanup but asserted so a stale re-import of
# cached XML can't silently regress.


def test_html_matrix_all_no_leading_space():
	assert (
		normalize_inclusion_gender("<br>Female: yes<br>Male: yes<br>")
		== TrialSexEligibility.ALL
	)


def test_html_matrix_all_leading_space_variant():
	assert (
		normalize_inclusion_gender("<br> Female: yes<br> Male: yes<br>")
		== TrialSexEligibility.ALL
	)


def test_html_matrix_female_only():
	assert (
		normalize_inclusion_gender("<br>Female: yes<br>Male: no<br>")
		== TrialSexEligibility.FEMALE
	)


def test_html_matrix_entity_encoded_all():
	"""A double-encoded upstream value can still reach us as "&lt;br&gt;..." even though
	importWHOXML.py's XML parser decodes ordinary entities on the way in."""
	assert (
		normalize_inclusion_gender("&lt;br&gt;Female: yes&lt;br&gt;Male: yes&lt;br&gt;")
		== TrialSexEligibility.ALL
	)


def test_html_matrix_entity_encoded_leading_space_variant():
	assert (
		normalize_inclusion_gender(
			"&lt;br&gt; Female: yes&lt;br&gt; Male: yes&lt;br&gt;"
		)
		== TrialSexEligibility.ALL
	)


def test_html_matrix_entity_encoded_female_only():
	assert (
		normalize_inclusion_gender("&lt;br&gt;Female: yes&lt;br&gt;Male: no&lt;br&gt;")
		== TrialSexEligibility.FEMALE
	)


# --- The regression guard ---------------------------------------------------------------


def test_female_comma_male_is_all_not_female():
	"""This is the bug the whole normalization fixes: substring-matching "female" would
	wrongly classify a both-sexes trial as female-only."""
	assert normalize_inclusion_gender("Female, Male") == TrialSexEligibility.ALL
	assert normalize_inclusion_gender("Female, Male") != TrialSexEligibility.FEMALE


# --- The contradictory "Female: no Male: no" case ---------------------------------------


def test_contradictory_value_returns_none():
	assert normalize_inclusion_gender("Female: no Male: no") is None


def test_contradictory_value_is_logged(caplog):
	with caplog.at_level("INFO", logger="gregory.utils.trial_field_normalizers"):
		normalize_inclusion_gender("Female: no Male: no")
	assert "Female: no Male: no" in caplog.text
	assert "Contradictory" in caplog.text


# --- Empty / placeholder input -----------------------------------------------------------


def test_none_returns_none():
	assert normalize_inclusion_gender(None) is None


def test_empty_string_returns_none():
	assert normalize_inclusion_gender("") is None


def test_whitespace_only_returns_none():
	assert normalize_inclusion_gender("   ") is None
	assert normalize_inclusion_gender("\n\t ") is None


def test_placeholders_are_not_logged(caplog):
	"""Placeholders carry no eligibility signal but aren't a data anomaly -- they should
	not show up in the same review log as a genuinely unmapped value."""
	with caplog.at_level("INFO", logger="gregory.utils.trial_field_normalizers"):
		normalize_inclusion_gender("Not Specified")
	assert caplog.text == ""


def test_double_space_variant_collapses_to_match_table():
	assert (
		normalize_inclusion_gender("Both  males and females") == TrialSexEligibility.ALL
	)


# --- Unmapped values ----------------------------------------------------------------------


def test_unmapped_value_returns_none():
	assert normalize_inclusion_gender("Some future registry sex value") is None


def test_unmapped_value_is_logged(caplog):
	with caplog.at_level("INFO", logger="gregory.utils.trial_field_normalizers"):
		normalize_inclusion_gender("Some future registry sex value")
	assert "Some future registry sex value" in caplog.text


def test_no_generic_token_fallback():
	"""Like normalize_recruitment_status/normalize_study_type, there is no regex
	fallback -- a near-miss spelling that isn't in the exact-match table must land in
	None, not get guessed at."""
	assert normalize_inclusion_gender("Female-ish") is None
	assert normalize_inclusion_gender("Sex: Female") is None


def test_no_other_bucket_exists():
	"""Unlike every other normalized trial field, there is deliberately no OTHER member
	on TrialSexEligibility."""
	assert not hasattr(TrialSexEligibility, "OTHER")
	assert set(TrialSexEligibility.values) == {"all", "female", "male"}


# --- Trials.save() hook -------------------------------------------------------------------


class TrialSaveHookTests(TestCase):
	def test_create_computes_inclusion_gender_normalized(self):
		trial = Trials.objects.create(
			title="Female-only trial",
			link="https://example.com/ig-hook-1",
			inclusion_gender="Female",
		)
		self.assertEqual(trial.inclusion_gender_normalized, "female")

	def test_changing_inclusion_gender_and_saving_recomputes(self):
		trial = Trials.objects.create(
			title="Inclusion gender trial",
			link="https://example.com/ig-hook-2",
			inclusion_gender="Female",
		)
		self.assertEqual(trial.inclusion_gender_normalized, "female")

		trial.inclusion_gender = "Male"
		trial.save()

		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_gender_normalized, "male")

	def test_save_update_fields_inclusion_gender_only_still_persists_derived_field(self):
		trial = Trials.objects.create(
			title="Inclusion gender trial",
			link="https://example.com/ig-hook-3",
			inclusion_gender="Female",
		)

		trial.inclusion_gender = "Both"
		trial.save(update_fields=["inclusion_gender"])

		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_gender_normalized, "all")

	def test_save_update_fields_without_inclusion_gender_leaves_stored_value_alone(self):
		trial = Trials.objects.create(
			title="Inclusion gender trial",
			link="https://example.com/ig-hook-4",
			inclusion_gender="Female",
		)
		# Simulate an out-of-band write that bypassed save(), then a save() that
		# doesn't touch inclusion_gender: inclusion_gender_normalized must not be
		# clobbered back.
		Trials.objects.filter(pk=trial.pk).update(inclusion_gender="Male")
		trial.title = "Renamed"
		trial.save(update_fields=["title"])

		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_gender_normalized, "female")  # unchanged

	def test_none_inclusion_gender_leaves_normalized_none(self):
		trial = Trials.objects.create(
			title="No inclusion gender", link="https://example.com/ig-hook-5"
		)
		self.assertIsNone(trial.inclusion_gender_normalized)

	def test_save_recomputes_phase_and_inclusion_gender_together(self):
		"""Sanity check that the NORMALIZED_TRIAL_FIELDS loop in Trials.save() handles
		every registered field on the same write, not just whichever was touched."""
		trial = Trials.objects.create(
			title="Both fields",
			link="https://example.com/ig-hook-6",
			phase="Phase II",
			inclusion_gender="Male",
		)
		self.assertEqual(trial.phase_normalized, "phase_2")
		self.assertEqual(trial.inclusion_gender_normalized, "male")


# --- backfill_trial_normalized_fields command (inclusion_gender coverage) -----------------


class BackfillTrialInclusionGenderTest(TestCase):
	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_normalized_fields", stdout=out, stderr=err, **kwargs
		)
		return out.getvalue(), err.getvalue()

	def _make_stale(self, title, link, inclusion_gender):
		"""Create a trial, then blank inclusion_gender_normalized via bulk-style update()
		to simulate a row written before the save() hook existed (or by bulk_update)."""
		trial = Trials.objects.create(
			title=title, link=link, inclusion_gender=inclusion_gender
		)
		Trials.objects.filter(pk=trial.pk).update(inclusion_gender_normalized=None)
		trial.refresh_from_db()
		return trial

	def test_backfill_restores_values_bulk_update_bypassed(self):
		t1 = self._make_stale("A", "https://example.com/ig-bf-1", "Female")
		t2 = self._make_stale("B", "https://example.com/ig-bf-2", "Female, Male")

		self.run_command(field="inclusion_gender")

		t1.refresh_from_db()
		t2.refresh_from_db()
		self.assertEqual(t1.inclusion_gender_normalized, "female")
		self.assertEqual(t2.inclusion_gender_normalized, "all")

	def test_dry_run_changes_nothing(self):
		t1 = self._make_stale("A", "https://example.com/ig-bf-3", "Female")

		out, _ = self.run_command(field="inclusion_gender", dry_run=True)

		t1.refresh_from_db()
		self.assertIsNone(t1.inclusion_gender_normalized)
		self.assertIn("Would update", out)

	def test_idempotent_rerun_updates_nothing(self):
		Trials.objects.create(
			title="A", link="https://example.com/ig-bf-4", inclusion_gender="Female"
		)

		out, _ = self.run_command(field="inclusion_gender")

		self.assertIn("Updated 0 rows", out)

	def test_field_filter_scopes_to_inclusion_gender_only(self):
		"""--field inclusion_gender must not touch phase_normalized, even when it is
		stale -- the counterpart to the phase-only test in
		test_trial_phase_normalization.py."""
		trial = Trials.objects.create(
			title="A",
			link="https://example.com/ig-bf-field",
			phase="Phase III",
			inclusion_gender="Female",
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, inclusion_gender_normalized=None
		)
		trial.refresh_from_db()

		self.run_command(field="inclusion_gender")

		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_gender_normalized, "female")
		self.assertIsNone(trial.phase_normalized)  # untouched: not selected

	def test_default_scope_backfills_inclusion_gender_alongside_other_fields(self):
		"""Omitting --field entirely covers every registered field, not just one."""
		trial = Trials.objects.create(
			title="A",
			link="https://example.com/ig-bf-both",
			phase="Phase III",
			inclusion_gender="Female",
		)
		Trials.objects.filter(pk=trial.pk).update(
			phase_normalized=None, inclusion_gender_normalized=None
		)
		trial.refresh_from_db()

		self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.phase_normalized, "phase_3")
		self.assertEqual(trial.inclusion_gender_normalized, "female")


# --- Admin "Recompute normalized fields" action (inclusion_gender coverage) ---------------


class TrialAdminRecomputeInclusionGenderNormalizedTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()
		self.trial_admin = TrialAdmin(Trials, self.site)
		self.superuser = User.objects.create_superuser(
			username="inclusion-gender-admin-root",
			email="root4@example.com",
			password="pw",
		)

	def _request(self):
		request = self.factory.post("/admin/gregory/trials/")
		request.user = self.superuser
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def test_action_updates_stale_inclusion_gender_normalized(self):
		trial = Trials.objects.create(
			title="Male-only trial",
			link="https://example.com/ig-admin-1",
			inclusion_gender="Male",
		)
		Trials.objects.filter(pk=trial.pk).update(inclusion_gender_normalized=None)
		trial.refresh_from_db()
		self.assertIsNone(trial.inclusion_gender_normalized)

		request = self._request()
		queryset = Trials.objects.filter(pk=trial.pk)
		self.trial_admin.recompute_normalized_fields(request, queryset)

		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_gender_normalized, "male")
