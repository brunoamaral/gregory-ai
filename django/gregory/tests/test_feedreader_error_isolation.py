"""
Tests for per-source error isolation in the RSS feedreaders (pipeline audit, item 1.6).

A source whose fetch raises (timeout, DNS, SSL) must be skipped with a logged
error; the sources after it in the loop must still be processed.

Run:
  docker exec gregory python manage.py test gregory.tests.test_feedreader_error_isolation
"""

import os
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase

from gregory.management.commands.feedreader_articles import Command as ArticlesCommand
from gregory.management.commands.feedreader_trials import Command as TrialsCommand
from gregory.models import Sources


class ArticlesFeedIsolationTests(TestCase):
	def setUp(self):
		self.bad = Sources.objects.create(
			name="Broken feed",
			method="rss",
			source_for="science paper",
			active=True,
			link="https://broken.example/feed",
		)
		self.good = Sources.objects.create(
			name="Working feed",
			method="rss",
			source_for="science paper",
			active=True,
			link="https://working.example/feed",
		)

	def test_broken_source_does_not_skip_later_sources(self):
		cmd = ArticlesCommand()
		with patch.object(
			ArticlesCommand,
			"fetch_feed",
			side_effect=[Exception("boom"), {"entries": []}],
		) as mock_fetch:
			# Must not raise; the second source must still be fetched.
			cmd.update_articles_from_feeds()
		self.assertEqual(mock_fetch.call_count, 2)


class TrialsFeedIsolationTests(TestCase):
	def setUp(self):
		self.bad = Sources.objects.create(
			name="Broken trials feed",
			method="rss",
			source_for="trials",
			active=True,
			link="https://broken.example/trials.rss",
		)
		self.good = Sources.objects.create(
			name="Working trials feed",
			method="rss",
			source_for="trials",
			active=True,
			link="https://working.example/trials.rss",
		)

	def test_broken_source_does_not_skip_later_sources(self):
		cmd = TrialsCommand()
		cmd.setup()
		with patch(
			"gregory.management.commands.feedreader_trials.feedparser.parse",
			side_effect=[Exception("boom"), {"entries": []}],
		) as mock_parse:
			cmd.process_feeds()
		self.assertEqual(mock_parse.call_count, 2)
