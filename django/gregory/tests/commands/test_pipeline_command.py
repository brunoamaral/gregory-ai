import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch

class PipelineCommandTest(TestCase):
	@patch('gregory.management.commands.pipeline.call_command')
	def test_pipeline_runs_all_commands(self, mock_call):
		call_command('pipeline')
	expected = [
		patch.call('feedreader_articles'),
		patch.call('feedreader_trials'),
		patch.call('find_doi'),
		patch.call('update_articles_info'),
		patch.call('get_authors'),
		patch.call('update_orcid'),
		patch.call('rebuild_categories'),
		patch.call('get_takeaways'),
		patch.call('predict_articles', all_teams=True),
		patch.call('detect_trial_references', recent=True, days=30),
	]
	self.assertEqual(mock_call.call_args_list, expected)

	@patch('gregory.management.commands.pipeline.call_command')
	def test_command_errors_are_logged_and_execution_continues(self, mock_call):
		mock_call.side_effect = [
			None,
			Exception('boom'),
			None,
			None,
			None,
			None,
			None,
			None,
			None,
			None,
		]
		out = StringIO()
		err = StringIO()
		call_command('pipeline', '--recent-days', '10', stdout=out, stderr=err)
		self.assertIn('Error running command feedreader_trials', err.getvalue())
		mock_call.assert_any_call('detect_trial_references', recent=True, days=10)
		self.assertEqual(len(mock_call.call_args_list), 10)
