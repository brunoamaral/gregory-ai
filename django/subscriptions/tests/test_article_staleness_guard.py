"""
Tests for the article staleness guard (Lists.article_max_age_days).

`Articles.discovery_date` is `auto_now_add` — it records when the
feedreaders first saw the row, not when the paper was published. A bulk
import stamps every row with the same day, and without this guard the whole
historical set becomes eligible for the next digest. This mirrors the
trials fix (`Lists.trial_max_age_days`, see test_weekly_digest_staleness.py)
but for articles, checked against `published_date` (nulls always kept).

Parametrised across every place articles are selected:
- send_weekly_summary, all-articles mode
- send_weekly_summary, date-sort mode
- send_weekly_summary, relevancy mode
- get_articles_for_list (admin summary)
- get_latest_research_by_category (Latest Research section)
"""

import os
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	Subject,
	Team,
	TeamCategory,
)
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.management.commands.utils.subscription import (
	apply_article_max_age_filter,
	get_articles_for_list,
	get_latest_research_by_category,
)
from subscriptions.models import Lists, SentArticleNotification, Subscribers


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class _ArticleStalenessBase(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(
			name="Article Staleness Org", slug="article-staleness-org"
		)
		self.team = Team.objects.create(
			name="Article Staleness Team",
			organization=self.org,
			slug="article-staleness-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Article Staleness Subject",
			team=self.team,
			subject_slug="article-staleness-subject",
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
		self.subscriber = Subscribers.objects.create(
			first_name="Article",
			last_name="Staleness",
			email="article-staleness@example.com",
			active=True,
		)

	def _make_article(
		self, title, discovery_days_ago=1, published_days_ago=None, doi=None
	):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/articles/{title.replace(' ', '-').lower()}",
			doi=doi or f"10.8888/{title.replace(' ', '-').lower()}",
		)
		if published_days_ago is not None:
			article.published_date = timezone.now() - timedelta(days=published_days_ago)
			article.save(update_fields=["published_date"])
		Articles.objects.filter(pk=article.pk).update(
			discovery_date=timezone.now() - timedelta(days=discovery_days_ago)
		)
		article.refresh_from_db()
		article.subjects.add(self.subject)
		return article

	def _make_list(self, **kwargs):
		defaults = dict(
			list_name="Article Staleness Digest",
			team=self.team,
			weekly_digest=True,
			admin_summary=True,
			article_sort_order="date",
			article_limit=50,
			lookback_days=30,
			list_email_subject="Article Staleness Weekly",
		)
		defaults.update(kwargs)
		lst = Lists.objects.create(**defaults)
		lst.subjects.add(self.subject)
		self.subscriber.subscriptions.add(lst)
		return lst

	def _run_weekly_summary(self, **options):
		with patch(
			"subscriptions.management.commands.send_weekly_summary.send_email",
			return_value=_mock_ok_result(),
		):
			out = StringIO()
			call_command("send_weekly_summary", stdout=out, **options)
			return out.getvalue()

	def _run_admin_summary(self, **options):
		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=_mock_ok_result(),
		):
			out = StringIO()
			call_command("send_admin_summary", stdout=out, **options)
			return out.getvalue()

	def _sent_article_ids(self, lst):
		return set(
			SentArticleNotification.objects.filter(
				list=lst, subscriber=self.subscriber
			).values_list("article_id", flat=True)
		)


class AllArticlesModeStalenessTest(_ArticleStalenessBase):
	def test_stale_article_excluded(self):
		lst = self._make_list(article_max_age_days=90, article_sort_order="date")
		stale = self._make_article(
			"Stale AA", discovery_days_ago=1, published_days_ago=200
		)
		self._run_weekly_summary(all_articles=True)
		self.assertNotIn(stale.pk, self._sent_article_ids(lst))

	def test_recent_article_included(self):
		lst = self._make_list(article_max_age_days=90, article_sort_order="date")
		recent = self._make_article(
			"Recent AA", discovery_days_ago=1, published_days_ago=7
		)
		self._run_weekly_summary(all_articles=True)
		self.assertIn(recent.pk, self._sent_article_ids(lst))


class DateSortModeStalenessTest(_ArticleStalenessBase):
	def test_stale_article_excluded(self):
		lst = self._make_list(article_max_age_days=90, article_sort_order="date")
		stale = self._make_article(
			"Stale Date", discovery_days_ago=1, published_days_ago=200
		)
		self._run_weekly_summary()
		self.assertNotIn(stale.pk, self._sent_article_ids(lst))

	def test_recent_article_included(self):
		lst = self._make_list(article_max_age_days=90, article_sort_order="date")
		recent = self._make_article(
			"Recent Date", discovery_days_ago=1, published_days_ago=7
		)
		self._run_weekly_summary()
		self.assertIn(recent.pk, self._sent_article_ids(lst))


class RelevancyModeStalenessTest(_ArticleStalenessBase):
	def _make_reviewed_article(self, title, discovery_days_ago, published_days_ago):
		article = self._make_article(
			title,
			discovery_days_ago=discovery_days_ago,
			published_days_ago=published_days_ago,
		)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject, is_relevant=True
		)
		return article

	def test_stale_article_excluded(self):
		lst = self._make_list(article_max_age_days=90, article_sort_order="relevancy")
		stale = self._make_reviewed_article(
			"Stale Relevancy", discovery_days_ago=1, published_days_ago=200
		)
		self._run_weekly_summary()
		self.assertNotIn(stale.pk, self._sent_article_ids(lst))

	def test_recent_article_included(self):
		lst = self._make_list(article_max_age_days=90, article_sort_order="relevancy")
		recent = self._make_reviewed_article(
			"Recent Relevancy", discovery_days_ago=1, published_days_ago=7
		)
		self._run_weekly_summary()
		self.assertIn(recent.pk, self._sent_article_ids(lst))


class AdminSummaryStalenessTest(_ArticleStalenessBase):
	def test_stale_article_excluded(self):
		lst = self._make_list(article_max_age_days=90)
		stale = self._make_article(
			"Stale Admin", discovery_days_ago=1, published_days_ago=200
		)
		self._run_admin_summary()
		self.assertNotIn(stale.pk, self._sent_article_ids(lst))

	def test_recent_article_included(self):
		lst = self._make_list(article_max_age_days=90)
		recent = self._make_article(
			"Recent Admin", discovery_days_ago=1, published_days_ago=7
		)
		self._run_admin_summary()
		self.assertIn(recent.pk, self._sent_article_ids(lst))


class LatestResearchStalenessTest(_ArticleStalenessBase):
	def setUp(self):
		super().setUp()
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name="Staleness Category",
			category_slug="staleness-category",
		)

	def _make_category_article(
		self, title, discovery_days_ago=1, published_days_ago=None
	):
		article = self._make_article(
			title,
			discovery_days_ago=discovery_days_ago,
			published_days_ago=published_days_ago,
		)
		article.team_categories.add(self.category)
		return article

	def test_stale_article_excluded(self):
		lst = self._make_list(article_max_age_days=90)
		lst.latest_research_categories.add(self.category)
		stale = self._make_category_article(
			"Stale LR", discovery_days_ago=1, published_days_ago=200
		)
		result = get_latest_research_by_category(lst, days=lst.lookback_days)
		all_articles = [a for arts in result.values() for a in arts]
		self.assertNotIn(stale, all_articles)

	def test_recent_article_included(self):
		lst = self._make_list(article_max_age_days=90)
		lst.latest_research_categories.add(self.category)
		recent = self._make_category_article(
			"Recent LR", discovery_days_ago=1, published_days_ago=7
		)
		result = get_latest_research_by_category(lst, days=lst.lookback_days)
		all_articles = [a for arts in result.values() for a in arts]
		self.assertIn(recent, all_articles)


class NullPublishedDateAlwaysKeptTest(_ArticleStalenessBase):
	"""Articles with published_date NULL must be kept regardless of the
	guard — dropping unknown-age articles would silently hide new ones."""

	def test_null_published_date_included_all_sites(self):
		lst = self._make_list(article_max_age_days=90, article_sort_order="date")
		undated = self._make_article(
			"Undated", discovery_days_ago=1, published_days_ago=None
		)
		self.assertIsNone(undated.published_date)

		qs = apply_article_max_age_filter(Articles.objects.all(), lst)
		self.assertIn(undated.pk, qs.values_list("pk", flat=True))

		self.assertIn(
			undated.pk,
			get_articles_for_list(lst, days=lst.lookback_days).values_list(
				"pk", flat=True
			),
		)


class ArticleMaxAgeDaysNoneDisablesFilterTest(_ArticleStalenessBase):
	def test_none_disables_check(self):
		lst = self._make_list(article_max_age_days=None)
		ancient = self._make_article(
			"Ancient", discovery_days_ago=1, published_days_ago=3000
		)
		qs = apply_article_max_age_filter(Articles.objects.all(), lst)
		self.assertIn(ancient.pk, qs.values_list("pk", flat=True))


class BulkImportRegressionTest(_ArticleStalenessBase):
	"""Regression reproducing the incident shape: many articles sharing
	today's discovery_date with publication dates spread over 20 years must
	reduce to only the handful inside the article_max_age_days window."""

	def test_bulk_import_shape_reduces_to_recent_window(self):
		lst = self._make_list(article_max_age_days=90)

		expected_recent_pks = set()
		for i in range(1000):
			# Spread publication dates over ~20 years (7300 days); every 100th
			# article lands inside the 90-day window.
			published_days_ago = (i * 7300) // 1000
			article = self._make_article(
				f"Bulk {i}",
				discovery_days_ago=0,
				published_days_ago=published_days_ago,
			)
			if published_days_ago <= 90:
				expected_recent_pks.add(article.pk)

		self.assertGreater(len(expected_recent_pks), 0)
		self.assertLess(len(expected_recent_pks), 1000)

		qs = apply_article_max_age_filter(
			Articles.objects.filter(subjects=self.subject), lst
		)
		result_pks = set(qs.values_list("pk", flat=True))
		self.assertEqual(result_pks, expected_recent_pks)
