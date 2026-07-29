"""
Regression test for the authors N+1 described in subscriptions-audit-2026-07.md
(P3, finding: content_organizer.py:437's prefetch guard is skipped once
article_limit truncation turns the article queryset into a plain list).

`{% if article.authors.exists %}` / `{% for author in article.authors.all %}`
in article_card.html cost two queries per article, per subscriber, unless
`authors` was prefetched on the queryset BEFORE it was sliced/listified.
send_weekly_summary must prefetch at the point it builds the `articles`
queryset, not rely on content_organizer's own attempt (which only ever sees
whatever it's handed, and can't recover once that's already a list).

The test forces article_limit truncation (article_limit < available
articles) in two lists of different sizes, and asserts total query count
does not grow proportionally with the number of articles actually rendered
— an O(N) growth is exactly the bug, since it means authors are being
fetched one query at a time instead of via a single prefetch.
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


class WeeklySummaryAuthorsPrefetchTest(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Prefetch Org", slug="prefetch-org"
		)
		self.team = Team.objects.create(
			name="Prefetch Team",
			organization=self.organization,
			slug="prefetch-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Prefetch Subject",
			team=self.team,
			subject_slug="prefetch-subject",
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

	def _make_list_with_articles(self, name, article_limit, article_count):
		"""A digest list truncated to `article_limit`, with `article_count`
		available articles (> article_limit, so truncation always fires and
		the vulnerable list-conversion path is always exercised) each
		carrying one author."""
		digest_list = Lists.objects.create(
			list_name=name,
			weekly_digest=True,
			team=self.team,
			article_sort_order="date",
			article_limit=article_limit,
			lookback_days=30,
			list_email_subject=f"{name} Weekly",
		)
		digest_list.subjects.add(self.subject)

		subscriber = Subscribers.objects.create(
			first_name="Sub",
			last_name=name,
			email=f"{name.lower().replace(' ', '-')}@example.com",
			active=True,
		)
		subscriber.subscriptions.add(digest_list)

		for i in range(article_count):
			article = Articles.objects.create(
				title=f"{name} Article {i}",
				doi=f"10.9999/{name.lower()}-{i}",
				link=f"https://example.com/{name.lower()}-{i}",
			)
			article.subjects.add(self.subject)
			author = Authors.objects.create(given_name="Jane", family_name=f"Doe{i}")
			article.authors.add(author)

		return digest_list

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

	def test_query_count_does_not_scale_with_rendered_article_count(self):
		self._make_list_with_articles("Small List", article_limit=3, article_count=4)
		small_queries = self._run_and_count_queries()

		Lists.objects.all().delete()
		Subscribers.objects.all().delete()
		Articles.objects.all().delete()
		Authors.objects.all().delete()

		self._make_list_with_articles("Large List", article_limit=15, article_count=16)
		large_queries = self._run_and_count_queries()

		# Rendering 15 articles instead of 3 must not cost ~2 extra queries
		# per additional article (the N+1). A small constant overhead is
		# fine; anything close to 2 * (15 - 3) = 24 means the authors
		# prefetch isn't surviving truncation.
		self.assertLess(
			large_queries - small_queries,
			10,
			f"Query count scaled with article count (small={small_queries}, "
			f"large={large_queries}) — authors prefetch is not surviving "
			f"article_limit truncation.",
		)
