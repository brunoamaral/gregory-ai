from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient
from gregory.models import Trials, Team, Subject, Organization, OrganizationApiSettings
from django.utils import timezone


class TrialSearchViewTests(TestCase):
	"""Test cases for the trial search endpoint"""

	def setUp(self):
		# Create test organization, team and subject
		self.organization = Organization.objects.create(
			name="Test Organization", slug="trial-search-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Test Team", slug="trial-search-team", organization=self.organization
		)
		self.subject = Subject.objects.create(
			subject_name="Test Subject", subject_slug="test-subject", team=self.team
		)

		# Create test data
		self.trial1 = Trials.objects.create(
			title="COVID-19 Vaccine Trial",
			summary="Testing efficacy of mRNA vaccines for coronavirus.",
			link="https://example.com/trial1",
			published_date=timezone.now(),
			recruitment_status="Recruiting",
		)
		self.trial1.teams.add(self.team)
		self.trial1.subjects.add(self.subject)

		self.trial2 = Trials.objects.create(
			title="Multiple Sclerosis Treatment Trial",
			summary="New MS medication phase 3 study.",
			link="https://example.com/trial2",
			published_date=timezone.now(),
			recruitment_status="Active, not recruiting",
		)
		self.trial2.teams.add(self.team)
		self.trial2.subjects.add(self.subject)

		self.trial3 = Trials.objects.create(
			title="Cancer Research Protocol 2025",
			summary="Breakthrough in COVID-19 related cancer treatment.",
			link="https://example.com/trial3",
			published_date=timezone.now(),
			recruitment_status="Completed",
		)
		self.trial3.teams.add(self.team)
		self.trial3.subjects.add(self.subject)

		self.client = APIClient()

	def test_missing_required_parameters(self):
		"""Test that team_id and subject_id are required"""
		url = reverse("trial-search")

		# Missing both parameters
		response = self.client.post(url, {}, format="json")
		self.assertEqual(response.status_code, 400)

		# Missing subject_id
		response = self.client.post(url, {"team_id": self.team.id}, format="json")
		self.assertEqual(response.status_code, 400)

		# Missing team_id
		response = self.client.post(url, {"subject_id": self.subject.id}, format="json")
		self.assertEqual(response.status_code, 400)

	def test_search_by_title(self):
		"""Test searching trials by title"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": self.subject.id,
			"title": "COVID",
		}
		response = self.client.post(url, data, format="json")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(response.data["results"][0]["title"], self.trial1.title)

	def test_search_by_summary(self):
		"""Test searching trials by summary/abstract"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": self.subject.id,
			"summary": "coronavirus",
		}
		response = self.client.post(url, data, format="json")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(response.data["results"][0]["title"], self.trial1.title)

	def test_search_combined(self):
		"""Test searching in both title and summary"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": self.subject.id,
			"search": "COVID",
		}
		response = self.client.post(url, data, format="json")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			len(response.data["results"]), 2
		)  # Should find both trial1 and trial3

		# Check that we got the expected trials
		trial_titles = [trial["title"] for trial in response.data["results"]]
		self.assertIn(self.trial1.title, trial_titles)
		self.assertIn(self.trial3.title, trial_titles)

	def test_filter_by_status(self):
		"""Test filtering trials by recruitment status"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": self.subject.id,
			"status": "Recruiting",
		}
		response = self.client.post(url, data, format="json")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(response.data["results"][0]["title"], self.trial1.title)

	def test_combined_search_and_status(self):
		"""Test combined search and status filtering"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": self.subject.id,
			"search": "COVID",
			"status": "Completed",
		}
		response = self.client.post(url, data, format="json")

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(response.data["results"][0]["title"], self.trial3.title)

	def test_boolean_or_search(self):
		"""Boolean OR returns trials matching either term"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": self.subject.id,
			"search": "coronavirus OR sclerosis",
		}
		response = self.client.post(url, data, format="json")
		self.assertEqual(response.status_code, 200)
		titles = [t["title"] for t in response.data["results"]]
		self.assertIn(self.trial1.title, titles)
		self.assertIn(self.trial2.title, titles)
		self.assertNotIn(self.trial3.title, titles)

	def test_boolean_and_search(self):
		"""Space-separated terms use AND semantics"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": self.subject.id,
			"search": "COVID cancer",
		}
		response = self.client.post(url, data, format="json")
		self.assertEqual(response.status_code, 200)
		titles = [t["title"] for t in response.data["results"]]
		# trial3 has both COVID (in summary) and cancer (in title)
		self.assertIn(self.trial3.title, titles)
		self.assertNotIn(self.trial1.title, titles)

	def test_boolean_negation(self):
		"""Terms prefixed with - exclude matching trials"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": self.subject.id,
			"search": "COVID -cancer",
		}
		response = self.client.post(url, data, format="json")
		self.assertEqual(response.status_code, 200)
		titles = [t["title"] for t in response.data["results"]]
		self.assertIn(self.trial1.title, titles)
		self.assertNotIn(self.trial3.title, titles)

	def test_boolean_quoted_phrase(self):
		"""Quoted phrases match the exact phrase"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": self.subject.id,
			"search": '"Multiple Sclerosis"',
		}
		response = self.client.post(url, data, format="json")
		self.assertEqual(response.status_code, 200)
		titles = [t["title"] for t in response.data["results"]]
		self.assertIn(self.trial2.title, titles)
		self.assertNotIn(self.trial1.title, titles)

	def test_invalid_team_id(self):
		"""Test with invalid team ID"""
		url = reverse("trial-search")
		data = {
			"team_id": 9999,  # Non-existent team ID
			"subject_id": self.subject.id,
		}
		response = self.client.post(url, data, format="json")
		self.assertEqual(response.status_code, 404)

	def test_invalid_subject_id(self):
		"""Test with invalid subject ID"""
		url = reverse("trial-search")
		data = {
			"team_id": self.team.id,
			"subject_id": 9999,  # Non-existent subject ID
		}
		response = self.client.post(url, data, format="json")
		self.assertEqual(response.status_code, 404)


class TrialSearchViewQueryCountTests(TestCase):
	"""Regression tests for the N+1 prefetch and DISTINCT-pagination fixes."""

	def setUp(self):
		self.organization = Organization.objects.create(
			name="Trial Query Count Org", slug="trial-search-query-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Trial Query Count Team",
			slug="trial-search-query-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Trial Query Count Subject",
			subject_slug="trial-query-count-subject",
			team=self.team,
		)
		other_subject = Subject.objects.create(
			subject_name="Trial Other Subject",
			subject_slug="trial-other-subject",
			team=self.team,
		)

		for i in range(3):
			trial = Trials.objects.create(
				title=f"Query Count Trial {i}",
				summary="Body text for query count regression test.",
				link=f"https://example.com/query-count-trial-{i}",
				published_date=timezone.now(),
				recruitment_status="Recruiting",
			)
			trial.teams.add(self.team)
			trial.subjects.add(self.subject, other_subject)

		self.client = APIClient()

	def test_list_query_count_is_bounded_and_avoids_distinct(self):
		url = reverse("trial-search")
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(
				url, {"team_id": self.team.id, "subject_id": self.subject.id}
			)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data["results"]), 3)

		self.assertLess(len(ctx.captured_queries), 15)

		for query in ctx.captured_queries:
			sql = query["sql"].lower()
			if "gregory_trials" in sql and "select distinct" in sql:
				self.fail(f"unexpected DISTINCT over trials: {query['sql']}")
