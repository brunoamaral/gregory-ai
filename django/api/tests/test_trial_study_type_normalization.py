"""API-level tests for the study_type_normalized filter/field on /trials/. Mirrors
api/tests/test_trial_recruitment_status_normalization.py.

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


class TrialStudyTypeNormalizedFilterTests(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Study Type Filter Org", slug="study-type-filter-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Study Type Filter Team",
			slug="study-type-filter-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Study Type Filter Subject",
			subject_slug="study-type-filter-subject",
			team=self.team,
		)

		self.interventional_trial = Trials.objects.create(
			title="Interventional Trial",
			link="https://example.com/study-type-filter-interventional",
			published_date=timezone.now(),
			study_type="INTERVENTIONAL",
		)
		self.interventional_trial.teams.add(self.team)
		self.interventional_trial.subjects.add(self.subject)

		self.observational_trial = Trials.objects.create(
			title="Observational Trial",
			link="https://example.com/study-type-filter-observational",
			published_date=timezone.now(),
			study_type="OBSERVATIONAL",
		)
		self.observational_trial.teams.add(self.team)
		self.observational_trial.subjects.add(self.subject)

		self.client = APIClient()

	def test_study_type_normalized_filter_returns_only_matching_trials(self):
		response = self.client.get("/trials/?study_type_normalized=interventional")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.interventional_trial.trial_id
		)

	def test_study_type_normalized_invalid_choice_errors_cleanly(self):
		response = self.client.get("/trials/?study_type_normalized=not-a-real-type")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_response_body_includes_study_type_normalized(self):
		response = self.client.get(
			f"/trials/?trial_id={self.interventional_trial.trial_id}"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data["results"][0]["study_type_normalized"], "interventional"
		)
		self.assertEqual(
			response.data["results"][0]["study_type"], "INTERVENTIONAL"
		)  # raw value untouched

	def test_study_type_normalized_field_is_read_only_on_serializer(self):
		"""editable=False on the model field makes DRF mark it read-only automatically,
		so it can never be set via a POST body."""
		serializer = TrialSerializer()
		self.assertTrue(serializer.fields["study_type_normalized"].read_only)

	def test_raw_study_type_filter_remains_icontains(self):
		"""The existing raw-text study_type filter must be unaffected by the new
		normalized filter — it still does a case-insensitive substring match against the
		raw column."""
		response = self.client.get("/trials/?study_type=interventional")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.interventional_trial.trial_id
		)
