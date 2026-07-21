"""Tests for gregory.utils.trial_field_normalizers.normalize_age and the Trials.save()
hook that keeps inclusion_age_min_years/inclusion_age_max_years in lockstep. Mirrors the
structure of test_trial_inclusion_gender_normalization.py.

See docs/trials-field-normalization.md, "age" section, for the raw-value inventory
(inclusion_agemin/inclusion_agemax) behind the parser's placeholder list and unit handling.
"""

import pytest
from django.test import TestCase

from gregory.models import Trials
from gregory.utils.trial_field_normalizers import normalize_age

# Every case transcribed independently from the distinct-value inventory in
# docs/trials-field-normalization.md rather than restating the parser's own logic.
PARSE_CASES = [
	# Bare numbers -> years
	("18", 18.0),
	("0", 0.0),
	("40", 40.0),
	# Spaced / unspaced / abbreviated year units
	("18 Years", 18.0),
	("18 years", 18.0),
	("18Years", 18.0),
	("18Y", 18.0),
	("18y", 18.0),
	("1 Year", 1.0),
	("20 Year(s)", 20.0),
	("20years-old", 20.0),
	# Sub-year units convert to a fractional year
	("6 Months", 0.5),
	("3 Months", 0.25),
	("1 Month", 1 / 12),
	("1 Week", 1 / 52),
	("30 Days", 30 / 365),
	("1 Day", 1 / 365),
	# WHO ICTRP comparator-prefixed values parse to the plain number
	(">= 20age old", 20.0),
	("<= 80age old", 80.0),
	("< 80age old", 80.0),
	# Unrecognized trailing word after a number still parses as years
	("44 Pregnancy", 44.0),
	# Placeholders -> None
	(None, None),
	("", None),
	("-", None),
	("--", None),
	("N/A", None),
	("NA", None),
	("None", None),
	("no limit", None),
	("No limit", None),
	("Not applicable", None),
	("Not stated", None),
	("Not specified", None),
	# "no limit" as a substring of a corrupted/garbage value -> still None
	("-2147483648 No limit", None),
	("0 N/A (No limit)", None),
	# Unparseable garbage -> None
	("banana", None),
	# Sanity cap: 0-120 years inclusive
	("120 Years", 120.0),
	("121 Years", None),
	("200 Years", None),
	("149 years", None),
]


@pytest.mark.parametrize("raw,expected", PARSE_CASES)
def test_normalize_age_parses_expected_value(raw, expected):
	got = normalize_age(raw)
	if expected is None:
		assert got is None
	else:
		assert got == pytest.approx(expected)


def test_whitespace_and_case_insensitive():
	assert normalize_age("  18 YEARS  \n") == pytest.approx(18.0)
	assert normalize_age("  n/a  ") is None


def test_never_raises_on_garbage_input():
	# A defensive smoke test -- normalize_age must not raise regardless of input shape.
	for garbage in ["!!!", "18.5.2 Years", "∞", "18 " * 50]:
		normalize_age(garbage)


# --- Trials.save() hook -------------------------------------------------------------------


class TrialAgeSaveHookTests(TestCase):
	def test_create_computes_both_derived_age_fields(self):
		trial = Trials.objects.create(
			title="Age hook trial",
			link="https://example.com/age-hook-1",
			inclusion_agemin="18 Years",
			inclusion_agemax="65 Years",
		)
		self.assertEqual(trial.inclusion_age_min_years, 18.0)
		self.assertEqual(trial.inclusion_age_max_years, 65.0)

	def test_sub_year_unit_round_trips_as_fraction(self):
		trial = Trials.objects.create(
			title="Pediatric age hook trial",
			link="https://example.com/age-hook-2",
			inclusion_agemin="6 Months",
		)
		self.assertEqual(trial.inclusion_age_min_years, 0.5)

	def test_placeholder_leaves_derived_field_none(self):
		trial = Trials.objects.create(
			title="No age bound trial",
			link="https://example.com/age-hook-3",
			inclusion_agemin="N/A",
			inclusion_agemax="No limit",
		)
		self.assertIsNone(trial.inclusion_age_min_years)
		self.assertIsNone(trial.inclusion_age_max_years)

	def test_changing_raw_age_and_saving_recomputes(self):
		trial = Trials.objects.create(
			title="Age hook trial",
			link="https://example.com/age-hook-4",
			inclusion_agemin="18 Years",
		)
		self.assertEqual(trial.inclusion_age_min_years, 18.0)

		trial.inclusion_agemin = "21 Years"
		trial.save()

		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_age_min_years, 21.0)

	def test_none_inclusion_age_leaves_derived_fields_none(self):
		trial = Trials.objects.create(
			title="No inclusion age", link="https://example.com/age-hook-5"
		)
		self.assertIsNone(trial.inclusion_age_min_years)
		self.assertIsNone(trial.inclusion_age_max_years)
