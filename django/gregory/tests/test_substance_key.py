"""Tests for gregory.utils.substance_key: the key that puts one drug's EMA and FDA
records under the same name. Pure functions, no database."""

from unittest import TestCase

from gregory.utils.substance_key import (
	COUNTER_IONS,
	SALT_WORDS,
	strip_salt,
	substance_key,
)


class SubstanceKeyTests(TestCase):
	def test_the_expected_keys(self):
		cases = [
			(["fingolimod hydrochloride"], "fingolimod"),
			(["fingolimod lauryl sulfate"], "fingolimod"),
			(["ozanimod hydrochloride"], "ozanimod"),
			(["rivastigmine tartrate"], "rivastigmine"),
			(["donepezil hydrochloride"], "donepezil"),
			(["ocrelizumab", "hyaluronidase"], "ocrelizumab"),
			(["ocrelizumab", "hyaluronidase (human recombinant)"], "ocrelizumab"),
			(["hyaluronidase"], "hyaluronidase"),
			(["dimethyl fumarate"], "dimethyl fumarate"),
			(["diroximel fumarate"], "diroximel fumarate"),
			(["monomethyl fumarate"], "monomethyl fumarate"),
			(["sodium chloride"], "sodium chloride"),
			(["naproxen sodium"], "naproxen"),
			(["amlodipine besylate", "valsartan"], "amlodipine+valsartan"),
			(["interferon beta-1a"], "interferon beta-1a"),
		]
		for substances, expected in cases:
			with self.subTest(substances=substances):
				self.assertEqual(substance_key(substances), expected)

	def test_no_substances_stands_alone(self):
		self.assertEqual(substance_key([], pk=7), "medicine-7")
		self.assertEqual(substance_key(None, pk=7), "medicine-7")
		self.assertEqual(substance_key([""], pk=7), "medicine-7")
		self.assertEqual(substance_key([]), "")

	def test_regulator_does_not_change_the_key(self):
		self.assertEqual(
			substance_key(["fingolimod hydrochloride"], regulator="fda"),
			substance_key(["fingolimod"], regulator="ema"),
		)

	def test_order_does_not_matter(self):
		self.assertEqual(
			substance_key(["valsartan", "amlodipine besylate"]),
			substance_key(["amlodipine besylate", "valsartan"]),
		)

	def test_duplicates_collapse(self):
		self.assertEqual(
			substance_key(["fingolimod", "fingolimod hydrochloride"]), "fingolimod"
		)

	def test_only_enzymes_are_all_kept(self):
		self.assertEqual(
			substance_key(["hyaluronidase", "berahyaluronidase alfa"]),
			"berahyaluronidase alfa+hyaluronidase",
		)

	def test_a_combination_with_an_enzyme_keeps_the_other_parts(self):
		self.assertEqual(
			substance_key(["pertuzumab", "trastuzumab", "hyaluronidase"]),
			"pertuzumab+trastuzumab",
		)

	def test_the_key_fits_its_column(self):
		key = substance_key(["x" * 255, "y" * 255, "z" * 255])
		self.assertEqual(len(key), 500)


class StripSaltTests(TestCase):
	def test_one_word_is_never_stripped(self):
		for word in ("sodium", "chloride", "hydrochloride", "acetate"):
			with self.subTest(word=word):
				self.assertEqual(strip_salt(word), word)

	def test_hydrates_and_several_trailing_words(self):
		self.assertEqual(strip_salt("codeine phosphate hemihydrate"), "codeine")
		self.assertEqual(strip_salt("betamethasone sodium phosphate"), "betamethasone")

	def test_a_counter_ion_alone_stays(self):
		for name in ("sodium chloride", "potassium chloride", "calcium chloride", "magnesium sulfate"):
			with self.subTest(name=name):
				self.assertEqual(strip_salt(name), name)

	def test_a_cation_after_an_anion(self):
		self.assertEqual(strip_salt("rosuvastatin calcium"), "rosuvastatin")
		self.assertEqual(strip_salt("atorvastatin calcium trihydrate"), "atorvastatin")

	def test_whole_names_that_end_in_a_salt_word(self):
		for name in ("dimethyl fumarate", "diroximel fumarate", "monomethyl fumarate"):
			with self.subTest(name=name):
				self.assertEqual(strip_salt(name), name)

	def test_case_and_spaces_are_normalised(self):
		self.assertEqual(strip_salt("  Fingolimod   Hydrochloride "), "fingolimod")

	def test_a_name_that_merely_contains_a_salt_word_stays(self):
		self.assertEqual(strip_salt("sodium oxybate"), "sodium oxybate")
		self.assertEqual(strip_salt("interferon beta-1a"), "interferon beta-1a")

	def test_empty(self):
		self.assertEqual(strip_salt(""), "")
		self.assertEqual(strip_salt(None), "")

	def test_the_lists_do_not_overlap_the_exceptions(self):
		self.assertTrue(COUNTER_IONS <= SALT_WORDS)
