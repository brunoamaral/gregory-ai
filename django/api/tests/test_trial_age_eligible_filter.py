from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone

from gregory.models import (
	Trials,
	Subject,
	Team,
	Organization,
	OrganizationApiSettings,
)


class TrialAgeEligibleFilterTests(TestCase):
	"""Tests for the numeric ?age_eligible= range-containment filter on /trials/"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(name="Age Org", slug="age-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Age Team", slug="age-team", organization=self.org
		)
		self.subject = Subject.objects.create(
			subject_name="Age Subject",
			subject_slug="age-subject",
			team=self.team,
		)
		self.other_subject = Subject.objects.create(
			subject_name="Age Other Subject",
			subject_slug="age-other-subject",
			team=self.team,
		)

		# min 18 / max 65 -> includes 40
		self.trial_18_65 = Trials.objects.create(
			title="Trial 18-65",
			link="https://example.com/trial-18-65",
			published_date=timezone.now(),
			inclusion_agemin="18 Years",
			inclusion_agemax="65 Years",
		)
		self.trial_18_65.subjects.add(self.subject)
		self.trial_18_65.teams.add(self.team)

		# min 18 / max null (open-ended) -> includes 40
		self.trial_18_open = Trials.objects.create(
			title="Trial 18-open",
			link="https://example.com/trial-18-open",
			published_date=timezone.now(),
			inclusion_agemin="18 Years",
			inclusion_agemax="No limit",
		)
		self.trial_18_open.subjects.add(self.subject)
		self.trial_18_open.teams.add(self.team)

		# both bounds null -> includes 40
		self.trial_open_open = Trials.objects.create(
			title="Trial open-open",
			link="https://example.com/trial-open-open",
			published_date=timezone.now(),
			inclusion_agemin="N/A",
			inclusion_agemax="N/A",
		)
		self.trial_open_open.subjects.add(self.subject)
		self.trial_open_open.teams.add(self.team)

		# min 50 / max null -> excludes 40
		self.trial_50_open = Trials.objects.create(
			title="Trial 50-open",
			link="https://example.com/trial-50-open",
			published_date=timezone.now(),
			inclusion_agemin="50 Years",
			inclusion_agemax="No limit",
		)
		self.trial_50_open.subjects.add(self.subject)
		self.trial_50_open.teams.add(self.team)

	def test_age_eligible_includes_range_with_both_bounds(self):
		response = self.client.get("/trials/?age_eligible=40")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertIn(self.trial_18_65.trial_id, ids)

	def test_age_eligible_includes_null_max(self):
		response = self.client.get("/trials/?age_eligible=40")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertIn(self.trial_18_open.trial_id, ids)

	def test_age_eligible_includes_both_bounds_null(self):
		response = self.client.get("/trials/?age_eligible=40")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertIn(self.trial_open_open.trial_id, ids)

	def test_age_eligible_excludes_trial_with_higher_min(self):
		response = self.client.get("/trials/?age_eligible=40")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertNotIn(self.trial_50_open.trial_id, ids)

	def test_age_eligible_matches_trial_with_higher_min_at_its_own_boundary(self):
		response = self.client.get("/trials/?age_eligible=50")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertIn(self.trial_50_open.trial_id, ids)

	def test_age_eligible_combined_with_subject_id_ands_correctly(self):
		url = f"/trials/?age_eligible=40&subject_id={self.other_subject.id}"
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

		url_matching = f"/trials/?age_eligible=40&subject_id={self.subject.id}"
		response_matching = self.client.get(url_matching)
		self.assertEqual(response_matching.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response_matching.data["results"]}
		self.assertEqual(
			ids,
			{
				self.trial_18_65.trial_id,
				self.trial_18_open.trial_id,
				self.trial_open_open.trial_id,
			},
		)

	def test_no_value_returns_unfiltered(self):
		response = self.client.get("/trials/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertIn(self.trial_50_open.trial_id, ids)
