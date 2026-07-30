"""Tests for the admin summary's ML field health line: send_admin_summary
computes live drift on Articles.ml_score/relevant (see
docs/ml-prediction-signal-bypass-plan.md) and renders it on every send."""

import os
from io import StringIO
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase

from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	MLPredictions,
	Subject,
	Team,
)
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class AdminSummaryMlDriftTestCase(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="ML Drift Org", slug="ml-drift-org")
		self.team = Team.objects.create(
			name="ML Drift Team", organization=self.org, slug="ml-drift-team"
		)
		self.subject = Subject.objects.create(
			subject_name="ML Drift Subject",
			team=self.team,
			subject_slug="ml-drift-subject",
			auto_predict=True,
			ml_consensus_type="any",
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
			first_name="Admin",
			last_name="Tester",
			email="ml-drift-admin@example.com",
			active=True,
		)
		self.admin_list = Lists.objects.create(
			list_name="ML Drift Admin List",
			admin_summary=True,
			team=self.team,
			list_email_subject="Admin Summary",
		)
		self.admin_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(self.admin_list)

	def _make_article(self, title):
		return Articles.objects.create(
			title=title,
			link=f"https://example.com/articles/{title.replace(' ', '-').lower()}",
			doi=f"10.6666/{title.replace(' ', '-').lower()}",
		)

	def _run_and_capture(self, **command_kwargs):
		"""Run send_admin_summary, capturing both the render context and the
		rendered HTML for the admin_summary template."""
		captured = {"context": None, "html": None}
		real_get_template = __import__(
			"django.template.loader", fromlist=["get_template"]
		).get_template

		def fake_get_template(template_name, using=None):
			tmpl = real_get_template(template_name, using=using)
			original_render = tmpl.render

			def capturing_render(context=None, request=None):
				html = original_render(context, request)
				if template_name == "emails/admin_summary.html" and isinstance(
					context, dict
				):
					captured["context"] = context
					captured["html"] = html
				return html

			tmpl.render = capturing_render
			return tmpl

		with (
			patch(
				"subscriptions.management.commands.send_admin_summary.send_email",
				return_value=_mock_ok_result(),
			),
			patch(
				"subscriptions.management.commands.send_admin_summary.get_template",
				side_effect=fake_get_template,
			),
		):
			out = StringIO()
			call_command("send_admin_summary", stdout=out, **command_kwargs)

		return captured

	def _seed_reviewable_content(self):
		"""send_admin_summary skips sends entirely with nothing to review, so
		every test needs at least one fresh article to trigger a send."""
		article = self._make_article("Reviewable Article")
		article.subjects.add(self.subject)
		return article

	def test_zero_drift_renders_health_line_at_zero(self):
		self._seed_reviewable_content()

		ctx = self._run_and_capture()

		self.assertIsNotNone(ctx["context"], "template must have been rendered")
		drift = ctx["context"]["ml_drift"]
		self.assertEqual(drift["stale_ml_score"], 0)
		self.assertEqual(drift["missing_relevant"], 0)
		self.assertEqual(drift["unexpected_relevant"], 0)
		self.assertIn("0 drifted", ctx["html"])

	def test_seeded_stale_ml_score_is_reflected_in_context_and_html(self):
		self._seed_reviewable_content()

		stale_article = self._make_article("Stale Score Article")
		MLPredictions.objects.create(
			article=stale_article,
			subject=self.subject,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.5,
			predicted_relevant=False,
		)
		# The MLPredictions signal already set ml_score; force it back to NULL
		# to simulate a bulk_create write that bypassed the recompute.
		Articles.objects.filter(pk=stale_article.pk).update(ml_score=None)

		ctx = self._run_and_capture()

		drift = ctx["context"]["ml_drift"]
		self.assertEqual(drift["stale_ml_score"], 1)
		self.assertIn("1 drifted", ctx["html"])

	def test_seeded_missing_relevant_is_reflected_in_context_and_html(self):
		self._seed_reviewable_content()

		should_be_relevant = self._make_article("Should Be Relevant Article")
		should_be_relevant.subjects.add(self.subject)
		ArticleSubjectRelevance.objects.create(
			article=should_be_relevant, subject=self.subject, is_relevant=True
		)
		# The signal already synced relevant=True; force it back out of sync
		# to simulate a write path that bypassed the recompute.
		Articles.objects.filter(pk=should_be_relevant.pk).update(relevant=False)

		ctx = self._run_and_capture()

		drift = ctx["context"]["ml_drift"]
		self.assertEqual(drift["missing_relevant"], 1)
		self.assertEqual(drift["unexpected_relevant"], 0)
		self.assertIn("1 drifted", ctx["html"])
