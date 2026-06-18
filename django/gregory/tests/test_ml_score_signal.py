"""Tests for ml_score denormalized field: signal and backfill command."""

from io import StringIO
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization

from gregory.models import Articles, MLPredictions, Subject, Team


class MlScoreSignalTestCase(TestCase):
	"""Signal on MLPredictions.post_save updates article.ml_score."""

	def setUp(self):
		org = Organization.objects.create(name="Test Org")
		self.team = Team.objects.create(organization=org, slug="sig-test-team")
		self.subject_a = Subject.objects.create(
			subject_name="Subject A", subject_slug="subject-a", team=self.team
		)
		self.subject_b = Subject.objects.create(
			subject_name="Subject B", subject_slug="subject-b", team=self.team
		)
		self.article = Articles.objects.create(
			title="Signal test article",
			link="https://example.com/sig1",
		)

	def _refresh(self):
		self.article.refresh_from_db()

	def test_first_prediction_sets_score(self):
		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.8,
		)
		self._refresh()
		self.assertAlmostEqual(self.article.ml_score, 0.8, places=5)

	def test_score_averages_across_algorithms(self):
		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.6,
		)
		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="lgbm_tfidf",
			model_version="v1",
			probability_score=0.8,
		)
		self._refresh()
		self.assertAlmostEqual(self.article.ml_score, 0.7, places=5)

	def test_score_averages_across_subjects(self):
		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.4,
		)
		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_b,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.6,
		)
		self._refresh()
		self.assertAlmostEqual(self.article.ml_score, 0.5, places=5)

	def test_only_latest_per_algorithm_subject_pair_counts(self):
		"""Two predictions for the same (algorithm, subject) — only the newer one counts."""
		now = timezone.now()

		older = MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.3,
		)
		newer = MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v2",
			probability_score=0.9,
		)
		# Force timestamps so there's no ambiguity even if both created within same second
		MLPredictions.objects.filter(pk=older.pk).update(created_date=now - timedelta(hours=1))
		MLPredictions.objects.filter(pk=newer.pk).update(created_date=now)

		self._refresh()
		self.assertAlmostEqual(self.article.ml_score, 0.9, places=5)

	def test_null_probability_score_excluded(self):
		"""Predictions with null probability_score don't affect ml_score."""
		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.7,
		)
		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="lgbm_tfidf",
			model_version="v1",
			probability_score=None,
		)
		self._refresh()
		# Only the bert prediction (0.7) should count
		self.assertAlmostEqual(self.article.ml_score, 0.7, places=5)

	def test_no_predictions_leaves_score_null(self):
		"""Article with no predictions keeps ml_score as None."""
		self._refresh()
		self.assertIsNone(self.article.ml_score)

	def test_signal_does_not_update_last_updated(self):
		"""Signal uses .update() so it must not bump Articles.last_updated."""
		self._refresh()
		before = self.article.last_updated

		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.75,
		)
		self._refresh()
		self.assertEqual(self.article.last_updated, before)

	def test_prediction_with_null_article_is_ignored(self):
		"""Prediction not linked to any article must not raise."""
		MLPredictions.objects.create(
			article=None,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.5,
		)
		# Article stays None because no prediction is linked
		self._refresh()
		self.assertIsNone(self.article.ml_score)

	def test_delete_prediction_recomputes_score(self):
		"""Deleting a prediction triggers recompute, not a stale value."""
		pred = MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.8,
		)
		self._refresh()
		self.assertAlmostEqual(self.article.ml_score, 0.8, places=5)

		pred.delete()
		self._refresh()
		self.assertIsNone(self.article.ml_score)

	def test_delete_one_of_two_predictions_recomputes(self):
		"""Deleting one algorithm's prediction averages the remaining one."""
		pred_bert = MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.6,
		)
		MLPredictions.objects.create(
			article=self.article,
			subject=self.subject_a,
			algorithm="lgbm_tfidf",
			model_version="v1",
			probability_score=0.8,
		)
		self._refresh()
		self.assertAlmostEqual(self.article.ml_score, 0.7, places=5)

		pred_bert.delete()
		self._refresh()
		self.assertAlmostEqual(self.article.ml_score, 0.8, places=5)


class BackfillMlScoresCommandTestCase(TestCase):
	"""backfill_ml_scores management command."""

	def setUp(self):
		org = Organization.objects.create(name="Backfill Org")
		team = Team.objects.create(organization=org, slug="backfill-team")
		self.subject = Subject.objects.create(
			subject_name="Backfill Subject", subject_slug="backfill-sub", team=team
		)

		self.art_with_preds = Articles.objects.create(
			title="Article with predictions",
			link="https://example.com/b1",
		)
		self.art_no_preds = Articles.objects.create(
			title="Article without predictions",
			link="https://example.com/b2",
		)

		now = timezone.now()
		older = MLPredictions.objects.create(
			article=self.art_with_preds,
			subject=self.subject,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.4,
		)
		newer = MLPredictions.objects.create(
			article=self.art_with_preds,
			subject=self.subject,
			algorithm="pubmed_bert",
			model_version="v2",
			probability_score=0.8,
		)
		MLPredictions.objects.filter(pk=older.pk).update(created_date=now - timedelta(hours=2))
		MLPredictions.objects.filter(pk=newer.pk).update(created_date=now)

		# Reset scores so the command has real work to do
		Articles.objects.update(ml_score=None)

	def _run(self):
		out = StringIO()
		call_command("backfill_ml_scores", stdout=out)
		return out.getvalue()

	def test_sets_score_for_article_with_predictions(self):
		self._run()
		self.art_with_preds.refresh_from_db()
		self.assertAlmostEqual(self.art_with_preds.ml_score, 0.8, places=5)

	def test_nulls_score_for_article_without_predictions(self):
		self._run()
		self.art_no_preds.refresh_from_db()
		self.assertIsNone(self.art_no_preds.ml_score)

	def test_command_prints_success_message(self):
		output = self._run()
		self.assertIn("Done", output)
