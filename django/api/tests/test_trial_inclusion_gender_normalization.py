"""API-level tests for the inclusion_gender_normalized filter/field on /trials/. Mirrors
api/tests/test_trial_study_type_normalization.py.

Also covers the removal of the legacy `inclusion_gender` substring filter (2026-07-20) —
see docs/trials-field-normalization.md and INCLUSION-GENDER-NORMALIZATION-PLAN.md. Unlike
the other legacy filters (which are merely weak), this one returned confidently wrong
results, so it was deleted outright instead of kept as a labeled "legacy" option.

Fixture/org-visibility setup mirrors api/tests/test_enhanced_filters.py and
api/tests/test_trial_search.py — a public organization is required for the default
(anonymous) APIClient to see any trials at all.
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers import TrialSerializer
from gregory.models import (
	Organization,
	OrganizationApiSettings,
	Subject,
	Team,
	Trials,
)


class TrialInclusionGenderNormalizedFilterTests(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Inclusion Gender Filter Org", slug="inclusion-gender-filter-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Inclusion Gender Filter Team",
			slug="inclusion-gender-filter-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Inclusion Gender Filter Subject",
			subject_slug="inclusion-gender-filter-subject",
			team=self.team,
		)

		self.female_trial = Trials.objects.create(
			title="Female Only Trial",
			link="https://example.com/inclusion-gender-filter-female",
			published_date=timezone.now(),
			inclusion_gender="Female",
		)
		self.female_trial.teams.add(self.team)
		self.female_trial.subjects.add(self.subject)

		# Both-sexes trial whose raw string contains "female" as a substring — the exact
		# case the legacy icontains filter got wrong.
		self.both_sexes_trial = Trials.objects.create(
			title="Both Sexes Trial",
			link="https://example.com/inclusion-gender-filter-both",
			published_date=timezone.now(),
			inclusion_gender="Female, Male",
		)
		self.both_sexes_trial.teams.add(self.team)
		self.both_sexes_trial.subjects.add(self.subject)

		self.client = APIClient()

	def test_inclusion_gender_normalized_filter_returns_only_matching_trials(self):
		response = self.client.get("/trials/?inclusion_gender_normalized=female")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.female_trial.trial_id
		)

	def test_both_sexes_trial_excluded_from_female_filter(self):
		"""Regression guard: "Female, Male" must not match ?inclusion_gender_normalized=female
		— that substring-match bug is exactly what this normalization fixes."""
		response = self.client.get("/trials/?inclusion_gender_normalized=female")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		trial_ids = {row["trial_id"] for row in response.data["results"]}
		self.assertNotIn(self.both_sexes_trial.trial_id, trial_ids)

	def test_inclusion_gender_normalized_all_filter_matches_both_sexes_trial(self):
		response = self.client.get("/trials/?inclusion_gender_normalized=all")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.both_sexes_trial.trial_id
		)

	def test_inclusion_gender_normalized_invalid_choice_errors_cleanly(self):
		response = self.client.get("/trials/?inclusion_gender_normalized=not-a-real-value")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_response_body_includes_inclusion_gender_normalized(self):
		response = self.client.get(
			f"/trials/?trial_id={self.female_trial.trial_id}"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data["results"][0]["inclusion_gender_normalized"], "female"
		)
		self.assertEqual(
			response.data["results"][0]["inclusion_gender"], "Female"
		)  # raw value untouched

	def test_inclusion_gender_normalized_field_is_read_only_on_serializer(self):
		"""editable=False on the model field makes DRF mark it read-only automatically,
		so it can never be set via a POST body."""
		serializer = TrialSerializer()
		self.assertTrue(serializer.fields["inclusion_gender_normalized"].read_only)

	def test_legacy_inclusion_gender_filter_is_removed_and_silently_ignored(self):
		"""The old ?inclusion_gender=Female icontains filter must no longer exist as a
		recognised parameter — django-filter ignores unknown params rather than erroring,
		so a request using it must return the *unfiltered* result set (both trials), not
		a 400 and not the old (wrong) filtered behaviour. This is the deliberate breaking
		change documented in docs/trials-field-normalization.md."""
		unfiltered = self.client.get("/trials/")
		filtered_by_legacy_param = self.client.get("/trials/?inclusion_gender=Female")

		self.assertEqual(unfiltered.status_code, status.HTTP_200_OK)
		self.assertEqual(filtered_by_legacy_param.status_code, status.HTTP_200_OK)
		self.assertEqual(
			len(filtered_by_legacy_param.data["results"]),
			len(unfiltered.data["results"]),
		)
		self.assertEqual(len(filtered_by_legacy_param.data["results"]), 2)
