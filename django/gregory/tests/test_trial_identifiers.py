"""
Unit tests for gregory.utils.trial_identifiers — canonical identifier
extraction shared by article-text scanning and trial identifier normalization.

These tests have no database dependency.

Run:
  docker exec gregory python manage.py test gregory.tests.test_trial_identifiers
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import SimpleTestCase
from gregory.utils.trial_identifiers import (
	extract_identifiers,
	extract_identifiers_from_trial_identifiers,
	normalize_trial_identifiers,
)


class NctExtractionTest(SimpleTestCase):
	def test_extracts_bare_nct(self):
		self.assertEqual(
			extract_identifiers("see NCT04578639 for details"),
			{("nct", "NCT04578639")},
		)

	def test_case_insensitive(self):
		self.assertEqual(
			extract_identifiers("nct04578639"), {("nct", "NCT04578639")}
		)

	def test_no_false_positive_on_longer_token(self):
		self.assertEqual(extract_identifiers("XNCT04578639X"), set())

	def test_no_false_positive_on_trailing_alphanumeric_suffix(self):
		"""A trailing letter right after the 8 digits must not be treated as
		a word boundary — otherwise NCT04578639X would match as NCT04578639."""
		self.assertEqual(extract_identifiers("NCT04578639X"), set())


class EudractCtisExtractionTest(SimpleTestCase):
	def test_extracts_bare_eudract(self):
		self.assertEqual(
			extract_identifiers("EudraCT number, 2020-001205-23."),
			{("eudract", "2020-001205-23")},
		)

	def test_extracts_euctr_prefixed_value(self):
		"""The exact case that failed: trial stores EUCTR2020-001205-23-NO,
		the article cites the bare EudraCT number."""
		self.assertEqual(
			extract_identifiers("EUCTR2020-001205-23-NO"),
			{("eudract", "2020-001205-23")},
		)

	def test_extracts_ctis_number(self):
		self.assertEqual(
			extract_identifiers("EU Clinical Trials Register number, 2024-510716-71-00."),
			{("ctis", "2024-510716-71-00")},
		)

	def test_ctis_not_split_into_eudract(self):
		"""A 4-segment CTIS number must not also register as a 3-segment
		EudraCT match on its leading segments."""
		found = extract_identifiers("2024-510716-71-00")
		self.assertEqual(found, {("ctis", "2024-510716-71-00")})

	def test_eudract_and_ctis_both_present(self):
		text = (
			"EudraCT number, 2020-001205-23; "
			"EU Clinical Trials Register number, 2024-510716-71-00."
		)
		self.assertEqual(
			extract_identifiers(text),
			{("eudract", "2020-001205-23"), ("ctis", "2024-510716-71-00")},
		)


class OtherRegistryExtractionTest(SimpleTestCase):
	def test_isrctn(self):
		self.assertEqual(
			extract_identifiers("ISRCTN12345678"), {("isrctn", "ISRCTN12345678")}
		)

	def test_actrn(self):
		self.assertEqual(
			extract_identifiers("ACTRN12345678901234"),
			{("actrn", "ACTRN12345678901234")},
		)

	def test_drks(self):
		self.assertEqual(extract_identifiers("DRKS00012345"), {("drks", "DRKS00012345")})

	def test_ctri(self):
		self.assertEqual(
			extract_identifiers("CTRI/2020/01/012345"),
			{("ctri", "CTRI/2020/01/012345")},
		)

	def test_chictr_variants_normalize_consistently(self):
		self.assertEqual(
			extract_identifiers("ChiCTR1900021332"),
			{("chictr", "CHICTR-1900021332")},
		)
		self.assertEqual(
			extract_identifiers("ChiCTR-TRC-12345678"),
			{("chictr", "CHICTR-TRC-12345678")},
		)

	def test_rbr(self):
		self.assertEqual(extract_identifiers("RBR-4nzq6z"), {("rbr", "RBR-4NZQ6Z")})

	def test_irct(self):
		self.assertEqual(
			extract_identifiers("IRCT2017010805N1"), {("irct", "IRCT2017010805N1")}
		)


class NewRegistryExtractionTest(SimpleTestCase):
	"""Registry patterns added alongside identifiers_normalized."""

	def test_nl_omon(self):
		self.assertEqual(
			extract_identifiers("NL-OMON19915"), {("nl_omon", "NL-OMON19915")}
		)

	def test_nl(self):
		self.assertEqual(extract_identifiers("NL7921"), {("nl", "NL7921")})

	def test_nl_ccmo_dossier_number_is_not_a_trial_id(self):
		# CCMO ethics-committee dossier numbers share the NL prefix but aren't
		# registry ids: 5 digits, then ".<digits>".
		self.assertEqual(extract_identifiers("NL67805.068.18"), set())
		self.assertEqual(
			extract_identifiers("NL7921;NL67805.068.18;NL"), {("nl", "NL7921")}
		)

	def test_nl_trial_id_before_a_full_stop(self):
		self.assertEqual(
			extract_identifiers("Netherlands Trial Register, NL8496. Registered 2020"),
			{("nl", "NL8496")},
		)

	def test_nl_omon_does_not_also_register_as_bare_nl(self):
		"""NL-OMON19915 must not also match the plain "nl" pattern — there is no
		"NL" immediately followed by 4-5 digits anywhere in that string."""
		self.assertEqual(
			extract_identifiers("NL-OMON19915"), {("nl_omon", "NL-OMON19915")}
		)

	def test_ntr(self):
		self.assertEqual(extract_identifiers("NTR7203"), {("ntr", "NTR7203")})

	def test_repec(self):
		self.assertEqual(
			extract_identifiers("PER-024-15"), {("repec", "PER-024-15")}
		)

	def test_lbctr(self):
		self.assertEqual(
			extract_identifiers("LBCTR2020033434"), {("lbctr", "LBCTR2020033434")}
		)

	def test_jrct_widened_with_sub_prefix_letter(self):
		"""JPRN-jRCTs031180248: the "s" sub-prefix and the 9-digit number both
		upper-case into the canonical value."""
		self.assertEqual(
			extract_identifiers("JPRN-jRCTs031180248"),
			{("jrct", "JRCTS031180248")},
		)

	def test_jrct_bare_ten_digit_still_matches(self):
		self.assertEqual(
			extract_identifiers("jRCT1031180248"), {("jrct", "JRCT1031180248")}
		)

	def test_ctri_widened_three_digit_middle_segment(self):
		self.assertEqual(
			extract_identifiers("CTRI/2009/091/000088"),
			{("ctri", "CTRI/2009/091/000088")},
		)

	def test_ctri_two_digit_middle_segment_still_matches(self):
		self.assertEqual(
			extract_identifiers("CTRI/2020/01/012345"),
			{("ctri", "CTRI/2020/01/012345")},
		)

	def test_utn(self):
		self.assertEqual(
			extract_identifiers("U1111-1299-8084"), {("utn", "U1111-1299-8084")}
		)

	def test_japic(self):
		self.assertEqual(
			extract_identifiers("JapicCTI-142447"), {("japic", "JAPICCTI-142447")}
		)

	def test_japic_case_insensitive(self):
		self.assertEqual(
			extract_identifiers("japicCTI-142447"), {("japic", "JAPICCTI-142447")}
		)


class LabelledTokenExtractionTest(SimpleTestCase):
	"""A registry id embedded in a labelled sentence, not a bare token — parsing
	must search inside tokens, not require a whole-token match (55 dev
	secondary_id rows label the number, e.g. "EudraCT No.: …")."""

	def test_eudract_no_colon_label(self):
		self.assertEqual(
			extract_identifiers("EudraCT No.: 2006-006752-35"),
			{("eudract", "2006-006752-35")},
		)

	def test_eudract_label_with_trailing_extra_token(self):
		self.assertEqual(
			extract_identifiers("EudraCT: 2005-001540-23, 309560"),
			{("eudract", "2005-001540-23")},
		)


class NegativeSampleExtractionTest(SimpleTestCase):
	"""None of these should ever yield an identifier — the WHO ICTRP sample
	Secondary_ID values (deliberately not registry ids), plus a few more
	known non-IDs."""

	def test_ictrp_sample_secondary_ids_yield_nothing(self):
		samples = [
			"REDIFINE MS",
			"R21NR022143;STUDY00008963",
			"1583821/ 17043-H33",
			"25-12875-BO",
			"2026/2-122",
			"IM047-1132",
			"HEALTHYFIT-UVIGO 3/2026",
			"OCtINN",
			"1591730",
			"23-232 BO",
		]
		for sample in samples:
			with self.subTest(sample=sample):
				self.assertEqual(extract_identifiers(sample), set())

	def test_cpms_label_not_matched(self):
		self.assertEqual(extract_identifiers("CPMS: 54274"), set())

	def test_short_alpha_code_not_matched(self):
		self.assertEqual(extract_identifiers("ND001"), set())

	def test_truncated_eudract_not_matched(self):
		"""Truncated at source (53 chars short of the full 2-digit suffix) —
		stays unparsed; the legacy exact-match filter branch is what still
		finds these."""
		self.assertEqual(extract_identifiers("2019-004822-1"), set())

	def test_bare_six_digits_without_domain_context_not_matched(self):
		"""A bare 6-digit number is only ever recognised as a JAPIC id via the
		domain rule in normalize_trial_identifiers (it needs ctg_secondary_ids'
		domain field) — extract_identifiers alone has no textual cue here."""
		self.assertEqual(extract_identifiers("153082"), set())


class ExtractFromTrialIdentifiersTest(SimpleTestCase):
	def test_normalizes_euctr_key_to_eudract_canonical(self):
		self.assertEqual(
			extract_identifiers_from_trial_identifiers(
				{"euctr": "EUCTR2020-001205-23-NO"}
			),
			{("eudract", "2020-001205-23")},
		)

	def test_matches_article_side_for_nct_and_eudract(self):
		trial_ids = extract_identifiers_from_trial_identifiers(
			{"nct": "NCT04578639", "euctr": "EUCTR2020-001205-23-NO"}
		)
		article_ids = extract_identifiers(
			"OVERLORD-MS ClinicalTrials.gov number, NCT04578639; "
			"EudraCT number, 2020-001205-23"
		)
		self.assertTrue(trial_ids & article_ids)
		self.assertEqual(
			trial_ids & article_ids,
			{("nct", "NCT04578639"), ("eudract", "2020-001205-23")},
		)

	def test_empty_and_none_values_ignored(self):
		self.assertEqual(
			extract_identifiers_from_trial_identifiers({"nct": None, "euctr": ""}),
			set(),
		)

	def test_none_dict(self):
		self.assertEqual(extract_identifiers_from_trial_identifiers(None), set())


class NormalizeTrialIdentifiersTest(SimpleTestCase):
	"""gregory.utils.trial_identifiers.normalize_trial_identifiers."""

	def test_all_empty_inputs_return_none(self):
		self.assertIsNone(normalize_trial_identifiers(None, None, None))
		self.assertIsNone(normalize_trial_identifiers({}, "", []))

	def test_registry_keys_always_count_with_prefixes_stripped(self):
		result = normalize_trial_identifiers(
			{
				"euctr": "EUCTR2020-001205-23-NO",
				"ctis": "CTIS2023-507431-37-00",
				"eudract": "EUDRACT2019-004822-12",
			},
			None,
			None,
		)
		self.assertEqual(
			result,
			[
				"ctis:2023-507431-37-00",
				"eudract:2019-004822-12",
				"eudract:2020-001205-23",
			],
		)

	def test_octopus_eudract_in_secondary_id_is_added(self):
		"""The regression case this PR exists to fix: trial 519's EudraCT number
		sits only in secondary_id."""
		result = normalize_trial_identifiers(
			{"isrctn": "ISRCTN14048364"},
			"2021-003034-37;CPMS: 54274, ND001",
			None,
		)
		self.assertEqual(
			result, ["eudract:2021-003034-37", "isrctn:ISRCTN14048364"]
		)

	def test_org_study_id_sponsor_code_ignored(self):
		"""A non-registry-shaped org_study_id contributes nothing — it just
		isn't a registry-sourced key, and it doesn't parse as free text either."""
		result = normalize_trial_identifiers(
			{"nct": "NCT01594346", "org_study_id": "HSC-MS-15-0278"},
			None,
			None,
		)
		self.assertEqual(result, ["nct:NCT01594346"])

	def test_org_study_id_eudract_number_is_added(self):
		result = normalize_trial_identifiers(
			{"nct": "NCT99999999", "org_study_id": "2020-001234-12"},
			None,
			None,
		)
		self.assertEqual(result, ["eudract:2020-001234-12", "nct:NCT99999999"])

	def test_org_study_id_foreign_nct_excluded(self):
		"""Trial 30842: identifiers['nct'] and identifiers['org_study_id'] are
		two DIFFERENT NCT numbers — the org_study_id one is dropped."""
		result = normalize_trial_identifiers(
			{"nct": "NCT01594346", "org_study_id": "NCT00056329"},
			None,
			None,
		)
		self.assertEqual(result, ["nct:NCT01594346"])

	def test_secondary_id_type_the_row_lacks_is_added(self):
		result = normalize_trial_identifiers(
			{"nct": "NCT01234567"}, "ISRCTN14048364", None
		)
		self.assertEqual(result, ["isrctn:ISRCTN14048364", "nct:NCT01234567"])

	def test_secondary_id_clashing_type_is_dropped(self):
		result = normalize_trial_identifiers(
			{"nct": "NCT01234567"}, "NCT09999999", None
		)
		self.assertEqual(result, ["nct:NCT01234567"])

	def test_secondary_id_multiple_of_one_type_none_registry_sourced_kept(self):
		result = normalize_trial_identifiers(
			None, "NCT11111111 and also NCT22222222", None
		)
		self.assertEqual(result, ["nct:NCT11111111", "nct:NCT22222222"])

	def test_ctg_eudract_ctis_registry_types_are_registry_sourced(self):
		"""These three types win a clash against a differing free-text id of
		the same type — unlike ordinary free text."""
		result = normalize_trial_identifiers(
			{"nct": "NCT01234567"},
			"2020-001111-11",  # a DIFFERENT eudract number, free text
			[
				{"id": "2020-002222-22", "type": "EUDRACT_NUMBER", "domain": "", "link": ""},
			],
		)
		self.assertEqual(
			result, ["eudract:2020-002222-22", "nct:NCT01234567"]
		)

	def test_ctg_other_and_untyped_entries_are_free_text(self):
		result = normalize_trial_identifiers(
			{"nct": "NCT01234567"},
			None,
			[
				{"id": "ISRCTN14048364", "type": "OTHER", "domain": "", "link": ""},
				{"id": "DRKS00041145", "type": "", "domain": "", "link": ""},
			],
		)
		self.assertEqual(
			result,
			["drks:DRKS00041145", "isrctn:ISRCTN14048364", "nct:NCT01234567"],
		)

	def test_non_string_json_values_do_not_crash(self):
		# ctg_secondary_ids is a JSONField, so tolerate whatever shapes it holds.
		result = normalize_trial_identifiers(
			None,
			None,
			[
				{"id": 153082, "type": "REGISTRY", "domain": "JAPIC-CTI"},
				{"id": "NCT12345678", "type": None},
				{"id": None, "type": 7, "domain": 3},
			],
		)
		self.assertEqual(result, ["japic:JAPICCTI-153082", "nct:NCT12345678"])

	def test_ctg_grant_types_skipped_even_when_value_would_parse(self):
		result = normalize_trial_identifiers(
			None,
			None,
			[
				{"id": "NCT12345678", "type": "NIH", "domain": "", "link": ""},
				{"id": "NCT87654321", "type": "OTHER_GRANT", "domain": "", "link": ""},
				{"id": "NCT11112222", "type": "AHRQ", "domain": "", "link": ""},
				{"id": "NCT22223333", "type": "FDA", "domain": "", "link": ""},
				{"id": "NCT33334444", "type": "SAMHSA", "domain": "", "link": ""},
				{"id": "NCT44445555", "type": "VA", "domain": "", "link": ""},
				{"id": "NCT55556666", "type": "CDC", "domain": "", "link": ""},
			],
		)
		self.assertIsNone(result)

	def test_ctg_ctis_shaped_value_typed_eudract_number_lands_as_ctis(self):
		"""Format decides the canonical type, never the source's own type
		label."""
		result = normalize_trial_identifiers(
			None,
			None,
			[{"id": "2023-507431-37-00", "type": "EUDRACT_NUMBER", "domain": "", "link": ""}],
		)
		self.assertEqual(result, ["ctis:2023-507431-37-00"])

	def test_ctg_japic_domain_bare_digit_becomes_japic_id(self):
		result = normalize_trial_identifiers(
			None,
			None,
			[{"id": "153082", "type": "REGISTRY", "domain": "JapicCTI", "link": ""}],
		)
		self.assertEqual(result, ["japic:JAPICCTI-153082"])

	def test_ctg_registry_type_non_japic_domain_bare_digit_unmatched(self):
		"""A REGISTRY-typed entry with an unrelated domain and a bare-digit id
		(French ID-RCB, NCI CTRP, ...) stays unrecognised — it's raw data only
		in ctg_secondary_ids."""
		result = normalize_trial_identifiers(
			None,
			None,
			[{"id": "153082", "type": "REGISTRY", "domain": "ID-RCB", "link": ""}],
		)
		self.assertIsNone(result)

	def test_output_is_sorted_and_deduplicated(self):
		result = normalize_trial_identifiers(
			{"nct": "NCT01234567"},
			"NCT01234567",  # same id, also present as free text
			[{"id": "NCT01234567", "type": "OTHER", "domain": "", "link": ""}],
		)
		self.assertEqual(result, ["nct:NCT01234567"])
