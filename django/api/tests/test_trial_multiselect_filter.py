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


class TrialPhaseNormalizedMultiSelectFilterTests(TestCase):
	"""Tests for the comma-separated OR filter on ?phase_normalized= for /trials/"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(
			name="Phase MS Org", slug="phase-ms-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Phase MS Team", slug="phase-ms-team", organization=self.org
		)
		self.subject = Subject.objects.create(
			subject_name="Phase MS Subject",
			subject_slug="phase-ms-subject",
			team=self.team,
		)

		# raw "phase" text drives phase_normalized via Trials.save()
		self.trial_phase_2 = Trials.objects.create(
			title="Trial Phase 2",
			link="https://example.com/trial-phase-2",
			published_date=timezone.now(),
			phase="Phase 2",
		)
		self.trial_phase_2.subjects.add(self.subject)
		self.trial_phase_2.teams.add(self.team)

		self.trial_phase_3 = Trials.objects.create(
			title="Trial Phase 3",
			link="https://example.com/trial-phase-3",
			published_date=timezone.now(),
			phase="Phase 3",
		)
		self.trial_phase_3.subjects.add(self.subject)
		self.trial_phase_3.teams.add(self.team)

		self.trial_phase_4 = Trials.objects.create(
			title="Trial Phase 4",
			link="https://example.com/trial-phase-4",
			published_date=timezone.now(),
			phase="Phase 4",
		)
		self.trial_phase_4.subjects.add(self.subject)
		self.trial_phase_4.teams.add(self.team)

		# unrelated subject, used for the AND-composition test
		self.other_subject = Subject.objects.create(
			subject_name="Phase MS Other Subject",
			subject_slug="phase-ms-other-subject",
			team=self.team,
		)

	def test_single_value_still_works(self):
		"""?phase_normalized=phase_2 behaves exactly as before (regression)."""
		response = self.client.get("/trials/?phase_normalized=phase_2")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertEqual(ids, {self.trial_phase_2.trial_id})

	def test_comma_separated_returns_union(self):
		"""?phase_normalized=phase_2,phase_3 returns the union of both phases."""
		response = self.client.get(
			"/trials/?phase_normalized=phase_2,phase_3"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertEqual(
			ids, {self.trial_phase_2.trial_id, self.trial_phase_3.trial_id}
		)
		self.assertNotIn(self.trial_phase_4.trial_id, ids)

	def test_no_match_returns_empty(self):
		"""A phase value present in choices but with no matching trial returns empty."""
		response = self.client.get("/trials/?phase_normalized=phase_1")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	def test_combined_with_subject_id_ands_correctly(self):
		"""?phase_normalized=phase_2,phase_3&subject_id=X ANDs with the subject filter."""
		url = (
			f"/trials/?phase_normalized=phase_2,phase_3&subject_id={self.other_subject.id}"
		)
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

		url_matching = (
			f"/trials/?phase_normalized=phase_2,phase_3&subject_id={self.subject.id}"
		)
		response_matching = self.client.get(url_matching)
		self.assertEqual(response_matching.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response_matching.data["results"]}
		self.assertEqual(
			ids, {self.trial_phase_2.trial_id, self.trial_phase_3.trial_id}
		)


class TrialRecruitmentStatusNormalizedMultiSelectFilterTests(TestCase):
	"""Tests for the comma-separated OR filter on ?recruitment_status_normalized= for /trials/"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(
			name="Status MS Org", slug="status-ms-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Status MS Team", slug="status-ms-team", organization=self.org
		)
		self.subject = Subject.objects.create(
			subject_name="Status MS Subject",
			subject_slug="status-ms-subject",
			team=self.team,
		)

		self.trial_recruiting = Trials.objects.create(
			title="Trial Recruiting",
			link="https://example.com/trial-recruiting",
			published_date=timezone.now(),
			recruitment_status="recruiting",
		)
		self.trial_recruiting.subjects.add(self.subject)
		self.trial_recruiting.teams.add(self.team)

		self.trial_active = Trials.objects.create(
			title="Trial Active Not Recruiting",
			link="https://example.com/trial-active",
			published_date=timezone.now(),
			recruitment_status="active_not_recruiting",
		)
		self.trial_active.subjects.add(self.subject)
		self.trial_active.teams.add(self.team)

		self.trial_completed = Trials.objects.create(
			title="Trial Completed",
			link="https://example.com/trial-completed",
			published_date=timezone.now(),
			recruitment_status="completed",
		)
		self.trial_completed.subjects.add(self.subject)
		self.trial_completed.teams.add(self.team)

	def test_single_value_still_works(self):
		"""?recruitment_status_normalized=recruiting behaves exactly as before (regression)."""
		response = self.client.get(
			"/trials/?recruitment_status_normalized=recruiting"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertEqual(ids, {self.trial_recruiting.trial_id})

	def test_comma_separated_returns_union(self):
		"""?recruitment_status_normalized=recruiting,active_not_recruiting returns the union."""
		response = self.client.get(
			"/trials/?recruitment_status_normalized=recruiting,active_not_recruiting"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["trial_id"] for r in response.data["results"]}
		self.assertEqual(
			ids, {self.trial_recruiting.trial_id, self.trial_active.trial_id}
		)
		self.assertNotIn(self.trial_completed.trial_id, ids)

	def test_no_match_returns_empty(self):
		"""A status value present in choices but with no matching trial returns empty."""
		response = self.client.get(
			"/trials/?recruitment_status_normalized=withdrawn"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)
