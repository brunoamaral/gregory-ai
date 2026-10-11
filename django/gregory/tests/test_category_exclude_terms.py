"""
Tests for TeamCategory.category_exclude_terms: a hard veto applied by
rebuild_categories on top of inclusion terms and scoring.
"""

from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from api.tests.test_editor_endpoints import _subject
from gregory.management.commands.rebuild_categories import Command
from gregory.models import (
	ArticleCategoryAssignment,
	Articles,
	CategoryAssignmentSource,
	CategoryMatchScope,
	TeamCategory,
	TrialCategoryAssignment,
	Trials,
)
from mcpauth.tests.helpers import make_org


class ExcludeTermsTestCase(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = make_org("Exclude Org")
		cls.subject = _subject(cls.org, "Exclude Subject")

	def make_category(self, terms=("remyelination",), exclude=(), **kwargs):
		cat = TeamCategory.objects.create(
			team=self.subject.team,
			category_name="Cat",
			category_terms=list(terms),
			category_exclude_terms=list(exclude),
			**kwargs,
		)
		cat.subjects.add(self.subject)
		return cat

	def make_article(self, title, summary="", link=None):
		article = Articles.objects.create(
			title=title, summary=summary, link=link or f"https://x.test/{title}"
		)
		article.subjects.add(self.subject)
		return article

	def make_trial(self, title, **fields):
		trial = Trials.objects.create(title=title, link=f"https://t.test/{title}", **fields)
		trial.subjects.add(self.subject)
		return trial

	def rebuild(self, cat, **kwargs):
		call_command("rebuild_categories", category=cat.pk, verbosity=0, **kwargs)

	def assigned_articles(self, cat):
		return set(
			ArticleCategoryAssignment.objects.filter(teamcategory=cat).values_list(
				"articles_id", flat=True
			)
		)


class ArticleExclusionTest(ExcludeTermsTestCase):
	def test_excluded_term_blocks_assignment(self):
		cat = self.make_category(exclude=["mouse"])
		blocked = self.make_article("Remyelination in the mouse")
		allowed = self.make_article("Remyelination in patients")
		self.rebuild(cat)
		self.assertEqual(self.assigned_articles(cat), {allowed.pk})
		self.assertNotIn(blocked.pk, self.assigned_articles(cat))

	def test_title_scope_ignores_excluded_term_in_summary(self):
		cat = self.make_category(exclude=["mouse"], match_scope=CategoryMatchScope.TITLE)
		article = self.make_article("Remyelination study", summary="Done in the mouse")
		self.rebuild(cat)
		self.assertEqual(self.assigned_articles(cat), {article.pk})

	def test_zero_weight_field_is_not_checked(self):
		cat = self.make_category(exclude=["mouse"])
		cat.match_weights = {"article": {"title": 3, "summary": 0}}
		cat.save()
		article = self.make_article("Remyelination study", summary="Done in the mouse")
		self.rebuild(cat)
		self.assertEqual(self.assigned_articles(cat), {article.pk})

	def test_whole_word_only(self):
		cat = self.make_category(exclude=["mouse"])
		article = self.make_article("Remyelination with a mousetrap")
		self.rebuild(cat)
		self.assertEqual(self.assigned_articles(cat), {article.pk})

	def test_case_insensitive(self):
		cat = self.make_category(exclude=["Mouse"])
		self.make_article("Remyelination in a mouse model")
		self.rebuild(cat)
		self.assertEqual(self.assigned_articles(cat), set())

	def test_existing_automatic_assignment_is_removed(self):
		cat = self.make_category()
		article = self.make_article("Remyelination in the mouse")
		self.rebuild(cat)
		self.assertEqual(self.assigned_articles(cat), {article.pk})
		cat.category_exclude_terms = ["mouse"]
		cat.save()
		self.rebuild(cat)
		self.assertEqual(self.assigned_articles(cat), set())

	def test_manual_assignment_is_preserved(self):
		cat = self.make_category(exclude=["mouse"])
		article = self.make_article("Remyelination in the mouse")
		ArticleCategoryAssignment.objects.create(
			teamcategory=cat, articles=article, source=CategoryAssignmentSource.MANUAL
		)
		self.rebuild(cat)
		self.assertEqual(self.assigned_articles(cat), {article.pk})

	def test_empty_list_behaves_as_before(self):
		with_empty = self.make_category(exclude=[])
		a = self.make_article("Remyelination in the mouse")
		b = self.make_article("Remyelination in patients")
		self.rebuild(with_empty)
		self.assertEqual(self.assigned_articles(with_empty), {a.pk, b.pk})

	def test_dry_run_verbose_reports_the_excluded_term(self):
		from io import StringIO

		cat = self.make_category(exclude=["mouse"])
		self.make_article("Remyelination in the mouse")
		out = StringIO()
		call_command(
			"rebuild_categories", category=cat.pk, dry_run=True, verbose=True, stdout=out
		)
		self.assertIn("excluded by 'mouse'", out.getvalue())
		self.assertIn("(1 excluded)", out.getvalue())


class ConfigHashTest(ExcludeTermsTestCase):
	def test_hash_changes_with_excluded_terms(self):
		cat = self.make_category()
		command = Command()
		before = command.category_config_hash(cat)
		cat.category_exclude_terms = ["mouse"]
		self.assertNotEqual(before, command.category_config_hash(cat))

	def test_changing_excluded_terms_forces_full_rematch(self):
		cat = self.make_category()
		article = self.make_article("Remyelination in the mouse")
		self.rebuild(cat, days=1)
		self.assertEqual(self.assigned_articles(cat), {article.pk})
		# Push the article outside the incremental window
		old = timezone.now() - timedelta(days=30)
		Articles.objects.filter(pk=article.pk).update(discovery_date=old, last_updated=old)
		cat = TeamCategory.objects.get(pk=cat.pk)
		cat.category_exclude_terms = ["mouse"]
		cat.save()
		self.rebuild(cat, days=1)
		self.assertEqual(self.assigned_articles(cat), set())


class TrialExclusionTest(ExcludeTermsTestCase):
	def test_excluded_term_in_intervention_blocks_trial(self):
		cat = self.make_category(exclude=["placebo"])
		blocked = self.make_trial("Remyelination trial", intervention="Placebo comparator")
		allowed = self.make_trial("Remyelination trial two", intervention="Drug X")
		self.rebuild(cat)
		assigned = set(
			TrialCategoryAssignment.objects.filter(teamcategory=cat).values_list(
				"trials_id", flat=True
			)
		)
		self.assertEqual(assigned, {allowed.pk})
		self.assertNotIn(blocked.pk, assigned)
