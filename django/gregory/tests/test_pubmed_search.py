"""build_pubmed_term and the date-clause check (no network)."""

from datetime import date

from django.test import SimpleTestCase

from gregory.source_types import PUBMED_DATE_CLAUSE
from gregory.utils.pubmed_search import build_pubmed_term

START, END = date(2026, 9, 1), date(2026, 10, 1)


class BuildPubmedTermTests(SimpleTestCase):
	def test_query_is_bracketed_and_dated(self):
		self.assertEqual(
			build_pubmed_term('ms AND "myelin repair"', [], START, END),
			'(ms AND "myelin repair") AND 2026/09/01:2026/10/01[crdt]',
		)

	def test_exclusions_become_a_not_clause_before_the_dates(self):
		self.assertEqual(
			build_pubmed_term("ms", ["Review", "Letter"], START, END),
			'(ms) NOT ("Review"[pt] OR "Letter"[pt]) AND 2026/09/01:2026/10/01[crdt]',
		)

	def test_quotes_in_a_type_name_cannot_break_the_term(self):
		term = build_pubmed_term("ms", ['Bad"Name'], START, END)
		self.assertIn('"BadName"[pt]', term)

	def test_surrounding_whitespace_in_the_query_is_dropped(self):
		self.assertTrue(build_pubmed_term("  ms \n", [], START, END).startswith("(ms) AND"))

	def test_single_day_window(self):
		self.assertTrue(build_pubmed_term("ms", [], START, START).endswith("2026/09/01:2026/09/01[crdt]"))


class DateClauseTests(SimpleTestCase):
	def test_date_clauses_are_found(self):
		for query in (
			"ms AND 2020[dp]",
			"ms AND 2020:2021[PDAT]",
			"ms AND 2020/01/01[crdt]",
			"ms AND 2020[edat]",
			"ms AND 2020[mhda]",
			"ms AND 2020[lr]",
			"ms AND 2020[epdat]",
			"ms AND 2020[ppdat]",
			"ms AND 2020[Date - Publication]",
			"ms AND 2020[Date - Create]",
			"ms AND 2020 [ dp ]",
			"ms AND last 5 years",
			"ms AND Last 30 Days",
			"ms AND last 1 month",
		):
			self.assertTrue(PUBMED_DATE_CLAUSE.search(query), query)

	def test_ordinary_searches_are_not_flagged(self):
		for query in (
			'"multiple sclerosis"[MeSH Terms] AND (remyelination OR myelin repair)',
			"dp therapy[tiab]",
			'"last day of treatment"[tiab]',
			"cladribine[Title/Abstract] AND lr[tiab]",
		):
			self.assertFalse(PUBMED_DATE_CLAUSE.search(query), query)
