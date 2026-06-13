"""
Tests for the reassign_team_objects management command and the
underlying gregory.services.team_reassignment.reassign_team() service.

Coverage:
 - --dry-run: report is populated but no DB writes occur
 - --conflict skip: conflicting subject stays on old team
 - --conflict rename: conflicting subject gets a new slug on target team
 - --conflict merge: dependents moved, duplicate subject deleted
 - Cross-org guard: ValueError raised when teams are in different orgs
 - Inactive target guard: ValueError when to_team is inactive
 - Sources, TeamCategory, Lists, PredictionRunLog all reassigned
 - Articles / Trials M2M relinked
 - management command wires up correctly (bad slug → CommandError)
"""

from io import StringIO

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from organizations.models import Organization

from gregory.models import (
	Articles,
	Sources,
	Subject,
	Team,
	TeamCategory,
	PredictionRunLog,
	ArticleSubjectRelevance,
)
from gregory.services.team_reassignment import reassign_team
from subscriptions.models import Lists


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org(name="Test Org"):
	return Organization.objects.create(name=name)


def _make_team(org, name="Team A", slug="team-a", is_active=True):
	site = Site.objects.get_or_create(domain="example.com", name="example.com")[0]
	return Team.all_objects.create(
		organization=org,
		name=name,
		slug=slug,
		site=site,
		is_active=is_active,
	)


def _make_subject(team, name="MS", slug="ms"):
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slug)


def _make_source(team, subject=None, name="RSS Feed"):
	return Sources.objects.create(
		team=team,
		subject=subject,
		name=name,
		link="https://example.com/feed",
		source_for="articles",
	)


def _make_article(teams=None, subjects=None, title="Article"):
	article = Articles.objects.create(title=title, link="https://example.com/article")
	if teams:
		article.teams.set(teams)
	if subjects:
		article.subjects.set(subjects)
	return article


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


class ReassignTeamServiceGuardsTest(TestCase):
	"""Tests for the safety guards in reassign_team()."""

	def setUp(self):
		self.org = _make_org()
		self.org2 = _make_org("Other Org")
		self.team_a = _make_team(self.org, "Team A", "team-a")
		self.team_b = _make_team(self.org, "Team B", "team-b")
		self.team_other = _make_team(self.org2, "Other Team", "other-team")

	def test_different_organisations_raises(self):
		with self.assertRaises(
			ValueError, msg="Should raise when teams are in different orgs"
		):
			reassign_team(self.team_a, self.team_other)

	def test_inactive_target_raises(self):
		inactive = _make_team(self.org, "Inactive", "inactive", is_active=False)
		with self.assertRaises(
			ValueError, msg="Should raise when target team is inactive"
		):
			reassign_team(self.team_a, inactive)


class ReassignTeamDryRunTest(TestCase):
	"""--dry-run must leave the database completely unchanged."""

	def setUp(self):
		self.org = _make_org()
		self.team_a = _make_team(self.org, "Team A", "team-a")
		self.team_b = _make_team(self.org, "Team B", "team-b")
		self.subject = _make_subject(self.team_a, "MS", "ms")
		self.source = _make_source(self.team_a, self.subject)
		self.category = TeamCategory.objects.create(
			team=self.team_a, category_name="Cat A"
		)
		self.lst = Lists.objects.create(
			team=self.team_a, list_name="List A", list_email_subject="subj"
		)

	def test_dry_run_does_not_modify_subject(self):
		report = reassign_team(self.team_a, self.team_b, dry_run=True)
		self.subject.refresh_from_db()
		self.assertEqual(self.subject.team, self.team_a)
		self.assertIn("ms", report.subjects_moved)

	def test_dry_run_does_not_modify_source(self):
		reassign_team(self.team_a, self.team_b, dry_run=True)
		self.source.refresh_from_db()
		self.assertEqual(self.source.team, self.team_a)

	def test_dry_run_does_not_modify_category(self):
		reassign_team(self.team_a, self.team_b, dry_run=True)
		self.category.refresh_from_db()
		self.assertEqual(self.category.team, self.team_a)

	def test_dry_run_does_not_modify_list(self):
		reassign_team(self.team_a, self.team_b, dry_run=True)
		self.lst.refresh_from_db()
		self.assertEqual(self.lst.team, self.team_a)

	def test_dry_run_report_counts_correctly(self):
		report = reassign_team(self.team_a, self.team_b, dry_run=True)
		self.assertEqual(report.sources_moved, 1)
		self.assertEqual(report.categories_moved, 1)
		self.assertEqual(report.lists_moved, 1)


class ReassignTeamBasicMoveTest(TestCase):
	"""Non-conflicting reassignment moves all objects."""

	def setUp(self):
		self.org = _make_org()
		self.team_a = _make_team(self.org, "Team A", "team-a")
		self.team_b = _make_team(self.org, "Team B", "team-b")
		self.subject = _make_subject(self.team_a, "MS", "ms")
		self.source = _make_source(self.team_a, self.subject)
		self.category = TeamCategory.objects.create(
			team=self.team_a, category_name="Cat A"
		)
		self.lst = Lists.objects.create(
			team=self.team_a, list_name="List A", list_email_subject="subj"
		)
		self.article = _make_article(teams=[self.team_a], subjects=[self.subject])
		self.log = PredictionRunLog.objects.create(
			team=self.team_a,
			subject=self.subject,
			run_type="train",
			algorithm="lgbm_tfidf",
		)

	def test_subject_moved(self):
		reassign_team(self.team_a, self.team_b)
		self.subject.refresh_from_db()
		self.assertEqual(self.subject.team, self.team_b)

	def test_source_moved(self):
		reassign_team(self.team_a, self.team_b)
		self.source.refresh_from_db()
		self.assertEqual(self.source.team, self.team_b)

	def test_category_moved(self):
		reassign_team(self.team_a, self.team_b)
		self.category.refresh_from_db()
		self.assertEqual(self.category.team, self.team_b)

	def test_list_moved(self):
		reassign_team(self.team_a, self.team_b)
		self.lst.refresh_from_db()
		self.assertEqual(self.lst.team, self.team_b)

	def test_article_relinked(self):
		reassign_team(self.team_a, self.team_b)
		self.article.refresh_from_db()
		self.assertIn(self.team_b, self.article.teams.all())
		self.assertNotIn(self.team_a, self.article.teams.all())

	def test_prediction_log_moved(self):
		reassign_team(self.team_a, self.team_b)
		self.log.refresh_from_db()
		self.assertEqual(self.log.team, self.team_b)

	def test_report_has_no_errors(self):
		report = reassign_team(self.team_a, self.team_b)
		self.assertEqual(report.errors, [])
		self.assertEqual(len(report.subjects_moved), 1)
		self.assertEqual(report.sources_moved, 1)


# ---------------------------------------------------------------------------
# Conflict: skip
# ---------------------------------------------------------------------------


class ReassignTeamConflictSkipTest(TestCase):
	"""conflict='skip' — the conflicting subject stays on the old team."""

	def setUp(self):
		self.org = _make_org()
		self.team_a = _make_team(self.org, "Team A", "team-a")
		self.team_b = _make_team(self.org, "Team B", "team-b")
		# Same slug "ms" on both teams — collision!
		self.subject_a = _make_subject(self.team_a, "MS", "ms")
		self.subject_b = _make_subject(self.team_b, "MS", "ms")

	def test_conflicting_subject_stays_on_old_team(self):
		report = reassign_team(self.team_a, self.team_b, conflict="skip")
		self.subject_a.refresh_from_db()
		self.assertEqual(self.subject_a.team, self.team_a)

	def test_report_records_skip(self):
		report = reassign_team(self.team_a, self.team_b, conflict="skip")
		self.assertIn("ms", report.subjects_skipped)
		self.assertEqual(len(report.subjects_moved), 0)


# ---------------------------------------------------------------------------
# Conflict: rename
# ---------------------------------------------------------------------------


class ReassignTeamConflictRenameTest(TestCase):
	"""conflict='rename' — conflicting subject gets a new unique slug."""

	def setUp(self):
		self.org = _make_org()
		self.team_a = _make_team(self.org, "Team A", "team-a")
		self.team_b = _make_team(self.org, "Team B", "team-b")
		self.subject_a = _make_subject(self.team_a, "MS", "ms")
		self.subject_b = _make_subject(self.team_b, "MS", "ms")

	def test_subject_moved_with_new_slug(self):
		reassign_team(self.team_a, self.team_b, conflict="rename")
		self.subject_a.refresh_from_db()
		self.assertEqual(self.subject_a.team, self.team_b)
		self.assertNotEqual(self.subject_a.subject_slug, "ms")

	def test_new_slug_contains_from_team_slug(self):
		reassign_team(self.team_a, self.team_b, conflict="rename")
		self.subject_a.refresh_from_db()
		self.assertIn("team-a", self.subject_a.subject_slug)

	def test_report_records_rename(self):
		report = reassign_team(self.team_a, self.team_b, conflict="rename")
		self.assertEqual(len(report.subjects_renamed), 1)
		self.assertEqual(len(report.subjects_skipped), 0)
		# Entry must show "old → new", not "new → new"
		entry = report.subjects_renamed[0]
		self.assertTrue(
			entry.startswith("ms →"),
			f"Expected entry to start with 'ms →', got: {entry!r}",
		)

	def test_rename_slug_is_unique_when_suffix_also_collides(self):
		# Pre-create "ms-from-team-a" to force a counter suffix
		_make_subject(self.team_b, "MS2", "ms-from-team-a")
		report = reassign_team(self.team_a, self.team_b, conflict="rename")
		self.subject_a.refresh_from_db()
		self.assertNotEqual(self.subject_a.subject_slug, "ms-from-team-a")
		self.assertEqual(self.subject_a.team, self.team_b)


# ---------------------------------------------------------------------------
# Conflict: merge
# ---------------------------------------------------------------------------


class ReassignTeamConflictMergeTest(TestCase):
	"""conflict='merge' — duplicate subject is folded into the target."""

	def setUp(self):
		self.org = _make_org()
		self.team_a = _make_team(self.org, "Team A", "team-a")
		self.team_b = _make_team(self.org, "Team B", "team-b")
		self.subject_a = _make_subject(self.team_a, "MS", "ms")
		self.subject_b = _make_subject(self.team_b, "MS", "ms")

	def test_duplicate_subject_deleted_after_merge(self):
		reassign_team(self.team_a, self.team_b, conflict="merge")
		exists = Subject.objects.filter(pk=self.subject_a.pk).exists()
		self.assertFalse(exists, "Source subject should be deleted after merge")

	def test_report_records_merge(self):
		report = reassign_team(self.team_a, self.team_b, conflict="merge")
		self.assertIn("ms", report.subjects_merged)

	def test_merge_sources_moved_to_target_subject(self):
		source = _make_source(self.team_a, self.subject_a, "Feed A")
		reassign_team(self.team_a, self.team_b, conflict="merge")
		source.refresh_from_db()
		self.assertEqual(source.subject, self.subject_b)

	def test_merge_articles_linked_to_target_subject(self):
		article = _make_article(teams=[self.team_a], subjects=[self.subject_a])
		reassign_team(self.team_a, self.team_b, conflict="merge")
		article.refresh_from_db()
		self.assertIn(self.subject_b, article.subjects.all())
		self.assertNotIn(self.subject_a, article.subjects.all())

	def test_merge_skips_duplicate_article_subject_relevance(self):
		"""If both subjects already have an ASR for the same article, the duplicate is dropped."""
		article = _make_article(teams=[self.team_a, self.team_b])
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_a, is_relevant=True
		)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject_b, is_relevant=False
		)
		reassign_team(self.team_a, self.team_b, conflict="merge")
		asr_count = ArticleSubjectRelevance.objects.filter(
			article=article, subject=self.subject_b
		).count()
		self.assertEqual(asr_count, 1)

	def test_merge_dry_run_does_not_delete_subject(self):
		reassign_team(self.team_a, self.team_b, conflict="merge", dry_run=True)
		exists = Subject.objects.filter(pk=self.subject_a.pk).exists()
		self.assertTrue(exists, "Dry run must not delete the source subject")


# ---------------------------------------------------------------------------
# Management command wiring
# ---------------------------------------------------------------------------


class ReassignTeamCommandTest(TestCase):
	"""Management command correctly wires options → service."""

	def setUp(self):
		self.org = _make_org()
		self.team_a = _make_team(self.org, "Team A", "cmd-team-a")
		self.team_b = _make_team(self.org, "Team B", "cmd-team-b")
		_make_subject(self.team_a, "MS", "ms")

	def test_bad_from_slug_raises_command_error(self):
		with self.assertRaises(CommandError):
			call_command(
				"reassign_team_objects",
				"--from-team",
				"no-such-slug",
				"--to-team",
				"cmd-team-b",
			)

	def test_bad_to_slug_raises_command_error(self):
		with self.assertRaises(CommandError):
			call_command(
				"reassign_team_objects",
				"--from-team",
				"cmd-team-a",
				"--to-team",
				"no-such-slug",
			)

	def test_dry_run_does_not_move_subject(self):
		out = StringIO()
		call_command(
			"reassign_team_objects",
			"--from-team",
			"cmd-team-a",
			"--to-team",
			"cmd-team-b",
			"--dry-run",
			stdout=out,
		)
		subject = Subject.objects.get(subject_slug="ms")
		self.assertEqual(subject.team, self.team_a)
		self.assertIn("dry run", out.getvalue().lower())

	def test_live_run_moves_subject(self):
		call_command(
			"reassign_team_objects",
			"--from-team",
			"cmd-team-a",
			"--to-team",
			"cmd-team-b",
		)
		subject = Subject.objects.get(subject_slug="ms")
		self.assertEqual(subject.team, self.team_b)

	def test_conflict_skip_flag_passed_through(self):
		# Create a collision so skip mode is exercised
		_make_subject(self.team_b, "MS", "ms")
		out = StringIO()
		call_command(
			"reassign_team_objects",
			"--from-team",
			"cmd-team-a",
			"--to-team",
			"cmd-team-b",
			"--conflict",
			"skip",
			stdout=out,
		)
		# Subject should remain on team_a due to skip
		subject = Subject.objects.get(team=self.team_a, subject_slug="ms")
		self.assertIsNotNone(subject)

	def test_conflict_rename_flag_passed_through(self):
		_make_subject(self.team_b, "MS", "ms")
		call_command(
			"reassign_team_objects",
			"--from-team",
			"cmd-team-a",
			"--to-team",
			"cmd-team-b",
			"--conflict",
			"rename",
		)
		# Original slug gone from team_a, a renamed slug now exists on team_b
		self.assertFalse(
			Subject.objects.filter(team=self.team_a, subject_slug="ms").exists()
		)
		self.assertTrue(
			Subject.objects.filter(
				team=self.team_b, subject_slug__startswith="ms-from-"
			).exists()
		)

	def test_conflict_merge_flag_passed_through(self):
		_make_subject(self.team_b, "MS", "ms")
		call_command(
			"reassign_team_objects",
			"--from-team",
			"cmd-team-a",
			"--to-team",
			"cmd-team-b",
			"--conflict",
			"merge",
		)
		# Only one "ms" subject should remain (the target's)
		count = Subject.objects.filter(subject_slug="ms").count()
		self.assertEqual(count, 1)
		remaining = Subject.objects.get(subject_slug="ms")
		self.assertEqual(remaining.team, self.team_b)
