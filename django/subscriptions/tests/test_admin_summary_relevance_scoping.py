"""
Tests for P1 findings 4 and 5 as resolved for the admin summary (Option A —
the featured/regular split is kept and made correct, since it drives the
admin's "High-Confidence" vs "Needs Review" triage, unlike the weekly digest
where the same split was invisible and was removed instead).

- Finding 4: the list's `ml_threshold` was never threaded into
  `confidence_threshold`, so the featured/regular split always used the
  hardcoded 0.8 default regardless of what a list was configured with.
- Finding 5: `_get_max_ml_score` must only consider ML predictions for the
  admin list's own subjects, not any subject on any team. `send_admin_summary`
  already prefetches `ml_predictions_detail` filtered to `subject__in`
  `list_subjects` into `filtered_ml_predictions`, and `_get_max_ml_score`
  prefers that attribute when present — this test guards the existing
  correct behavior against regression.
"""

import os
from io import StringIO
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase

from gregory.models import Articles, MLPredictions, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class AdminSummaryMlThresholdTestCase(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(
			name="Admin Threshold Org", slug="admin-threshold-org"
		)
		self.team = Team.objects.create(
			name="Admin Threshold Team",
			organization=self.org,
			slug="admin-threshold-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Admin Threshold Subject",
			team=self.team,
			subject_slug="admin-threshold-subject",
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
			email="admin-threshold@example.com",
			active=True,
		)

	def _make_article(self, title, doi=None):
		return Articles.objects.create(
			title=title,
			link=f"https://example.com/articles/{title.replace(' ', '-').lower()}",
			doi=doi or f"10.6666/{title.replace(' ', '-').lower()}",
		)

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

	def test_list_ml_threshold_below_default_features_article_default_would_not(self):
		"""Regression: a list configured at ml_threshold=0.6 must feature an
		article scoring 0.7 — the hardcoded 0.8 default would not. Must fail
		against current code, which never passes confidence_threshold."""
		admin_list = Lists.objects.create(
			list_name="Low Threshold Admin List",
			admin_summary=True,
			team=self.team,
			ml_threshold=0.6,
			list_email_subject="Admin Summary",
		)
		admin_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(admin_list)

		article = self._make_article("Mid Confidence Article")
		article.subjects.add(self.subject)
		MLPredictions.objects.create(
			article=article,
			subject=self.subject,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.7,
			predicted_relevant=True,
		)

		ctx = self._run_and_capture_context()

		featured = list(ctx.get("articles", []))
		self.assertIn(article, featured)

	def test_ml_prediction_for_subject_outside_list_does_not_feature_article(self):
		"""An article's only high-confidence ML prediction belongs to a
		subject that is NOT one of the admin list's subjects (e.g. another
		team's subject) — it must not be featured on the strength of that
		prediction. This currently passes because send_admin_summary already
		prefetches ml_predictions_detail scoped to the list's own subjects;
		this test guards that scoping against regression."""
		admin_list = Lists.objects.create(
			list_name="Scoped Admin List",
			admin_summary=True,
			team=self.team,
			ml_threshold=0.8,
			list_email_subject="Admin Summary",
		)
		admin_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(admin_list)

		other_team = Team.objects.create(
			name="Other Team", organization=self.org, slug="other-team-admin"
		)
		other_subject = Subject.objects.create(
			subject_name="Other Team Subject",
			team=other_team,
			subject_slug="other-team-subject-admin",
		)

		article = self._make_article("Cross Team Article")
		article.subjects.add(self.subject, other_subject)
		# High-confidence prediction, but for a subject outside this list.
		MLPredictions.objects.create(
			article=article,
			subject=other_subject,
			algorithm="pubmed_bert",
			model_version="v1",
			probability_score=0.95,
			predicted_relevant=True,
		)

		ctx = self._run_and_capture_context()

		featured = list(ctx.get("articles", []))
		needs_review = list(ctx.get("additional_articles", []))
		self.assertNotIn(article, featured)
		self.assertIn(article, needs_review)
