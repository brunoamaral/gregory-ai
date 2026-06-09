import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from organizations.models import Organization

from gregory.models import Articles, Subject, Team, TeamCategory, Trials

class RebuildCategoriesCommandTest(TestCase):
	@patch('gregory.management.commands.rebuild_categories.Command.rebuild_cats_articles')
	@patch('gregory.management.commands.rebuild_categories.Command.rebuild_cats_trials')
	def test_handle_invokes_methods(self, mock_trials, mock_articles):
		call_command('rebuild_categories')
		mock_articles.assert_called_once()
		mock_trials.assert_called_once()

class RebuildCategoriesDiffSyncTest(TestCase):
	"""Functional tests for the diff-based sync: matching items are added, stale
	items removed, and untouched associations are never deleted and recreated."""

	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Research', slug='research')
		self.subject = Subject.objects.create(subject_name='Neurology', subject_slug='neurology', team=self.team)
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name='Neuroplasticity',
			category_terms=['neuroplasticity'],
		)
		self.category.subjects.add(self.subject)

	def make_article(self, title, summary=''):
		article = Articles.objects.create(
			title=title,
			summary=summary,
			link=f'https://example.com/{abs(hash(title))}',
		)
		article.subjects.add(self.subject)
		return article

	def make_trial(self, title, summary=''):
		trial = Trials.objects.create(
			title=title,
			summary=summary,
			link=f'https://example.com/trial/{abs(hash(title))}',
			discovery_date=timezone.now(),
		)
		trial.subjects.add(self.subject)
		return trial

	def test_adds_matching_articles(self):
		matching = self.make_article('Neuroplasticity in adults')
		non_matching = self.make_article('Unrelated study of something else')

		call_command('rebuild_categories', articles_only=True)

		self.assertIn(matching, self.category.articles.all())
		self.assertNotIn(non_matching, self.category.articles.all())

	def test_removes_stale_and_preserves_matching_associations(self):
		matching = self.make_article('Neuroplasticity in adults')
		stale = self.make_article('Unrelated study of something else')
		self.category.articles.add(matching, stale)

		through = Articles.team_categories.through
		original_row_id = through.objects.get(articles=matching, teamcategory=self.category).pk

		call_command('rebuild_categories', articles_only=True)

		self.assertIn(matching, self.category.articles.all())
		self.assertNotIn(stale, self.category.articles.all())
		# The matching association must survive untouched (no wipe-and-recreate)
		self.assertEqual(
			through.objects.get(articles=matching, teamcategory=self.category).pk,
			original_row_id,
		)

	def test_dry_run_makes_no_changes(self):
		matching = self.make_article('Neuroplasticity in adults')
		stale = self.make_article('Unrelated study of something else')
		self.category.articles.add(stale)

		call_command('rebuild_categories', articles_only=True, dry_run=True)

		self.assertNotIn(matching, self.category.articles.all())
		self.assertIn(stale, self.category.articles.all())

	def test_days_scopes_changes_to_recent_items(self):
		old_stale = self.make_article('Old article without matching terms')
		Articles.objects.filter(pk=old_stale.pk).update(
			discovery_date=timezone.now() - timedelta(days=30)
		)
		self.category.articles.add(old_stale)
		recent_matching = self.make_article('Neuroplasticity in adults')

		call_command('rebuild_categories', articles_only=True, days=7)

		# Items outside the window are left alone; recent matches are added
		self.assertIn(old_stale, self.category.articles.all())
		self.assertIn(recent_matching, self.category.articles.all())

	def test_batching_processes_all_matches(self):
		articles = [
			self.make_article(f'Neuroplasticity study number {i}')
			for i in range(5)
		]

		call_command('rebuild_categories', articles_only=True, batch_size=2)

		for article in articles:
			self.assertIn(article, self.category.articles.all())

	def test_category_without_terms_removes_associations(self):
		self.category.category_terms = []
		self.category.save()
		article = self.make_article('Neuroplasticity in adults')
		self.category.articles.add(article)

		call_command('rebuild_categories', articles_only=True)

		self.assertNotIn(article, self.category.articles.all())

	def test_trials_add_matching_and_remove_stale(self):
		matching = self.make_trial('Neuroplasticity rehabilitation trial')
		stale = self.make_trial('Unrelated intervention trial')
		self.category.trials.add(stale)

		call_command('rebuild_categories', trials_only=True)

		self.assertIn(matching, self.category.trials.all())
		self.assertNotIn(stale, self.category.trials.all())

	def test_rerun_is_idempotent(self):
		matching = self.make_article('Neuroplasticity in adults')

		call_command('rebuild_categories', articles_only=True)
		through = Articles.team_categories.through
		row_id = through.objects.get(articles=matching, teamcategory=self.category).pk

		call_command('rebuild_categories', articles_only=True)

		self.assertEqual(through.objects.count(), 1)
		self.assertEqual(
			through.objects.get(articles=matching, teamcategory=self.category).pk,
			row_id,
		)
