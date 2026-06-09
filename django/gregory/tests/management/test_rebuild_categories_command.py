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

from gregory.models import (
	Articles,
	ArticleCategoryAssignment,
	CategoryAssignmentSource,
	Subject,
	Team,
	TeamCategory,
	TrialCategoryAssignment,
	Trials,
)

AUTOMATIC = {'source': CategoryAssignmentSource.AUTOMATIC}

class RebuildCategoriesCommandTest(TestCase):
	@patch('gregory.management.commands.rebuild_categories.Command.rebuild_cats_articles')
	@patch('gregory.management.commands.rebuild_categories.Command.rebuild_cats_trials')
	def test_handle_invokes_methods(self, mock_trials, mock_articles):
		call_command('rebuild_categories')
		mock_articles.assert_called_once()
		mock_trials.assert_called_once()

class RebuildCategoriesDiffSyncTest(TestCase):
	"""Functional tests for the diff-based sync: matching items are added, stale
	automatic items removed, manual assignments always preserved, and untouched
	associations are never deleted and recreated."""

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

	def article_assignment(self, article):
		return ArticleCategoryAssignment.objects.get(articles=article, teamcategory=self.category)

	def test_adds_matching_articles_as_automatic(self):
		matching = self.make_article('Neuroplasticity in adults')
		non_matching = self.make_article('Unrelated study of something else')

		call_command('rebuild_categories', articles_only=True)

		self.assertIn(matching, self.category.articles.all())
		self.assertNotIn(non_matching, self.category.articles.all())
		self.assertEqual(self.article_assignment(matching).source, CategoryAssignmentSource.AUTOMATIC)

	def test_removes_stale_and_preserves_matching_associations(self):
		matching = self.make_article('Neuroplasticity in adults')
		stale = self.make_article('Unrelated study of something else')
		self.category.articles.add(matching, stale, through_defaults=AUTOMATIC)

		original_row_id = self.article_assignment(matching).pk

		call_command('rebuild_categories', articles_only=True)

		self.assertIn(matching, self.category.articles.all())
		self.assertNotIn(stale, self.category.articles.all())
		# The matching association must survive untouched (no wipe-and-recreate)
		self.assertEqual(self.article_assignment(matching).pk, original_row_id)

	def test_manual_assignment_is_never_removed(self):
		manual = self.make_article('Unrelated study assigned by an editor')
		self.category.articles.add(manual)  # default source: manual

		call_command('rebuild_categories', articles_only=True)

		self.assertIn(manual, self.category.articles.all())
		self.assertEqual(self.article_assignment(manual).source, CategoryAssignmentSource.MANUAL)

	def test_matching_manual_assignment_stays_manual(self):
		matching = self.make_article('Neuroplasticity in adults')
		self.category.articles.add(matching)  # default source: manual

		call_command('rebuild_categories', articles_only=True)

		assignment = self.article_assignment(matching)
		self.assertEqual(assignment.source, CategoryAssignmentSource.MANUAL)
		self.assertEqual(
			ArticleCategoryAssignment.objects.filter(articles=matching, teamcategory=self.category).count(),
			1,
		)

	def test_plain_add_defaults_to_manual(self):
		article = self.make_article('Any article at all')
		self.category.articles.add(article)

		self.assertEqual(self.article_assignment(article).source, CategoryAssignmentSource.MANUAL)

	def test_dry_run_makes_no_changes(self):
		matching = self.make_article('Neuroplasticity in adults')
		stale = self.make_article('Unrelated study of something else')
		self.category.articles.add(stale, through_defaults=AUTOMATIC)

		call_command('rebuild_categories', articles_only=True, dry_run=True)

		self.assertNotIn(matching, self.category.articles.all())
		self.assertIn(stale, self.category.articles.all())

	def test_days_scopes_changes_to_recent_items(self):
		old_stale = self.make_article('Old article without matching terms')
		Articles.objects.filter(pk=old_stale.pk).update(
			discovery_date=timezone.now() - timedelta(days=30)
		)
		self.category.articles.add(old_stale, through_defaults=AUTOMATIC)
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

	def test_category_without_terms_removes_automatic_keeps_manual(self):
		self.category.category_terms = []
		self.category.save()
		automatic = self.make_article('Neuroplasticity in adults')
		manual = self.make_article('Editor curated article')
		self.category.articles.add(automatic, through_defaults=AUTOMATIC)
		self.category.articles.add(manual)

		call_command('rebuild_categories', articles_only=True)

		self.assertNotIn(automatic, self.category.articles.all())
		self.assertIn(manual, self.category.articles.all())

	def test_trials_add_matching_and_remove_stale_keep_manual(self):
		matching = self.make_trial('Neuroplasticity rehabilitation trial')
		stale = self.make_trial('Unrelated intervention trial')
		manual = self.make_trial('Editor curated trial')
		self.category.trials.add(stale, through_defaults=AUTOMATIC)
		self.category.trials.add(manual)

		call_command('rebuild_categories', trials_only=True)

		self.assertIn(matching, self.category.trials.all())
		self.assertNotIn(stale, self.category.trials.all())
		self.assertIn(manual, self.category.trials.all())
		self.assertEqual(
			TrialCategoryAssignment.objects.get(trials=matching, teamcategory=self.category).source,
			CategoryAssignmentSource.AUTOMATIC,
		)

	def test_rerun_is_idempotent(self):
		matching = self.make_article('Neuroplasticity in adults')

		call_command('rebuild_categories', articles_only=True)
		row_id = self.article_assignment(matching).pk

		call_command('rebuild_categories', articles_only=True)

		self.assertEqual(ArticleCategoryAssignment.objects.count(), 1)
		self.assertEqual(self.article_assignment(matching).pk, row_id)
