"""
Tests for gregory.utils.text_utils.clean_field_html.

Run:
  docker exec gregory python manage.py test gregory.tests.test_text_utils
"""

import os
import unittest

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from gregory.utils.text_utils import clean_field_html


class CleanFieldHtmlTest(unittest.TestCase):
	def test_br_becomes_space_not_concatenation(self):
		self.assertEqual(clean_field_html("A<br>B"), "A B")

	def test_nested_attributed_tag_extracts_text(self):
		self.assertEqual(clean_field_html('<a href="https://example.org">x</a>'), "x")

	def test_entities_are_unescaped(self):
		self.assertEqual(clean_field_html("a &amp; b"), "a & b")
		self.assertEqual(clean_field_html("a&nbsp;b"), "a b")
		self.assertEqual(clean_field_html("it&#39;s ok"), "it's ok")

	def test_whitespace_is_collapsed_and_stripped(self):
		self.assertEqual(clean_field_html("  A   B  "), "A B")

	def test_empty_and_blank_input_returns_none(self):
		self.assertIsNone(clean_field_html(None))
		self.assertIsNone(clean_field_html(""))
		self.assertIsNone(clean_field_html("   "))

	def test_angle_brackets_used_as_quotation_marks_are_preserved(self):
		# The false-positive guard: some ICTRP registries use angle brackets as
		# quotation marks, not markup. A tag-shaped regex would delete this text;
		# a real parser must leave it alone. This is why clean_field_html parses
		# HTML instead of regexing it.
		text = "criteria <the guide of diagnosis and treatment> apply"
		self.assertEqual(clean_field_html(text), text)

	def test_literal_tab_marker_is_preserved(self):
		text = r"\<TAB\>"
		self.assertEqual(clean_field_html(text), text)

	def test_real_fixture_inclusion_gender(self):
		text = "<br>Female: yes<br>Male: yes<br>"
		self.assertEqual(clean_field_html(text), "Female: yes Male: yes")

	def test_block_tags_preserve_inner_text(self):
		self.assertEqual(
			clean_field_html("<p>para1</p><p>para2</p>"), "para1 para2"
		)
		self.assertEqual(
			clean_field_html("<ul><li>one</li><li>two</li></ul>"), "one two"
		)
		self.assertEqual(
			clean_field_html("<tr><td>a</td><td>b</td></tr>"), "a b"
		)

	def test_no_markup_returns_unchanged_text(self):
		text = "plain text with no markup at all, just words"
		self.assertEqual(clean_field_html(text), text)
