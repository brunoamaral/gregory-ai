"""
Tests for P1 finding 3: Latest Research must implement its own definition —
new articles since the subscriber's last email for that list, grouped by
category, deduplicated against the main section, tracked through the same
`SentArticleNotification` table as the main content (not a fixed 30-day
standing digest built from a query the size-shrink loop can't see).

Regression coverage for `send_weekly_summary` and
`EmailRenderingPipeline.prepare_optimized_context`. Trials are deliberately out
of scope for this section (a documented decision, not an oversight — see
docs/subscriptions.md).
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
	ArticleOrgContent,
	Subject,
	Team,
	TeamCategory,
	Trials,
)
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, SentArticleNotification, Subscribers
from templates.emails.components.content_organizer import EmailRenderingPipeline


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class LatestResearchDeltaTestCase(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="LR Org", slug="lr-org")
		self.team = Team.objects.create(
			name="LR Team", organization=self.org, slug="lr-team"
		)
		self.subject = Subject.objects.create(
			subject_name="LR Subject", team=self.team, subject_slug="lr-subject"
		)
		self.category = TeamCategory.objects.create(
			team=self.team, category_name="LR Category", category_slug="lr-category"
		)
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Test Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]
		self.subscriber = Subscribers.objects.create(
			first_name="LR",
			last_name="Tester",
			email="lr@example.com",
			active=True,
		)
		self.digest_list = Lists.objects.create(
			list_name="LR Digest",
			weekly_digest=True,
			team=self.team,
			list_email_subject="LR Weekly",
			article_sort_order="date",
		)
		self.digest_list.subjects.add(self.subject)
		self.digest_list.latest_research_categories.add(self.category)
		self.subscriber.subscriptions.add(self.digest_list)

	def _make_category_article(self, title, days_ago=1, doi=None):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/articles/{title.replace(' ', '-').lower()}",
			doi=doi or f"10.7777/{title.replace(' ', '-').lower()}",
		)
		Articles.objects.filter(pk=article.pk).update(
			discovery_date=timezone.now() - timedelta(days=days_ago)
		)
		article.refresh_from_db()
		article.team_categories.add(self.category)
		return article

	def _make_main_article(self, title, days_ago=1):
		"""An article matched by list subjects, not by category — lands in the
		main section, not Latest Research."""
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/articles/{title.replace(' ', '-').lower()}",
			doi=f"10.7777/{title.replace(' ', '-').lower()}",
		)
		Articles.objects.filter(pk=article.pk).update(
			discovery_date=timezone.now() - timedelta(days=days_ago)
		)
		article.refresh_from_db()
		article.subjects.add(self.subject)
		return article

	def _run_and_capture_context(self, **command_kwargs):
		captured = {}
		real_get_template = __import__(
			"django.template.loader", fromlist=["get_template"]
		).get_template

		def fake_get_template(template_name, using=None):
			tmpl = real_get_template(template_name, using=using)
			original_render = tmpl.render

			def capturing_render(context=None, request=None):
				if isinstance(context, dict):
					captured.update(context)
				return original_render(context, request)

			tmpl.render = capturing_render
			return tmpl

		with (
			patch(
				"subscriptions.management.commands.send_weekly_summary.send_email",
				return_value=_mock_ok_result(),
			),
			patch(
				"subscriptions.management.commands.send_weekly_summary.get_template",
				side_effect=fake_get_template,
			),
		):
			out = StringIO()
			call_command("send_weekly_summary", stdout=out, **command_kwargs)

		return captured

	def _run_and_capture_html(self, **command_kwargs):
		"""Like _run_and_capture_context, but also captures the exact HTML
		string passed to send_email — needed to measure real render sizes for
		the shrink test rather than guessing at SAFE_BODY_CHARS."""
		captured_html = {}

		def _send_email_side_effect(**kwargs):
			captured_html["html"] = kwargs.get("html")
			return _mock_ok_result()

		with patch(
			"subscriptions.management.commands.send_weekly_summary.send_email",
			side_effect=_send_email_side_effect,
		):
			out = StringIO()
			call_command("send_weekly_summary", stdout=out, **command_kwargs)

		return captured_html.get("html", "")

	def _lr_articles_in_context(self, ctx):
		flat = []
		for category_data in ctx.get("latest_research", {}).get("categories", []):
			flat.extend(category_data.get("articles", []))
		return flat


class SentRecordExclusionTest(LatestResearchDeltaTestCase):
	def test_already_sent_article_excluded_from_latest_research(self):
		"""Regression: an article already recorded as sent to this subscriber
		for this list must not appear in Latest Research. Must fail against
		current code, which never checks SentArticleNotification at all."""
		already_sent = self._make_category_article("Already Sent Article")
		SentArticleNotification.objects.create(
			article=already_sent, list=self.digest_list, subscriber=self.subscriber
		)
		fresh = self._make_category_article("Fresh Article")

		ctx = self._run_and_capture_context()
		lr_articles = self._lr_articles_in_context(ctx)

		self.assertNotIn(already_sent, lr_articles)
		self.assertIn(fresh, lr_articles)

	def test_latest_research_article_recorded_as_sent_and_absent_next_run(self):
		article = self._make_category_article("Recorded Article")

		ctx1 = self._run_and_capture_context()
		self.assertIn(article, self._lr_articles_in_context(ctx1))
		self.assertTrue(
			SentArticleNotification.objects.filter(
				article=article, list=self.digest_list, subscriber=self.subscriber
			).exists()
		)

		# Second run: the same article must not reappear.
		ctx2 = self._run_and_capture_context()
		self.assertNotIn(article, self._lr_articles_in_context(ctx2))


class CategoryTrialsOnlyTest(LatestResearchDeltaTestCase):
	def test_category_with_only_trials_contributes_nothing(self):
		"""TeamCategory.trials is a real relation but Latest Research is
		articles-only by decision — a category whose only new content is
		trials must contribute nothing, and the section must be omitted."""
		# A qualifying main-list trial so the send isn't skipped outright —
		# without it, zero main content and zero LR candidates would make
		# this test pass vacuously (the whole list gets skipped).
		main_trial = Trials.objects.create(
			title="Main List Trial", link="https://example.com/trials/main-list-trial"
		)
		Trials.objects.filter(pk=main_trial.pk).update(
			discovery_date=timezone.now() - timedelta(days=1)
		)
		main_trial.refresh_from_db()
		main_trial.subjects.add(self.subject)

		# A trial linked to the Latest Research category via team_categories —
		# category.articles (what Latest Research reads) stays empty even
		# though the category has fresh content through this other relation.
		category_trial = Trials.objects.create(
			title="Category-Linked Trial",
			link="https://example.com/trials/category-linked-trial",
		)
		Trials.objects.filter(pk=category_trial.pk).update(
			discovery_date=timezone.now() - timedelta(days=1)
		)
		category_trial.refresh_from_db()
		category_trial.team_categories.add(self.category)

		ctx = self._run_and_capture_context()

		self.assertEqual(self._lr_articles_in_context(ctx), [])
		latest_research = ctx.get("latest_research", {})
		self.assertFalse(latest_research.get("has_latest_research", False))


class DedupAgainstMainSectionTest(LatestResearchDeltaTestCase):
	def test_article_in_both_main_and_category_renders_once_in_main(self):
		"""An article matched by both the list's subjects (main section) and a
		Latest Research category must render once, in the main section."""
		shared = Articles.objects.create(
			title="Shared Article",
			link="https://example.com/articles/shared-article",
			doi="10.7777/shared-article",
		)
		Articles.objects.filter(pk=shared.pk).update(
			discovery_date=timezone.now() - timedelta(days=1)
		)
		shared.refresh_from_db()
		shared.subjects.add(self.subject)
		shared.team_categories.add(self.category)

		ctx = self._run_and_capture_context()

		main_articles = list(ctx.get("articles", [])) + list(
			ctx.get("additional_articles", [])
		)
		self.assertIn(shared, main_articles)
		self.assertNotIn(shared, self._lr_articles_in_context(ctx))


class LookbackDaysTest(LatestResearchDeltaTestCase):
	def test_honours_list_lookback_days_not_fixed_30(self):
		self.digest_list.lookback_days = 8
		self.digest_list.save()

		old_article = self._make_category_article("Old Category Article", days_ago=20)
		recent_article = self._make_category_article(
			"Recent Category Article", days_ago=3
		)

		ctx = self._run_and_capture_context()
		lr_articles = self._lr_articles_in_context(ctx)

		self.assertNotIn(old_article, lr_articles)
		self.assertIn(recent_article, lr_articles)


class ShrinkOnOversizeTest(LatestResearchDeltaTestCase):
	def test_oversized_latest_research_is_shrunk_not_failed(self):
		"""A Latest Research section large enough to blow past SAFE_BODY_CHARS
		must be shrunk by render_within_limit, not left to fail the send on the
		give-up path.

		Titles are DB-bounded (a generated unaccented-title column caps at
		varchar(2000)), so overflow is manufactured by patching
		SAFE_BODY_CHARS down to a threshold calibrated from one real render,
		rather than by using huge titles.
		"""
		calibration_article = self._make_category_article(
			"Calibration Article", days_ago=1
		)
		baseline_html = self._run_and_capture_html()
		baseline_len = len(baseline_html)
		self.assertGreater(baseline_len, 0)
		# The calibration send counts as sent — clear it so it's eligible
		# again below, alongside the rest of the batch.
		SentArticleNotification.objects.all().delete()

		# 30 more articles of the same shape: the full set overflows a
		# threshold set just above one article's worth of content; shrunk by
		# half repeatedly, some prefix of them fits.
		articles = [calibration_article] + [
			self._make_category_article(f"Category Article {i}", days_ago=1)
			for i in range(30)
		]
		test_limit = baseline_len + 3_000

		with patch("subscriptions.utils.email_limits.SAFE_BODY_CHARS", test_limit):
			ctx = self._run_and_capture_context()

		lr_articles = self._lr_articles_in_context(ctx)
		self.assertGreater(len(lr_articles), 0)
		self.assertLess(len(lr_articles), len(articles))

		sent_count = SentArticleNotification.objects.filter(
			list=self.digest_list, subscriber=self.subscriber
		).count()
		self.assertEqual(sent_count, len(lr_articles))


class OrgContentMapCoverageTest(TestCase):
	"""Unit-level: prepare_optimized_context must build org_content_map
	entries for Latest Research articles, not just the main section."""

	def setUp(self):
		self.org = Organization.objects.create(name="LR Map Org", slug="lr-map-org")
		self.team = Team.objects.create(
			name="LR Map Team", organization=self.org, slug="lr-map-team"
		)
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name="LR Map Category",
			category_slug="lr-map-category",
		)
		self.site = Site.objects.create(
			domain="lrmaporg.example.com", name="LR Map Org"
		)
		self.article = Articles.objects.create(
			title="LR Map Article",
			link="https://example.com/article/lr-map",
		)
		self.article.team_categories.add(self.category)
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org,
			takeaways="LR takeaway",
		)

	def test_org_content_map_covers_latest_research_articles(self):
		pipeline = EmailRenderingPipeline()
		context = pipeline.prepare_optimized_context(
			email_type="weekly_summary",
			articles=Articles.objects.none(),
			organization=self.org,
			site=self.site,
			latest_research_category_map={self.category: [self.article]},
		)
		self.assertIn(self.article.article_id, context["org_content_map"])
		self.assertEqual(
			context["org_content_map"][self.article.article_id].takeaways,
			"LR takeaway",
		)


class SentRecordLookbackWindowTest(LatestResearchDeltaTestCase):
	"""The sent-record exclusion window must be at least as wide as the
	content lookback window, or an article sent between 30 days ago and
	lookback_days ago gets treated as unsent and resent (audit finding 11).
	This applies to both the main section and Latest Research, since they
	share the same SentArticleNotification exclusion set."""

	def test_article_sent_beyond_30_days_still_excluded_when_lookback_is_wider(self):
		self.digest_list.lookback_days = 60
		self.digest_list.save()

		# A fresh main article keeps the send from being skipped outright
		# (without it, the old article being correctly excluded would leave
		# zero unsent content and the whole send would skip, making the
		# assertion below pass vacuously rather than for the right reason).
		fresh = self._make_main_article("Fresh Main Article", days_ago=1)

		main_article = self._make_main_article("Old Sent Main Article", days_ago=50)
		notification = SentArticleNotification.objects.create(
			article=main_article, list=self.digest_list, subscriber=self.subscriber
		)
		# sent_at is auto_now_add — backdate it past the old fixed 30-day
		# exclusion window but still inside the list's 60-day lookback.
		SentArticleNotification.objects.filter(pk=notification.pk).update(
			sent_at=timezone.now() - timedelta(days=40)
		)

		ctx = self._run_and_capture_context()

		rendered_main = list(ctx.get("articles", [])) + list(
			ctx.get("additional_articles", [])
		)
		self.assertIn(fresh, rendered_main)
		self.assertNotIn(main_article, rendered_main)

	def test_category_article_sent_beyond_30_days_still_excluded_from_latest_research(
		self,
	):
		self.digest_list.lookback_days = 60
		self.digest_list.save()

		# A fresh category article keeps the send from being skipped outright
		# (without it, the old article being correctly excluded would leave
		# zero content and the whole send would skip, making the assertion
		# below pass vacuously rather than for the right reason).
		fresh = self._make_category_article("Fresh Category Article", days_ago=1)

		category_article = self._make_category_article(
			"Old Sent Category Article", days_ago=50
		)
		notification = SentArticleNotification.objects.create(
			article=category_article, list=self.digest_list, subscriber=self.subscriber
		)
		SentArticleNotification.objects.filter(pk=notification.pk).update(
			sent_at=timezone.now() - timedelta(days=40)
		)

		ctx = self._run_and_capture_context()

		lr_articles = self._lr_articles_in_context(ctx)
		self.assertIn(fresh, lr_articles)
		self.assertNotIn(category_article, lr_articles)
