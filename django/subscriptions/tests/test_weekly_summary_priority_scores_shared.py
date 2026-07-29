"""
Regression test for the per-subscriber priority-scoring loop measured in
subscriptions-audit-2026-07.md (P3, task 5): send_weekly_summary's relevancy
mode used to recompute each candidate article's manual-review + ML-consensus
priority score inside the per-subscriber loop, even though none of those
inputs depend on the subscriber — only the candidate set (which articles are
still unsent) does. Measured against the real MS Weekly Digest list in the
dev DB: ~2,440 queries and ~0.8s per subscriber for a 1,219-article candidate
pool, repeating identical work 88 times.

This pins the fix (article_priority_scores computed once per list) by
asserting total query count does not scale with subscriber count when
relevancy-mode truncation is forced for every subscriber.
"""

import os
from io import StringIO
from unittest.mock import patch, MagicMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from gregory.models import ArticleSubjectRelevance, Articles, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers


class WeeklySummaryPriorityScoresSharedTest(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Priority Org", slug="priority-org"
		)
		self.team = Team.objects.create(
			name="Priority Team",
			organization=self.organization,
			slug="priority-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Priority Subject",
			team=self.team,
			subject_slug="priority-subject",
			auto_predict=True,
		)
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]
		CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Test Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)

		# article_limit < article count forces the relevancy-mode priority
		# scoring / truncation branch for every subscriber.
		self.digest_list = Lists.objects.create(
			list_name="Priority Digest",
			weekly_digest=True,
			team=self.team,
			article_sort_order="relevancy",
			article_limit=3,
			lookback_days=30,
			list_email_subject="Priority Weekly",
		)
		self.digest_list.subjects.add(self.subject)

		for i in range(6):
			article = Articles.objects.create(
				title=f"Article {i}",
				doi=f"10.9997/priority-{i}",
				link=f"https://example.com/priority-{i}",
			)
			article.subjects.add(self.subject)
			# Manually-reviewed relevance is enough to make the article a
			# relevancy-mode candidate without needing full ML consensus
			# fixtures, and it's exactly one of the two inputs the
			# priority score is built from.
			ArticleSubjectRelevance.objects.create(
				article=article, subject=self.subject, is_relevant=True
			)

	def _add_subscribers(self, count):
		for i in range(count):
			sub = Subscribers.objects.create(
				first_name="Sub",
				last_name=str(i),
				email=f"priority-sub-{i}@example.com",
				active=True,
			)
			sub.subscriptions.add(self.digest_list)

	def _run_and_count_queries(self):
		mock_result = MagicMock(status_code=200)
		mock_result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
		with patch(
			"subscriptions.management.commands.send_weekly_summary.send_email",
			return_value=mock_result,
		):
			with CaptureQueriesContext(connection) as ctx:
				call_command("send_weekly_summary", stdout=StringIO(), dry_run=True)
		return len(ctx.captured_queries)

	def test_query_count_does_not_scale_with_subscriber_count(self):
		self._add_subscribers(1)
		one_subscriber_queries = self._run_and_count_queries()

		Subscribers.objects.all().delete()
		self._add_subscribers(5)
		five_subscriber_queries = self._run_and_count_queries()

		per_extra_subscriber = (five_subscriber_queries - one_subscriber_queries) / 4

		# If priority scores were still recomputed per subscriber, each
		# extra subscriber adds ~2 queries per candidate article (6 articles
		# here) on top of the fixed per-subscriber overhead every run pays
		# regardless (sent-record lookups, per-subscriber authors prefetch,
		# etc.) — measured at ~24 queries/extra subscriber before this fix,
		# ~9 after. 15 cleanly separates the two without being so tight that
		# unrelated per-subscriber overhead trips it.
		self.assertLess(
			per_extra_subscriber,
			15,
			f"Query count scales with subscriber count "
			f"(1 subscriber={one_subscriber_queries}, "
			f"5 subscribers={five_subscriber_queries}, "
			f"~{per_extra_subscriber:.1f} queries/extra subscriber) — "
			f"priority scores are being recomputed per subscriber instead "
			f"of shared across the list.",
		)
