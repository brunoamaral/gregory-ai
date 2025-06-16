import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch, MagicMock

class DetectTrialReferencesCommandTest(TestCase):
	@patch('gregory.management.commands.detect_trial_references.ArticleTrialReference')
	@patch('gregory.management.commands.detect_trial_references.Trials')
	@patch('gregory.management.commands.detect_trial_references.Articles')
	def test_handle_respects_options(self, mock_articles, mock_trials, mock_ref):
		qs = MagicMock()
		qs.filter.return_value = qs
		qs.exclude.return_value = qs
		qs.count.return_value = 0
		qs.__iter__.return_value = []
		mock_articles.objects.filter.return_value = qs
		mock_trials.objects.filter.return_value = qs
		mock_ref.objects.count.return_value = 0
		call_command('detect_trial_references', '--article-id', '1', '--trial-id', '2', '--limit', '5', '--dry-run')
		mock_articles.objects.filter.assert_any_call(article_id=1)
		mock_trials.objects.filter.assert_any_call(trial_id=2)

	@patch('gregory.management.commands.detect_trial_references.ArticleTrialReference')
	@patch('gregory.management.commands.detect_trial_references.Trials')
	@patch('gregory.management.commands.detect_trial_references.Articles')
	def test_reset_option_deletes_existing(self, mock_articles, mock_trials, mock_ref):
		qs = MagicMock()
		qs.filter.return_value = qs
		qs.exclude.return_value = qs
		qs.count.return_value = 0
		qs.__iter__.return_value = []
		mock_articles.objects.filter.return_value = qs
		mock_trials.objects.filter.return_value = qs
		mock_ref.objects.count.return_value = 0
		call_command('detect_trial_references', '--reset')
		mock_ref.objects.all.return_value.delete.assert_called_once()
