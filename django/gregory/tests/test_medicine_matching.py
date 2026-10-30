"""Tests for gregory.services.medicine_matching, the rule that links EMA medicines to
categories.

The medicines are the real rows in fixtures/ema/medicines-base.json (cut from EMA's
export of 2026-10-25), parsed with the production parser. The categories are built here.
Nothing touches EMA.
"""

import json
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.test import SimpleTestCase, TestCase
from organizations.models import Organization

from gregory.models import CategoryModality, Medicine, Subject, Team, TeamCategory
from gregory.services.medicine_linking import group_medicines
from gregory.services.medicine_matching import (
	MATCHED_ON_MAX,
	category_areas,
	category_patterns,
	group_key,
	group_lead,
	is_diagnostic,
	product_matches,
	subject_match,
	substance_matches,
)
from gregory.utils.ema import is_human, parse_row
from gregory.utils.fda import parse_application

FIXTURE = Path(__file__).parent / "fixtures" / "ema" / "medicines-base.json"
FDA_FIXTURE = Path(__file__).parent / "fixtures" / "fda" / "drugsfda-base.json"
UNTRACKED_FIXTURE = Path(__file__).parent / "fixtures" / "ema" / "medicines-untracked.json"
MS = ["Multiple Sclerosis"]
ALZHEIMERS = ["Alzheimer Disease", "Dementia"]


def medicines(fixture=FIXTURE):
	"""The human fixture rows as unsaved Medicine objects, by name."""
	with open(fixture) as handle:
		rows = json.load(handle)["data"]
	return {
		row["name_of_medicine"]: Medicine(**parse_row(row).model_values())
		for row in rows
		if is_human(row)
	}


MEDICINES = medicines()
# Real rows for the subject and group rules (Alzheimer's, Parkinson's, interferons).
UNTRACKED = medicines(UNTRACKED_FIXTURE)


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


class DiagnosticTests(SimpleTestCase):
	def test_v04_v08_and_v09_are_diagnostics(self):
		for code in ("V04CX01", "V08AB02", "V09AX06", "v09ab03"):
			self.assertTrue(is_diagnostic(SimpleNamespace(atc_code=code)), code)

	def test_other_atc_groups_and_no_code_are_not(self):
		for code in ("N06DX04", "L03AB07", "V03AB01", "V10XX01", "", None):
			self.assertFalse(is_diagnostic(SimpleNamespace(atc_code=code)), code)

	def test_the_real_imaging_agents_are_diagnostics(self):
		self.assertTrue(is_diagnostic(UNTRACKED["Neuraceq"]))
		self.assertTrue(is_diagnostic(UNTRACKED["DaTSCAN"]))
		self.assertFalse(is_diagnostic(UNTRACKED["Leqembi"]))

	def test_a_category_never_matches_a_diagnostic(self):
		cat = category(medicine_terms=["florbetaben*"])
		neuraceq = UNTRACKED["Neuraceq"]
		self.assertIsNone(product_matches(cat, neuraceq, ALZHEIMERS))
		# The same product with no ATC code yet: the pattern and the disease check match,
		# so it is the diagnostic rule that kept it out.
		neuraceq.atc_code = ""
		try:
			self.assertEqual(product_matches(cat, neuraceq, ALZHEIMERS), "florbetaben* · Alzheimer Disease")
		finally:
			neuraceq.atc_code = "V09AX06"


class SubjectMatchTests(SimpleTestCase):
	def test_a_product_approved_for_the_area_matches_with_that_area(self):
		self.assertEqual(subject_match(ALZHEIMERS, UNTRACKED["Leqembi"]), "Alzheimer Disease")

	def test_the_first_listed_area_that_matches_wins(self):
		# Exelon is listed for Dementia, Alzheimer Disease and Parkinson Disease.
		self.assertEqual(subject_match(["Dementia", "Alzheimer Disease"], UNTRACKED["Exelon"]), "Dementia")
		self.assertEqual(subject_match(["Alzheimer Disease", "Dementia"], UNTRACKED["Exelon"]), "Alzheimer Disease")

	def test_the_area_is_a_case_insensitive_prefix(self):
		self.assertEqual(subject_match(["parkinson"], UNTRACKED["Exelon"]), "parkinson")
		self.assertEqual(subject_match(MS, UNTRACKED["Rebif"]), "Multiple Sclerosis")

	def test_a_product_for_another_disease_does_not_match(self):
		self.assertIsNone(subject_match(MS, UNTRACKED["Leqembi"]))
		# Skilarence is dimethyl fumarate for psoriasis, not MS.
		self.assertIsNone(subject_match(MS, UNTRACKED["Skilarence"]))

	def test_a_diagnostic_never_matches(self):
		self.assertIsNone(subject_match(ALZHEIMERS, UNTRACKED["Neuraceq"]))
		self.assertIsNone(subject_match(["Parkinson Disease"], UNTRACKED["DaTSCAN"]))

	def test_no_areas_matches_nothing(self):
		self.assertIsNone(subject_match([], UNTRACKED["Leqembi"]))
		self.assertIsNone(subject_match(["", "  "], UNTRACKED["Leqembi"]))

	def test_refused_and_withdrawn_applications_still_match(self):
		# The read side hides generics and copies; the link is made anyway.
		self.assertEqual(subject_match(ALZHEIMERS, UNTRACKED["Aduhelm"]), "Alzheimer Disease")
		self.assertEqual(subject_match(ALZHEIMERS, UNTRACKED["Acrescent"]), "Alzheimer Disease")

	def test_the_label_fits_matched_on(self):
		product = SimpleNamespace(regulator="ema", atc_code="", therapeutic_areas=["x" * 300])
		self.assertEqual(len(subject_match(["x" * 300], product)), MATCHED_ON_MAX)


def product(name, indication, substances=("memantine",), **kwargs):
	values = dict(
		regulator="ema",
		name=name,
		indication=indication,
		substances=list(substances),
		generic=False,
		biosimilar=False,
		authorisation_date=date(2002, 5, 17),
		status="Authorised",
		ema_product_number="EMEA/H/C/000001",
	)
	values.update(kwargs)
	return SimpleNamespace(**values)


class GroupKeyTests(SimpleTestCase):
	def test_the_rivastigmine_copies_share_a_key(self):
		names = ("Exelon", "Prometax", "Rivastigmine Sandoz", "Rivastigmine 1 A Pharma", "Rivastigmine Hexal")
		keys = {group_key(UNTRACKED[name]) for name in names}
		self.assertEqual(len(keys), 1)
		self.assertNotIn(None, keys)

	def test_memantine_originator_and_copy_share_a_key(self):
		self.assertEqual(group_key(UNTRACKED["Ebixa"]), group_key(UNTRACKED["Axura"]))

	def test_a_salt_written_substance_stays_apart(self):
		self.assertNotEqual(group_key(UNTRACKED["Ebixa"]), group_key(UNTRACKED["Memantine Merz"]))

	def test_two_interferon_originators_stay_apart(self):
		# Same substance, two companies, different indication texts.
		self.assertEqual(UNTRACKED["Avonex"].substances, UNTRACKED["Rebif"].substances)
		self.assertNotEqual(group_key(UNTRACKED["Avonex"]), group_key(UNTRACKED["Rebif"]))

	def test_one_substance_for_two_diseases_stays_apart(self):
		self.assertEqual(UNTRACKED["Tecfidera"].substances, UNTRACKED["Skilarence"].substances)
		self.assertNotEqual(group_key(UNTRACKED["Tecfidera"]), group_key(UNTRACKED["Skilarence"]))

	def test_interferon_beta_1b_copies_share_a_key(self):
		self.assertEqual(group_key(UNTRACKED["Betaferon"]), group_key(UNTRACKED["Extavia"]))

	def test_a_product_never_authorised_is_not_grouped(self):
		self.assertIsNone(group_key(UNTRACKED["Aduhelm"]))
		self.assertIsNone(group_key(UNTRACKED["Acrescent"]))

	def test_generics_and_biosimilars_are_not_grouped(self):
		self.assertIsNone(group_key(product("Zorvex", "Treats X.", generic=True)))
		self.assertIsNone(group_key(product("Zorvex", "Treats X.", biosimilar=True)))

	def test_an_empty_indication_is_never_grouped(self):
		self.assertIsNone(group_key(product("Zorvex", "")))
		# Nothing but the product's own name and punctuation is empty too.
		self.assertIsNone(group_key(product("Axura", "Axura.")))

	def test_the_products_own_name_is_removed_from_the_indication(self):
		first = product("Exelon", "Exelon is indicated for mild dementia.")
		second = product("Prometax", "Prometax is indicated for mild dementia.")
		self.assertEqual(group_key(first), group_key(second))

	def test_punctuation_case_and_spacing_do_not_separate_products(self):
		first = product("Zorvex", "For  adults:  contra-indication, beta-blocker.")
		second = product("Qinta", "for adults contra indication beta blocker")
		self.assertEqual(group_key(first), group_key(second))

	def test_different_substances_do_not_group_whatever_the_text(self):
		self.assertNotEqual(
			group_key(product("Zorvex", "Treats X.", substances=["memantine"])),
			group_key(product("Qinta", "Treats X.", substances=["memantine hydrochloride"])),
		)

	def test_the_order_of_substances_does_not_matter(self):
		self.assertEqual(
			group_key(product("Zorvex", "Treats X.", substances=["a", "b"])),
			group_key(product("Qinta", "Treats X.", substances=["b", "a"])),
		)

	def test_substances_are_compared_lowercased(self):
		self.assertEqual(
			group_key(product("Zorvex", "Treats X.", substances=["Memantine"])),
			group_key(product("Qinta", "Treats X.", substances=["memantine"])),
		)


class GroupLeadTests(SimpleTestCase):
	def test_an_authorised_product_leads_over_an_earlier_withdrawn_one(self):
		withdrawn = product("Old", "x", status="Withdrawn", authorisation_date=date(1998, 1, 1))
		authorised = product("New", "x", status="Authorised", authorisation_date=date(2010, 1, 1))
		self.assertIs(group_lead([withdrawn, authorised]), authorised)
		self.assertIs(group_lead([authorised, withdrawn]), authorised)

	def test_the_earliest_authorisation_leads_among_equals(self):
		later = product("Later", "x", authorisation_date=date(2012, 1, 1))
		earlier = product("Earlier", "x", authorisation_date=date(2002, 1, 1))
		self.assertIs(group_lead([later, earlier]), earlier)

	def test_the_lowest_product_number_breaks_a_tie(self):
		high = product("High", "x", ema_product_number="EMEA/H/C/000255")
		low = product("Low", "x", ema_product_number="EMEA/H/C/000169")
		self.assertIs(group_lead([high, low]), low)

	def test_only_the_exact_word_authorised_counts_as_authorised(self):
		odd = product("Odd", "x", status="authorised", authorisation_date=date(1990, 1, 1))
		real = product("Real", "x", status="Authorised", authorisation_date=date(2010, 1, 1))
		self.assertIs(group_lead([odd, real]), real)

	def test_the_real_rivastigmine_group_is_led_by_exelon(self):
		names = ("Prometax", "Rivastigmine Sandoz", "Rivastigmine 1 A Pharma", "Rivastigmine Hexal", "Exelon")
		self.assertEqual(group_lead([UNTRACKED[name] for name in names]).name, "Exelon")

	def test_the_real_memantine_group_is_led_by_ebixa(self):
		self.assertEqual(group_lead([UNTRACKED["Axura"], UNTRACKED["Ebixa"]]).name, "Ebixa")

	def test_a_withdrawn_betaferon_copy_does_not_lead(self):
		self.assertEqual(UNTRACKED["Extavia"].status, "Withdrawn")
		self.assertEqual(group_lead([UNTRACKED["Extavia"], UNTRACKED["Betaferon"]]).name, "Betaferon")


def fda_medicines():
	"""The real Drugs@FDA fixture applications as unsaved Medicine objects, by brand name."""
	with open(FDA_FIXTURE) as handle:
		records = json.load(handle)["results"]
	parsed = [parse_application(record) for record in records]
	return {p.brand_names[0]: Medicine(**p.model_values()) for p in parsed if p is not None}


class FdaRowsAreNotMatchedYetTests(SimpleTestCase):
	"""An FDA row has no EMA areas, so the disease check cannot run on it and a category with
	no areas would link it unchecked. Until the FDA rules exist, no rule matches one."""

	def setUp(self):
		self.fda = fda_medicines()

	def test_a_category_with_no_areas_does_not_link_an_fda_row(self):
		cat = category(["ocrelizumab", "natalizumab", "kesimpta"], modality=CategoryModality.BIOLOGIC_ANTIBODY)
		for name in ("OCREVUS", "TYRUKO", "KESIMPTA"):
			with self.subTest(name=name):
				self.assertIsNone(product_matches(cat, self.fda[name], []))
		# The EMA row of the same substance does link, so the guard is the only difference.
		self.assertIsNotNone(product_matches(cat, MEDICINES["Ocrevus"], []))

	def test_a_category_with_areas_does_not_link_an_fda_row_either(self):
		cat = category(["ocrelizumab"], modality=CategoryModality.BIOLOGIC_ANTIBODY)
		self.assertIsNone(product_matches(cat, self.fda["OCREVUS"], MS))

	def test_an_fda_row_never_gets_a_subject_link(self):
		ocrevus = self.fda["OCREVUS"]
		ocrevus.therapeutic_areas = ["Multiple Sclerosis"]
		self.assertIsNone(subject_match(MS, ocrevus))

	def test_an_fda_row_is_never_grouped(self):
		ocrevus = self.fda["OCREVUS"]
		ocrevus.indication = "Ocrevus is indicated for relapsing multiple sclerosis."
		self.assertIsNotNone(ocrevus.authorisation_date)
		self.assertIsNone(group_key(ocrevus))

	def test_an_fda_row_without_an_ema_number_does_not_break_the_lead_sort(self):
		fda_row = product("Fda", "x", regulator="fda", ema_product_number=None)
		ema_row = product("Ema", "x")
		# Nothing groups an FDA row, but the sort must not compare None with a string.
		self.assertIn(group_lead([fda_row, ema_row]), (fda_row, ema_row))


# LOCAL ONLY. The full EMA export of 2026-10-25 lives in the gitignored specs/ folder, so
# CI skips this test. Point EMA_FULL_EXPORT at another copy to run it elsewhere.
FULL_EXPORT = Path(
	os.environ.get(
		"EMA_FULL_EXPORT",
		"/Users/brunoamaral/Labs/gregory/specs/ema-medicines-refs/medicines-export-2026-10-25.json",
	)
)


@skipUnless(FULL_EXPORT.is_file(), f"local-only: the full EMA export is not at {FULL_EXPORT}")
class FullExportGroupingTests(SimpleTestCase):
	"""The grouping rule on every real row of the 2026-10-25 export: 106 products fold into
	78 groups. A count far from that means the key is wrong."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		with open(FULL_EXPORT) as handle:
			rows = [row for row in json.load(handle)["data"] if is_human(row)]
		cls.medicines = [
			Medicine(pk=index, **parse_row(row).model_values()) for index, row in enumerate(rows, start=1)
		]
		by_key = defaultdict(list)
		for medicine in cls.medicines:
			key = group_key(medicine)
			if key is not None:
				by_key[key].append(medicine)
		cls.groups = [products for products in by_key.values() if len(products) > 1]
		cls.lead_of = {}
		for products in cls.groups:
			lead = group_lead(products)
			for product_ in products:
				cls.lead_of[product_.name] = lead.name

	def test_106_products_fold_into_78_groups(self):
		counts = group_medicines(self.medicines, dry_run=True)
		self.assertEqual(counts["groups"], 78)
		self.assertEqual(counts["grouped"], 106)
		self.assertEqual(counts["changed"], 106)

	def test_the_groups_in_our_subjects(self):
		lead = self.lead_of
		for name in ("Prometax", "Rivastigmine Sandoz", "Rivastigmine 1 A Pharma", "Rivastigmine Hexal"):
			self.assertEqual(lead[name], "Exelon", name)
		self.assertEqual(lead["Axura"], "Ebixa")
		self.assertEqual(lead["Entacapone Orion"], "Comtan")
		self.assertEqual(lead["Rasagiline ratiopharm"], "Azilect")
		self.assertEqual(lead["Levodopa/Carbidopa/Entacapone Orion"], "Stalevo")
		self.assertEqual(lead["Extavia"], "Betaferon")

	def test_what_stays_apart(self):
		grouped = set(self.lead_of)
		for name in ("Avonex", "Rebif", "Tecfidera", "Skilarence", "Memantine Merz", "Sifrol", "Mirapexin"):
			self.assertNotIn(name, grouped, name)
		# Leads are in a group but lead themselves.
		self.assertEqual(self.lead_of["Exelon"], "Exelon")
