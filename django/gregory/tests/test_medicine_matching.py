"""Tests for gregory.services.medicine_matching, the rule that links EMA medicines to
categories.

The medicines are the real rows in fixtures/ema/medicines-base.json (cut from EMA's
export of 2026-10-25), parsed with the production parser. The categories are built here.
Nothing touches EMA.
"""

import json
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from organizations.models import Organization

from gregory.models import CategoryModality, Medicine, Subject, Team, TeamCategory
from gregory.services.medicine_matching import (
	category_areas,
	category_patterns,
	product_matches,
	substance_matches,
)
from gregory.utils.ema import is_human, parse_row

FIXTURE = Path(__file__).parent / "fixtures" / "ema" / "medicines-base.json"
MS = ["Multiple Sclerosis"]


def medicines():
	"""The human fixture rows as unsaved Medicine objects, by name."""
	with open(FIXTURE) as handle:
		rows = json.load(handle)["data"]
	return {
		row["name_of_medicine"]: Medicine(**parse_row(row).model_values())
		for row in rows
		if is_human(row)
	}


MEDICINES = medicines()


def category(terms=(), modality=CategoryModality.SMALL_MOLECULE, **kwargs):
	return TeamCategory(category_name="Test", category_terms=list(terms), modality=modality, **kwargs)


def matches(cat, areas, *names):
	"""The matched_on label for each named medicine, or None."""
	return {name: product_matches(cat, MEDICINES[name], areas) for name in names}


class SubstanceMatchesTests(SimpleTestCase):
	def test_exact(self):
		self.assertTrue(substance_matches("ocrelizumab", "ocrelizumab"))

	def test_is_case_and_whitespace_insensitive(self):
		self.assertTrue(substance_matches("  Ocrelizumab ", "ocrelizumab"))
		self.assertTrue(substance_matches("insulin  glargine", "Insulin glargine"))

	def test_the_term_plus_one_word_matches(self):
		self.assertTrue(substance_matches("pixantrone", "pixantrone dimaleate"))

	def test_the_term_plus_two_words_does_not(self):
		self.assertFalse(substance_matches("pixantrone", "pixantrone dimaleate monohydrate"))

	def test_the_term_must_start_on_a_word(self):
		self.assertFalse(substance_matches("zumab", "ocrelizumab"))
		self.assertFalse(substance_matches("ocrelizumab", "ocrelizumabs"))

	def test_a_wildcard_matches_the_whole_substance(self):
		self.assertTrue(substance_matches("*cabtagene*", "axicabtagene ciloleucel"))
		self.assertTrue(substance_matches("*brutinib", "tolebrutinib"))

	def test_a_wildcard_does_not_match_part_of_a_substance(self):
		# *brutinib has to end the whole name: no extra salt word.
		self.assertFalse(substance_matches("*brutinib", "tolebrutinib maleate"))
		self.assertFalse(substance_matches("cabtagene*", "axicabtagene ciloleucel"))

	def test_regex_characters_in_a_pattern_are_literal(self):
		self.assertFalse(substance_matches("a.c*", "abc"))
		self.assertTrue(substance_matches("a.c*", "a.c d"))

	def test_blank_never_matches(self):
		self.assertFalse(substance_matches("", "ocrelizumab"))
		self.assertFalse(substance_matches("ocrelizumab", ""))
		self.assertFalse(substance_matches("*", ""))


class CategoryPatternsTests(SimpleTestCase):
	def test_medicine_terms_win_over_category_terms(self):
		cat = category(["car-t"], modality=CategoryModality.RESEARCH_TOPIC, medicine_terms=["*cabtagene*"])
		self.assertEqual(category_patterns(cat), ["*cabtagene*"])

	def test_intervention_modalities_use_category_terms(self):
		for modality in (
			CategoryModality.SMALL_MOLECULE,
			CategoryModality.BIOLOGIC_ANTIBODY,
			CategoryModality.CELL_GENE_THERAPY,
			CategoryModality.NATURAL_PRODUCT,
		):
			with self.subTest(modality=str(modality)):
				self.assertEqual(category_patterns(category(["Ocrelizumab"], modality)), ["ocrelizumab"])

	def test_other_modalities_get_nothing(self):
		for modality in (
			CategoryModality.RESEARCH_TOPIC,
			CategoryModality.REHABILITATION,
			CategoryModality.DEVICE_NEUROMODULATION,
			CategoryModality.OTHER,
			None,
		):
			with self.subTest(modality=str(modality)):
				self.assertEqual(category_patterns(category(["ocrelizumab"], modality)), [])

	def test_duplicates_and_blanks_are_dropped(self):
		self.assertEqual(category_patterns(category(["B", " ", "b", "a"])), ["b", "a"])


class FixtureRowTests(SimpleTestCase):
	def test_ocrelizumab_links_ocrevus_with_the_area_it_matched(self):
		cat = category(["ocrelizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		result = matches(cat, MS, "Ocrevus", "Kesimpta", "Viagra")
		self.assertEqual(result["Ocrevus"], "ocrelizumab · Multiple Sclerosis")
		self.assertIsNone(result["Kesimpta"])
		self.assertIsNone(result["Viagra"])

	def test_the_label_is_just_the_pattern_when_the_disease_check_is_skipped(self):
		cat = category(["ocrelizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		self.assertEqual(product_matches(cat, MEDICINES["Ocrevus"], []), "ocrelizumab")

	def test_natalizumab_links_tysabri_and_tyruko_but_not_the_crohn_refusal(self):
		cat = category(["natalizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		result = matches(cat, MS, "Tysabri", "Tyruko", "Natalizumab Elan Pharma")
		self.assertEqual(result["Tysabri"], "natalizumab · Multiple Sclerosis")
		self.assertEqual(result["Tyruko"], "natalizumab · Multiple Sclerosis")
		self.assertIsNone(result["Natalizumab Elan Pharma"])

	def test_the_refusal_links_when_the_subject_has_no_ema_areas(self):
		cat = category(["natalizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		self.assertEqual(
			product_matches(cat, MEDICINES["Natalizumab Elan Pharma"], []), "natalizumab"
		)

	def test_sildenafil_on_ms_drops_viagra(self):
		cat = category(["sildenafil"])
		self.assertIsNone(product_matches(cat, MEDICINES["Viagra"], MS))
		self.assertEqual(product_matches(cat, MEDICINES["Viagra"], []), "sildenafil")

	def test_area_matching_is_a_case_insensitive_prefix(self):
		cat = category(["ofatumumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		# Kesimpta's area is "Multiple Sclerosis, Relapsing-Remitting".
		self.assertIsNotNone(product_matches(cat, MEDICINES["Kesimpta"], ["multiple sclerosis"]))
		self.assertIsNone(product_matches(cat, MEDICINES["Arzerra"], ["multiple sclerosis"]))

	def test_an_area_is_not_matched_in_the_middle(self):
		# ALS is "Amyotrophic Lateral Sclerosis": "Sclerosis" alone must not catch it.
		cat = category(["masitinib"])
		self.assertIsNone(product_matches(cat, MEDICINES["Masitinib AB Science"], ["Sclerosis"]))
		self.assertIsNotNone(
			product_matches(cat, MEDICINES["Masitinib AB Science"], ["Amyotrophic Lateral Sclerosis"])
		)

	def test_the_first_area_that_matches_is_named(self):
		cat = category(["ocrelizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		label = product_matches(cat, MEDICINES["Ocrevus"], ["Parkinson Disease", "multiple sclerosis"])
		self.assertEqual(label, "ocrelizumab · multiple sclerosis")

	def test_gilenya_matches_on_its_inn(self):
		cat = category(["fingolimod"])
		self.assertEqual(product_matches(cat, MEDICINES["Gilenya"], MS), "fingolimod · Multiple Sclerosis")

	def test_combinations_are_skipped_unless_the_category_opts_in(self):
		off = category(["metformin"])
		on = category(["metformin"], medicine_include_combinations=True)
		self.assertIsNone(product_matches(off, MEDICINES["Synjardy"], []))
		self.assertEqual(product_matches(on, MEDICINES["Synjardy"], []), "metformin")

	def test_a_combination_still_faces_the_disease_check(self):
		on = category(["metformin"], medicine_include_combinations=True)
		self.assertIsNone(product_matches(on, MEDICINES["Synjardy"], MS))
		self.assertEqual(
			product_matches(on, MEDICINES["Synjardy"], ["Diabetes Mellitus"]),
			"metformin · Diabetes Mellitus",
		)

	def test_car_t_gets_nothing_on_an_ms_subject(self):
		cat = category(
			["car-t"], CategoryModality.RESEARCH_TOPIC, medicine_terms=["*cabtagene*", "tisagenlecleucel"]
		)
		result = matches(cat, MS, "Yescarta", "Kymriah", "Ebvallo")
		self.assertEqual(result, {"Yescarta": None, "Kymriah": None, "Ebvallo": None})

	def test_car_t_on_an_unmapped_subject_gets_the_car_t_products_only(self):
		cat = category(
			["car-t"], CategoryModality.RESEARCH_TOPIC, medicine_terms=["*cabtagene*", "tisagenlecleucel"]
		)
		result = matches(cat, [], "Yescarta", "Kymriah", "Ebvallo")
		self.assertEqual(result["Yescarta"], "*cabtagene*")
		self.assertEqual(result["Kymriah"], "tisagenlecleucel")
		self.assertIsNone(result["Ebvallo"])

	def test_a_leucel_suffix_would_wrongly_catch_ebvallo(self):
		# Why the CAR-T pattern is not *leucel.
		cat = category([], CategoryModality.RESEARCH_TOPIC, medicine_terms=["*leucel"])
		self.assertIsNotNone(product_matches(cat, MEDICINES["Ebvallo"], []))

	def test_a_research_topic_gets_nothing_from_its_ordinary_terms(self):
		cat = category(["ocrelizumab", "natalizumab"], CategoryModality.RESEARCH_TOPIC)
		self.assertEqual(
			matches(cat, [], "Ocrevus", "Tysabri"), {"Ocrevus": None, "Tysabri": None}
		)

	def test_a_btk_wildcard_on_ms_links_only_cenrifki(self):
		cat = category(["btk inhibitors"], CategoryModality.RESEARCH_TOPIC, medicine_terms=["*brutinib"])
		linked = {name for name in MEDICINES if product_matches(cat, MEDICINES[name], MS)}
		self.assertEqual(linked, {"Cenrifki"})

	def test_a_pattern_matches_the_name_exactly_and_not_by_prefix(self):
		cat = category([], CategoryModality.RESEARCH_TOPIC, medicine_terms=["kesimpta"])
		self.assertEqual(product_matches(cat, MEDICINES["Kesimpta"], MS), "kesimpta · Multiple Sclerosis")
		partial = category([], CategoryModality.RESEARCH_TOPIC, medicine_terms=["kesim"])
		self.assertIsNone(product_matches(partial, MEDICINES["Kesimpta"], MS))

	def test_a_name_match_does_not_get_the_extra_word_allowance(self):
		cat = category([], CategoryModality.RESEARCH_TOPIC, medicine_terms=["natalizumab elan"])
		self.assertIsNone(product_matches(cat, MEDICINES["Natalizumab Elan Pharma"], []))

	def test_a_product_with_no_inn_matches_through_its_active_substance(self):
		cat = category(["hydrocortisone"])
		self.assertEqual(product_matches(cat, MEDICINES["Hydrocortisone Aguettant"], []), "hydrocortisone")

	def test_exclude_terms_rule_a_product_out_by_substance(self):
		cat = category(["natalizumab"], category_exclude_terms=["natalizumab"])
		self.assertIsNone(product_matches(cat, MEDICINES["Tysabri"], []))

	def test_exclude_terms_rule_a_product_out_by_name(self):
		cat = category(["natalizumab"], category_exclude_terms=["Tyruko"])
		result = matches(cat, [], "Tysabri", "Tyruko")
		self.assertIsNotNone(result["Tysabri"])
		self.assertIsNone(result["Tyruko"])

	def test_exclude_terms_are_whole_words(self):
		cat = category(["natalizumab"], category_exclude_terms=["natal"])
		self.assertIsNotNone(product_matches(cat, MEDICINES["Tysabri"], []))

	def test_exclude_terms_accept_a_wildcard(self):
		cat = category(["natalizumab"], category_exclude_terms=["*lizumab"])
		self.assertIsNone(product_matches(cat, MEDICINES["Tysabri"], []))

	def test_no_patterns_means_no_match(self):
		self.assertIsNone(product_matches(category([]), MEDICINES["Ocrevus"], []))


class CategoryAreasTests(TestCase):
	def test_it_is_the_union_over_the_subjects_without_duplicates(self):
		team = Team.objects.create(
			organization=Organization.objects.create(name="Areas Org", slug="areas-org"),
			name="Areas Team",
			slug="areas-team",
		)
		first = Subject.objects.create(
			subject_name="A", subject_slug="a", team=team, ema_therapeutic_areas=["Alzheimer Disease", "Dementia"]
		)
		second = Subject.objects.create(
			subject_name="B", subject_slug="b", team=team, ema_therapeutic_areas=["dementia", "Parkinson Disease"]
		)
		unmapped = Subject.objects.create(subject_name="C", subject_slug="c", team=team)
		cat = TeamCategory.objects.create(team=team, category_name="X", category_terms=["x"])
		cat.subjects.add(first, second, unmapped)
		self.assertEqual(
			category_areas(cat), ["Alzheimer Disease", "Dementia", "Parkinson Disease"]
		)

	def test_a_category_with_only_unmapped_subjects_has_no_areas(self):
		team = Team.objects.create(
			organization=Organization.objects.create(name="Areas Org 2", slug="areas-org-2"),
			name="Areas Team 2",
			slug="areas-team-2",
		)
		subject = Subject.objects.create(subject_name="C", subject_slug="c2", team=team)
		cat = TeamCategory.objects.create(team=team, category_name="Y", category_terms=["y"])
		cat.subjects.add(subject)
		self.assertEqual(category_areas(cat), [])
