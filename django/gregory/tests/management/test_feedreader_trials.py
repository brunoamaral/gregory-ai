import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch, MagicMock
import pytz

from gregory.management.commands.feedreader_trials import Command
from gregory.classes import EUTrialParser


class FeedreaderTrialsCommandTest(TestCase):
	@patch("gregory.management.commands.feedreader_trials.Command.setup")
	@patch("gregory.management.commands.feedreader_trials.Command.process_feeds")
	def test_handle_invokes_setup_and_process(self, mock_process, mock_setup):
		call_command("feedreader_trials")
		mock_setup.assert_called_once()
		mock_process.assert_called_once()

	def test_safe_change_reason_truncates_long_reason(self):
		from gregory.utils.registry_utils import safe_change_reason
		long_reason = "a" * 120
		self.assertEqual(len(safe_change_reason(long_reason)), 100)
		self.assertEqual(safe_change_reason("short"), "short")

	def test_parse_date_returns_utc_datetime(self):
		cmd = Command()
		cmd.setup()  # Initialize tzinfos
		dt = cmd.parse_date("2024-01-02 12:34:56 EST")
		self.assertEqual(dt.tzinfo, pytz.utc)
		self.assertEqual(dt.year, 2024)

	def test_extract_identifiers_from_link_and_guid(self):
		parser = EUTrialParser()
		# Use a link that includes clinicaltrials.gov to make the test pass
		link = "https://clinicaltrials.gov/example/?EUDRACT=2024-123456-12-34&EUCT=2024-123456-12-34"
		result = parser.extract_identifiers(link, "NCT12345678")
		self.assertEqual(result["eudract"], "2024-123456-12-34")
		self.assertEqual(result["nct"], "NCT12345678")
		self.assertEqual(result["euct"], "2024-123456-12-34")

	def test_parse_summary_parses_html(self):
		parser = EUTrialParser()
		html = (
			"Trial number</b>: 2024-123456-12<br>"
			"Therapeutic Areas</b>: Oncology<br>"
			"Status in each country</b>: US:Ongoing<br>"
			"Trial region</b>: Europe<br>"
			"Results posted</b>: Yes<br>"
			"Medical conditions</b>: Cancer<br>"
			"Overall trial status</b>: Completed<br>"
			"Primary end point</b>: Survival<br>"
			"Secondary end point</b>: Response<br>"
			"Overall decision date</b>: 2024-01-01<br>"
			"Countries decision date</b>: US:2024-01-02<br>"
			"Sponsor</b>: Example Inc<br>"
			"Sponsor type</b>: Industry"
		)
		data = parser.parse_summary(html)
		self.assertEqual(data["therapeutic_areas"], "Oncology")
		self.assertTrue(data["results_posted"])
		self.assertEqual(data["trial_region"], "Europe")
		# Consolidation additions: source_register set, overall status -> recruitment_status
		self.assertEqual(data["source_register"], "EU CTIS")
		self.assertEqual(data["recruitment_status"], "Completed")

	def test_parse_summary_real_ctis_item(self):
		"""Fields drawn from a real euclinicaltrials.eu CTIS feed item."""
		parser = EUTrialParser()
		html = (
			"<b>Trial number</b>: 2025-524316-11-00<br />"
			"<b>Overall trial status</b>: Authorised, recruitment pending<br />"
			"<b>Medical conditions</b>: Primary Progressive Multiple Sclerosis (PPMS)<br />"
			"<b>Trial phase</b>: Therapeutic confirmatory  (Phase III)<br />"
			"<b>Age of participants</b>: 18-64 years<br />"
			"<b>Gender of participants</b>: Female, Male<br />"
			"<b>Planned number of participants</b>: 398<br />"
			"<b>Sponsor</b>: Zenas Biopharma (USA) LLC<br />"
			"<b>Sponsor type</b>: Pharmaceutical company<br />"
			"<b>Trial product</b>: Orelabrutinib, Placebo tablets<br />"
			"<b>Results posted</b>: No<br />"
			"<b>Overall decision date</b>: 08/12/2025<br />"
			"<b>Last updated date</b>: 25/05/2026"
		)
		import datetime

		data = parser.parse_summary(html)
		self.assertEqual(data["phase"], "Therapeutic confirmatory  (Phase III)")
		self.assertEqual(data["inclusion_agemin"], "18")
		self.assertEqual(data["inclusion_agemax"], "64")
		self.assertEqual(data["inclusion_gender"], "Female, Male")
		self.assertEqual(data["target_size"], "398")
		self.assertEqual(data["intervention"], "Orelabrutinib, Placebo tablets")
		# Explicit "No" -> False (a real value, not None)
		self.assertIs(data["results_posted"], False)
		self.assertEqual(data["last_refreshed_on"], datetime.date(2026, 5, 25))
		# Day-first parsing: 08/12/2025 is 8 December, not 12 August
		self.assertEqual(data["overall_decision_date"], datetime.date(2025, 12, 8))

	def test_parse_summary_results_posted_absent_is_none(self):
		"""When the feed omits the Results posted line, results_posted is None (not False)
		so a non-destructive update won't blank a value set by another source."""
		parser = EUTrialParser()
		html = (
			"<b>Trial number</b>: 2025-524316-11-00<br />"
			"<b>Overall trial status</b>: Ongoing, recruiting<br />"
			"<b>Medical conditions</b>: Multiple Sclerosis<br />"
		)
		data = parser.parse_summary(html)
		self.assertIsNone(data["results_posted"])

	@patch("gregory.management.commands.feedreader_trials.feedparser.parse")
	@patch("gregory.management.commands.feedreader_trials.requests.get")
	@patch("gregory.management.commands.feedreader_trials.Sources")
	def test_process_feeds_respects_ssl_flag(self, mock_sources, mock_get, mock_parse):
		cmd = Command()
		source = MagicMock(
			link="http://example.com",
			ignore_ssl=True,
			name="Src",
			team=MagicMock(),
			subject=MagicMock(),
		)
		mock_sources.objects.filter.return_value = [source]
		mock_parse.return_value = {"entries": []}
		mock_get.return_value.content = b""
		cmd.process_feeds()
		mock_get.assert_called_once_with("http://example.com", verify=False, timeout=30)
		mock_parse.assert_any_call(b"")
