from django.test import SimpleTestCase

from gregory.utils.patent_numbers import (
	clean_publication_number,
	normalise_publication_number,
)


class NormalisePublicationNumberTest(SimpleTestCase):
	def test_every_common_format(self):
		for raw in (
			"EP4123456A1",
			"EP 4123456 A1",
			"ep4123456a1",
			"EP-4123456-A1",
			"EP.4123456.A1",
			"  EP4123456A1  ",
			"EP4123456.A1",
		):
			with self.subTest(raw=raw):
				self.assertEqual(normalise_publication_number(raw), ("EP", "4123456", "A1"))

	def test_without_a_kind_code(self):
		self.assertEqual(normalise_publication_number("EP4123456"), ("EP", "4123456", None))
		self.assertEqual(normalise_publication_number("WO 2018 123456"), ("WO", "2018123456", None))

	def test_single_letter_kind(self):
		self.assertEqual(normalise_publication_number("CA2409515A"), ("CA", "2409515", "A"))

	def test_leading_zeros_are_kept(self):
		self.assertEqual(normalise_publication_number("WO0185797A1"), ("WO", "0185797", "A1"))

	def test_not_a_publication_number(self):
		for raw in ("", "   ", "123", "EP", "A61K31/435", "MXPA02011163A", "!!!"):
			with self.subTest(raw=raw):
				self.assertIsNone(normalise_publication_number(raw))

	def test_none_is_safe(self):
		self.assertIsNone(normalise_publication_number(None))

	def test_clean_publication_number(self):
		self.assertEqual(clean_publication_number("mx pa02011163-a"), "MXPA02011163A")
		self.assertEqual(clean_publication_number(None), "")
