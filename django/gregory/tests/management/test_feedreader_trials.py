import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch, MagicMock
import pytz

from gregory.management.commands.feedreader_trials import Command

class FeedreaderTrialsCommandTest(TestCase):
	@patch('gregory.management.commands.feedreader_trials.Command.setup')
	@patch('gregory.management.commands.feedreader_trials.Command.process_feeds')
	def test_handle_invokes_setup_and_process(self, mock_process, mock_setup):
	call_command('feedreader_trials')
	mock_setup.assert_called_once()
	mock_process.assert_called_once()

	def test_safe_change_reason_truncates_long_reason(self):
	cmd = Command()
	long_reason = 'a' * 120
	self.assertEqual(len(cmd._safe_change_reason(long_reason)), 100)
	self.assertEqual(cmd._safe_change_reason('short'), 'short')

	def test_parse_date_returns_utc_datetime(self):
	cmd = Command()
	dt = cmd.parse_date('2024-01-02 12:34:56 EST')
	self.assertEqual(dt.tzinfo, pytz.utc)
	self.assertEqual(dt.year, 2024)

	def test_extract_identifiers_from_link_and_guid(self):
	cmd = Command()
	link = 'https://example.com/?EUDRACT=2024-123456-12-34&EUCT=2024-123456-12-34'
	result = cmd.extract_identifiers(link, 'NCT12345678')
	self.assertEqual(result['eudract'], '2024-123456-12-34')
	self.assertEqual(result['nct'], 'NCT12345678')
	self.assertEqual(result['euct'], '2024-123456-12-34')

	def test_parse_eu_clinical_trial_data_parses_html(self):
	cmd = Command()
	html = (
		'Trial number</b>: 2024-123456-12<br>'
		'Therapeutic Areas</b>: Oncology<br>'
		'Status in each country</b>: US:Ongoing<br>'
		'Trial region</b>: Europe<br>'
		'Results posted</b>: Yes<br>'
		'Medical conditions</b>: Cancer<br>'
		'Overall trial status</b>: Completed<br>'
		'Primary end point</b>: Survival<br>'
		'Secondary end point</b>: Response<br>'
		'Overall decision date</b>: 2024-01-01<br>'
		'Countries decision date</b>: US:2024-01-02<br>'
		'Sponsor</b>: Example Inc<br>'
		'Sponsor type</b>: Industry'
	)
	data = cmd.parse_eu_clinical_trial_data(html)
	self.assertEqual(data['therapeutic_areas'], 'Oncology')
	self.assertTrue(data['results_posted'])
	self.assertEqual(data['trial_region'], 'Europe')

	@patch('gregory.management.commands.feedreader_trials.feedparser.parse')
	@patch('gregory.management.commands.feedreader_trials.requests.get')
	@patch('gregory.management.commands.feedreader_trials.Sources')
	def test_process_feeds_respects_ssl_flag(self, mock_sources, mock_get, mock_parse):
	cmd = Command()
	source = MagicMock(link='http://example.com', ignore_ssl=True, name='Src', team=MagicMock(), subject=MagicMock())
	mock_sources.objects.filter.return_value = [source]
	mock_parse.return_value = {'entries': []}
	mock_get.return_value.content = b''
	cmd.process_feeds()
	mock_get.assert_called_once_with('http://example.com', verify=False)
	mock_parse.assert_any_call(b'')
