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
