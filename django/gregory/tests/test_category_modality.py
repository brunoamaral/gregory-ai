"""Tests for the TeamCategory.modality field, the CategoryModality enum, and the
sync_category_modalities management command (see CATEGORY-MODALITY-PLAN.md).

Run with:
    docker exec gregory python manage.py test gregory.tests.test_category_modality
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from organizations.models import Organization

from gregory.models import CategoryModality, Team, TeamCategory


class CategoryModalityFieldTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Modality Field Org")
		self.team = Team.objects.create(
			organization=self.org, name="Modality Field Team", slug="modality-field-team"
		)

	def test_modality_defaults_to_null(self):
		category = TeamCategory.objects.create(
			team=self.team, category_name="Untouched Category"
		)
		self.assertIsNone(category.modality)

	def test_modality_accepts_every_choice(self):
		for value in CategoryModality.values:
			category = TeamCategory.objects.create(
				team=self.team,
				category_name=f"Category {value}",
				modality=value,
			)
			category.refresh_from_db()
			self.assertEqual(category.modality, value)


class SyncCategoryModalitiesCommandTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Sync Modality Org")
		self.team = Team.objects.create(
			organization=self.org, name="Sync Modality Team", slug="sync-modality-team"
		)
		self.seeds = {
			"seeded-drug": "small_molecule",
			"seeded-antibody": "biologic_antibody",
			"stale-slug-not-in-db": "other",
		}
		self.drug = TeamCategory.objects.create(
			team=self.team, category_name="Seeded Drug", category_slug="seeded-drug"
		)
		self.antibody = TeamCategory.objects.create(
			team=self.team,
			category_name="Seeded Antibody",
			category_slug="seeded-antibody",
		)
		self.uncurated = TeamCategory.objects.create(
			team=self.team,
			category_name="Uncurated Category",
			category_slug="uncurated-category",
		)

	def _run(self, *args):
		out = StringIO()
		with patch(
			"gregory.management.commands.sync_category_modalities.CATEGORY_MODALITY_SEEDS",
			self.seeds,
		):
			call_command("sync_category_modalities", *args, stdout=out)
		return out.getvalue()

	def test_assigns_from_seeds(self):
		self._run()
		self.drug.refresh_from_db()
		self.antibody.refresh_from_db()
		self.assertEqual(self.drug.modality, "small_molecule")
		self.assertEqual(self.antibody.modality, "biologic_antibody")

	def test_leaves_human_set_values_alone(self):
		self.drug.modality = "research_topic"
		self.drug.save(update_fields=["modality"])
		self._run()
		self.drug.refresh_from_db()
		self.assertEqual(self.drug.modality, "research_topic")

	def test_force_overwrites_human_set_values(self):
		self.drug.modality = "research_topic"
		self.drug.save(update_fields=["modality"])
		self._run("--force")
		self.drug.refresh_from_db()
		self.assertEqual(self.drug.modality, "small_molecule")

	def test_stale_seed_slug_reported_not_crashed(self):
		output = self._run()
		self.assertIn("stale-slug-not-in-db", output)

	def test_dry_run_writes_nothing(self):
		self._run("--dry-run")
		self.drug.refresh_from_db()
		self.antibody.refresh_from_db()
		self.assertIsNone(self.drug.modality)
		self.assertIsNone(self.antibody.modality)

	def test_dry_run_reports_backlog_as_if_applied(self):
		output = self._run("--dry-run")
		self.assertIn("Would assign modality to 2 categories", output)
		self.assertIn("Uncurated Category", output)

	def test_backlog_lists_categories_still_null(self):
		output = self._run()
		self.assertIn("Uncurated Category", output)
		self.assertNotIn("Seeded Drug", output)
		self.assertNotIn("Seeded Antibody", output)

	def test_idempotent_second_run(self):
		self._run()
		second_output = self._run()
		self.assertIn("Assigned modality to 0 categories", second_output)
		self.assertIn("skipped 2 already-curated", second_output)
