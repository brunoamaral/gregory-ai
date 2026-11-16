"""
Unit tests for gregory.utils.trial_acronyms. No database.
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import SimpleTestCase

from gregory.utils.trial_acronyms import eligible_acronym, find_mentions


class EligibleAcronymTest(SimpleTestCase):
	def test_single_tokens_pass(self):
		for value in ("CLARITY", "MS-STAT2", "CogEx"):
			self.assertEqual(eligible_acronym(value), value)

	def test_surrounding_space_is_stripped(self):
		self.assertEqual(eligible_acronym("  CLARITY "), "CLARITY")

	def test_ineligible_values(self):
		for value in ("AB", "Vitamin D Study", "12345", "A" * 31, "", None):
			self.assertIsNone(eligible_acronym(value), value)

	def test_thirty_characters_is_the_limit(self):
		self.assertEqual(eligible_acronym("A" * 30), "A" * 30)


class FindMentionsTest(SimpleTestCase):
	def test_trial_wording_after_is_adjacent(self):
		for text in (
			"results of the CLARITY trial in patients",
			"the CLARITY study found",
			"a (CLARITY) extension study",
			"CLARITY randomised clinical trial",
		):
			self.assertEqual(find_mentions(text, {"CLARITY"}), {"CLARITY": True}, text)

	def test_trial_wording_before_is_adjacent(self):
		self.assertEqual(
			find_mentions("a phase III trial (TEMSO) reported", {"TEMSO"}), {"TEMSO": True}
		)
		self.assertEqual(find_mentions("in the study: TEMSO", {"TEMSO"}), {"TEMSO": True})

	def test_lower_case_word_is_no_mention(self):
		self.assertEqual(find_mentions("improved clarity of the study", {"CLARITY"}), {})

	def test_longer_token_does_not_match(self):
		self.assertEqual(find_mentions("the CLARITY-EXT trial", {"CLARITY"}), {})

	def test_mention_without_trial_wording_is_not_adjacent(self):
		self.assertEqual(find_mentions("the EDSS score rose", {"EDSS"}), {"EDSS": False})

	def test_any_adjacent_occurrence_wins(self):
		text = "EDSS rose. In the EDSS study we saw it."
		self.assertEqual(find_mentions(text, {"EDSS"}), {"EDSS": True})

	def test_empty_text(self):
		self.assertEqual(find_mentions("", {"EDSS"}), {})
		self.assertEqual(find_mentions(None, {"EDSS"}), {})
