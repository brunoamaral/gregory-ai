"""
Tests verifying that the 'relevancy' sort order (the default) preserves
existing send_weekly_summary behaviour after the article_sort_order field
was introduced.
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

from gregory.models import Articles, ArticleSubjectRelevance, MLPredictions, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers


class TestWeeklySummaryRelevancySort(TestCase):
	"""Tests for the relevancy sort order mode (the default) in send_weekly_summary."""

	def setUp(self):
		self.organization = Organization.objects.create(
			name="Relevancy Org", slug="relevancy-org"
		)
		self.team = Team.objects.create(
			name="Relevancy Team",
			organization=self.organization,
			slug="relevancy-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Relevancy Subject",
			team=self.team,
			subject_slug="relevancy-subject",
			auto_predict=True,
		)

		# List created WITHOUT specifying article_sort_order — should default to 'relevancy'
		self.digest_list = Lists.objects.create(
			list_name="Relevancy Digest",
			weekly_digest=True,
			team=self.team,
			ml_threshold=0.8,
			list_email_subject="Relevancy Weekly",
		)
		self.digest_list.subjects.add(self.subject)

		self.subscriber = Subscribers.objects.create(
			first_name="Rel",
			last_name="Tester",
			email="rel@example.com",
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

	def _make_article(self, title, days_ago, doi=None):
		article = Articles.objects.create(
			title=title,
			discovery_date=timezone.now() - timedelta(days=days_ago),
			doi=doi or f"10.7777/{title.replace(' ', '-').lower()}",
		)
		article.subjects.add(self.subject)
		return article

	def _add_high_ml_score(self, article):
		"""Give article a ML prediction above the default 0.8 threshold."""
		MLPredictions.objects.create(
			article=article,
			subject=self.subject,
			algorithm='pubmed_bert',
			model_version='v1',
			probability_score=0.95,
			predicted_relevant=True,
		)

	def _add_low_ml_score(self, article):
		"""Give article a ML prediction below the default 0.8 threshold."""
		MLPredictions.objects.create(
			article=article,
			subject=self.subject,
			algorithm='pubmed_bert',
			model_version='v1',
			probability_score=0.2,
			predicted_relevant=False,
		)

	def _mark_relevant(self, article):
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject, is_relevant=True
		)

	def _run_dry_run(self):
		out = StringIO()
		call_command('send_weekly_summary', stdout=out, dry_run=True)
		return out.getvalue()

	def _run_and_capture_context(self):
		captured = {}
		real_get_template = __import__(
			'django.template.loader', fromlist=['get_template']
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

		_mock_result = MagicMock(status_code=200)
		_mock_result.json.return_value = {'ErrorCode': 0, 'Message': 'OK'}

		with patch(
			'subscriptions.management.commands.send_weekly_summary.send_email',
			return_value=_mock_result,
		), patch(
			'subscriptions.management.commands.send_weekly_summary.get_template',
			side_effect=fake_get_template,
		):
			out = StringIO()
			call_command('send_weekly_summary', stdout=out)

		return captured

	# ── Tests ────────────────────────────────────────────────────────────────

	def test_relevancy_mode_default_for_existing_lists(self):
		"""A list created without specifying article_sort_order has 'relevancy' as default."""
		fresh_list = Lists.objects.create(
			list_name="Fresh List",
			weekly_digest=True,
			team=self.team,
		)
		self.assertEqual(fresh_list.article_sort_order, 'relevancy')

	def test_relevancy_mode_priority_ranking_unchanged(self):
		"""
		With article_limit=2 and three articles, the two highest-priority articles
		are selected: manual > ML agreement > date.
		"""
		self.digest_list.article_limit = 2
		self.digest_list.save()

		# lowest priority: only low ML score, oldest
		low_priority = self._make_article("Low Priority", days_ago=3, doi="10.7777/low")
		self._add_low_ml_score(low_priority)

		# medium priority: high ML score
		medium = self._make_article("Medium Priority", days_ago=2, doi="10.7777/med")
		self._add_high_ml_score(medium)

		# highest priority: manually marked relevant
		high = self._make_article("High Priority", days_ago=4, doi="10.7777/high")
		self._mark_relevant(high)
		self._add_high_ml_score(high)

		ctx = self._run_and_capture_context()

		all_rendered = list(ctx.get('articles', [])) + list(ctx.get('additional_articles', []))
		rendered_pks = [a.pk for a in all_rendered]

		self.assertEqual(len(rendered_pks), 2)
		self.assertIn(high.pk, rendered_pks, "manually relevant article should be included")
		self.assertIn(medium.pk, rendered_pks, "high-ML article should be included")
		self.assertNotIn(low_priority.pk, rendered_pks, "low-ML article should be excluded by limit")

	def test_relevancy_mode_filters_low_ml_scores(self):
		"""
		In relevancy mode, an article below the ML threshold and not manually
		marked relevant is excluded from the digest.
		"""
		low_article = self._make_article("Low ML Article", days_ago=1, doi="10.7777/low-ml")
		self._add_low_ml_score(low_article)

		# Subject.ml_consensus_type defaults to 'any', so even one model must pass threshold.
		# With probability_score=0.2 and threshold=0.8 it should be excluded.
		output = self._run_dry_run()

		# The command should indicate RELEVANCY SORT MODE
		self.assertIn("RELEVANCY SORT MODE", output)
		# No articles should be found for sending
		self.assertNotIn("Would include", output)

	def test_relevancy_mode_includes_manually_relevant_regardless_of_ml(self):
		"""An article manually marked relevant is included even with no ML predictions."""
		article = self._make_article("Manual Only", days_ago=1, doi="10.7777/manual")
		self._mark_relevant(article)

		output = self._run_dry_run()

		self.assertIn("RELEVANCY SORT MODE", output)
		self.assertIn("1 articles", output)
