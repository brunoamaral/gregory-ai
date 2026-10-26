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
	MedicineSubjectLink,
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


UNTRACKED = Path(__file__).resolve().parent.parent / "fixtures" / "ema" / "medicines-untracked.json"

ALZHEIMERS_PRODUCTS = {
	"Exelon", "Prometax", "Rivastigmine Sandoz", "Rivastigmine 1 A Pharma", "Rivastigmine Hexal",
	"Ebixa", "Axura", "Memantine Merz", "Leqembi", "Kisunla", "Aduhelm", "Acrescent",
}
PARKINSONS_PRODUCTS = {
	"Exelon", "Prometax", "Rivastigmine Sandoz", "Rivastigmine 1 A Pharma", "Rivastigmine Hexal",
}
MS_PRODUCTS = {"Avonex", "Rebif", "Tecfidera", "Betaferon", "Extavia"}


class UntrackedTestCase(TestCase):
	"""Twenty real EMA rows for Alzheimer's, Parkinson's and MS: copies of one medicine,
	diagnostics, refused and withdrawn applications (fixtures/ema/medicines-untracked.json)."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Subject Org", slug="subject-org")
		cls.team = Team.objects.create(organization=cls.org, name="Subject Team", slug="subject-team")
		cls.alz = Subject.objects.create(
			subject_name="Alzheimer's", subject_slug="alz", team=cls.team,
			ema_therapeutic_areas=["Alzheimer Disease", "Dementia"],
		)
		cls.pd = Subject.objects.create(
			subject_name="Parkinson's", subject_slug="pd", team=cls.team,
			ema_therapeutic_areas=["Parkinson Disease"],
		)
		cls.ms = Subject.objects.create(
			subject_name="MS", subject_slug="ms", team=cls.team,
			ema_therapeutic_areas=["Multiple Sclerosis"],
		)
		cls.research = Subject.objects.create(subject_name="Neuroinflammation", subject_slug="neuro", team=cls.team)
		call_command("sync_ema_medicines", file=str(UNTRACKED), stdout=StringIO())
		# The sync ends with match_medicines; start each test from the unmatched state.
		MedicineSubjectLink.objects.all().delete()
		Medicine.objects.update(grouped_under=None)
		cls.by_name = {m.name: m for m in Medicine.objects.all()}

	def match(self, **options):
		out = StringIO()
		call_command("match_medicines", stdout=out, **options)
		return out.getvalue()

	def subject_links(self, subject, suppressed=None):
		links = MedicineSubjectLink.objects.filter(subject=subject).select_related("medicine")
		if suppressed is not None:
			links = links.filter(suppressed=suppressed)
		return {link.medicine.name: link for link in links}

	def grouped(self):
		return {
			m.name: m.grouped_under.name
			for m in Medicine.objects.exclude(grouped_under=None).select_related("grouped_under")
		}


class SubjectPassTests(UntrackedTestCase):
	def test_a_subject_with_areas_links_every_non_diagnostic_product_approved_for_them(self):
		self.match()
		self.assertEqual(set(self.subject_links(self.alz)), ALZHEIMERS_PRODUCTS)
		self.assertEqual(set(self.subject_links(self.pd)), PARKINSONS_PRODUCTS)
		self.assertEqual(set(self.subject_links(self.ms)), MS_PRODUCTS)

	def test_the_link_names_the_area_that_matched(self):
		self.match()
		links = self.subject_links(self.alz)
		self.assertEqual(links["Leqembi"].matched_on, "Alzheimer Disease")
		self.assertEqual(self.subject_links(self.pd)["Exelon"].matched_on, "Parkinson Disease")
		self.assertFalse(links["Leqembi"].suppressed)

	def test_copies_are_linked_too(self):
		# The read side hides grouped copies by default; the link exists all the same.
		self.match()
		self.assertIn("Axura", self.subject_links(self.alz))
		self.assertIn("Prometax", self.subject_links(self.alz))

	def test_diagnostics_are_linked_to_no_subject(self):
		self.match()
		for subject in (self.alz, self.pd):
			links = self.subject_links(subject)
			self.assertNotIn("Neuraceq", links)
			self.assertNotIn("DaTSCAN", links)
		self.assertFalse(MedicineSubjectLink.objects.filter(medicine__name__in=["Neuraceq", "DaTSCAN"]).exists())

	def test_a_subject_without_areas_gets_nothing(self):
		output = self.match()
		self.assertFalse(MedicineSubjectLink.objects.filter(subject=self.research).exists())
		self.assertNotIn("Neuroinflammation", output)

	def test_the_matcher_records_no_editor_history(self):
		before = MedicineSubjectLink.history.count()
		self.match()
		self.assertTrue(MedicineSubjectLink.objects.exists())
		self.assertEqual(MedicineSubjectLink.history.count(), before)
		# A removal the matcher makes names no editor either.
		self.alz.ema_therapeutic_areas = []
		self.alz.save()
		self.match()
		removed = MedicineSubjectLink.history.order_by("-history_id")[:12]
		self.assertEqual({(row.history_type, row.editor_label, row.history_user_id) for row in removed}, {("-", "", None)})

	def test_a_second_run_changes_nothing(self):
		self.match()
		before = list(MedicineSubjectLink.objects.order_by("pk").values_list("pk", "created_at", "matched_on"))
		output = self.match()
		self.assertEqual(
			list(MedicineSubjectLink.objects.order_by("pk").values_list("pk", "created_at", "matched_on")), before
		)
		self.assertIn(f"Subject links: 0 added, 0 removed, 0 relabelled, {len(before)} unchanged, in 0 subjects.", output)
		self.assertNotIn("(id", output)

	def test_a_changed_area_relabels_and_removes(self):
		self.match()
		self.alz.ema_therapeutic_areas = ["Dementia"]
		self.alz.save()
		output = self.match()
		# Only the rivastigmine products are listed for Dementia, and the label is now that area.
		links = self.subject_links(self.alz)
		self.assertEqual(set(links), PARKINSONS_PRODUCTS)
		self.assertEqual({link.matched_on for link in links.values()}, {"Dementia"})
		self.assertIn(f"Alzheimer's (id {self.alz.pk}, Subject Team): +0 -7 ~5 (0 unchanged)", output)

	def test_a_new_area_adds_links_on_the_next_run(self):
		self.match()
		self.pd.ema_therapeutic_areas = ["Parkinson Disease", "Lewy Body Disease"]
		self.pd.save()
		self.match()
		# DaTSCAN is listed for Lewy Body Disease but is a diagnostic.
		self.assertEqual(set(self.subject_links(self.pd)), PARKINSONS_PRODUCTS)

	def test_clearing_the_areas_removes_the_links(self):
		self.match()
		self.alz.ema_therapeutic_areas = []
		self.alz.save()
		output = self.match()
		self.assertEqual(self.subject_links(self.alz), {})
		self.assertIn(f"Alzheimer's (id {self.alz.pk}, Subject Team): +0 -12 ~0 (0 unchanged)", output)
		# Other subjects are untouched.
		self.assertEqual(set(self.subject_links(self.pd)), PARKINSONS_PRODUCTS)

	def test_a_suppressed_link_is_never_recreated_changed_or_restored(self):
		self.match()
		link = self.subject_links(self.alz)["Leqembi"]
		link.suppressed = True
		link.matched_on = "edited by hand"
		link.save()
		history = MedicineSubjectLink.history.count()
		self.match()
		self.match()
		link.refresh_from_db()
		self.assertTrue(link.suppressed)
		self.assertEqual(link.matched_on, "edited by hand")
		self.assertEqual(MedicineSubjectLink.objects.filter(subject=self.alz, medicine=link.medicine).count(), 1)
		self.assertEqual(MedicineSubjectLink.history.count(), history)

	def test_a_suppressed_link_survives_a_change_that_stops_the_match(self):
		self.match()
		MedicineSubjectLink.objects.filter(subject=self.alz, medicine__name="Leqembi").update(suppressed=True)
		self.alz.ema_therapeutic_areas = ["Dementia"]
		self.alz.save()
		self.match()
		self.assertIn("Leqembi", self.subject_links(self.alz, suppressed=True))
		self.alz.ema_therapeutic_areas = []
		self.alz.save()
		self.match()
		self.assertEqual(set(self.subject_links(self.alz)), {"Leqembi"})

	def test_a_suppression_on_one_subject_leaves_the_other_alone(self):
		self.match()
		MedicineSubjectLink.objects.filter(subject=self.alz, medicine__name="Exelon").update(suppressed=True)
		self.match()
		self.assertFalse(self.subject_links(self.pd)["Exelon"].suppressed)

	def test_a_deleted_link_comes_back(self):
		# Editors can't delete a subject link, because the next run would recreate it.
		self.match()
		MedicineSubjectLink.objects.filter(subject=self.alz, medicine__name="Leqembi").delete()
		self.match()
		self.assertIn("Leqembi", self.subject_links(self.alz))

	def test_deleting_a_medicine_deletes_its_links(self):
		self.match()
		self.by_name["Leqembi"].delete()
		self.assertNotIn("Leqembi", self.subject_links(self.alz))

	def test_the_subject_line_names_the_subject_and_team(self):
		output = self.match()
		self.assertIn(
			f"Alzheimer's (id {self.alz.pk}, Subject Team): +12 -0 ~0 (0 unchanged)", output
		)
		self.assertIn("Subject links: 22 added, 0 removed, 0 relabelled, 0 unchanged, in 3 subjects.", output)


class DiagnosticCategoryTests(UntrackedTestCase):
	def category(self, name, **kwargs):
		category = TeamCategory.objects.create(team=self.team, category_name=name, **kwargs)
		category.subjects.add(self.alz)
		return category

	def test_an_automatic_category_never_links_a_diagnostic(self):
		florbetaben = self.category(
			"Florbetaben", category_terms=["florbetaben*"], modality=CategoryModality.SMALL_MOLECULE
		)
		self.match()
		self.assertFalse(MedicineCategoryAssignment.objects.filter(teamcategory=florbetaben).exists())

	def test_an_existing_automatic_link_to_a_diagnostic_is_removed(self):
		category = self.category("Imaging", category_terms=["x"], modality=CategoryModality.SMALL_MOLECULE)
		link = MedicineCategoryAssignment.objects.create(
			medicine=self.by_name["Neuraceq"], teamcategory=category, source=Source.AUTOMATIC
		)
		output = self.match()
		self.assertFalse(MedicineCategoryAssignment.objects.filter(pk=link.pk).exists())
		self.assertIn("+0 -1 ~0", output)

	def test_a_manual_link_to_a_diagnostic_is_kept(self):
		category = self.category("Imaging", category_terms=["x"], modality=CategoryModality.SMALL_MOLECULE)
		link = MedicineCategoryAssignment.objects.create(
			medicine=self.by_name["Neuraceq"], teamcategory=category, source=Source.MANUAL
		)
		self.match()
		self.assertTrue(MedicineCategoryAssignment.objects.filter(pk=link.pk).exists())

	def test_a_category_still_links_the_medicine_itself(self):
		lecanemab = self.category(
			"Lecanemab", category_terms=["lecanemab"], modality=CategoryModality.BIOLOGIC_ANTIBODY
		)
		self.match()
		link = MedicineCategoryAssignment.objects.get(teamcategory=lecanemab)
		self.assertEqual(link.medicine.name, "Leqembi")
		self.assertEqual(link.matched_on, "lecanemab · Alzheimer Disease")


class GroupPassTests(UntrackedTestCase):
	def test_copies_point_at_their_lead(self):
		self.match()
		self.assertEqual(
			self.grouped(),
			{
				"Prometax": "Exelon",
				"Rivastigmine Sandoz": "Exelon",
				"Rivastigmine 1 A Pharma": "Exelon",
				"Rivastigmine Hexal": "Exelon",
				"Axura": "Ebixa",
				"Extavia": "Betaferon",
			},
		)

	def test_different_medicines_and_unauthorised_products_stay_ungrouped(self):
		self.match()
		for name in ("Avonex", "Rebif", "Tecfidera", "Skilarence", "Memantine Merz", "Aduhelm", "Acrescent", "Leqembi"):
			self.assertIsNone(Medicine.objects.get(name=name).grouped_under_id, name)

	def test_the_output_reports_the_groups(self):
		output = self.match()
		self.assertIn("Groups: 6 products grouped under a lead, in 3 groups; 6 changed.", output)

	def test_a_second_run_writes_nothing(self):
		self.match()
		output = self.match()
		self.assertIn("Groups: 6 products grouped under a lead, in 3 groups; 0 changed.", output)

	def test_a_stale_group_is_cleared(self):
		memantine_merz = self.by_name["Memantine Merz"]
		memantine_merz.grouped_under = self.by_name["Ebixa"]
		memantine_merz.save()
		self.match()
		memantine_merz.refresh_from_db()
		self.assertIsNone(memantine_merz.grouped_under_id)

	def test_a_lead_never_points_at_anyone(self):
		exelon = self.by_name["Exelon"]
		exelon.grouped_under = self.by_name["Prometax"]
		exelon.save()
		self.match()
		exelon.refresh_from_db()
		self.assertIsNone(exelon.grouped_under_id)

	def test_a_copy_whose_indication_changes_leaves_its_group(self):
		self.match()
		axura = self.by_name["Axura"]
		axura.indication = "A different indication."
		axura.save()
		self.match()
		axura.refresh_from_db()
		self.assertIsNone(axura.grouped_under_id)

	def test_a_withdrawn_product_does_not_lead_over_an_authorised_one(self):
		# Exelon is the earliest and Authorised; once it is withdrawn, Prometax leads.
		exelon = self.by_name["Exelon"]
		exelon.status = "Withdrawn"
		exelon.save()
		self.match()
		self.assertEqual(self.grouped()["Exelon"], "Prometax")
		self.assertEqual(self.grouped()["Rivastigmine Sandoz"], "Prometax")
		self.assertNotIn("Prometax", self.grouped())

	def test_a_new_copy_joins_the_group(self):
		self.match()
		exelon = self.by_name["Exelon"]
		new = Medicine.objects.create(
			ema_product_number="EMEA/H/C/999999", name="Rivastigmine Test", status="Authorised",
			substances=list(exelon.substances), indication=exelon.indication.replace("Exelon", "Rivastigmine Test"),
			authorisation_date=exelon.authorisation_date.replace(year=2020),
		)
		self.match()
		new.refresh_from_db()
		self.assertEqual(new.grouped_under_id, exelon.pk)

	def test_deleting_the_lead_clears_the_copies(self):
		self.match()
		self.by_name["Ebixa"].delete()
		self.assertIsNone(Medicine.objects.get(name="Axura").grouped_under_id)


class PassOptionTests(UntrackedTestCase):
	def category(self):
		category = TeamCategory.objects.create(
			team=self.team, category_name="Lecanemab", category_terms=["lecanemab"],
			modality=CategoryModality.BIOLOGIC_ANTIBODY,
		)
		category.subjects.add(self.alz)
		return category

	def test_all_three_passes_run_without_options(self):
		category = self.category()
		output = self.match()
		self.assertEqual(MedicineCategoryAssignment.objects.filter(teamcategory=category).count(), 1)
		self.assertTrue(MedicineSubjectLink.objects.exists())
		self.assertTrue(Medicine.objects.exclude(grouped_under=None).exists())
		self.assertIn("Groups:", output)
		self.assertIn("Medicine links:", output)
		self.assertIn("Subject links:", output)

	def test_the_passes_run_in_order_groups_categories_subjects(self):
		self.category()
		output = self.match()
		self.assertLess(output.index("Groups:"), output.index("Medicine links:"))
		self.assertLess(output.index("Medicine links:"), output.index("Subject links:"))

	def test_subject_option_runs_only_that_subject(self):
		category = self.category()
		output = self.match(subject=self.pd.pk)
		self.assertEqual(set(self.subject_links(self.pd)), PARKINSONS_PRODUCTS)
		self.assertFalse(MedicineSubjectLink.objects.exclude(subject=self.pd).exists())
		self.assertFalse(MedicineCategoryAssignment.objects.filter(teamcategory=category).exists())
		self.assertFalse(Medicine.objects.exclude(grouped_under=None).exists())
		self.assertNotIn("Groups:", output)
		self.assertNotIn("Medicine links:", output)

	def test_category_option_runs_only_that_category(self):
		category = self.category()
		output = self.match(category=category.pk)
		self.assertEqual(MedicineCategoryAssignment.objects.filter(teamcategory=category).count(), 1)
		self.assertFalse(MedicineSubjectLink.objects.exists())
		self.assertFalse(Medicine.objects.exclude(grouped_under=None).exists())
		self.assertNotIn("Groups:", output)
		self.assertNotIn("Subject links:", output)

	def test_category_and_subject_together_is_an_error(self):
		category = self.category()
		with self.assertRaisesMessage(CommandError, "not both"):
			self.match(category=category.pk, subject=self.alz.pk)

	def test_unknown_subject_is_an_error(self):
		with self.assertRaises(CommandError):
			self.match(subject=999999)

	def test_subject_option_works_on_a_subject_without_areas(self):
		output = self.match(subject=self.research.pk)
		self.assertIn("Subject links: 0 added, 0 removed, 0 relabelled, 0 unchanged, in 0 subjects.", output)

	def test_dry_run_writes_nothing_in_any_pass(self):
		category = self.category()
		# Seed something every pass would change: a stale group, a stale subject link.
		stale_copy = self.by_name["Memantine Merz"]
		stale_copy.grouped_under = self.by_name["Ebixa"]
		stale_copy.save()
		stale_link = MedicineSubjectLink.objects.create(medicine=self.by_name["Avonex"], subject=self.alz)
		output = self.match(dry_run=True)
		self.assertFalse(MedicineCategoryAssignment.objects.filter(teamcategory=category).exists())
		self.assertEqual(MedicineSubjectLink.objects.count(), 1)
		self.assertTrue(MedicineSubjectLink.objects.filter(pk=stale_link.pk).exists())
		stale_copy.refresh_from_db()
		self.assertEqual(stale_copy.grouped_under_id, self.by_name["Ebixa"].pk)
		self.assertEqual(Medicine.objects.exclude(grouped_under=None).count(), 1)
		self.assertIn("[dry run] Groups: 6 products grouped under a lead, in 3 groups; 7 changed.", output)
		self.assertIn("[dry run] Subject links: 22 added, 1 removed", output)
		self.assertIn("[dry run] Medicine links: 1 added", output)
		self.assertIn("Dry run: nothing was written.", output)

	def test_dry_run_with_subject_writes_nothing(self):
		self.match(subject=self.alz.pk, dry_run=True)
		self.assertFalse(MedicineSubjectLink.objects.exists())
