"""
Tests for P1 finding 2: a blanket `except Exception` in
`EmailRenderingPipeline.prepare_optimized_context` used to catch any bug and
silently return an empty fallback context, which both send commands would then
mail out as an empty digest.

The fix:
- `prepare_optimized_context` no longer swallows exceptions; they propagate to
  the caller (the management command).
- `send_weekly_summary` and `send_admin_summary` now wrap the render call in
  their own try/except, recording a `FailedNotification` and skipping the send
  rather than crashing the whole run or mailing nothing.
- Both commands also gained a post-render guard: if the organized content is
  empty (both articles and trials), the send is skipped and a
  `FailedNotification` is recorded — a backstop distinct from the pre-existing
  early skip that fires before any content is fetched for the subscriber.
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

from gregory.models import Articles, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import (
	FailedNotification,
	Lists,
	SentArticleNotification,
	Subscribers,
)
from templates.emails.components.content_organizer import (
	get_optimized_email_context as real_get_optimized_email_context,
)


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class BaseRenderErrorTestCase(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(
			name="Render Error Org", slug="render-error-org"
		)
		self.team = Team.objects.create(
			name="Render Error Team", organization=self.org, slug="render-error-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Render Error Subject",
			team=self.team,
			subject_slug="render-error-subject",
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
			first_name="Render",
			last_name="Tester",
			email="render-error@example.com",
			active=True,
		)

	def _make_article(self, title, days_ago=1):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/articles/{title.replace(' ', '-').lower()}",
			doi=f"10.9999/{title.replace(' ', '-').lower()}",
		)
		Articles.objects.filter(pk=article.pk).update(
			discovery_date=timezone.now() - timedelta(days=days_ago)
		)
		article.refresh_from_db()
		article.subjects.add(self.subject)
		return article


class WeeklySummaryRenderErrorTest(BaseRenderErrorTestCase):
	def setUp(self):
		super().setUp()
		self.digest_list = Lists.objects.create(
			list_name="Render Error Digest",
			weekly_digest=True,
			team=self.team,
			list_email_subject="Render Error Weekly",
			# Date mode bypasses ML-consensus filtering so a plain article
			# (no ML predictions, no manual review) is still included.
			article_sort_order="date",
		)
		self.digest_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(self.digest_list)

	def test_render_exception_skips_send_and_records_failure(self):
		self._make_article("Boom Article")

		with (
			patch(
				"subscriptions.management.commands.send_weekly_summary.get_optimized_email_context",
				side_effect=RuntimeError("boom"),
			),
			patch(
				"subscriptions.management.commands.send_weekly_summary.send_email"
			) as mock_send,
		):
			call_command("send_weekly_summary", stdout=StringIO())

		mock_send.assert_not_called()
		self.assertTrue(
			FailedNotification.objects.filter(
				subscriber=self.subscriber, list=self.digest_list
			).exists()
		)
		self.assertFalse(
			SentArticleNotification.objects.filter(
				subscriber=self.subscriber, list=self.digest_list
			).exists()
		)

	def test_context_organized_to_zero_content_skips_send(self):
		"""A context that legitimately organizes to zero articles and zero
		trials must not produce a send, even though unsent content existed
		going into the render (simulating a downstream organizer bug)."""
		self._make_article("Would-Be Article")

		def _empty_context(*args, **kwargs):
			ctx = real_get_optimized_email_context(*args, **kwargs)
			ctx["articles"] = []
			ctx["additional_articles"] = []
			ctx["trials"] = []
			ctx["additional_trials"] = []
			return ctx

		with (
			patch(
				"subscriptions.management.commands.send_weekly_summary.get_optimized_email_context",
				side_effect=_empty_context,
			),
			patch(
				"subscriptions.management.commands.send_weekly_summary.send_email"
			) as mock_send,
		):
			call_command("send_weekly_summary", stdout=StringIO())

		mock_send.assert_not_called()
		self.assertTrue(
			FailedNotification.objects.filter(
				subscriber=self.subscriber, list=self.digest_list
			).exists()
		)

	def test_no_unsent_content_early_skip_is_not_a_failure(self):
		"""The pre-existing early skip (no unsent articles/trials at all)
		fires before render is ever attempted and must not itself write a
		FailedNotification — that would conflate 'nothing new to send' with
		an actual rendering failure."""
		with patch(
			"subscriptions.management.commands.send_weekly_summary.send_email"
		) as mock_send:
			call_command("send_weekly_summary", stdout=StringIO())

		mock_send.assert_not_called()
		self.assertFalse(
			FailedNotification.objects.filter(
				subscriber=self.subscriber, list=self.digest_list
			).exists()
		)


class AdminSummaryRenderErrorTest(BaseRenderErrorTestCase):
	def setUp(self):
		super().setUp()
		self.admin_list = Lists.objects.create(
			list_name="Render Error Admin",
			admin_summary=True,
			team=self.team,
			list_email_subject="Render Error Admin Summary",
		)
		self.admin_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(self.admin_list)

	def test_render_exception_skips_send_and_records_failure(self):
		self._make_article("Admin Boom Article")

		with (
			patch(
				"subscriptions.management.commands.send_admin_summary.get_optimized_email_context",
				side_effect=RuntimeError("boom"),
			),
			patch(
				"subscriptions.management.commands.send_admin_summary.send_email"
			) as mock_send,
		):
			call_command("send_admin_summary", stdout=StringIO())

		mock_send.assert_not_called()
		self.assertTrue(
			FailedNotification.objects.filter(
				subscriber=self.subscriber, list=self.admin_list
			).exists()
		)
		self.assertFalse(
			SentArticleNotification.objects.filter(
				subscriber=self.subscriber, list=self.admin_list
			).exists()
		)

	def test_context_organized_to_zero_content_skips_send(self):
		self._make_article("Admin Would-Be Article")

		def _empty_context(*args, **kwargs):
			ctx = real_get_optimized_email_context(*args, **kwargs)
			ctx["articles"] = []
			ctx["additional_articles"] = []
			ctx["trials"] = []
			ctx["additional_trials"] = []
			return ctx

		with (
			patch(
				"subscriptions.management.commands.send_admin_summary.get_optimized_email_context",
				side_effect=_empty_context,
			),
			patch(
				"subscriptions.management.commands.send_admin_summary.send_email"
			) as mock_send,
		):
			call_command("send_admin_summary", stdout=StringIO())

		mock_send.assert_not_called()
		self.assertTrue(
			FailedNotification.objects.filter(
				subscriber=self.subscriber, list=self.admin_list
			).exists()
		)

	def test_no_unsent_content_early_skip_is_not_a_failure(self):
		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email"
		) as mock_send:
			call_command("send_admin_summary", stdout=StringIO())

		mock_send.assert_not_called()
		self.assertFalse(
			FailedNotification.objects.filter(
				subscriber=self.subscriber, list=self.admin_list
			).exists()
		)
