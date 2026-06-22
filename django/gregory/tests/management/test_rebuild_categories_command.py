import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse

from organizations.models import Organization

from gregory.models import (
	Articles,
	ArticleCategoryAssignment,
	CategoryAssignmentSource,
	CategoryMatchScope,
	CategoryType,
	Subject,
	Team,
	TeamCategory,
	TrialCategoryAssignment,
	Trials,
)

AUTOMATIC = {"source": CategoryAssignmentSource.AUTOMATIC}


class RebuildCategoriesCommandTest(TestCase):
	@patch(
		"gregory.management.commands.rebuild_categories.Command.rebuild_cats_articles"
	)
	@patch("gregory.management.commands.rebuild_categories.Command.rebuild_cats_trials")
	def test_handle_invokes_methods(self, mock_trials, mock_articles):
		call_command("rebuild_categories")
		mock_articles.assert_called_once()
		mock_trials.assert_called_once()


class RebuildCategoriesDiffSyncTest(TestCase):
	"""Functional tests for the diff-based sync: matching items are added, stale
	automatic items removed, manual assignments always preserved, and untouched
	associations are never deleted and recreated."""

	def setUp(self):
		self.org = Organization.objects.create(name="Test Org")
		self.team = Team.objects.create(
			organization=self.org, name="Research", slug="research"
		)
		self.subject = Subject.objects.create(
			subject_name="Neurology", subject_slug="neurology", team=self.team
		)
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name="Neuroplasticity",
			category_terms=["neuroplasticity"],
		)
		self.category.subjects.add(self.subject)

	def make_article(self, title, summary=""):
		article = Articles.objects.create(
			title=title,
			summary=summary,
			link=f"https://example.com/{abs(hash(title))}",
		)
		article.subjects.add(self.subject)
		return article

	def make_trial(self, title, summary=""):
		trial = Trials.objects.create(
			title=title,
			summary=summary,
			link=f"https://example.com/trial/{abs(hash(title))}",
			discovery_date=timezone.now(),
		)
		trial.subjects.add(self.subject)
		return trial

	def article_assignment(self, article):
		return ArticleCategoryAssignment.objects.get(
			articles=article, teamcategory=self.category
		)

	def test_adds_matching_articles_as_automatic(self):
		matching = self.make_article("Neuroplasticity in adults")
		non_matching = self.make_article("Unrelated study of something else")

		call_command("rebuild_categories", articles_only=True)

		self.assertIn(matching, self.category.articles.all())
		self.assertNotIn(non_matching, self.category.articles.all())
		self.assertEqual(
			self.article_assignment(matching).source, CategoryAssignmentSource.AUTOMATIC
		)

	def test_removes_stale_and_preserves_matching_associations(self):
		matching = self.make_article("Neuroplasticity in adults")
		stale = self.make_article("Unrelated study of something else")
		self.category.articles.add(matching, stale, through_defaults=AUTOMATIC)

		original_row_id = self.article_assignment(matching).pk

		call_command("rebuild_categories", articles_only=True)

		self.assertIn(matching, self.category.articles.all())
		self.assertNotIn(stale, self.category.articles.all())
		# The matching association must survive untouched (no wipe-and-recreate)
		self.assertEqual(self.article_assignment(matching).pk, original_row_id)

	def test_manual_assignment_is_never_removed(self):
		manual = self.make_article("Unrelated study assigned by an editor")
		self.category.articles.add(manual)  # default source: manual

		call_command("rebuild_categories", articles_only=True)

		self.assertIn(manual, self.category.articles.all())
		self.assertEqual(
			self.article_assignment(manual).source, CategoryAssignmentSource.MANUAL
		)

	def test_matching_manual_assignment_stays_manual(self):
		matching = self.make_article("Neuroplasticity in adults")
		self.category.articles.add(matching)  # default source: manual

		call_command("rebuild_categories", articles_only=True)

		assignment = self.article_assignment(matching)
		self.assertEqual(assignment.source, CategoryAssignmentSource.MANUAL)
		self.assertEqual(
			ArticleCategoryAssignment.objects.filter(
				articles=matching, teamcategory=self.category
			).count(),
			1,
		)

	def test_plain_add_defaults_to_manual(self):
		article = self.make_article("Any article at all")
		self.category.articles.add(article)

		self.assertEqual(
			self.article_assignment(article).source, CategoryAssignmentSource.MANUAL
		)

	def test_dry_run_makes_no_changes(self):
		matching = self.make_article("Neuroplasticity in adults")
		stale = self.make_article("Unrelated study of something else")
		self.category.articles.add(stale, through_defaults=AUTOMATIC)

		call_command("rebuild_categories", articles_only=True, dry_run=True)

		self.assertNotIn(matching, self.category.articles.all())
		self.assertIn(stale, self.category.articles.all())

	def backdate(self, article, days=30):
		old = timezone.now() - timedelta(days=days)
		Articles.objects.filter(pk=article.pk).update(
			discovery_date=old, last_updated=old
		)

	def test_days_scopes_changes_to_recent_items(self):
		# Full run first so the category's matching configuration is recorded;
		# otherwise the incremental run falls back to a full re-match.
		call_command("rebuild_categories")

		old_stale = self.make_article("Old article without matching terms")
		self.backdate(old_stale)
		self.category.articles.add(old_stale, through_defaults=AUTOMATIC)
		recent_matching = self.make_article("Neuroplasticity in adults")

		call_command("rebuild_categories", articles_only=True, days=7)

		# Items outside the window are left alone; recent matches are added
		self.assertIn(old_stale, self.category.articles.all())
		self.assertIn(recent_matching, self.category.articles.all())

	def test_updated_article_is_recategorized_incrementally(self):
		call_command("rebuild_categories")

		article = self.make_article("Old article without matching terms")
		self.backdate(article)

		# Editing the article bumps last_updated, pulling it into the window
		article.title = "Old article, now about neuroplasticity"
		article.save()

		call_command("rebuild_categories", articles_only=True, days=7)

		self.assertIn(article, self.category.articles.all())

	def test_config_change_triggers_full_rematch(self):
		matching = self.make_article("Neuroplasticity in adults")
		call_command("rebuild_categories")
		self.assertIn(matching, self.category.articles.all())

		# Both articles end up outside the incremental window
		self.backdate(matching)
		new_match = self.make_article("Dopamine signalling in adults")
		self.backdate(new_match)

		self.category.category_terms = ["dopamine"]
		self.category.save()

		call_command("rebuild_categories", days=7)

		# The changed term list forces a full re-match despite --days
		self.assertNotIn(matching, self.category.articles.all())
		self.assertIn(new_match, self.category.articles.all())

	def test_unchanged_config_stays_incremental(self):
		call_command("rebuild_categories")

		old_matching = self.make_article("Neuroplasticity in adults")
		self.backdate(old_matching)

		call_command("rebuild_categories", days=7)

		# Outside the window and config unchanged: not picked up until a full run
		self.assertNotIn(old_matching, self.category.articles.all())

		call_command("rebuild_categories")
		self.assertIn(old_matching, self.category.articles.all())

	def test_batching_processes_all_matches(self):
		articles = [
			self.make_article(f"Neuroplasticity study number {i}") for i in range(5)
		]

		call_command("rebuild_categories", articles_only=True, batch_size=2)

		for article in articles:
			self.assertIn(article, self.category.articles.all())

	def test_category_without_terms_removes_automatic_keeps_manual(self):
		self.category.category_terms = []
		self.category.save()
		automatic = self.make_article("Neuroplasticity in adults")
		manual = self.make_article("Editor curated article")
		self.category.articles.add(automatic, through_defaults=AUTOMATIC)
		self.category.articles.add(manual)

		call_command("rebuild_categories", articles_only=True)

		self.assertNotIn(automatic, self.category.articles.all())
		self.assertIn(manual, self.category.articles.all())

	def test_trials_add_matching_and_remove_stale_keep_manual(self):
		matching = self.make_trial("Neuroplasticity rehabilitation trial")
		stale = self.make_trial("Unrelated intervention trial")
		manual = self.make_trial("Editor curated trial")
		self.category.trials.add(stale, through_defaults=AUTOMATIC)
		self.category.trials.add(manual)

		call_command("rebuild_categories", trials_only=True)

		self.assertIn(matching, self.category.trials.all())
		self.assertNotIn(stale, self.category.trials.all())
		self.assertIn(manual, self.category.trials.all())
		self.assertEqual(
			TrialCategoryAssignment.objects.get(
				trials=matching, teamcategory=self.category
			).source,
			CategoryAssignmentSource.AUTOMATIC,
		)

	def test_manual_category_is_skipped_entirely(self):
		self.category.category_type = CategoryType.MANUAL
		self.category.save()
		matching = self.make_article("Neuroplasticity in adults")
		# Even an automatic-source assignment is left alone once the category is manual
		stale = self.make_article("Unrelated study of something else")
		self.category.articles.add(stale, through_defaults=AUTOMATIC)

		call_command("rebuild_categories", articles_only=True)

		self.assertNotIn(matching, self.category.articles.all())
		self.assertIn(stale, self.category.articles.all())

	def test_manual_category_trials_are_skipped(self):
		self.category.category_type = CategoryType.MANUAL
		self.category.save()
		matching = self.make_trial("Neuroplasticity rehabilitation trial")
		curated = self.make_trial("Editor curated trial")
		self.category.trials.add(curated)

		call_command("rebuild_categories", trials_only=True)

		self.assertNotIn(matching, self.category.trials.all())
		self.assertIn(curated, self.category.trials.all())

	def test_new_categories_default_to_automatic(self):
		self.assertEqual(self.category.category_type, CategoryType.AUTOMATIC)

	def test_category_scope_only_processes_target(self):
		other_category = TeamCategory.objects.create(
			team=self.team,
			category_name="Dopamine",
			category_terms=["dopamine"],
		)
		other_category.subjects.add(self.subject)
		neuro_article = self.make_article("Neuroplasticity in adults")
		dopamine_article = self.make_article("Dopamine signalling in adults")

		call_command("rebuild_categories", category=self.category.pk)

		self.assertIn(neuro_article, self.category.articles.all())
		# The other category is out of scope and stays empty
		self.assertNotIn(dopamine_article, other_category.articles.all())
		self.category.refresh_from_db()
		other_category.refresh_from_db()
		self.assertIsNotNone(self.category.match_config_hash)
		self.assertIsNone(other_category.match_config_hash)

	def test_category_scope_manual_category_is_noop(self):
		self.category.category_type = CategoryType.MANUAL
		self.category.save()
		matching = self.make_article("Neuroplasticity in adults")

		call_command("rebuild_categories", category=self.category.pk)

		self.assertNotIn(matching, self.category.articles.all())

	def test_category_scope_unknown_id_raises(self):
		from django.core.management.base import CommandError

		with self.assertRaises(CommandError):
			call_command("rebuild_categories", category=999999)

	def test_rerun_is_idempotent(self):
		matching = self.make_article("Neuroplasticity in adults")

		call_command("rebuild_categories", articles_only=True)
		row_id = self.article_assignment(matching).pk

		call_command("rebuild_categories", articles_only=True)

		self.assertEqual(ArticleCategoryAssignment.objects.count(), 1)
		self.assertEqual(self.article_assignment(matching).pk, row_id)


class MatchScopeAndScoringTest(TestCase):
	"""Per-category match scope, score threshold, and field weights."""

	def setUp(self):
		self.org = Organization.objects.create(name="Test Org")
		self.team = Team.objects.create(
			organization=self.org, name="Research", slug="research"
		)
		self.subject = Subject.objects.create(
			subject_name="Neurology", subject_slug="neurology", team=self.team
		)
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name="Neuroplasticity",
			category_terms=["neuroplasticity"],
		)
		self.category.subjects.add(self.subject)

	def make_article(self, title, summary=""):
		article = Articles.objects.create(
			title=title,
			summary=summary,
			link=f"https://example.com/{abs(hash(title))}",
		)
		article.subjects.add(self.subject)
		return article

	def make_trial(self, title, summary="", **fields):
		trial = Trials.objects.create(
			title=title,
			summary=summary,
			link=f"https://example.com/trial/{abs(hash(title))}",
			discovery_date=timezone.now(),
			**fields,
		)
		trial.subjects.add(self.subject)
		return trial

	def backdate(self, model, obj, days=30):
		old = timezone.now() - timedelta(days=days)
		model.objects.filter(pk=obj.pk).update(discovery_date=old, last_updated=old)

	# --- match scope ---------------------------------------------------------

	def test_default_scope_scores_summary(self):
		# title+summary is the default: a summary-only mention still qualifies
		summary_only = self.make_article("Unrelated topic", "A study of neuroplasticity")

		call_command("rebuild_categories", articles_only=True)

		self.assertIn(summary_only, self.category.articles.all())

	def test_title_only_scope_ignores_summary(self):
		self.category.match_scope = CategoryMatchScope.TITLE
		self.category.save()
		title_match = self.make_article("Neuroplasticity in adults")
		summary_only = self.make_article("Unrelated topic", "About neuroplasticity")

		call_command("rebuild_categories", articles_only=True)

		self.assertIn(title_match, self.category.articles.all())
		self.assertNotIn(summary_only, self.category.articles.all())

	# --- per-category threshold ---------------------------------------------

	def test_higher_min_score_excludes_single_title_match(self):
		# title (3) + bonus (2) = 5; raise the bar above that
		self.category.match_min_score = 6
		self.category.save()
		single = self.make_article("Neuroplasticity in adults")
		# title (3) + summary (1) + bonus (2) = 6 clears the bar
		both = self.make_article("Neuroplasticity in adults", "more neuroplasticity")

		call_command("rebuild_categories", articles_only=True)

		self.assertNotIn(single, self.category.articles.all())
		self.assertIn(both, self.category.articles.all())

	# --- per-field weights ---------------------------------------------------

	def test_zero_weight_disables_field(self):
		self.category.match_weights = {
			"article": {"title": 3, "summary": 0},
			"trial": {},
		}
		self.category.save()
		title_match = self.make_article("Neuroplasticity in adults")
		summary_only = self.make_article("Unrelated topic", "About neuroplasticity")

		call_command("rebuild_categories", articles_only=True)

		self.assertIn(title_match, self.category.articles.all())
		self.assertNotIn(summary_only, self.category.articles.all())

	# --- trials --------------------------------------------------------------

	def test_trial_default_scope_uses_extra_fields(self):
		self.category.category_terms = ["dopamine"]
		self.category.save()
		# intervention (2) + bonus (2) = 4 >= 3 under the default title+summary scope
		via_intervention = self.make_trial(
			"Unrelated trial", intervention="dopamine agonist"
		)

		call_command("rebuild_categories", trials_only=True)

		self.assertIn(via_intervention, self.category.trials.all())

	def test_trial_title_only_ignores_extra_fields(self):
		self.category.category_terms = ["dopamine"]
		self.category.match_scope = CategoryMatchScope.TITLE
		self.category.save()
		title_match = self.make_trial("Dopamine rehabilitation trial")
		via_intervention = self.make_trial(
			"Unrelated trial", intervention="dopamine agonist"
		)

		call_command("rebuild_categories", trials_only=True)

		self.assertIn(title_match, self.category.trials.all())
		self.assertNotIn(via_intervention, self.category.trials.all())

	# --- config-change re-match ---------------------------------------------

	def test_changing_scope_triggers_full_rematch(self):
		summary_only = self.make_article("Unrelated topic", "About neuroplasticity")
		call_command("rebuild_categories")
		self.assertIn(summary_only, self.category.articles.all())

		# Push it outside the incremental window, then narrow the scope
		self.backdate(Articles, summary_only)
		self.category.match_scope = CategoryMatchScope.TITLE
		self.category.save()

		call_command("rebuild_categories", days=7)

		# The scope change forces a full re-match despite --days
		self.assertNotIn(summary_only, self.category.articles.all())

	def test_changing_weight_triggers_full_rematch(self):
		summary_only = self.make_article("Unrelated topic", "About neuroplasticity")
		call_command("rebuild_categories")
		self.assertIn(summary_only, self.category.articles.all())

		self.backdate(Articles, summary_only)
		self.category.match_weights = {
			"article": {"title": 3, "summary": 0},
			"trial": {},
		}
		self.category.save()

		call_command("rebuild_categories", days=7)

		self.assertNotIn(summary_only, self.category.articles.all())


class TeamCategoryAdminBackfillTest(TestCase):
	"""Creating an automatic category in the admin backfills it immediately."""

	def setUp(self):
		self.admin_user = get_user_model().objects.create_superuser(
			"admin", "admin@example.com", "password"
		)
		self.client.force_login(self.admin_user)
		self.org = Organization.objects.create(name="Test Org")
		self.team = Team.objects.create(
			organization=self.org, name="Research", slug="research"
		)
		self.subject = Subject.objects.create(
			subject_name="Neurology", subject_slug="neurology", team=self.team
		)
		self.article = Articles.objects.create(
			title="Neuroplasticity in adults",
			link="https://example.com/backfill-article",
		)
		self.article.subjects.add(self.subject)
		self.trial = Trials.objects.create(
			title="Neuroplasticity rehabilitation trial",
			link="https://example.com/backfill-trial",
			discovery_date=timezone.now(),
		)
		self.trial.subjects.add(self.subject)

	# Default values for the matching-settings form fields, mirroring the model
	# defaults so a posted form reproduces today's behaviour unless overridden.
	DEFAULT_MATCH_FIELDS = {
		"match_scope": "title_summary",
		"match_min_score": 3,
		"weight_article_title": 3,
		"weight_article_summary": 1,
		"weight_trial_title": 3,
		"weight_trial_summary": 2,
		"weight_trial_scientific_title": 2,
		"weight_trial_intervention": 2,
		"weight_trial_primary_outcome": 1,
		"weight_trial_secondary_outcome": 1,
		"weight_trial_therapeutic_areas": 1,
	}

	def create_category_via_admin(self, category_type, **overrides):
		data = {
			"team": self.team.pk,
			"subjects": [self.subject.pk],
			"category_name": "Neuroplasticity",
			"category_slug": "neuroplasticity",
			"category_description": "",
			"category_terms": "neuroplasticity",
			"category_type": category_type,
			**self.DEFAULT_MATCH_FIELDS,
		}
		data.update(overrides)
		return self.client.post(reverse("admin:gregory_teamcategory_add"), data)

	def test_new_automatic_category_is_backfilled(self):
		response = self.create_category_via_admin(CategoryType.AUTOMATIC)
		self.assertEqual(response.status_code, 302)

		category = TeamCategory.objects.get(category_slug="neuroplasticity")
		self.assertIn(self.article, category.articles.all())
		self.assertIn(self.trial, category.trials.all())
		self.assertEqual(
			ArticleCategoryAssignment.objects.get(
				articles=self.article, teamcategory=category
			).source,
			CategoryAssignmentSource.AUTOMATIC,
		)
		self.assertIsNotNone(category.match_config_hash)

	def test_new_manual_category_is_not_backfilled(self):
		response = self.create_category_via_admin(CategoryType.MANUAL)
		self.assertEqual(response.status_code, 302)

		category = TeamCategory.objects.get(category_slug="neuroplasticity")
		self.assertEqual(category.articles.count(), 0)
		self.assertEqual(category.trials.count(), 0)

	def edit_category_via_admin(self, category, **overrides):
		article_weights = category.get_match_weights("article")
		trial_weights = category.get_match_weights("trial")
		data = {
			"team": self.team.pk,
			"subjects": [self.subject.pk],
			"category_name": category.category_name,
			"category_slug": category.category_slug,
			"category_description": category.category_description or "",
			"category_terms": ",".join(category.category_terms or []),
			# category_terms has a callable default, so the admin renders a
			# hidden initial input and compares against it to detect changes
			"initial-category_terms": ",".join(category.category_terms or []),
			"category_type": category.category_type,
			"match_scope": category.match_scope,
			"match_min_score": category.match_min_score,
			"weight_article_title": article_weights["title"],
			"weight_article_summary": article_weights["summary"],
			"weight_trial_title": trial_weights["title"],
			"weight_trial_summary": trial_weights["summary"],
			"weight_trial_scientific_title": trial_weights["scientific_title"],
			"weight_trial_intervention": trial_weights["intervention"],
			"weight_trial_primary_outcome": trial_weights["primary_outcome"],
			"weight_trial_secondary_outcome": trial_weights["secondary_outcome"],
			"weight_trial_therapeutic_areas": trial_weights["therapeutic_areas"],
		}
		data.update(overrides)
		return self.client.post(
			reverse("admin:gregory_teamcategory_change", args=[category.pk]), data
		)

	def test_admin_saves_custom_match_settings(self):
		response = self.create_category_via_admin(
			CategoryType.AUTOMATIC,
			match_scope="title",
			match_min_score=5,
			weight_article_summary=4,
		)
		self.assertEqual(response.status_code, 302)

		category = TeamCategory.objects.get(category_slug="neuroplasticity")
		self.assertEqual(category.match_scope, "title")
		self.assertEqual(category.match_min_score, 5)
		self.assertEqual(category.match_weights["article"]["summary"], 4)
		self.assertEqual(category.match_weights["article"]["title"], 3)

	def test_changing_scope_via_admin_rematches(self):
		summary_only = Articles.objects.create(
			title="Unrelated topic",
			summary="A study of neuroplasticity",
			link="https://example.com/admin-summary-only",
		)
		summary_only.subjects.add(self.subject)

		self.create_category_via_admin(CategoryType.AUTOMATIC)
		category = TeamCategory.objects.get(category_slug="neuroplasticity")
		# default title+summary scope matched the summary-only article on backfill
		self.assertIn(summary_only, category.articles.all())

		response = self.edit_category_via_admin(category, match_scope="title")
		self.assertEqual(response.status_code, 302)

		category.refresh_from_db()
		self.assertEqual(category.match_scope, "title")
		self.assertNotIn(summary_only, category.articles.all())

	def test_editing_terms_triggers_immediate_rematch(self):
		self.create_category_via_admin(CategoryType.AUTOMATIC)
		category = TeamCategory.objects.get(category_slug="neuroplasticity")
		self.assertIn(self.article, category.articles.all())

		dopamine_article = Articles.objects.create(
			title="Dopamine signalling in adults",
			link="https://example.com/rematch-article",
		)
		dopamine_article.subjects.add(self.subject)

		response = self.edit_category_via_admin(category, category_terms="dopamine")
		self.assertEqual(response.status_code, 302)

		self.assertIn(dopamine_article, category.articles.all())
		self.assertNotIn(self.article, category.articles.all())

	def test_editing_unrelated_field_does_not_rematch(self):
		self.create_category_via_admin(CategoryType.AUTOMATIC)
		category = TeamCategory.objects.get(category_slug="neuroplasticity")

		with patch("gregory.admin.call_command") as mock_call:
			response = self.edit_category_via_admin(
				category, category_description="New description"
			)

		self.assertEqual(response.status_code, 302)
		mock_call.assert_not_called()

	def test_editing_manual_category_does_not_rematch(self):
		self.create_category_via_admin(CategoryType.MANUAL)
		category = TeamCategory.objects.get(category_slug="neuroplasticity")

		response = self.edit_category_via_admin(category, category_terms="dopamine")
		self.assertEqual(response.status_code, 302)

		self.assertEqual(category.articles.count(), 0)
