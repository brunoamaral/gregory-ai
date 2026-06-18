"""Tests for ml_score API ordering on the /articles/ endpoint."""

from django.test import TestCase
from django.urls import reverse
from organizations.models import Organization
from rest_framework import status
from rest_framework.test import APIClient

from gregory.models import Articles, MLPredictions, OrganizationApiSettings, Sources, Subject, Team


class MlScoreOrderingTestCase(TestCase):
	"""?ordering=ml_score and ?ordering=-ml_score on /articles/."""

	def setUp(self):
		self.client = APIClient()

		org = Organization.objects.create(name="ML Score Test Org")
		OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=True)
		self.team = Team.objects.create(organization=org, slug="ml-score-team")
		self.subject = Subject.objects.create(
			subject_name="ML Subject", subject_slug="ml-subject", team=self.team
		)
		source = Sources.objects.create(name="ML Source", link="http://mlsource.com")

		def make_article(title, link, score):
			a = Articles.objects.create(title=title, link=link)
			a.teams.add(self.team)
			a.subjects.add(self.subject)
			a.sources.add(source)
			Articles.objects.filter(pk=a.pk).update(ml_score=score)
			return a

		self.high = make_article("High score article", "https://ex.com/high", 0.9)
		self.mid = make_article("Mid score article", "https://ex.com/mid", 0.5)
		self.low = make_article("Low score article", "https://ex.com/low", 0.1)
		self.no_score = make_article("No score article", "https://ex.com/none", None)

	def _ids(self, response):
		return [r["article_id"] for r in response.data["results"]]

	def test_ml_score_present_in_response(self):
		url = reverse("articles-list")
		response = self.client.get(url, {"team_id": self.team.id})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("ml_score", response.data["results"][0])

	def test_ordering_desc_highest_first(self):
		url = reverse("articles-list")
		response = self.client.get(url, {"team_id": self.team.id, "ordering": "-ml_score"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = self._ids(response)
		self.assertEqual(ids[0], self.high.article_id)
		self.assertEqual(ids[1], self.mid.article_id)
		self.assertEqual(ids[2], self.low.article_id)

	def test_ordering_asc_lowest_first(self):
		url = reverse("articles-list")
		response = self.client.get(url, {"team_id": self.team.id, "ordering": "ml_score"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = self._ids(response)
		self.assertEqual(ids[0], self.low.article_id)
		self.assertEqual(ids[1], self.mid.article_id)
		self.assertEqual(ids[2], self.high.article_id)

	def test_null_scores_sort_last_desc(self):
		url = reverse("articles-list")
		response = self.client.get(url, {"team_id": self.team.id, "ordering": "-ml_score"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = self._ids(response)
		self.assertEqual(ids[-1], self.no_score.article_id)

	def test_null_scores_sort_last_asc(self):
		url = reverse("articles-list")
		response = self.client.get(url, {"team_id": self.team.id, "ordering": "ml_score"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = self._ids(response)
		self.assertEqual(ids[-1], self.no_score.article_id)

	def test_unknown_ordering_field_ignored(self):
		"""DRF silently ignores unrecognised ordering fields — no 400."""
		url = reverse("articles-list")
		response = self.client.get(url, {"team_id": self.team.id, "ordering": "nonexistent_field"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_other_orderings_unaffected(self):
		"""Existing ordering fields still work after introducing NullsLastOrderingFilter."""
		url = reverse("articles-list")
		response = self.client.get(url, {"team_id": self.team.id, "ordering": "title"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		titles = [r["title"] for r in response.data["results"]]
		self.assertEqual(titles, sorted(titles))


class MlScoreSignalIntegrationTestCase(TestCase):
	"""Verify that ml_score is live on the article after a prediction is saved,
	and that the API reflects the updated value."""

	def setUp(self):
		self.client = APIClient()
		org = Organization.objects.create(name="Signal Integration Org")
		OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=True)
		self.team = Team.objects.create(organization=org, slug="sig-integ-team")
		self.subject = Subject.objects.create(
			subject_name="Sig Subject", subject_slug="sig-subject", team=self.team
		)
		self.article = Articles.objects.create(
			title="Signal integration article",
			link="https://ex.com/siginteg",
		)
		self.article.teams.add(self.team)
		self.article.subjects.add(self.subject)

	def test_api_returns_updated_ml_score_after_prediction_saved(self):
		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject,
			algorithm="lgbm_tfidf",
			model_version="v1",
			probability_score=0.72,
		)
		url = reverse("articles-list")
		response = self.client.get(url, {"team_id": self.team.id})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		result = response.data["results"][0]
		self.assertAlmostEqual(result["ml_score"], 0.72, places=5)
