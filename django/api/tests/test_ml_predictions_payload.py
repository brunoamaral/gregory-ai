"""
Regression tests for the ArticleViewSet/ArticleSearchView ml_predictions
payload fix: only the latest prediction per (subject, algorithm) should be
serialized, matching the "current relevance" semantics introduced in PR #748.

Run with:
    docker exec gregory python manage.py test api.tests.test_ml_predictions_payload
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from gregory.models import (
	Articles,
	MLPredictions,
	Organization,
	OrganizationApiSettings,
	Subject,
	Team,
)


def _make_prediction(article, subject, algorithm, probability, created_date):
	pred = MLPredictions.objects.create(
		article=article,
		subject=subject,
		algorithm=algorithm,
		probability_score=probability,
		predicted_relevant=True,
	)
	MLPredictions.objects.filter(pk=pred.pk).update(created_date=created_date)
	pred.refresh_from_db()
	return pred


class ArticleMlPredictionsPayloadTests(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="ML Payload Org", slug="ml-payload-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="ML Payload Team", slug="ml-payload-team", organization=self.organization
		)
		self.subject = Subject.objects.create(
			subject_name="ML Payload Subject",
			subject_slug="ml-payload-subject",
			team=self.team,
		)
		self.article = Articles.objects.create(
			title="ML Payload Article",
			summary="Article used to test ml_predictions payload filtering.",
			link="https://example.com/ml-payload-article",
			published_date=timezone.now(),
		)
		self.article.teams.add(self.team)
		self.article.subjects.add(self.subject)
		self.client = APIClient()

	def test_only_latest_prediction_per_subject_and_algorithm_is_serialized(self):
		now = timezone.now()
		_make_prediction(
			self.article, self.subject, "lgbm_tfidf", 0.4, now - timedelta(days=10)
		)
		latest = _make_prediction(
			self.article, self.subject, "lgbm_tfidf", 0.9, now
		)

		response = self.client.get(f"/articles/{self.article.pk}/")
		self.assertEqual(response.status_code, 200)
		predictions = response.data["ml_predictions"]
		self.assertEqual(len(predictions), 1)
		self.assertEqual(predictions[0]["id"], latest.pk)
		self.assertAlmostEqual(predictions[0]["probability_score"], 0.9)

	def test_predictions_from_two_algorithms_both_serialized(self):
		now = timezone.now()
		bert_pred = _make_prediction(
			self.article, self.subject, "pubmed_bert", 0.7, now
		)
		lgbm_pred = _make_prediction(
			self.article, self.subject, "lgbm_tfidf", 0.6, now
		)

		response = self.client.get(f"/articles/{self.article.pk}/")
		self.assertEqual(response.status_code, 200)
		predictions = response.data["ml_predictions"]
		self.assertEqual(len(predictions), 2)
		ids = {p["id"] for p in predictions}
		self.assertEqual(ids, {bert_pred.pk, lgbm_pred.pk})

	def test_search_view_also_serializes_latest_only(self):
		now = timezone.now()
		_make_prediction(
			self.article, self.subject, "lstm", 0.3, now - timedelta(days=5)
		)
		latest = _make_prediction(self.article, self.subject, "lstm", 0.95, now)

		url = reverse("article-search")
		response = self.client.get(
			url, {"team_id": self.team.id, "subject_id": self.subject.id}
		)
		self.assertEqual(response.status_code, 200)
		results = response.data["results"]
		self.assertEqual(len(results), 1)
		predictions = results[0]["ml_predictions"]
		self.assertEqual(len(predictions), 1)
		self.assertEqual(predictions[0]["id"], latest.pk)
