"""Regression tests for the ?relevant= and ?ml_threshold= article filters.

Every model retrain writes a new MLPredictions row (unique per
article/subject/model_version/algorithm). Before this fix, ArticleFilter's
ML-relevance check counted ANY historical prediction meeting the threshold,
so an article stayed "relevant" forever once a since-retired model_version
had scored it high -- even if every current model scores it low. Only the
*latest* prediction per (article, subject, algorithm) should count.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization
from rest_framework.test import APIClient

from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	MLPredictions,
	OrganizationApiSettings,
	Subject,
	Team,
)


class RelevantFilterLatestPredictionTestCase(TestCase):
	def setUp(self):
		self.client = APIClient()

		org = Organization.objects.create(name="Latest Pred Org", slug="latest-pred-org")
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=org, name="Latest Pred Team", slug="latest-pred-team"
		)
		self.subject_any = Subject.objects.create(
			team=self.team,
			subject_name="Any Subj",
			subject_slug="latest-pred-any-subj",
			auto_predict=True,
			ml_consensus_type="any",
		)
		self.subject_majority = Subject.objects.create(
			team=self.team,
			subject_name="Majority Subj",
			subject_slug="latest-pred-majority-subj",
			auto_predict=True,
			ml_consensus_type="majority",
		)

	def _article(self, title, subject):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/{title.lower().replace(' ', '-')}",
		)
		article.subjects.add(subject)
		article.teams.add(self.team)
		return article

	def _predict(self, article, subject, algorithm, score, model_version, days_ago=0):
		pred = MLPredictions.objects.create(
			article=article,
			subject=subject,
			algorithm=algorithm,
			model_version=model_version,
			probability_score=score,
			predicted_relevant=score >= 0.5,
		)
		if days_ago:
			MLPredictions.objects.filter(pk=pred.pk).update(
				created_date=timezone.now() - timedelta(days=days_ago)
			)
		return pred

	def _relevant_ids(self, value="true", **extra):
		params = {"relevant": value}
		params.update(extra)
		resp = self.client.get("/articles/", params)
		self.assertEqual(resp.status_code, 200)
		return {r["article_id"] for r in resp.data["results"]}

	# ------------------------------------------------------------------
	# Superseded prediction: old high score, new low score -> not relevant.
	# ------------------------------------------------------------------

	def test_superseded_high_score_excluded_from_relevant_true(self):
		article = self._article("Superseded high score", self.subject_any)
		self._predict(
			article, self.subject_any, "pubmed_bert", 0.9, "v1", days_ago=10
		)
		self._predict(article, self.subject_any, "pubmed_bert", 0.3, "v2", days_ago=0)

		self.assertNotIn(article.article_id, self._relevant_ids("true"))
		self.assertIn(article.article_id, self._relevant_ids("false"))

	def test_superseded_high_score_excluded_from_ml_threshold(self):
		article = self._article("Superseded high score threshold", self.subject_any)
		self._predict(
			article, self.subject_any, "pubmed_bert", 0.9, "v1", days_ago=10
		)
		self._predict(article, self.subject_any, "pubmed_bert", 0.3, "v2", days_ago=0)

		resp = self.client.get("/articles/", {"ml_threshold": "0.8"})
		self.assertEqual(resp.status_code, 200)
		ids = {r["article_id"] for r in resp.data["results"]}
		self.assertNotIn(article.article_id, ids)

	# ------------------------------------------------------------------
	# Reverse: old low score, new high score -> relevant.
	# ------------------------------------------------------------------

	def test_superseded_low_score_becomes_relevant(self):
		article = self._article("Superseded low score", self.subject_any)
		self._predict(
			article, self.subject_any, "pubmed_bert", 0.3, "v1", days_ago=10
		)
		self._predict(article, self.subject_any, "pubmed_bert", 0.9, "v2", days_ago=0)

		self.assertIn(article.article_id, self._relevant_ids("true"))
		self.assertNotIn(article.article_id, self._relevant_ids("false"))

	# ------------------------------------------------------------------
	# Manual relevance always qualifies, regardless of ML predictions.
	# ------------------------------------------------------------------

	def test_manual_relevance_qualifies_regardless_of_stale_predictions(self):
		article = self._article("Manually relevant despite stale ML", self.subject_any)
		# Only a stale (superseded, now-low) prediction exists.
		self._predict(
			article, self.subject_any, "pubmed_bert", 0.9, "v1", days_ago=10
		)
		self._predict(article, self.subject_any, "pubmed_bert", 0.1, "v2", days_ago=0)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_any, is_relevant=True
		)

		self.assertIn(article.article_id, self._relevant_ids("true"))
		self.assertNotIn(article.article_id, self._relevant_ids("false"))

	def test_manual_relevance_qualifies_with_no_predictions_at_all(self):
		article = self._article("Manually relevant no predictions", self.subject_any)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_any, is_relevant=True
		)

		self.assertIn(article.article_id, self._relevant_ids("true"))

	# ------------------------------------------------------------------
	# Consensus (majority) honored on latest-only predictions.
	# ------------------------------------------------------------------

	def test_majority_consensus_on_latest_predictions_only(self):
		article = self._article("Majority latest only", self.subject_majority)

		# pubmed_bert: latest prediction passes.
		self._predict(
			article, self.subject_majority, "pubmed_bert", 0.9, "v1", days_ago=10
		)
		self._predict(
			article, self.subject_majority, "pubmed_bert", 0.85, "v2", days_ago=0
		)
		# lgbm_tfidf: latest prediction passes too -> majority (2) reached.
		self._predict(
			article, self.subject_majority, "lgbm_tfidf", 0.9, "v1", days_ago=10
		)
		self._predict(
			article, self.subject_majority, "lgbm_tfidf", 0.82, "v2", days_ago=0
		)

		self.assertIn(article.article_id, self._relevant_ids("true"))

	def test_majority_consensus_fails_when_one_algorithm_drops_below_threshold(self):
		article = self._article("Majority one dropped", self.subject_majority)

		# pubmed_bert: latest prediction still passes.
		self._predict(
			article, self.subject_majority, "pubmed_bert", 0.9, "v1", days_ago=10
		)
		self._predict(
			article, self.subject_majority, "pubmed_bert", 0.85, "v2", days_ago=0
		)
		# lgbm_tfidf: old prediction passed, but the retrained latest dropped
		# below threshold -- only 1 algorithm now qualifies, majority fails.
		self._predict(
			article, self.subject_majority, "lgbm_tfidf", 0.9, "v1", days_ago=10
		)
		self._predict(
			article, self.subject_majority, "lgbm_tfidf", 0.2, "v2", days_ago=0
		)

		self.assertNotIn(article.article_id, self._relevant_ids("true"))
		self.assertIn(article.article_id, self._relevant_ids("false"))
