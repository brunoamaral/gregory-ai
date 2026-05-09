"""
Tests for the 'date' article_sort_order mode of send_weekly_summary.

In date mode:
- All subject-matched articles within the lookback window are included regardless
  of ML predictions or manual review status.
- Articles are ordered by discovery_date descending.
- article_limit is respected (newest N articles).
- Articles manually tagged as irrelevant for ALL their subjects in the list are
  still excluded.
- The content organizer returns a flat list (featured_articles empty,
  regular_articles populated).
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
from templates.emails.components.content_organizer import EmailContentOrganizer


class TestWeeklySummaryDateSort(TestCase):
	"""Tests for the date sort order mode in send_weekly_summary."""

	def setUp(self):
		self.organization = Organization.objects.create(
			name="Date Sort Org", slug="date-sort-org"
		)
		self.team = Team.objects.create(
			name="Date Sort Team",
			organization=self.organization,
			slug="date-sort-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Date Sort Subject",
			team=self.team,
			subject_slug="date-sort-subject",
		)
		self.subject_b = Subject.objects.create(
			subject_name="Date Sort Subject B",
			team=self.team,
			subject_slug="date-sort-subject-b",
		)

		self.digest_list = Lists.objects.create(
			list_name="Date Sort Digest",
			weekly_digest=True,
			team=self.team,
			ml_threshold=0.8,
			article_sort_order='date',
			list_email_subject="Date Sort Weekly",
		)
		self.digest_list.subjects.add(self.subject, self.subject_b)

		self.subscriber = Subscribers.objects.create(
			first_name="Date",
			last_name="Tester",
			email="date@example.com",
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
			doi=doi or f"10.9999/{title.replace(' ', '-').lower()}",
		)
		article.subjects.add(self.subject)
		return article

	def _tag_irrelevant(self, article, subject):
		ArticleSubjectRelevance.objects.create(
			article=article, subject=subject, is_relevant=False
		)

	def _run_and_capture_context(self):
		"""Run the command, intercept the template context, return it."""
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

	def _run_dry_run(self):
		out = StringIO()
		call_command('send_weekly_summary', stdout=out, dry_run=True)
		return out.getvalue()

	# ── Tests ────────────────────────────────────────────────────────────────

	def test_date_mode_includes_articles_below_ml_threshold(self):
		"""In date mode, articles with ML scores below the threshold are still included."""
		article = self._make_article("Low Score Article", days_ago=2)

		# Create a low-confidence ML prediction (well below the 0.8 threshold)
		MLPredictions.objects.create(
			article=article,
			subject=self.subject,
			algorithm='pubmed_bert',
			model_version='v1',
			probability_score=0.1,
			predicted_relevant=False,
		)

		output = self._run_dry_run()

		# Date mode should include the article despite the low ML score
		self.assertIn("DATE SORT MODE", output)
		self.assertIn("1 articles", output)

	def test_date_mode_orders_by_discovery_date_desc(self):
		"""Articles appear in discovery_date descending order in date mode."""
		oldest = self._make_article("Oldest Article", days_ago=5, doi="10.9999/oldest")
		middle = self._make_article("Middle Article", days_ago=3, doi="10.9999/middle")
		newest = self._make_article("Newest Article", days_ago=1, doi="10.9999/newest")

		ctx = self._run_and_capture_context()

		# In date mode all articles land in additional_articles (regular_articles bucket)
		additional = ctx.get('additional_articles', [])
		self.assertEqual(len(additional), 3)
		self.assertEqual(additional[0].pk, newest.pk)
		self.assertEqual(additional[1].pk, middle.pk)
		self.assertEqual(additional[2].pk, oldest.pk)

	def test_date_mode_respects_article_limit(self):
		"""Only the N most recent articles are included when count exceeds article_limit."""
		self.digest_list.article_limit = 2
		self.digest_list.save()

		for i in range(5):
			self._make_article(f"Article {i}", days_ago=i + 1, doi=f"10.9999/art{i}")

		ctx = self._run_and_capture_context()

		additional = ctx.get('additional_articles', [])
		total = len(ctx.get('articles', [])) + len(additional)
		self.assertEqual(total, 2)

	def test_date_mode_excludes_articles_irrelevant_for_all_subjects(self):
		"""Articles manually tagged irrelevant for ALL their subjects in the list are excluded."""
		included = self._make_article("Included Article", days_ago=1, doi="10.9999/inc")
		excluded = self._make_article("Excluded Article", days_ago=2, doi="10.9999/exc")
		excluded.subjects.add(self.subject_b)

		# Tag excluded as not-relevant for every subject it shares with the list
		self._tag_irrelevant(excluded, self.subject)
		self._tag_irrelevant(excluded, self.subject_b)

		output = self._run_dry_run()

		self.assertIn("DATE SORT MODE", output)
		self.assertIn("1 articles", output)

	def test_date_mode_includes_articles_partially_relevant(self):
		"""Articles irrelevant for one list subject but relevant for another are included."""
		article = self._make_article("Partial Article", days_ago=2, doi="10.9999/partial")
		article.subjects.add(self.subject_b)

		# Tag irrelevant for subject, but RELEVANT for subject_b
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject, is_relevant=False
		)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_b, is_relevant=True
		)

		output = self._run_dry_run()

		self.assertIn("DATE SORT MODE", output)
		# Article should be included because not ALL subjects are irrelevant
		self.assertIn("1 articles", output)

	def test_date_mode_flat_list(self):
		"""Content organizer returns empty featured_articles and populated regular_articles."""
		self._make_article("Flat List Article", days_ago=1)

		ctx = self._run_and_capture_context()

		featured = ctx.get('articles', [])
		additional = ctx.get('additional_articles', [])
		self.assertEqual(len(featured), 0, "featured_articles should be empty in date mode")
		self.assertGreater(len(additional), 0, "additional_articles should have articles in date mode")


class TestEmailContentOrganizerDateMode(TestCase):
	"""Unit-level tests for the content organizer in date mode."""

	def setUp(self):
		self.organization = Organization.objects.create(
			name="Organizer Org", slug="organizer-org"
		)
		self.team = Team.objects.create(
			name="Organizer Team",
			organization=self.organization,
			slug="organizer-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Organizer Subject",
			team=self.team,
			subject_slug="organizer-subject",
		)

	def _make_list(self, sort_order):
		lst = Lists.objects.create(
			list_name=f"List {sort_order}",
			weekly_digest=True,
			team=self.team,
			article_sort_order=sort_order,
		)
		lst.subjects.add(self.subject)
		return lst

	def _make_article(self, title, days_ago):
		article = Articles.objects.create(
			title=title,
			discovery_date=timezone.now() - timedelta(days=days_ago),
			doi=f"10.8888/{title.replace(' ', '-').lower()}",
		)
		article.subjects.add(self.subject)
		return article

	def test_date_mode_returns_empty_featured_and_populated_regular(self):
		"""_organize_weekly_articles returns featured=[] and regular=articles in date mode."""
		list_obj = self._make_list('date')
		a1 = self._make_article("Old", days_ago=3)
		a2 = self._make_article("New", days_ago=1)

		organizer = EmailContentOrganizer(email_type='weekly_summary')
		result = organizer.organize_articles([a1, a2], list_obj=list_obj)

		self.assertEqual(result['featured_articles'], [])
		self.assertEqual(result['high_confidence_count'], 0)
		self.assertEqual(len(result['regular_articles']), 2)

	def test_date_mode_regular_articles_ordered_newest_first(self):
		"""regular_articles are sorted newest-first in date mode."""
		list_obj = self._make_list('date')
		old = self._make_article("Old Article", days_ago=5)
		new = self._make_article("New Article", days_ago=1)

		organizer = EmailContentOrganizer(email_type='weekly_summary')
		result = organizer.organize_articles([old, new], list_obj=list_obj)

		regular = result['regular_articles']
		self.assertEqual(regular[0].pk, new.pk)
		self.assertEqual(regular[1].pk, old.pk)

	def test_relevancy_mode_uses_featured_regular_split(self):
		"""Relevancy mode still splits articles into featured/regular buckets."""
		list_obj = self._make_list('relevancy')
		article = self._make_article("Any Article", days_ago=1)
		# Mark as manually relevant so it lands in featured
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject, is_relevant=True
		)

		organizer = EmailContentOrganizer(email_type='weekly_summary')
		result = organizer.organize_articles([article], list_obj=list_obj)

		# featured_articles should be non-empty for a manually-relevant article
		self.assertGreater(len(result['featured_articles']), 0)
