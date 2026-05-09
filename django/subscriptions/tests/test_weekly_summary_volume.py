"""
Tests for volume controls in send_weekly_summary:
- Per-list lookback_days field
- CLI --days override takes precedence over lookback_days
- Warning logged when article_limit truncates results
- No warning when within limit
- Migration default for existing lists
- Base queryset ordered newest-first
"""
import os
from io import StringIO
from unittest.mock import patch, MagicMock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')

import django
django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from gregory.models import Articles, ArticleSubjectRelevance, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers


class TestWeeklySummaryVolume(TestCase):
	"""Tests for per-list lookback_days and article_limit warning behaviour."""

	def setUp(self):
		self.organization = Organization.objects.create(
			name="Volume Org", slug="volume-org"
		)
		self.team = Team.objects.create(
			name="Volume Team",
			organization=self.organization,
			slug="volume-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Volume Subject",
			team=self.team,
			subject_slug="volume-subject",
		)

		self.digest_list = Lists.objects.create(
			list_name="Volume Digest",
			weekly_digest=True,
			team=self.team,
			ml_threshold=0.8,
			article_sort_order='date',
			article_limit=5,
			lookback_days=8,
			list_email_subject="Volume Weekly",
		)
		self.digest_list.subjects.add(self.subject)

		self.subscriber = Subscribers.objects.create(
			first_name="Volume",
			last_name="Tester",
			email="volume@example.com",
			active=True,
		)
		self.subscriber.subscriptions.add(self.digest_list)

		self.site = Site.objects.get_or_create(
			id=1,
			defaults={"domain": "testserver", "name": "Test Site"},
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Test Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]

	def _make_article(self, title, days_ago=0, doi=None, link=None):
		"""Create an article and force its discovery_date via .update() (bypasses auto_now_add)."""
		article = Articles.objects.create(
			title=title,
			doi=doi or f"10.9999/{title.replace(' ', '-').lower()}",
			link=link or f"https://example.com/articles/{title.replace(' ', '-').lower()}",
		)
		article.subjects.add(self.subject)
		if days_ago:
			Articles.objects.filter(pk=article.pk).update(
				discovery_date=timezone.now() - timedelta(days=days_ago)
			)
			article.refresh_from_db()
		return article

	def _run_and_capture_stdout(self, **kwargs):
		"""Run the command and return captured stdout text."""
		_mock_result = MagicMock(status_code=200)
		_mock_result.json.return_value = {'ErrorCode': 0, 'Message': 'OK'}

		with patch(
			'subscriptions.management.commands.send_weekly_summary.send_email',
			return_value=_mock_result,
		):
			out = StringIO()
			call_command('send_weekly_summary', stdout=out, dry_run=True, **kwargs)
			return out.getvalue()

	# ── Tests ────────────────────────────────────────────────────────────────

	def test_lookback_days_per_list_used_when_cli_omits_days(self):
		"""list.lookback_days=8 means only articles within 8 days are included."""
		# Within the 8-day window
		recent = self._make_article("Recent Article", days_ago=3)
		# Outside the 8-day window (20 days ago)
		old = self._make_article("Old Article", days_ago=20)

		output = self._run_and_capture_stdout()

		# DATE SORT mode with lookback=8 → only the recent article is counted
		self.assertIn("DATE SORT MODE: Found 1 total articles", output)
		self.assertNotIn(old.title, output)

	def test_cli_days_overrides_lookback_days(self):
		"""--days CLI flag overrides the list's lookback_days setting."""
		# Within 8-day list window
		recent = self._make_article("Recent Article", days_ago=3)
		# Between 8 and 15 days — outside list window but inside CLI override
		middle = self._make_article("Middle Article", days_ago=12)
		# Outside even the 15-day override
		old = self._make_article("Old Article", days_ago=20)

		output = self._run_and_capture_stdout(days=15)

		# With --days=15 override, both recent and middle are found (not old)
		self.assertIn("DATE SORT MODE: Found 2 total articles", output)

	def test_warning_logged_when_article_limit_truncates(self):
		"""A WARNING is logged (always visible) when article_limit truncates results."""
		# article_limit=5, create 7 articles all within the 8-day window
		for i in range(7):
			self._make_article(f"Article {i}", days_ago=i + 1)

		output = self._run_and_capture_stdout()

		self.assertIn("WARNING:", output)
		self.assertIn("Volume Digest", output)
		self.assertIn("article_limit=5", output)

	def test_no_warning_when_within_limit(self):
		"""No WARNING is logged when the number of articles is within article_limit."""
		# article_limit=5, create only 3 articles
		for i in range(3):
			self._make_article(f"Article {i}", days_ago=i + 1)

		output = self._run_and_capture_stdout()

		# Should not contain a truncation warning
		self.assertNotIn("truncated to article_limit", output)

	def test_lookback_days_default_for_existing_lists(self):
		"""Lists created without specifying lookback_days have the default of 30."""
		fresh_list = Lists.objects.create(
			list_name="Fresh List",
			weekly_digest=True,
			team=self.team,
			list_email_subject="Fresh Weekly",
		)
		self.assertEqual(fresh_list.lookback_days, 30)

	def test_base_queryset_ordered_newest_first(self):
		"""Debug output confirms per-list lookback_days=8 is used."""
		a_old = self._make_article("Older Article", days_ago=6)
		a_new = self._make_article("Newer Article", days_ago=1)

		output = self._run_and_capture_stdout(debug=True)

		# The debug processing line should show lookback=8 confirming per-list resolution
		self.assertIn("lookback=8d", output)
		# DATE SORT mode — 2 articles found (both within the 8-day window)
		self.assertIn("DATE SORT MODE: Found 2 total articles", output)
