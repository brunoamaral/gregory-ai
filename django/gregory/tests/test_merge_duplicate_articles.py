"""
Tests for duplicate-article merging (DOI collision fix).

Covers:
  * gregory.services.article_merge.merge_articles — M2M/relation union, curation
    preservation, survivor relevance recompute.
  * gregory.services.article_merge.assign_doi_or_merge — the guard that stops two
    rows from silently converging on one DOI.
  * the merge_duplicate_articles management command — dry-run default, --scan,
    --doi, and --keep/--remove modes.
  * the unique_article_doi constraint.

Run:
  docker exec gregory python manage.py test gregory.tests.test_merge_duplicate_articles
"""

from contextlib import contextmanager
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization

from gregory.models import (
	ArticleCategoryAssignment,
	ArticleOrgContent,
	Articles,
	ArticleSubjectRelevance,
	CategoryAssignmentSource,
	MLPredictions,
	Sources,
	Subject,
	Team,
	TeamCategory,
)
from gregory.services.article_merge import (
	assign_doi_or_merge,
	merge_articles,
	pick_survivor,
)


class MergeFixtureMixin:
	def build_common(self):
		self.org = Organization.objects.create(name="Org")
		self.team = Team.objects.create(organization=self.org, slug="merge-team")
		self.subject = Subject.objects.create(
			subject_name="Subject", subject_slug="subject", team=self.team
		)
		self.source_a = Sources.objects.create(
			name="PubMed", method="rss", source_for="science paper",
			active=True, link="https://feed.example/pubmed",
		)
		self.source_b = Sources.objects.create(
			name="BASE", method="rss", source_for="science paper",
			active=True, link="https://feed.example/base",
		)

	def make_article(self, **kwargs):
		kwargs.setdefault("title", "A paper")
		kwargs.setdefault("link", f"https://ex.org/{Articles.objects.count()}")
		return Articles.objects.create(**kwargs)

	@contextmanager
	def without_doi_constraint(self):
		"""Simulate the pre-constraint state so legacy same-DOI duplicates can be
		created. The DROP is DDL inside the test's transaction, so TestCase's
		rollback restores the index automatically."""
		with connection.cursor() as cursor:
			cursor.execute("DROP INDEX IF EXISTS unique_article_doi")
		yield

	def make_dup_pair(self, doi, links=("https://ex.org/1", "https://ex.org/2")):
		with self.without_doi_constraint():
			a = self.make_article(doi=doi, link=links[0])
			b = self.make_article(doi=doi, link=links[1])
		return a, b


class MergeArticlesServiceTests(MergeFixtureMixin, TestCase):
	def setUp(self):
		self.build_common()

	def test_unions_m2m_and_deletes_loser(self):
		keep, loser = self.make_dup_pair(
			"10.1/x", links=("https://ex.org/keep", "https://ex.org/loser")
		)
		keep.sources.add(self.source_a)
		keep.subjects.add(self.subject)
		keep.teams.add(self.team)
		loser.sources.add(self.source_b)

		with transaction.atomic():
			survivor = merge_articles(keep, [loser])

		self.assertFalse(Articles.objects.filter(pk=loser.pk).exists())
		self.assertEqual(survivor.pk, keep.pk)
		self.assertSetEqual(
			set(survivor.sources.values_list("pk", flat=True)),
			{self.source_a.pk, self.source_b.pk},
		)

	def test_preserves_manual_relevance_from_loser(self):
		# Survivor is unreviewed for the subject; loser has a manual "relevant".
		keep, loser = self.make_dup_pair(
			"10.1/x", links=("https://ex.org/keep", "https://ex.org/loser")
		)
		ArticleSubjectRelevance.objects.create(
			article=keep, subject=self.subject, is_relevant=None
		)
		ArticleSubjectRelevance.objects.create(
			article=loser, subject=self.subject, is_relevant=True
		)

		with transaction.atomic():
			merge_articles(keep, [loser])

		rel = ArticleSubjectRelevance.objects.get(
			article=keep, subject=self.subject
		)
		self.assertTrue(rel.is_relevant)
		# No orphaned relevance rows remain for the deleted loser.
		self.assertEqual(
			ArticleSubjectRelevance.objects.filter(subject=self.subject).count(), 1
		)

	def test_pick_survivor_prefers_manual_review_then_earliest(self):
		older, newer = self.make_dup_pair(
			"10.1/x", links=("https://ex.org/older", "https://ex.org/newer")
		)
		# Force a later discovery_date on the second row.
		Articles.objects.filter(pk=newer.pk).update(
			discovery_date=timezone.now() + timezone.timedelta(days=30)
		)
		newer.refresh_from_db()

		# With no curation, the earliest-discovered row wins.
		self.assertEqual(pick_survivor([newer, older]).pk, older.pk)

		# A manual review on the newer row flips the choice.
		ArticleSubjectRelevance.objects.create(
			article=newer, subject=self.subject, is_relevant=False
		)
		self.assertEqual(pick_survivor([newer, older]).pk, newer.pk)

	def test_pick_survivor_uses_real_ml_prediction_relation(self):
		# Predictions live on the ml_predictions_detail reverse FK, not the
		# vestigial ml_predictions M2M. An older row without a prediction must
		# lose to a newer row that has one.
		older, newer = self.make_dup_pair(
			"10.1/ml", links=("https://ex.org/older", "https://ex.org/newer")
		)
		Articles.objects.filter(pk=newer.pk).update(
			discovery_date=timezone.now() + timezone.timedelta(days=30)
		)
		newer.refresh_from_db()
		MLPredictions.objects.create(
			article=newer, subject=self.subject,
			algorithm="pubmed_bert", model_version="v1", probability_score=0.9,
		)
		self.assertEqual(pick_survivor([older, newer]).pk, newer.pk)

	def test_team_category_provenance_survives_merge(self):
		# Regression: the automatic/manual `source` on ArticleCategoryAssignment
		# must be preserved. A bare M2M .add() would fabricate a manual row.
		keep, loser = self.make_dup_pair(
			"10.1/cat", links=("https://ex.org/keep", "https://ex.org/loser")
		)
		category = TeamCategory.objects.create(
			team=self.team, category_name="Cat", category_slug="cat"
		)
		ArticleCategoryAssignment.objects.create(
			articles=loser,
			teamcategory=category,
			source=CategoryAssignmentSource.AUTOMATIC,
		)

		with transaction.atomic():
			merge_articles(keep, [loser])

		assignment = ArticleCategoryAssignment.objects.get(
			articles=keep, teamcategory=category
		)
		self.assertEqual(assignment.source, CategoryAssignmentSource.AUTOMATIC)

	def test_org_content_adopted_into_empty_survivor_slot(self):
		keep, loser = self.make_dup_pair(
			"10.1/org", links=("https://ex.org/keep", "https://ex.org/loser")
		)
		# Survivor has an empty editorial row; loser's is filled.
		ArticleOrgContent.objects.create(
			article=keep, organization=self.org, takeaways="", summary_plain_english=""
		)
		ArticleOrgContent.objects.create(
			article=loser, organization=self.org,
			takeaways="Key finding", summary_plain_english="Plain summary",
		)

		with transaction.atomic():
			merge_articles(keep, [loser])

		content = ArticleOrgContent.objects.get(
			article=keep, organization=self.org
		)
		self.assertEqual(content.takeaways, "Key finding")
		self.assertEqual(content.summary_plain_english, "Plain summary")
		self.assertEqual(ArticleOrgContent.objects.count(), 1)

	def test_three_way_merge(self):
		with self.without_doi_constraint():
			a = self.make_article(doi="10.1/three", link="https://ex.org/a")
			b = self.make_article(doi="10.1/three", link="https://ex.org/b")
			c = self.make_article(doi="10.1/three", link="https://ex.org/c")
		b.sources.add(self.source_a)
		c.sources.add(self.source_b)

		with transaction.atomic():
			survivor = merge_articles(a, [b, c])

		self.assertEqual(Articles.objects.filter(doi__iexact="10.1/three").count(), 1)
		self.assertSetEqual(
			set(survivor.sources.values_list("pk", flat=True)),
			{self.source_a.pk, self.source_b.pk},
		)


class AssignDoiOrMergeTests(MergeFixtureMixin, TestCase):
	def setUp(self):
		self.build_common()

	def test_no_collision_assigns_doi(self):
		art = self.make_article(link="https://ex.org/a")
		with transaction.atomic():
			survivor, merged = assign_doi_or_merge(art, "10.1/new")
		self.assertFalse(merged)
		self.assertEqual(survivor.pk, art.pk)
		art.refresh_from_db()
		self.assertEqual(art.doi, "10.1/new")

	def test_collision_merges_into_existing_holder(self):
		# The original (curated) row already holds the DOI.
		original = self.make_article(doi="10.1/dup", link="https://ex.org/orig")
		ArticleSubjectRelevance.objects.create(
			article=original, subject=self.subject, is_relevant=True
		)
		# A later DOI-less BASE row is about to receive the same DOI.
		late = self.make_article(link="https://ex.org/late")
		late.sources.add(self.source_b)

		with transaction.atomic():
			survivor, merged = assign_doi_or_merge(late, "10.1/dup")

		self.assertTrue(merged)
		self.assertEqual(survivor.pk, original.pk)
		self.assertFalse(Articles.objects.filter(pk=late.pk).exists())
		self.assertEqual(Articles.objects.filter(doi="10.1/dup").count(), 1)
		# BASE source moved onto the survivor.
		self.assertIn(
			self.source_b.pk, survivor.sources.values_list("pk", flat=True)
		)

	def test_collision_case_insensitive(self):
		self.make_article(doi="10.1/AbC", link="https://ex.org/orig")
		late = self.make_article(link="https://ex.org/late")
		with transaction.atomic():
			survivor, merged = assign_doi_or_merge(late, "10.1/abc")
		self.assertTrue(merged)
		self.assertEqual(Articles.objects.count(), 1)


class UniqueDoiConstraintTests(MergeFixtureMixin, TestCase):
	def setUp(self):
		self.build_common()

	def test_duplicate_doi_rejected_by_db(self):
		self.make_article(doi="10.1/uniq", link="https://ex.org/a")
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				self.make_article(doi="10.1/uniq", link="https://ex.org/b")

	def test_case_variant_doi_rejected(self):
		self.make_article(doi="10.1/Case", link="https://ex.org/a")
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				self.make_article(doi="10.1/case", link="https://ex.org/b")

	def test_null_and_empty_dois_allowed(self):
		self.make_article(doi=None, link="https://ex.org/a")
		self.make_article(doi=None, link="https://ex.org/b")
		self.make_article(doi="", link="https://ex.org/c")
		self.make_article(doi="", link="https://ex.org/d")
		self.assertEqual(Articles.objects.count(), 4)


class MergeCommandTests(MergeFixtureMixin, TestCase):
	def setUp(self):
		self.build_common()

	def test_dry_run_default_rolls_back(self):
		a, b = self.make_dup_pair("10.1/dry")
		out = StringIO()
		call_command("merge_duplicate_articles", "--scan", stdout=out)
		# Nothing removed without --commit.
		self.assertTrue(Articles.objects.filter(pk=b.pk).exists())
		self.assertIn("DRY RUN", out.getvalue())

	def test_scan_commit_merges(self):
		a, b = self.make_dup_pair("10.1/scan")
		out = StringIO()
		call_command("merge_duplicate_articles", "--scan", "--commit", stdout=out)
		remaining = Articles.objects.filter(doi__iexact="10.1/scan")
		self.assertEqual(remaining.count(), 1)

	def test_doi_mode_commit(self):
		a, b = self.make_dup_pair("10.1/byd")
		call_command(
			"merge_duplicate_articles", "--doi", "10.1/byd", "--commit",
			stdout=StringIO(),
		)
		self.assertEqual(Articles.objects.filter(doi__iexact="10.1/byd").count(), 1)

	def test_keep_remove_mode_for_different_dois(self):
		# Preprint vs published: different DOIs, same paper.
		preprint = self.make_article(doi="10.1/preprint", link="https://ex.org/pre")
		published = self.make_article(doi="10.1/published", link="https://ex.org/pub")
		call_command(
			"merge_duplicate_articles",
			"--keep", str(published.pk),
			"--remove", str(preprint.pk),
			"--commit",
			stdout=StringIO(),
		)
		self.assertFalse(Articles.objects.filter(pk=preprint.pk).exists())
		published.refresh_from_db()
		self.assertEqual(published.doi, "10.1/published")

	def test_check_duplicate_dois_reports_clean(self):
		self.make_article(doi="10.1/only", link="https://ex.org/a")
		out = StringIO()
		call_command("check_duplicate_dois", stdout=out)
		self.assertIn("No duplicate DOIs found", out.getvalue())
