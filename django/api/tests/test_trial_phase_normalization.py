"""API-level tests for the phase_normalized filter/field on /trials/.

Fixture/org-visibility setup mirrors api/tests/test_enhanced_filters.py and
api/tests/test_trial_search.py — a public organization is required for the
default (anonymous) APIClient to see any trials at all.
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


class TrialPhaseNormalizedFilterTests(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Phase Filter Org", slug="phase-filter-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Phase Filter Team",
			slug="phase-filter-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Phase Filter Subject",
			subject_slug="phase-filter-subject",
			team=self.team,
		)

		self.phase3_trial = Trials.objects.create(
			title="Phase III Trial",
			link="https://example.com/phase-filter-3",
			published_date=timezone.now(),
			phase="Phase III",
		)
		self.phase3_trial.teams.add(self.team)
		self.phase3_trial.subjects.add(self.subject)

		self.phase2_trial = Trials.objects.create(
			title="Phase II Trial",
			link="https://example.com/phase-filter-2",
			published_date=timezone.now(),
			phase="Phase II",
		)
		self.phase2_trial.teams.add(self.team)
		self.phase2_trial.subjects.add(self.subject)

		self.client = APIClient()

	def test_phase_normalized_filter_returns_only_matching_trials(self):
		response = self.client.get("/trials/?phase_normalized=phase_3")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.phase3_trial.trial_id
		)

	def test_phase_normalized_invalid_choice_errors_cleanly(self):
		response = self.client.get("/trials/?phase_normalized=not-a-real-phase")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_response_body_includes_phase_normalized(self):
		response = self.client.get(f"/trials/?trial_id={self.phase3_trial.trial_id}")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data["results"][0]["phase_normalized"], "phase_3"
		)
		self.assertEqual(
			response.data["results"][0]["phase"], "Phase III"
		)  # raw value untouched

	def test_phase_normalized_field_is_read_only_on_serializer(self):
		"""editable=False on the model field makes DRF mark it read-only automatically,
		so it can never be set via a POST body."""
		serializer = TrialSerializer()
		self.assertTrue(serializer.fields["phase_normalized"].read_only)
