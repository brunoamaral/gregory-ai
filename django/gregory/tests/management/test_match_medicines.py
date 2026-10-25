"""Tests for match_medicines. EMA is never called: the medicines come from the real rows in
fixtures/ema/medicines-base.json, loaded with sync_ema_medicines --file."""

from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase
from organizations.models import Organization

from gregory.models import (
	CategoryAssignmentSource,
	CategoryModality,
	CategoryType,
	Medicine,
	MedicineCategoryAssignment,
	Subject,
	Team,
	TeamCategory,
)

BASE = Path(__file__).resolve().parent.parent / "fixtures" / "ema" / "medicines-base.json"

Source = CategoryAssignmentSource


class MatchMedicinesTestCase(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Match Org", slug="match-org")
		cls.team = Team.objects.create(organization=cls.org, name="Match Team", slug="match-team")
		cls.ms = Subject.objects.create(
			subject_name="MS", subject_slug="ms", team=cls.team, ema_therapeutic_areas=["Multiple Sclerosis"]
		)
		cls.unmapped = Subject.objects.create(subject_name="Other", subject_slug="other", team=cls.team)
		call_command("sync_ema_medicines", file=str(BASE), stdout=StringIO())
		cls.ocrevus = Medicine.objects.get(name="Ocrevus")
		cls.tysabri = Medicine.objects.get(name="Tysabri")
		cls.tyruko = Medicine.objects.get(name="Tyruko")
		cls.viagra = Medicine.objects.get(name="Viagra")

	def category(self, name, terms, subject=None, modality=CategoryModality.BIOLOGIC_ANTIBODY, **kwargs):
		cat = TeamCategory.objects.create(
			team=self.team, category_name=name, category_terms=terms, modality=modality, **kwargs
		)
		cat.subjects.add(subject or self.ms)
		return cat

	def match(self, **options):
		out = StringIO()
		call_command("match_medicines", stdout=out, **options)
		return out.getvalue()

	def links(self, category):
		return {
			a.medicine.name: a
			for a in MedicineCategoryAssignment.objects.filter(teamcategory=category).select_related("medicine")
		}


class MatchTests(MatchMedicinesTestCase):
	def test_creates_automatic_links_with_the_reason(self):
		ocrelizumab = self.category("Ocrelizumab", ["ocrelizumab"])
		self.match()
		links = self.links(ocrelizumab)
		self.assertEqual(set(links), {"Ocrevus"})
		self.assertEqual(links["Ocrevus"].source, Source.AUTOMATIC)
		self.assertEqual(links["Ocrevus"].matched_on, "ocrelizumab · Multiple Sclerosis")
		self.assertFalse(links["Ocrevus"].suppressed)
		self.assertEqual(list(self.ocrevus.categories.all()), [ocrelizumab])

	def test_the_disease_check_keeps_the_crohn_refusal_and_viagra_out(self):
		natalizumab = self.category("Natalizumab", ["natalizumab"])
		sildenafil = self.category("Sildenafil", ["sildenafil"], modality=CategoryModality.SMALL_MOLECULE)
		self.match()
		self.assertEqual(set(self.links(natalizumab)), {"Tysabri", "Tyruko"})
		self.assertEqual(self.links(sildenafil), {})

	def test_an_unmapped_subject_skips_the_disease_check(self):
		sildenafil = self.category(
			"Sildenafil", ["sildenafil"], subject=self.unmapped, modality=CategoryModality.SMALL_MOLECULE
		)
		self.match()
		self.assertEqual(self.links(sildenafil)["Viagra"].matched_on, "sildenafil")

	def test_a_category_on_a_mapped_and_an_unmapped_subject_uses_the_mapped_areas(self):
		sildenafil = self.category("Sildenafil", ["sildenafil"], modality=CategoryModality.SMALL_MOLECULE)
		sildenafil.subjects.add(self.unmapped)
		self.match()
		self.assertEqual(self.links(sildenafil), {})

	def test_car_t_needs_medicine_terms_and_an_unmapped_subject(self):
		car_t_ms = self.category(
			"CAR-T on MS", ["car-t"], modality=CategoryModality.RESEARCH_TOPIC,
			medicine_terms=["*cabtagene*", "tisagenlecleucel"],
		)
		car_t_other = self.category(
			"CAR-T elsewhere", ["car-t"], subject=self.unmapped, modality=CategoryModality.RESEARCH_TOPIC,
			medicine_terms=["*cabtagene*", "tisagenlecleucel"],
		)
		no_terms = self.category(
			"CAR-T no pattern", ["car-t"], subject=self.unmapped, modality=CategoryModality.RESEARCH_TOPIC,
		)
		self.match()
		self.assertEqual(self.links(car_t_ms), {})
		self.assertEqual(set(self.links(car_t_other)), {"Yescarta", "Kymriah"})
		self.assertEqual(self.links(no_terms), {})

	def test_a_second_run_changes_nothing(self):
		self.category("Natalizumab", ["natalizumab"])
		self.match()
		before = list(
			MedicineCategoryAssignment.objects.order_by("pk").values_list("pk", "created_at", "matched_on")
		)
		history_before = MedicineCategoryAssignment.history.count()
		output = self.match()
		self.assertEqual(
			list(MedicineCategoryAssignment.objects.order_by("pk").values_list("pk", "created_at", "matched_on")),
			before,
		)
		self.assertEqual(MedicineCategoryAssignment.history.count(), history_before)
		self.assertIn("0 added, 0 removed, 0 relabelled, 2 unchanged", output)

	def test_a_category_edit_removes_stale_automatic_links_and_adds_new_ones(self):
		category = self.category("Antibody", ["ocrelizumab"])
		self.match()
		category.category_terms = ["natalizumab"]
		category.save()
		output = self.match()
		self.assertEqual(set(self.links(category)), {"Tysabri", "Tyruko"})
		self.assertIn("+2 -1", output)

	def test_clearing_the_terms_removes_every_automatic_link(self):
		category = self.category("Antibody", ["ocrelizumab"])
		self.match()
		category.category_terms = []
		category.save()
		self.match()
		self.assertEqual(self.links(category), {})

	def test_changing_the_modality_to_a_research_topic_removes_the_links(self):
		category = self.category("Antibody", ["ocrelizumab"])
		self.match()
		category.modality = CategoryModality.RESEARCH_TOPIC
		category.save()
		self.match()
		self.assertEqual(self.links(category), {})

	def test_a_changed_pattern_relabels_the_link_without_recreating_it(self):
		category = self.category("Antibody", ["ocrelizumab"])
		self.match()
		link = self.links(category)["Ocrevus"]
		category.medicine_terms = ["ocrelizumab", "ocrevus"]
		category.category_terms = []
		category.save()
		# Same first pattern, so the label is unchanged.
		self.match()
		self.assertEqual(self.links(category)["Ocrevus"].pk, link.pk)
		category.medicine_terms = ["ocrevus"]
		category.save()
		output = self.match()
		self.assertEqual(self.links(category)["Ocrevus"].pk, link.pk)
		self.assertEqual(self.links(category)["Ocrevus"].matched_on, "ocrevus · Multiple Sclerosis")
		self.assertIn("~1", output)

	def test_a_new_subject_area_adds_links_on_the_next_run(self):
		sildenafil = self.category("Sildenafil", ["sildenafil"], modality=CategoryModality.SMALL_MOLECULE)
		self.match()
		self.assertEqual(self.links(sildenafil), {})
		self.ms.ema_therapeutic_areas = []
		self.ms.save()
		self.match()
		self.assertEqual(set(self.links(sildenafil)), {"Viagra"})


class SuppressedAndManualLinkTests(MatchMedicinesTestCase):
	def test_a_suppressed_link_is_never_recreated_or_changed(self):
		category = self.category("Ocrelizumab", ["ocrelizumab"])
		self.match()
		link = self.links(category)["Ocrevus"]
		link.suppressed = True
		link.matched_on = "edited by hand"
		link.save()
		history = MedicineCategoryAssignment.history.count()
		self.match()
		self.match()
		link.refresh_from_db()
		self.assertTrue(link.suppressed)
		self.assertEqual(link.matched_on, "edited by hand")
		self.assertEqual(MedicineCategoryAssignment.objects.filter(teamcategory=category).count(), 1)
		self.assertEqual(MedicineCategoryAssignment.history.count(), history)

	def test_a_suppressed_link_survives_when_the_medicine_stops_matching(self):
		category = self.category("Ocrelizumab", ["ocrelizumab"])
		self.match()
		MedicineCategoryAssignment.objects.filter(teamcategory=category).update(suppressed=True)
		category.category_terms = ["natalizumab"]
		category.save()
		self.match()
		self.assertTrue(self.links(category)["Ocrevus"].suppressed)

	def test_manual_links_survive_and_skip_every_rule(self):
		category = self.category("Ocrelizumab", ["ocrelizumab"])
		MedicineCategoryAssignment.objects.create(
			medicine=self.viagra, teamcategory=category, source=Source.MANUAL
		)
		self.match()
		links = self.links(category)
		self.assertEqual(set(links), {"Ocrevus", "Viagra"})
		self.assertEqual(links["Viagra"].source, Source.MANUAL)
		self.assertEqual(links["Viagra"].matched_on, "")

	def test_a_manual_link_is_not_turned_into_an_automatic_one(self):
		category = self.category("Ocrelizumab", ["ocrelizumab"])
		MedicineCategoryAssignment.objects.create(
			medicine=self.ocrevus, teamcategory=category, source=Source.MANUAL, matched_on="by hand"
		)
		self.match()
		link = self.links(category)["Ocrevus"]
		self.assertEqual((link.source, link.matched_on), (Source.MANUAL, "by hand"))
		self.assertEqual(MedicineCategoryAssignment.objects.filter(teamcategory=category).count(), 1)

	def test_a_manual_link_is_kept_when_the_category_stops_matching(self):
		category = self.category("Ocrelizumab", ["ocrelizumab"])
		MedicineCategoryAssignment.objects.create(
			medicine=self.viagra, teamcategory=category, source=Source.MANUAL
		)
		category.category_terms = []
		category.save()
		self.match()
		self.assertEqual(set(self.links(category)), {"Viagra"})

	def test_manual_categories_are_skipped_entirely(self):
		manual = self.category("Manual", ["ocrelizumab"], category_type=CategoryType.MANUAL)
		stale = MedicineCategoryAssignment.objects.create(
			medicine=self.viagra, teamcategory=manual, source=Source.AUTOMATIC
		)
		output = self.match()
		self.assertEqual(set(self.links(manual)), {"Viagra"})
		stale.refresh_from_db()
		self.assertIn("Skipping 1 manual categories", output)

	def test_naming_a_manual_category_does_nothing(self):
		manual = self.category("Manual", ["ocrelizumab"], category_type=CategoryType.MANUAL)
		output = self.match(category=manual.pk)
		self.assertIn("is manual; nothing to do", output)
		self.assertEqual(self.links(manual), {})


class OptionTests(MatchMedicinesTestCase):
	def test_dry_run_reports_and_writes_nothing(self):
		self.category("Ocrelizumab", ["ocrelizumab"])
		output = self.match(dry_run=True)
		self.assertEqual(MedicineCategoryAssignment.objects.count(), 0)
		self.assertIn("[dry run] Ocrelizumab", output)
		self.assertIn("+1 -0 ~0", output)
		self.assertIn("Dry run: nothing was written.", output)

	def test_category_option_matches_only_that_category(self):
		first = self.category("Ocrelizumab", ["ocrelizumab"])
		second = self.category("Natalizumab", ["natalizumab"])
		self.match(category=first.pk)
		self.assertEqual(set(self.links(first)), {"Ocrevus"})
		self.assertEqual(self.links(second), {})

	def test_unknown_category_is_an_error(self):
		with self.assertRaises(CommandError):
			self.match(category=999999)

	def test_unchanged_categories_are_not_listed(self):
		self.category("Ocrelizumab", ["ocrelizumab"])
		self.match()
		self.assertNotIn("Ocrelizumab (id", self.match())

	def test_a_run_with_no_medicines_or_categories_is_quiet(self):
		Medicine.objects.all().delete()
		self.assertIn("0 added, 0 removed", self.match())


class WiringTests(MatchMedicinesTestCase):
	def test_sync_relinks_categories_in_the_same_run(self):
		self.category("Ocrelizumab", ["ocrelizumab"])
		call_command("sync_ema_medicines", file=str(BASE), stdout=StringIO())
		self.assertEqual(MedicineCategoryAssignment.objects.count(), 1)

	def test_a_dry_run_sync_does_not_match(self):
		self.category("Ocrelizumab", ["ocrelizumab"])
		call_command("sync_ema_medicines", file=str(BASE), dry_run=True, stdout=StringIO())
		self.assertEqual(MedicineCategoryAssignment.objects.count(), 0)

	def test_the_pipeline_runs_match_medicines_after_rebuild_categories(self):
		with patch("gregory.management.commands.pipeline.call_command") as mocked:
			call_command("pipeline", stdout=StringIO(), stderr=StringIO())
		names = [call.args[0] for call in mocked.call_args_list]
		self.assertIn("match_medicines", names)
		self.assertLess(names.index("rebuild_categories"), names.index("match_medicines"))
		self.assertLess(names.index("match_medicines"), names.index("detect_patent_trial_links"))
