"""Regression tests for Articles.is_ml_relevant_for_subject staleness handling.

Every model retrain writes a new MLPredictions row (unique per
article/subject/model_version/algorithm). Only the *latest* prediction per
(article, subject, algorithm) should count toward ML consensus -- an old
model_version's score must not keep an article "relevant" forever after a
retrain.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization

from gregory.models import Articles, MLPredictions, Subject, Team


class IsMlRelevantForSubjectStalePredictionsTestCase(TestCase):
	def setUp(self):
		org = Organization.objects.create(name="Stale Prediction Org")
		self.team = Team.objects.create(
			organization=org, name="Stale Prediction Team", slug="stale-prediction-team"
		)
		self.subject_any = Subject.objects.create(
			subject_name="Any Subject",
			subject_slug="stale-any-subject",
			team=self.team,
			auto_predict=True,
			ml_consensus_type="any",
		)
		self.subject_majority = Subject.objects.create(
			subject_name="Majority Subject",
			subject_slug="stale-majority-subject",
			team=self.team,
			auto_predict=True,
			ml_consensus_type="majority",
		)

	def _article(self, title, link):
		return Articles.objects.create(title=title, link=link)

	def _predict(self, article, subject, algorithm, score, model_version, days_ago=0, predicted_relevant=None):
		if predicted_relevant is None:
			predicted_relevant = score >= 0.5
		pred = MLPredictions.objects.create(
			article=article,
			subject=subject,
			algorithm=algorithm,
			model_version=model_version,
			probability_score=score,
			predicted_relevant=predicted_relevant,
		)
		if days_ago:
			MLPredictions.objects.filter(pk=pred.pk).update(
				created_date=timezone.now() - timedelta(days=days_ago)
			)
		return pred

	def test_superseded_high_score_is_not_relevant(self):
		"""Old model_version scored 0.9 (qualifying); newer model_version (same
		algorithm+subject) scored 0.3. Only the latest counts, so this must NOT
		be relevant."""
		article = self._article("Superseded high score", "https://example.com/stale-1")
		article.subjects.add(self.subject_any)
		self._predict(
			article, self.subject_any, "pubmed_bert", 0.9, "v1", days_ago=10
		)
		self._predict(article, self.subject_any, "pubmed_bert", 0.3, "v2", days_ago=0)

		self.assertFalse(article.is_ml_relevant_for_subject(self.subject_any, threshold=0.8))

	def test_superseded_low_score_becomes_relevant(self):
		"""Reverse case: old model_version scored low, newer model_version scored
		high. Latest prediction qualifies, so this must be relevant."""
		article = self._article("Superseded low score", "https://example.com/stale-2")
		article.subjects.add(self.subject_any)
		self._predict(
			article, self.subject_any, "pubmed_bert", 0.3, "v1", days_ago=10
		)
		self._predict(article, self.subject_any, "pubmed_bert", 0.9, "v2", days_ago=0)

		self.assertTrue(article.is_ml_relevant_for_subject(self.subject_any, threshold=0.8))

	def test_majority_consensus_uses_latest_predictions_only(self):
		"""Majority consensus (>=2 algorithms) must be evaluated on each
		algorithm's *latest* prediction. Two algorithms' old predictions passed,
		but only one algorithm's latest prediction still passes -- not relevant."""
		article = self._article("Majority stale", "https://example.com/stale-3")
		article.subjects.add(self.subject_majority)

		# pubmed_bert: latest prediction still passes.
		self._predict(
			article, self.subject_majority, "pubmed_bert", 0.9, "v1", days_ago=10
		)
		self._predict(
			article, self.subject_majority, "pubmed_bert", 0.85, "v2", days_ago=0
		)

		# lgbm_tfidf: old prediction passed, but the latest (retrained) dropped
		# below threshold.
		self._predict(
			article, self.subject_majority, "lgbm_tfidf", 0.9, "v1", days_ago=10
		)
		self._predict(
			article, self.subject_majority, "lgbm_tfidf", 0.2, "v2", days_ago=0
		)

		# Only 1 algorithm's latest prediction qualifies -- majority (>=2) fails.
		self.assertFalse(
			article.is_ml_relevant_for_subject(self.subject_majority, threshold=0.8)
		)

		# Now bring lstm's latest prediction up to threshold too: majority holds.
		self._predict(article, self.subject_majority, "lstm", 0.9, "v1", days_ago=0)
		self.assertTrue(
			article.is_ml_relevant_for_subject(self.subject_majority, threshold=0.8)
		)

	def test_tied_created_date_counts_any_qualifying_row(self):
		"""Two predictions for the same algorithm share the same created_date (tie).
		All tied latest rows must be considered — the algorithm qualifies if any
		tied row does — matching the api.filters and gregory.relevance
		implementations, which filter on created_date == MAX(created_date)."""
		article = self._article("Tied created_date", "https://example.com/stale-tie")
		article.subjects.add(self.subject_any)

		low = self._predict(article, self.subject_any, "pubmed_bert", 0.3, "v1")
		high = self._predict(article, self.subject_any, "pubmed_bert", 0.9, "v2")

		# Force an exact created_date tie between the two rows.
		tied_at = timezone.now() - timedelta(days=1)
		MLPredictions.objects.filter(pk__in=[low.pk, high.pk]).update(
			created_date=tied_at
		)

		self.assertTrue(
			article.is_ml_relevant_for_subject(self.subject_any, threshold=0.8)
		)
