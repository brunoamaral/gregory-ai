"""Tests for gregory.services.medicine_matching, the rule that links EMA and FDA medicines
to categories.

The EMA medicines are the real rows in fixtures/ema/medicines-base.json (cut from EMA's
export of 2026-10-25), parsed with the production parser. The FDA medicines are the real
applications in fixtures/fda/drugsfda-base.json and the CBER rows of purplebook-base.csv,
with the indications of the trimmed DailyMed labels in fixtures/fda/dailymed/ (a few
labels are written here instead, marked as such). The categories are built here. Nothing
touches EMA, the FDA or DailyMed.
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
	category_fda_terms,
	category_patterns,
	group_key,
	group_lead,
	is_diagnostic,
	pattern_match,
	product_matches,
	subject_match,
	substance_matches,
)
from gregory.utils.ema import is_human, parse_row
from gregory.utils.dailymed import parse_indications
from gregory.utils.fda import parse_application, parse_purplebook

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


DAILYMED = Path(__file__).parent / "fixtures" / "fda" / "dailymed"
PURPLEBOOK_FIXTURE = Path(__file__).parent / "fixtures" / "fda" / "purplebook-base.csv"


def fda_medicines():
	"""The real Drugs@FDA fixture applications as unsaved Medicine objects, by brand name.
	The Purple Book's CBER rows are added by their brand name too (Kymriah, Yescarta, ...)."""
	with open(FDA_FIXTURE) as handle:
		records = json.load(handle)["results"]
	parsed = [parse_application(record) for record in records]
	cber, _ = parse_purplebook(PURPLEBOOK_FIXTURE.read_text())
	return {p.brand_names[0]: Medicine(**p.model_values()) for p in parsed + cber if p is not None}


def label(setid, title, indication, published="2026-01-01", version="1"):
	return {
		"setid": setid,
		"version": version,
		"title": title,
		"published": published,
		"indication": indication,
	}


def real_indication(name):
	"""The indications text of a trimmed DailyMed label, read with the production parser."""
	return parse_indications((DAILYMED / f"{name}.xml").read_bytes())


# Indications of real labels (fetched from DailyMed on 2026-10-30 and trimmed), read below
# with the production parser.
OCREVUS_TEXT = real_indication("9da42362-3bb5-4b83-b4bb-b59fd4e55f0d")
KESIMPTA_TEXT = real_indication("6a8a3f53-2062-48ff-9dbe-b939df133ca3")
VIAGRA_TEXT = real_indication("0b0be196-0c62-461c-94f4-9a35339b4501")
# Written for these tests, short and not copied from a label: only the disease words matter.
ARZERRA_TEXT = "ARZERRA is indicated for chronic lymphocytic leukemia."
TYSABRI_TEXT = "TYSABRI is indicated for relapsing forms of multiple sclerosis and Crohn's disease."
TYRUKO_TEXT = "TYRUKO is indicated for relapsing forms of multiple sclerosis (MS)."
MAVENCLAD_TEXT = "MAVENCLAD is indicated for relapsing forms of multiple sclerosis (MS)."
LEUSTATIN_TEXT = "Leustatin is indicated for hairy cell leukemia."


def with_labels(medicine, *entries):
	medicine.label_indications = list(entries)
	if entries:
		medicine.indication = entries[0]["indication"]
	return medicine


def fda_ms_medicines():
	"""The fixture medicines with the labels DailyMed holds for them (or, where a test
	stands in for a label, the text written above)."""
	fda = fda_medicines()
	with_labels(fda["OCREVUS"], label("ocrevus", "OCREVUS (OCRELIZUMAB) INJECTION", OCREVUS_TEXT))
	# Zunovo has its own application and its own label, which names MS: only its substance,
	# "ocrelizumab and hyaluronidase", keeps it out of an ocrelizumab category.
	with_labels(fda["OCREVUS ZUNOVO"], label("zunovo", "OCREVUS ZUNOVO INJECTION", OCREVUS_TEXT))
	with_labels(
		fda["KESIMPTA"],
		label("kesimpta", "KESIMPTA (OFATUMUMAB) INJECTION", KESIMPTA_TEXT),
		label("arzerra", "ARZERRA (OFATUMUMAB) INJECTION", ARZERRA_TEXT),
	)
	with_labels(fda["TYSABRI"], label("tysabri", "TYSABRI (NATALIZUMAB) INJECTION", TYSABRI_TEXT))
	with_labels(fda["TYRUKO"], label("tyruko", "TYRUKO (NATALIZUMAB-SZTN) INJECTION", TYRUKO_TEXT))
	with_labels(fda["MAVENCLAD"], label("mavenclad", "MAVENCLAD (CLADRIBINE) TABLET", MAVENCLAD_TEXT))
	with_labels(fda["LEUSTATIN"], label("leustatin", "LEUSTATIN (CLADRIBINE) INJECTION", LEUSTATIN_TEXT))
	with_labels(fda["VIAGRA"], label("viagra", "VIAGRA (SILDENAFIL CITRATE) TABLET", VIAGRA_TEXT))
	return fda


FDA_MS = ["multiple sclerosis"]


class FdaMatchingTests(SimpleTestCase):
	"""The FDA branch of the rule: the disease check reads the DailyMed labels."""

	def setUp(self):
		self.fda = fda_ms_medicines()

	def match(self, cat, terms, *names):
		return {name: product_matches(cat, self.fda[name], [], terms) for name in names}

	def test_ocrelizumab_links_ocrevus_on_its_label_and_not_zunovo(self):
		cat = category(["ocrelizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		result = self.match(cat, FDA_MS, "OCREVUS", "OCREVUS ZUNOVO")
		self.assertEqual(result["OCREVUS"], "ocrelizumab · multiple sclerosis")
		self.assertIsNone(result["OCREVUS ZUNOVO"])

	def test_zunovo_is_one_ingredient_name_not_a_combination(self):
		# Drugs@FDA lists "OCRELIZUMAB AND HYALURONIDASE-OCSQ" as one ingredient, so the
		# combination rule does not stop it. Its substance is the pattern plus two words.
		zunovo = self.fda["OCREVUS ZUNOVO"]
		self.assertFalse(zunovo.is_combination)
		self.assertEqual(zunovo.substances, ["ocrelizumab and hyaluronidase"])
		cat = category(["ocrelizumab"], CategoryModality.BIOLOGIC_ANTIBODY, medicine_include_combinations=True)
		self.assertIsNone(product_matches(cat, zunovo, [], []))

	def test_ofatumumab_links_kesimpta_through_its_own_label_though_arzerras_has_no_ms(self):
		cat = category(["ofatumumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		kesimpta = self.fda["KESIMPTA"]
		self.assertEqual(kesimpta.brand_names, ["KESIMPTA", "ARZERRA"])
		self.assertEqual(product_matches(cat, kesimpta, [], FDA_MS), "ofatumumab · multiple sclerosis")
		# Without the Kesimpta label the Arzerra one cannot link it.
		kesimpta.label_indications = [kesimpta.label_indications[1]]
		self.assertIsNone(product_matches(cat, kesimpta, [], FDA_MS))

	def test_a_pattern_matches_any_brand_on_an_fda_row(self):
		cat = category([], CategoryModality.RESEARCH_TOPIC, medicine_terms=["arzerra"])
		self.assertEqual(
			product_matches(cat, self.fda["KESIMPTA"], [], FDA_MS), "arzerra · multiple sclerosis"
		)
		partial = category([], CategoryModality.RESEARCH_TOPIC, medicine_terms=["arz"])
		self.assertIsNone(product_matches(partial, self.fda["KESIMPTA"], [], []))

	def test_natalizumab_links_tysabri_and_tyruko(self):
		cat = category(["natalizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		result = self.match(cat, FDA_MS, "TYSABRI", "TYRUKO")
		self.assertEqual(result["TYSABRI"], "natalizumab · multiple sclerosis")
		# The FDA's four-letter suffix is gone from the substance.
		self.assertEqual(self.fda["TYRUKO"].substances, ["natalizumab"])
		self.assertEqual(result["TYRUKO"], "natalizumab · multiple sclerosis")

	def test_cladribine_links_mavenclad_and_not_leustatin(self):
		cat = category(["cladribine"])
		result = self.match(cat, FDA_MS, "MAVENCLAD", "LEUSTATIN")
		self.assertEqual(result["MAVENCLAD"], "cladribine · multiple sclerosis")
		self.assertIsNone(result["LEUSTATIN"])

	def test_leustatin_without_a_label_gets_nothing_either(self):
		cat = category(["cladribine"])
		self.fda["LEUSTATIN"].label_indications = []
		self.assertIsNone(product_matches(cat, self.fda["LEUSTATIN"], [], FDA_MS))

	def test_sildenafil_on_ms_drops_viagra_and_without_terms_links_it(self):
		cat = category(["sildenafil"])
		self.assertIsNone(product_matches(cat, self.fda["VIAGRA"], [], FDA_MS))
		self.assertEqual(product_matches(cat, self.fda["VIAGRA"], [], []), "sildenafil")

	def test_a_candidate_with_no_label_gets_nothing(self):
		cat = category(["natalizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		self.fda["TYSABRI"].label_indications = []
		self.assertIsNone(product_matches(cat, self.fda["TYSABRI"], [], FDA_MS))

	def test_a_category_with_no_terms_links_a_medicine_with_no_label(self):
		cat = category(["natalizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		self.fda["TYSABRI"].label_indications = []
		self.assertEqual(product_matches(cat, self.fda["TYSABRI"], [], []), "natalizumab")

	def test_the_ema_areas_do_not_apply_to_an_fda_row_nor_the_fda_terms_to_an_ema_row(self):
		cat = category(["ocrelizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		# Areas that would reject Ocrevus on EMA's side are ignored on the FDA row.
		self.assertEqual(
			product_matches(cat, self.fda["OCREVUS"], ["Parkinson Disease"], []), "ocrelizumab"
		)
		# Terms that would reject the EMA row are ignored on it.
		self.assertEqual(
			product_matches(cat, MEDICINES["Ocrevus"], MS, ["parkinson"]),
			"ocrelizumab · Multiple Sclerosis",
		)

	def test_the_first_term_that_appears_in_any_label_is_named(self):
		cat = category(["ocrelizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		label_ = product_matches(cat, self.fda["OCREVUS"], [], ["parkinson", "Multiple Sclerosis", "MS"])
		self.assertEqual(label_, "ocrelizumab · Multiple Sclerosis")

	def test_the_label_fits_matched_on(self):
		cat = category(["ocrelizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		self.fda["OCREVUS"].label_indications = [label("x", "t", "word " + "x" * 300)]
		self.assertEqual(len(product_matches(cat, self.fda["OCREVUS"], [], ["x" * 300])), MATCHED_ON_MAX)

	def test_exclude_terms_apply_to_an_fda_row(self):
		cat = category(["natalizumab"], category_exclude_terms=["Tyruko"])
		result = self.match(cat, FDA_MS, "TYSABRI", "TYRUKO")
		self.assertIsNotNone(result["TYSABRI"])
		self.assertIsNone(result["TYRUKO"])

	def test_combinations_are_skipped_unless_the_category_opts_in(self):
		off = category(["metformin"])
		on = category(["metformin"], medicine_include_combinations=True)
		self.assertTrue(self.fda["SYNJARDY"].is_combination)
		self.assertIsNone(product_matches(off, self.fda["SYNJARDY"], [], []))
		self.assertEqual(product_matches(on, self.fda["SYNJARDY"], [], []), "metformin")

	def test_car_t_gets_nothing_on_an_ms_subject(self):
		cat = category(
			["car-t"], CategoryModality.RESEARCH_TOPIC, medicine_terms=["*cabtagene*", "tisagenlecleucel"]
		)
		for name in ("Yescarta", "Kymriah", "AMTAGVI"):
			with self.subTest(name=name):
				self.assertIsNone(product_matches(cat, self.fda[name], [], FDA_MS))

	def test_car_t_on_a_subject_with_no_terms_gets_kymriah_and_yescarta_not_amtagvi(self):
		cat = category(
			["car-t"], CategoryModality.RESEARCH_TOPIC, medicine_terms=["*cabtagene*", "tisagenlecleucel"]
		)
		result = self.match(cat, [], "Yescarta", "Kymriah", "AMTAGVI", "tecelra")
		self.assertEqual(result["Yescarta"], "*cabtagene*")
		self.assertEqual(result["Kymriah"], "tisagenlecleucel")
		self.assertIsNone(result["AMTAGVI"])
		self.assertIsNone(result["tecelra"])

	def test_a_contrast_agent_never_links(self):
		agent = self.fda["CARBON DIOXIDE, USP"]
		self.assertEqual(agent.fda_pharm_classes, ["Radiographic Contrast Agent [EPC]"])
		cat = category(["carbon dioxide"])
		self.assertTrue(is_diagnostic(agent))
		self.assertIsNone(product_matches(cat, agent, [], []))
		self.assertIsNone(pattern_match(cat, agent))
		# Without the class it would link, so the class is what keeps it out.
		agent.fda_pharm_classes = []
		self.assertEqual(product_matches(cat, agent, [], []), "carbon dioxide")

	def test_the_diagnostic_rule_reads_every_class_and_ignores_case(self):
		row = self.fda["OCREVUS"]
		row.fda_pharm_classes = ["Something [EPC]", "Radioactive DIAGNOSTIC Agent [EPC]"]
		self.assertTrue(is_diagnostic(row))
		row.fda_pharm_classes = ["gadolinium-based CONTRAST agent"]
		self.assertTrue(is_diagnostic(row))
		row.fda_pharm_classes = ["CD20-directed Cytolytic Antibody [EPC]"]
		self.assertFalse(is_diagnostic(row))
		row.fda_pharm_classes = []
		self.assertFalse(is_diagnostic(row))

	def test_an_fda_row_is_not_judged_by_an_atc_code(self):
		row = self.fda["OCREVUS"]
		row.atc_code = "V08AB01"
		self.assertFalse(is_diagnostic(row))

	def test_pattern_match_is_the_check_without_the_disease_step(self):
		cat = category(["natalizumab"], CategoryModality.BIOLOGIC_ANTIBODY)
		self.fda["TYSABRI"].label_indications = []
		self.assertEqual(pattern_match(cat, self.fda["TYSABRI"]), "natalizumab")
		self.assertIsNone(pattern_match(cat, self.fda["OCREVUS"]))
		# EMA rows go through it too.
		self.assertEqual(pattern_match(cat, MEDICINES["Tysabri"]), "natalizumab")

	def test_an_fda_row_never_gets_a_subject_link(self):
		ocrevus = self.fda["OCREVUS"]
		ocrevus.therapeutic_areas = ["Multiple Sclerosis"]
		self.assertIsNone(subject_match(MS, ocrevus))


class FdaTermTests(SimpleTestCase):
	"""Terms are whole words, ignoring case."""

	def setUp(self):
		self.row = fda_medicines()["OCREVUS"]
		self.cat = category(["ocrelizumab"], CategoryModality.BIOLOGIC_ANTIBODY)

	def check(self, term, text):
		self.row.label_indications = [label("x", "OCREVUS", text)]
		return product_matches(self.cat, self.row, [], [term])

	def test_parkinson_does_not_match_parkinsonism(self):
		self.assertIsNone(self.check("parkinson", "Indicated for drug-induced parkinsonism."))
		self.assertIsNotNone(self.check("parkinson", "Indicated for Parkinson's disease."))

	def test_alzheimer_matches_both_apostrophes(self):
		self.assertIsNotNone(self.check("alzheimer", "for dementia of the Alzheimer's type"))
		self.assertIsNotNone(self.check("alzheimer", "for dementia of the Alzheimer’s type"))
		self.assertIsNotNone(self.check("alzheimer", "for ALZHEIMER disease"))

	def test_a_term_inside_a_longer_word_does_not_match(self):
		self.assertIsNone(self.check("ms", "indicated for symptoms of disease"))
		self.assertIsNotNone(self.check("ms", "relapsing forms of MS."))

	def test_a_multi_word_term_matches_across_the_text(self):
		self.assertIsNotNone(self.check("multiple sclerosis", "relapsing Multiple   Sclerosis"))
		self.assertIsNone(self.check("multiple sclerosis", "multiple myeloma and sclerosis"))

	def test_a_term_with_punctuation_at_its_edge_still_matches(self):
		self.assertIsNotNone(self.check("(ms)", "multiple sclerosis (MS) in adults"))

	def test_blank_terms_and_malformed_entries_are_ignored(self):
		self.row.label_indications = ["junk", {"setid": "x"}, label("y", "OCREVUS", "multiple sclerosis")]
		self.assertEqual(
			product_matches(self.cat, self.row, [], [" ", "multiple sclerosis"]),
			"ocrelizumab · multiple sclerosis",
		)


class FdaGroupingTests(SimpleTestCase):
	def test_an_ema_and_an_fda_product_with_the_same_text_do_not_group(self):
		ema = product("Zorvex", "Treats X.", substances=["memantine"])
		fda = product(
			"Qinta",
			"Treats X.",
			substances=["memantine"],
			regulator="fda",
			ema_product_number=None,
			fda_application_number="NDA000001",
			brand_names=["Qinta"],
		)
		self.assertNotEqual(group_key(ema), group_key(fda))
		self.assertEqual(group_key(fda)[0], "fda")

	def test_two_fda_products_with_the_same_text_group(self):
		first = fda_product("Zorvex", "Zorvex is indicated for X.", "NDA000001")
		second = fda_product("Qinta", "Qinta is indicated for X.", "NDA000002")
		self.assertEqual(group_key(first), group_key(second))

	def test_every_brand_of_an_fda_row_is_removed_from_its_indication(self):
		first = fda_product("Kesimpta / Arzerra", "Kesimpta, Arzerra treats X.", "NDA000001", brands=["Kesimpta", "Arzerra"])
		second = fda_product("Other", "Other treats X.", "NDA000002")
		self.assertEqual(group_key(first), group_key(second))

	def test_fda_biosimilars_and_rows_without_a_date_or_indication_are_not_grouped(self):
		self.assertIsNone(group_key(fda_product("A", "A treats X.", "NDA000001", biosimilar=True)))
		self.assertIsNone(group_key(fda_product("A", "A treats X.", "NDA000001", authorisation_date=None)))
		self.assertIsNone(group_key(fda_product("A", "", "NDA000001")))

	def test_an_approved_fda_product_leads_over_an_earlier_discontinued_one(self):
		discontinued = fda_product("Old", "x", "NDA000009", status="Discontinued", authorisation_date=date(1998, 1, 1))
		approved = fda_product("New", "x", "NDA000008", status="Approved", authorisation_date=date(2010, 1, 1))
		self.assertIs(group_lead([discontinued, approved]), approved)

	def test_the_earliest_approval_then_the_lowest_application_number_leads(self):
		later = fda_product("Later", "x", "NDA000001", authorisation_date=date(2012, 1, 1))
		earlier = fda_product("Earlier", "x", "NDA000009", authorisation_date=date(2002, 1, 1))
		self.assertIs(group_lead([later, earlier]), earlier)
		high = fda_product("High", "x", "NDA000009")
		low = fda_product("Low", "x", "NDA000002")
		self.assertIs(group_lead([high, low]), low)

	def test_only_the_word_approved_counts_on_an_fda_row(self):
		odd = fda_product("Odd", "x", "NDA000001", status="Authorised", authorisation_date=date(1990, 1, 1))
		real = fda_product("Real", "x", "NDA000002", status="Approved", authorisation_date=date(2010, 1, 1))
		self.assertIs(group_lead([odd, real]), real)


def fda_product(name, indication, number, brands=None, **kwargs):
	return product(
		name,
		indication,
		substances=["memantine"],
		regulator="fda",
		ema_product_number=None,
		fda_application_number=number,
		brand_names=brands or [name],
		**kwargs,
	)


class CategoryFdaTermsTests(SimpleTestCase):
	def test_the_terms_of_every_subject_are_joined_without_duplicates(self):
		first = Subject(pk=1, subject_name="A", fda_indication_terms=["Multiple Sclerosis", " "])
		second = Subject(pk=2, subject_name="B", fda_indication_terms=["multiple sclerosis", "alzheimer"])
		cat = SimpleNamespace(subjects=SimpleNamespace(all=lambda: [second, first]))
		self.assertEqual(category_fda_terms(cat), ["Multiple Sclerosis", "alzheimer"])

	def test_no_subjects_no_terms(self):
		cat = SimpleNamespace(subjects=SimpleNamespace(all=lambda: []))
		self.assertEqual(category_fda_terms(cat), [])


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
