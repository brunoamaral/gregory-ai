"""
Tests for the capture_trial_streams management command.

The command must (1) write one JSONL line per inbound source record using mocked
importer fetches, (2) emit the documented JSON schema, and (3) NEVER read or write the
``Trials`` table — that no-DB-writes guarantee is the whole point of the command. A failing
feed must surface as a non-zero exit (CommandError).

Run:
  docker exec gregory python manage.py test gregory.tests.management.test_capture_trial_streams
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

import json
import tempfile
from unittest.mock import patch, MagicMock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from gregory.classes import ClinicalTrial
from gregory.models import Sources, Trials


class CaptureTrialStreamsTest(TestCase):
	def _make_source(self, method, name):
		return Sources.objects.create(
			name=name,
			link="https://feed.example/trials.rss",
			method=method,
			source_for="trials",
			active=True,
			ctgov_search_condition="multiple sclerosis",
		)

	def test_eu_capture_writes_jsonl_and_does_not_touch_db(self):
		self._make_source("rss", "EU Capture Source")
		fake_feed = {
			"entries": [
				{
					"title": "Captured EU Trial",
					"link": "https://www.clinicaltrialsregister.eu/ctr-search/search?query=eudract_number:2021-000123-45",
					"summary": "A summary",
					"guid": "",
				}
			]
		}
		with tempfile.TemporaryDirectory() as tmp:
			out = os.path.join(tmp, "cap.jsonl")
			with patch(
				"gregory.management.commands.feedreader_trials.feedparser.parse",
				return_value=fake_feed,
			):
				call_command("capture_trial_streams", feed="eu", output=out)
			lines = [ln for ln in open(out, encoding="utf-8") if ln.strip()]

		self.assertEqual(len(lines), 1)
		rec = json.loads(lines[0])
		self.assertEqual(rec["feed"], "eu_rss")
		self.assertEqual(rec["title"], "Captured EU Trial")
		# link is preserved (the colon may be URL-encoded to %3A by remove_utm)
		self.assertIn("2021-000123-45", rec["link"])
		self.assertIn("clinicaltrialsregister.eu", rec["link"])
		# documented schema keys are present
		for key in (
			"captured_at",
			"source_name",
			"identifiers",
			"extra_fields",
			"published_date",
		):
			self.assertIn(key, rec)
		# the guarantee: no Trials row was created (or read/matched) at any point
		self.assertEqual(Trials.objects.count(), 0)

	def test_ctgov_capture_writes_jsonl_and_does_not_touch_db(self):
		self._make_source("ctgov_api", "CTgov Capture Source")
		fake_ct = ClinicalTrial(
			title="Captured CTgov Trial",
			summary="A summary",
			link="https://clinicaltrials.gov/study/NCT00000001",
			identifiers={"nct": "NCT00000001"},
			extra_fields={},
		)
		mock_api = MagicMock()
		mock_api.get_version.return_value = {"dataTimestamp": "x"}
		mock_api.search_all.return_value = [{"protocolSection": {}}]
		mock_api.parse_study_to_clinical_trial.return_value = fake_ct

		with tempfile.TemporaryDirectory() as tmp:
			out = os.path.join(tmp, "cap.jsonl")
			with patch(
				"gregory.management.commands.feedreader_trials_ctgov.ClinicalTrialsGovAPI",
				return_value=mock_api,
			):
				call_command(
					"capture_trial_streams", feed="ctgov", output=out, max_results=5
				)
			lines = [ln for ln in open(out, encoding="utf-8") if ln.strip()]

		self.assertEqual(len(lines), 1)
		rec = json.loads(lines[0])
		self.assertEqual(rec["feed"], "ctgov_api")
		self.assertEqual(rec["identifiers"], {"nct": "NCT00000001"})
		self.assertEqual(Trials.objects.count(), 0)

	def test_feed_failure_raises_command_error(self):
		"""A failing feed must exit non-zero so schedulers notice."""
		self._make_source("rss", "EU Capture Source")
		with tempfile.TemporaryDirectory() as tmp:
			out = os.path.join(tmp, "cap.jsonl")
			with patch(
				"gregory.management.commands.feedreader_trials.feedparser.parse",
				side_effect=RuntimeError("feed boom"),
			):
				with self.assertRaises(CommandError):
					call_command("capture_trial_streams", feed="eu", output=out)
		self.assertEqual(Trials.objects.count(), 0)
