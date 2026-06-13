from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from gregory.models import (
	Articles,
	Trials,
	ArticleTrialReference,
	Team,
	OrganizationApiSettings,
)
from organizations.models import Organization


class HasClinicalTrialsFilterTests(TestCase):
	"""Tests for the has_clinical_trials filter on the articles endpoint."""

	def setUp(self):
		self.client = APIClient()

		# Public org so anonymous requests can see the articles
		org = Organization.objects.create(
			name="Clinical Trials Filter Org", slug="clin-filter-org"
		)
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Clin Filter Team", slug="clin-filter-team", organization=org
		)

		# Article linked to a trial
		self.article_with_trial = Articles.objects.create(
			title="Article referencing a clinical trial",
			link="https://example.com/article-with-trial",
		)
		self.article_with_trial.teams.add(self.team)

		# Article with no trial link
		self.article_without_trial = Articles.objects.create(
			title="Article with no clinical trial",
			link="https://example.com/article-without-trial",
		)
		self.article_without_trial.teams.add(self.team)

		# Trial to link
		self.trial = Trials.objects.create(
			title="Test Clinical Trial",
			link="https://example.com/trial",
		)

		# Create the ArticleTrialReference link
		ArticleTrialReference.objects.create(
			article=self.article_with_trial,
			trial=self.trial,
			identifier_type="nct_id",
			identifier_value="NCT12345678",
		)

	def test_filter_true_returns_only_linked_articles(self):
		"""has_clinical_trials=true returns only articles linked to a trial."""
		response = self.client.get("/articles/", {"has_clinical_trials": "true"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [a["article_id"] for a in response.data["results"]]
		self.assertIn(self.article_with_trial.article_id, ids)
		self.assertNotIn(self.article_without_trial.article_id, ids)

	def test_filter_false_returns_only_unlinked_articles(self):
		"""has_clinical_trials=false returns only articles not linked to any trial."""
		response = self.client.get("/articles/", {"has_clinical_trials": "false"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [a["article_id"] for a in response.data["results"]]
		self.assertIn(self.article_without_trial.article_id, ids)
		self.assertNotIn(self.article_with_trial.article_id, ids)

	def test_filter_absent_returns_all_articles(self):
		"""Omitting has_clinical_trials returns all articles."""
		response = self.client.get("/articles/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [a["article_id"] for a in response.data["results"]]
		self.assertIn(self.article_with_trial.article_id, ids)
		self.assertIn(self.article_without_trial.article_id, ids)

	def test_filter_empty_value_returns_all_articles(self):
		"""Passing has_clinical_trials= (empty) returns all articles."""
		response = self.client.get("/articles/?has_clinical_trials=")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [a["article_id"] for a in response.data["results"]]
		self.assertIn(self.article_with_trial.article_id, ids)
		self.assertIn(self.article_without_trial.article_id, ids)

	def test_filter_true_no_duplicates_with_multiple_references(self):
		"""Articles linked to multiple trials appear only once (distinct)."""
		trial2 = Trials.objects.create(
			title="Second Trial",
			link="https://example.com/trial2",
		)
		ArticleTrialReference.objects.create(
			article=self.article_with_trial,
			trial=trial2,
			identifier_type="isrctn",
			identifier_value="ISRCTN99999999",
		)

		response = self.client.get("/articles/", {"has_clinical_trials": "true"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [a["article_id"] for a in response.data["results"]]
		self.assertEqual(ids.count(self.article_with_trial.article_id), 1)
