"""
Tests for the staff email preview resembling what actually gets sent.

Before this change, templates/emails/views.py built preview context without
`organization=` and ignored `article_sort_order` — it always took the newest
N articles by date regardless of the list's configuration. A relevancy-mode
digest therefore previewed as something no recipient would ever receive.

The preview now routes through the same selection helpers
(`select_digest_articles`, `rank_and_limit_articles`, `get_articles_for_list`,
`get_trials_for_list`) as the send commands, so it can't drift from them.
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
from django.test import RequestFactory, TestCase
from django.utils import timezone

from gregory.models import Articles, ArticleSubjectRelevance, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers
from templates.emails.views import _build_preview_context


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class PreviewMatchesWeeklySummaryRelevancyModeTest(TestCase):
	"""A preview for a relevancy-mode list must return the same article set
	the command would select for the same list and window."""

	def setUp(self):
		self.org = Organization.objects.create(name="Preview Org", slug="preview-org")
		self.team = Team.objects.create(
			name="Preview Team", organization=self.org, slug="preview-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Preview Subject",
			team=self.team,
			subject_slug="preview-subject",
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
			first_name="Preview",
			last_name="Sub",
			email="preview-sub@example.com",
			active=True,
		)
		# article_limit smaller than the candidate count forces truncation —
		# exactly the branch that used to ignore article_sort_order.
		self.lst = Lists.objects.create(
			list_name="Preview Relevancy List",
			team=self.team,
			weekly_digest=True,
			article_sort_order="relevancy",
			article_limit=2,
			lookback_days=30,
			list_email_subject="Preview Weekly",
		)
		self.lst.subjects.add(self.subject)
		self.subscriber.subscriptions.add(self.lst)

		self.articles = []
		for i in range(4):
			article = Articles.objects.create(
				title=f"Preview Article {i}",
				link=f"https://example.com/preview-{i}",
				doi=f"10.6543/preview-{i}",
			)
			Articles.objects.filter(pk=article.pk).update(
				discovery_date=timezone.now() - timedelta(hours=i)
			)
			article.refresh_from_db()
			article.subjects.add(self.subject)
			ArticleSubjectRelevance.objects.create(
				article=article, subject=self.subject, is_relevant=True
			)
			self.articles.append(article)

	def _run_command_and_capture(self):
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
			call_command("send_weekly_summary", stdout=StringIO())

		return captured

	def _preview_article_pks(self):
		rf = RequestFactory()
		request = rf.get(
			"/emails/preview/weekly_summary/", {"list_id": str(self.lst.list_id)}
		)
		context = _build_preview_context(request, "weekly_summary")
		return sorted(
			a.pk
			for a in list(context.get("articles", []))
			+ list(context.get("additional_articles", []))
		)

	def test_preview_matches_command_selection(self):
		command_context = self._run_command_and_capture()
		command_pks = sorted(
			a.pk
			for a in list(command_context.get("articles", []))
			+ list(command_context.get("additional_articles", []))
		)

		preview_pks = self._preview_article_pks()

		self.assertEqual(len(command_pks), 2)
		self.assertEqual(preview_pks, command_pks)

	def test_preview_respects_article_limit(self):
		preview_pks = self._preview_article_pks()
		self.assertEqual(len(preview_pks), 2)

	def test_preview_picks_newest_among_equal_priority(self):
		"""All 4 candidate articles are equally manually-relevant (priority
		1000); the tie-break is discovery_date, newest first."""
		preview_pks = self._preview_article_pks()
		newest_two = sorted(a.pk for a in self.articles[:2])
		self.assertEqual(preview_pks, newest_two)


class PreviewOrganizationTest(TestCase):
	"""templates/emails/views.py must pass organization= through, the way
	all three send commands do, so org_content_map resolves identically."""

	def setUp(self):
		self.org = Organization.objects.create(
			name="Preview Org Ctx", slug="preview-org-ctx"
		)
		self.team = Team.objects.create(
			name="Preview Org Team", organization=self.org, slug="preview-org-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Preview Org Subject",
			team=self.team,
			subject_slug="preview-org-subject",
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
		self.lst = Lists.objects.create(
			list_name="Preview Org List",
			team=self.team,
			weekly_digest=True,
			article_sort_order="date",
			lookback_days=30,
			list_email_subject="Preview Org Weekly",
		)
		self.lst.subjects.add(self.subject)

	def test_preview_context_carries_organization_scoped_content_map(self):
		from gregory.models import ArticleOrgContent

		article = Articles.objects.create(
			title="Org Preview Article",
			link="https://example.com/org-preview",
			doi="10.6543/org-preview",
		)
		article.subjects.add(self.subject)
		ArticleOrgContent.objects.create(
			article=article, organization=self.org, takeaways="Org takeaways"
		)

		rf = RequestFactory()
		request = rf.get(
			"/emails/preview/weekly_summary/", {"list_id": str(self.lst.list_id)}
		)
		context = _build_preview_context(request, "weekly_summary")

		self.assertIn(article.article_id, context["org_content_map"])
