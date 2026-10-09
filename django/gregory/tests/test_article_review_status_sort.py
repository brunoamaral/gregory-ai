"""Sorting the admin review-status page by average ML score."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from organizations.models import Organization

from gregory.models import Articles, MLPredictions, Subject, Team


class ReviewStatusMLScoreSortTestCase(TestCase):
	@classmethod
	def setUpTestData(cls):
		org = Organization.objects.create(name="Review Sort Org")
		team = Team.objects.create(organization=org, name="Review Sort Team", slug="review-sort-team")
		cls.subject = Subject.objects.create(
			subject_name="Review Sort Subject", subject_slug="review-sort-subject", team=team
		)
		cls.user = get_user_model().objects.create_superuser(
			username="reviewsort", email="reviewsort@example.com", password="x"
		)
		now = timezone.now()

		# 30 scored articles: newer articles get lower scores, so the top scores
		# all sit beyond the first page of 25 when ordered by discovery date.
		cls.scored = []
		for i in range(30):
			article = Articles.objects.create(title=f"Scored {i}", link=f"https://example.com/s{i}")
			article.subjects.add(cls.subject)
			Articles.objects.filter(pk=article.pk).update(discovery_date=now - timedelta(days=i))
			cls._predict(article, "pubmed_bert", 0.01 * i)
			cls._predict(article, "lgbm_tfidf", 0.01 * i + 0.02)
			cls.scored.append(article)

		# Newest article with no predictions: must never come first
		cls.unscored = Articles.objects.create(title="Unscored", link="https://example.com/u")
		cls.unscored.subjects.add(cls.subject)
		Articles.objects.filter(pk=cls.unscored.pk).update(discovery_date=now + timedelta(days=1))

		# Old high score superseded by a newer low score for the same algorithm
		cls.superseded = Articles.objects.create(title="Superseded", link="https://example.com/x")
		cls.superseded.subjects.add(cls.subject)
		Articles.objects.filter(pk=cls.superseded.pk).update(discovery_date=now - timedelta(days=100))
		old = cls._predict(cls.superseded, "pubmed_bert", 0.99)
		MLPredictions.objects.filter(pk=old.pk).update(created_date=now - timedelta(days=10))
		cls._predict(cls.superseded, "pubmed_bert", 0.0, model_version="v2")

	@classmethod
	def _predict(cls, article, algorithm, score, model_version="v1"):
		return MLPredictions.objects.create(
			article=article,
			subject=cls.subject,
			algorithm=algorithm,
			model_version=model_version,
			probability_score=score,
			predicted_relevant=score >= 0.5,
		)

	def _rows(self, sort_by, page=1):
		self.client.force_login(self.user)
		response = self.client.get(
			reverse("admin:article_review_status"),
			{"subject_id": self.subject.id, "sort_by": sort_by, "page": page},
		)
		self.assertEqual(response.status_code, 200)
		return response.context["articles_with_review_status"]

	def test_descending_starts_with_highest_average_across_all_pages(self):
		rows = self._rows("-ml_score")
		self.assertEqual(rows[0]["article"].pk, self.scored[-1].pk)
		averages = [row["avg_ml_score"] for row in rows]
		self.assertEqual(averages, sorted(averages, reverse=True))

	def test_unscored_articles_go_last_in_both_directions(self):
		for sort_by in ("-ml_score", "ml_score"):
			last_page = self._rows(sort_by, page=2)
			self.assertEqual(last_page[-1]["article"].pk, self.unscored.pk, sort_by)
			self.assertIsNone(last_page[-1]["avg_ml_score"])

	def test_ascending_uses_latest_prediction_per_algorithm(self):
		rows = self._rows("ml_score")
		self.assertEqual(rows[0]["article"].pk, self.superseded.pk)
		averages = [row["avg_ml_score"] for row in rows]
		self.assertEqual(averages, sorted(averages))
