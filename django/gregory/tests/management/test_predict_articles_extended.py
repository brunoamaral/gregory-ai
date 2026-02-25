"""
Extended tests for the predict_articles management command.

Covers scenarios not in test_predict_articles.py:
1. get_articles with all_articles=True (bypasses date filter)
2. run_predictions_for when model load fails (ModelLoadError)
3. run_predictions_for when prepare_text returns None (skipped articles)
4. run_predictions_for when model.predict raises an exception (failure counting)
5. run_predictions_for with zero articles
6. PredictionRunLog records error messages on failure
7. prepare_text with None summary
8. handle: non-existent team raises CommandError
9. handle: invalid algorithm name rejected
10. handle: skips subjects with auto_predict=False
11. Prediction stores correct predicted_relevant and probability_score
12. load_model with unsupported algorithm
13. get_articles returns distinct results (no duplicates)
14. run_predictions_for correctly unwraps list returns from model.predict
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization

from gregory.management.commands.predict_articles import (
	Command, get_articles, resolve_model_version, load_model,
	ModelLoadError, prepare_text, DEFAULT_ALGORITHMS
)
from gregory.models import Team, Subject, Articles, MLPredictions, PredictionRunLog


# ---------------------------------------------------------------------------
# Helper mixin for common setUp
# ---------------------------------------------------------------------------
class PredictArticlesTestMixin:
	"""Shared setUp for predict_articles tests."""

	def _create_fixtures(self):
		self.organization = Organization.objects.create(name='Test Organization')
		self.team = Team.objects.create(slug='test-team', organization=self.organization)
		self.subject = Subject.objects.create(
			subject_name='Test Subject',
			subject_slug='test-subject',
			team=self.team,
			auto_predict=True,
		)
		self.article1 = Articles.objects.create(
			title='Article 1',
			link='http://example.com/1',
			summary='This is a sufficiently long summary for article one to pass cleaning',
		)
		self.article1.subjects.add(self.subject)
		self.article2 = Articles.objects.create(
			title='Article 2',
			link='http://example.com/2',
			summary='This is a sufficiently long summary for article two to pass cleaning',
		)
		self.article2.subjects.add(self.subject)


# ===========================================================================
# 1. get_articles with all_articles=True
# ===========================================================================
class TestGetArticlesAllArticles(PredictArticlesTestMixin, TestCase):
	def setUp(self):
		self._create_fixtures()

	def test_all_articles_ignores_date_filter(self):
		"""Articles older than lookback_days are still returned when all_articles=True."""
		today = timezone.now().date()
		# Make both articles very old
		old_date = timezone.make_aware(
			datetime.combine(today - timedelta(days=365), datetime.min.time())
		)
		for article in [self.article1, self.article2]:
			article.discovery_date = old_date
			article.save()

		# With lookback_days=30 and all_articles=False, neither should appear
		articles = get_articles(self.subject, 'pubmed_bert', 'v1', lookback_days=30, all_articles=False)
		self.assertEqual(articles.count(), 0)

		# With all_articles=True, both should appear
		articles = get_articles(self.subject, 'pubmed_bert', 'v1', lookback_days=30, all_articles=True)
		self.assertEqual(articles.count(), 2)


# ===========================================================================
# 2–6. run_predictions_for edge cases
# ===========================================================================
@patch('gregory.management.commands.predict_articles.get_articles')
@patch('gregory.management.commands.predict_articles.resolve_model_version')
@patch('gregory.management.commands.predict_articles.load_model')
@patch('gregory.management.commands.predict_articles.prepare_text')
class TestRunPredictionsEdgeCases(PredictArticlesTestMixin, TestCase):
	def setUp(self):
		self._create_fixtures()
		self.command = Command()

	# ---- 2. Model load failure ----
	def test_model_load_failure_logs_error(self, mock_prepare, mock_load, mock_resolve, mock_get):
		"""When load_model raises ModelLoadError, PredictionRunLog records the failure."""
		mock_resolve.return_value = 'v1'
		mock_load.side_effect = ModelLoadError('weights not found')

		with self.assertRaises(ModelLoadError):
			self.command.run_predictions_for(
				self.subject, 'pubmed_bert', None,
				lookback_days=90, dry_run=False, verbose=0,
			)

		log = PredictionRunLog.objects.filter(subject=self.subject).first()
		self.assertIsNotNone(log)
		self.assertFalse(log.success)
		self.assertIn('Failed to load model', log.error_message)

	# ---- 3. prepare_text returns None → article is skipped ----
	def test_skipped_when_prepare_text_returns_none(self, mock_prepare, mock_load, mock_resolve, mock_get):
		"""Articles whose cleaned text is None should be counted as skipped."""
		mock_resolve.return_value = 'v1'
		mock_get.return_value = [self.article1, self.article2]
		mock_load.return_value = MagicMock()
		mock_prepare.return_value = None  # cleaning produces nothing

		stats = self.command.run_predictions_for(
			self.subject, 'pubmed_bert', 'v1',
			lookback_days=90, dry_run=False, verbose=0,
		)
		self.assertEqual(stats['skipped'], 2)
		self.assertEqual(stats['processed'], 0)
		self.assertEqual(MLPredictions.objects.filter(subject=self.subject).count(), 0)

	# ---- 4. model.predict raises exception → failure counted ----
	def test_model_predict_exception_counted_as_failure(self, mock_prepare, mock_load, mock_resolve, mock_get):
		"""If model.predict raises, the article is counted as a failure, not as skipped."""
		mock_resolve.return_value = 'v1'
		mock_get.return_value = [self.article1]
		mock_prepare.return_value = 'some cleaned text'
		mock_model = MagicMock()
		mock_model.predict.side_effect = RuntimeError('CUDA OOM')
		mock_load.return_value = mock_model

		stats = self.command.run_predictions_for(
			self.subject, 'pubmed_bert', 'v1',
			lookback_days=90, dry_run=False, verbose=0,
		)
		self.assertEqual(stats['failures'], 1)
		self.assertEqual(stats['processed'], 0)

		# PredictionRunLog should record the failure
		log = PredictionRunLog.objects.filter(subject=self.subject).last()
		self.assertFalse(log.success)
		self.assertIn(str(self.article1.article_id), log.error_message)

	# ---- 5. Zero articles to process ----
	def test_zero_articles_returns_empty_stats(self, mock_prepare, mock_load, mock_resolve, mock_get):
		"""When no articles need prediction, stats should all be zero and the log should succeed."""
		mock_resolve.return_value = 'v1'
		mock_get.return_value = Articles.objects.none()
		mock_load.return_value = MagicMock()

		stats = self.command.run_predictions_for(
			self.subject, 'pubmed_bert', 'v1',
			lookback_days=90, dry_run=False, verbose=0,
		)
		self.assertEqual(stats['processed'], 0)
		self.assertEqual(stats['skipped'], 0)
		self.assertEqual(stats['failures'], 0)
		self.assertEqual(stats['new_predictions'], 0)

		log = PredictionRunLog.objects.filter(subject=self.subject).last()
		self.assertTrue(log.success)

	# ---- 6. Correct predicted_relevant & probability_score stored ----
	def test_prediction_values_stored_correctly(self, mock_prepare, mock_load, mock_resolve, mock_get):
		"""MLPredictions must store the exact probability_score and predicted_relevant."""
		mock_resolve.return_value = 'v1'
		mock_get.return_value = [self.article1, self.article2]
		mock_prepare.side_effect = ['text one', 'text two']
		mock_model = MagicMock()
		# First article relevant (prob 0.92), second not (prob 0.15)
		mock_model.predict.side_effect = [([1], [0.92]), ([0], [0.15])]
		mock_load.return_value = mock_model

		self.command.run_predictions_for(
			self.subject, 'pubmed_bert', 'v1',
			lookback_days=90, prob_threshold=0.8, dry_run=False, verbose=0,
		)

		pred1 = MLPredictions.objects.get(article=self.article1, subject=self.subject)
		self.assertTrue(pred1.predicted_relevant)
		self.assertAlmostEqual(pred1.probability_score, 0.92, places=2)

		pred2 = MLPredictions.objects.get(article=self.article2, subject=self.subject)
		self.assertFalse(pred2.predicted_relevant)
		self.assertAlmostEqual(pred2.probability_score, 0.15, places=2)

	# ---- unwraps list returns from model.predict ----
	def test_unwraps_single_element_list_predictions(self, mock_prepare, mock_load, mock_resolve, mock_get):
		"""model.predict may return ([1], [0.9]); the command must unwrap the inner lists."""
		mock_resolve.return_value = 'v1'
		mock_get.return_value = [self.article1]
		mock_prepare.return_value = 'text'
		mock_model = MagicMock()
		mock_model.predict.return_value = ([1], [0.95])
		mock_load.return_value = mock_model

		stats = self.command.run_predictions_for(
			self.subject, 'lgbm_tfidf', 'v1',
			lookback_days=90, dry_run=False, verbose=0,
		)

		self.assertEqual(stats['processed'], 1)
		pred = MLPredictions.objects.get(article=self.article1, subject=self.subject)
		self.assertTrue(pred.predicted_relevant)
		self.assertAlmostEqual(pred.probability_score, 0.95, places=2)


# ===========================================================================
# 7. prepare_text with None summary
# ===========================================================================
class TestPrepareTextNoneSummary(TestCase):
	@patch('gregory.management.commands.predict_articles.cleanHTML')
	@patch('gregory.management.commands.predict_articles.cleanText')
	def test_prepare_text_with_none_summary(self, mock_clean_text, mock_clean_html):
		"""When article.summary is None, only the title should be used."""
		mock_clean_html.return_value = 'cleaned html'
		mock_clean_text.return_value = 'cleaned text'
		article = MagicMock()
		article.title = 'Test Title'
		article.summary = None

		result = prepare_text(article)
		# summary is None → falsy → only title used
		mock_clean_html.assert_called_once_with('Test Title')
		mock_clean_text.assert_called_once_with('cleaned html')
		self.assertEqual(result, 'cleaned text')


# ===========================================================================
# 8–10. handle() integration: team not found, invalid algo, auto_predict
# ===========================================================================
class TestHandleValidation(PredictArticlesTestMixin, TestCase):
	def setUp(self):
		self._create_fixtures()

	def test_nonexistent_team_raises_command_error(self):
		"""Passing a team slug that doesn't exist should raise CommandError."""
		with self.assertRaises((CommandError, SystemExit)):
			call_command('predict_articles', '--team=does-not-exist')

	def test_invalid_algorithm_name_rejected(self):
		"""An unknown algorithm passed via --algo should cause an error exit."""
		with self.assertRaises(SystemExit):
			call_command('predict_articles', '--team=test-team', '--algo=random_forest')

	@patch('gregory.management.commands.predict_articles.Command.run_predictions_for')
	def test_skips_subjects_without_auto_predict(self, mock_run):
		"""Subjects with auto_predict=False should not be processed."""
		# Create a second subject with auto_predict=False
		Subject.objects.create(
			subject_name='Manual Only',
			subject_slug='manual-only',
			team=self.team,
			auto_predict=False,
		)

		# Mock run_predictions_for to avoid needing actual models,
		# but make it return stats and not call sys.exit
		mock_run.return_value = {
			'processed': 0, 'skipped': 0, 'failures': 0, 'new_predictions': 0
		}

		# Patch sys.exit so the command doesn't terminate the test runner
		with patch('gregory.management.commands.predict_articles.sys') as mock_sys:
			mock_sys.exit = MagicMock()
			call_command('predict_articles', '--team=test-team')

		# run_predictions_for should only have been called for 'test-subject'
		# (3 algorithms × 1 subject = 3 calls)
		for call_args in mock_run.call_args_list:
			subject_arg = call_args[1].get('subject') or call_args[0][0]
			self.assertEqual(subject_arg.subject_slug, 'test-subject')


# ===========================================================================
# 12. load_model with unsupported algorithm
# ===========================================================================
class TestLoadModelUnsupported(TestCase):
	def test_unsupported_algorithm_raises_model_load_error(self):
		team = MagicMock()
		subject = MagicMock()
		with self.assertRaises(ModelLoadError) as ctx:
			load_model(team, subject, 'random_forest', 'v1')
		self.assertIn('Unsupported algorithm', str(ctx.exception))


# ===========================================================================
# 13. get_articles returns distinct results
# ===========================================================================
class TestGetArticlesDistinct(PredictArticlesTestMixin, TestCase):
	def setUp(self):
		self._create_fixtures()

	def test_no_duplicate_articles(self):
		"""Even if an article belongs to multiple subjects, it should appear once."""
		subject2 = Subject.objects.create(
			subject_name='Subject 2',
			subject_slug='subject-2',
			team=self.team,
			auto_predict=True,
		)
		# article1 belongs to both subjects
		self.article1.subjects.add(subject2)

		articles = get_articles(self.subject, 'pubmed_bert', 'v1', all_articles=True)
		ids = list(articles.values_list('article_id', flat=True))
		self.assertEqual(len(ids), len(set(ids)), 'get_articles returned duplicates')


# ===========================================================================
# 14. PredictionRunLog records model_version after resolution
# ===========================================================================
@patch('gregory.management.commands.predict_articles.get_articles')
@patch('gregory.management.commands.predict_articles.resolve_model_version')
@patch('gregory.management.commands.predict_articles.load_model')
@patch('gregory.management.commands.predict_articles.prepare_text')
class TestPredictionRunLogModelVersion(PredictArticlesTestMixin, TestCase):
	def setUp(self):
		self._create_fixtures()
		self.command = Command()

	def test_run_log_has_resolved_model_version(self, mock_prepare, mock_load, mock_resolve, mock_get):
		"""PredictionRunLog should store the resolved (not requested) model version."""
		mock_resolve.return_value = 'v3.2'
		mock_get.return_value = [self.article1]
		mock_prepare.return_value = 'text'
		mock_model = MagicMock()
		mock_model.predict.return_value = (1, 0.9)
		mock_load.return_value = mock_model

		self.command.run_predictions_for(
			self.subject, 'pubmed_bert', None,  # None = "use latest"
			lookback_days=90, dry_run=False, verbose=0,
		)

		log = PredictionRunLog.objects.filter(subject=self.subject).last()
		self.assertEqual(log.model_version, 'v3.2')

	def test_run_log_marked_successful_on_clean_run(self, mock_prepare, mock_load, mock_resolve, mock_get):
		"""When all articles are processed without failures, success=True."""
		mock_resolve.return_value = 'v1'
		mock_get.return_value = [self.article1]
		mock_prepare.return_value = 'text'
		mock_model = MagicMock()
		mock_model.predict.return_value = (0, 0.2)
		mock_load.return_value = mock_model

		self.command.run_predictions_for(
			self.subject, 'pubmed_bert', 'v1',
			lookback_days=90, dry_run=False, verbose=0,
		)

		log = PredictionRunLog.objects.filter(subject=self.subject).last()
		self.assertTrue(log.success)
		self.assertIsNotNone(log.run_finished)
