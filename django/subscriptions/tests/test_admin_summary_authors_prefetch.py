"""
Same regression as test_weekly_summary_authors_prefetch.py, for
send_admin_summary: `list_articles` must prefetch `authors` alongside the
existing `filtered_ml_predictions` Prefetch, or article_limit truncation
turns the queryset into a plain list and article_card.html's
`article.authors.exists()` / `.all()` cost two queries per article.

This runs the real `send_admin_summary` command (not a reimplementation of
its queryset construction, which would pass trivially regardless of what the
command itself does) and counts only the queries that touch the authors
relation, ignoring the command's other, unrelated per-article costs — the
SentArticleNotification write loop (a real O(N) cost: it must write one row
per article) and the pre-existing `ml_predictions_detail.exists()` template
fallback (a separate bug, out of this test's scope). Filtering to
authors/articles_authors keeps the assertion about exactly what task 1
changed.
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

from gregory.models import Articles, Authors, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers


class AdminSummaryAuthorsPrefetchTest(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Admin Prefetch Org", slug="admin-prefetch-org"
		)
		self.team = Team.objects.create(
			name="Admin Prefetch Team",
			organization=self.organization,
			slug="admin-prefetch-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Admin Prefetch Subject",
			team=self.team,
			subject_slug="admin-prefetch-subject",
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

		self.admin_list = Lists.objects.create(
			list_name="Admin Prefetch List",
			admin_summary=True,
			team=self.team,
			article_limit=15,
			lookback_days=30,
			list_email_subject="Admin Prefetch",
		)
		self.admin_list.subjects.add(self.subject)

		self.subscriber = Subscribers.objects.create(
			first_name="Admin",
			last_name="Prefetch",
			email="admin-prefetch@example.com",
			active=True,
		)
		self.subscriber.subscriptions.add(self.admin_list)

		# One more article than article_limit forces the truncate-to-list
		# step (send_admin_summary.py: `new_articles = list(new_articles...
		# [:article_limit])`) — the exact step that drops the organizer's
		# own prefetch attempt, since a plain list has no
		# `prefetch_related` method.
		for i in range(16):
			article = Articles.objects.create(
				title=f"Article {i}",
				doi=f"10.9998/authors-prefetch-{i}",
				link=f"https://example.com/authors-prefetch-{i}",
			)
			article.subjects.add(self.subject)
			author = Authors.objects.create(given_name="John", family_name=f"Roe{i}")
			article.authors.add(author)

	def test_authors_queries_do_not_scale_with_article_count(self):
		mock_result = MagicMock(status_code=200)
		mock_result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=mock_result,
		):
			with CaptureQueriesContext(connection) as ctx:
				call_command("send_admin_summary", stdout=StringIO())

		authors_queries = [
			q
			for q in ctx.captured_queries
			if "authors" in q["sql"]
			and "sentarticlenotification" not in q["sql"].lower()
		]

		# 15 articles are rendered (article_limit=15 out of 16 available).
		# A single batched prefetch issues one query total, regardless of
		# article count; the pre-fix bug issued two per article
		# (`.exists()` + `.all()`), i.e. ~30 here.
		self.assertLessEqual(
			len(authors_queries),
			2,
			"Query count against the authors relation scales with article "
			"count — the authors prefetch is not surviving article_limit "
			"truncation in send_admin_summary:\n"
			+ "\n".join(q["sql"][:250] for q in authors_queries),
		)
