import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from gregory.models import Articles


class BackfillCleanTitlesCommandTest(TestCase):
	"""Tests for the backfill_clean_titles management command."""

	def test_cleans_dirty_title(self):
		"""A stored title with markup/whitespace is normalised in place."""
		article = Articles.objects.create(
			title=(
				"<scp>KDM2B</scp>\n                    ‐\n"
				"                    <scp>PP1</scp>\n"
				"                    Promotes Remyelination"
			),
			link="https://example.com/a",
		)
		call_command("backfill_clean_titles", stdout=StringIO())
		article.refresh_from_db()
		self.assertEqual(
			article.title, "KDM2B ‐ PP1 Promotes Remyelination"
		)

	def test_preserves_semantic_tags(self):
		"""Meaningful inline tags survive the backfill."""
		article = Articles.objects.create(
			title="CO<sub>2</sub> capture", link="https://example.com/b"
		)
		call_command("backfill_clean_titles", stdout=StringIO())
		article.refresh_from_db()
		self.assertEqual(article.title, "CO<sub>2</sub> capture")

	def test_clean_title_left_unchanged(self):
		"""An already-clean title is not modified."""
		article = Articles.objects.create(
			title="A perfectly clean title", link="https://example.com/c"
		)
		call_command("backfill_clean_titles", stdout=StringIO())
		article.refresh_from_db()
		self.assertEqual(article.title, "A perfectly clean title")

	def test_dry_run_does_not_save(self):
		"""--dry-run reports candidates without changing the database."""
		dirty = "<scp>ABC</scp>   spaced"
		article = Articles.objects.create(title=dirty, link="https://example.com/d")
		call_command("backfill_clean_titles", "--dry-run", stdout=StringIO())
		article.refresh_from_db()
		self.assertEqual(article.title, dirty)

	def test_shared_cleaned_title_is_allowed(self):
		"""Distinct articles may share a title now that the single-column unique
		is gone (dedup is DOI/link-based); cleaning proceeds for both rows."""
		clean = Articles.objects.create(
			title="Shared Title", link="https://example.com/e1"
		)
		dirty = Articles.objects.create(
			title="Shared <scp>Title</scp>", link="https://example.com/e2"
		)
		call_command(
			"backfill_clean_titles", stdout=StringIO(), stderr=StringIO()
		)
		clean.refresh_from_db()
		dirty.refresh_from_db()
		self.assertEqual(clean.title, "Shared Title")
		self.assertEqual(dirty.title, "Shared Title")

	def test_title_link_collision_is_skipped(self):
		"""Two rows with the SAME link whose titles clean to the same string
		would violate unique_article_title_link; that row is skipped."""
		Articles.objects.create(
			title="Shared Title", link="https://example.com/same"
		)
		dirty = Articles.objects.create(
			title="Shared <scp>Title</scp>", link="https://example.com/same"
		)
		call_command(
			"backfill_clean_titles", stdout=StringIO(), stderr=StringIO()
		)
		dirty.refresh_from_db()
		self.assertEqual(dirty.title, "Shared <scp>Title</scp>")
