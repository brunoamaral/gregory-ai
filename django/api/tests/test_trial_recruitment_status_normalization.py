"""API-level tests for the recruitment_status_normalized filter/field on /trials/.
Mirrors api/tests/test_trial_phase_normalization.py.

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


class TrialRecruitmentStatusNormalizedFilterTests(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Recruitment Status Filter Org", slug="recruitment-status-filter-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Recruitment Status Filter Team",
			slug="recruitment-status-filter-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Recruitment Status Filter Subject",
			subject_slug="recruitment-status-filter-subject",
			team=self.team,
		)

		self.recruiting_trial = Trials.objects.create(
			title="Recruiting Trial",
			link="https://example.com/recruitment-status-filter-recruiting",
			published_date=timezone.now(),
			recruitment_status="Recruiting",
		)
		self.recruiting_trial.teams.add(self.team)
		self.recruiting_trial.subjects.add(self.subject)

		self.completed_trial = Trials.objects.create(
			title="Completed Trial",
			link="https://example.com/recruitment-status-filter-completed",
			published_date=timezone.now(),
			recruitment_status="Completed",
		)
		self.completed_trial.teams.add(self.team)
		self.completed_trial.subjects.add(self.subject)

		self.client = APIClient()

	def test_recruitment_status_normalized_filter_returns_only_matching_trials(self):
		response = self.client.get("/trials/?recruitment_status_normalized=recruiting")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.recruiting_trial.trial_id
		)

	def test_recruitment_status_normalized_invalid_choice_errors_cleanly(self):
		response = self.client.get(
			"/trials/?recruitment_status_normalized=not-a-real-status"
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_response_body_includes_recruitment_status_normalized(self):
		response = self.client.get(f"/trials/?trial_id={self.recruiting_trial.trial_id}")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data["results"][0]["recruitment_status_normalized"], "recruiting"
		)
		self.assertEqual(
			response.data["results"][0]["recruitment_status"], "Recruiting"
		)  # raw value untouched

	def test_recruitment_status_normalized_field_is_read_only_on_serializer(self):
		"""editable=False on the model field makes DRF mark it read-only automatically,
		so it can never be set via a POST body."""
		serializer = TrialSerializer()
		self.assertTrue(
			serializer.fields["recruitment_status_normalized"].read_only
		)

	def test_raw_recruitment_status_and_status_filters_remain_iexact(self):
		"""Existing raw-text filters (status/recruitment_status) must be unaffected by
		the new normalized filter — they still do a case-insensitive exact match
		against the raw column."""
		response = self.client.get("/trials/?recruitment_status=recruiting")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.recruiting_trial.trial_id
		)

		response = self.client.get("/trials/?status=COMPLETED")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.completed_trial.trial_id
		)
