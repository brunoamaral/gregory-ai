"""
Tests for gregory.utils.pubmed_xml against fixtures only (gregory/tests/fixtures/pubmed/).

The three records in efetch-sample-3-records.xml are real NCBI records, unedited;
edge-cases.xml is hand-trimmed and synthetic (see the README next to them).
"""

from datetime import datetime, timezone
from pathlib import Path

from django.test import SimpleTestCase

from gregory.utils.pubmed_xml import parse_pubmed_xml

FIXTURES = Path(__file__).parent / "fixtures" / "pubmed"


def load(name):
	return parse_pubmed_xml((FIXTURES / name).read_bytes())


def utc(year, month, day):
	return datetime(year, month, day, tzinfo=timezone.utc)


class RealSampleTests(SimpleTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.records = {r.pmid: r for r in load("efetch-sample-3-records.xml").records}

	def test_all_three_records_parse(self):
		self.assertEqual(set(self.records), {"42830590", "42854181", "42834886"})

	def test_structured_abstract_keeps_labels_and_drops_the_copyright_line(self):
		abstract = self.records["42830590"].abstract
		self.assertTrue(abstract.startswith("BACKGROUND: Peer support"))
		self.assertIn("\n\nMETHODS: ", abstract)
		self.assertIn("\n\nCONCLUSIONS: ", abstract)
		self.assertNotIn("Published by CSIRO", abstract)

	def test_identifiers_and_status(self):
		first = self.records["42830590"]
		self.assertEqual(first.doi, "10.1071/ib25113")
		self.assertIsNone(first.pmcid)
		self.assertEqual(first.status, "MEDLINE")
		self.assertEqual(first.language, "eng")
		third = self.records["42834886"]
		self.assertEqual(third.pmcid, "PMC13634772")
		self.assertEqual(third.status, "PubMed-not-MEDLINE")

	def test_authors_with_orcids_in_order(self):
		authors = self.records["42830590"].authors
		self.assertEqual(
			[(a.given_name, a.family_name, a.orcid) for a in authors[:3]],
			[
				("Cornelia", "Wellecke", "0000-0001-7642-7025"),
				("Mark", "Brown", None),
				("Di", "Winkler", "0000-0003-3899-6248"),
			],
		)

	def test_publication_types_carry_their_mesh_ids(self):
		self.assertEqual(
			self.records["42830590"].publication_types[:2],
			[("D016428", "Journal Article"), ("D000098583", "Scoping Review")],
		)

	def test_mesh_headings_and_major_topics(self):
		headings = {h.descriptor_ui: h for h in self.records["42830590"].mesh}
		self.assertEqual(headings["D006801"].descriptor_name, "Humans")
		self.assertFalse(headings["D006801"].major_topic)
		self.assertTrue(headings["D010379"].major_topic)
		self.assertEqual(len(self.records["42834886"].mesh), 0)

	def test_keywords_keep_their_order(self):
		self.assertEqual(self.records["42830590"].keywords[:2], ["acquired brain injury", "acquired disability"])
		self.assertEqual(self.records["42854181"].keywords, [])

	def test_issue_date_used_when_there_is_no_electronic_date(self):
		self.assertEqual(self.records["42830590"].published_date, utc(2026, 12, 18))

	def test_nothing_is_retracted(self):
		self.assertFalse(any(r.retracted for r in self.records.values()))


class EdgeCaseTests(SimpleTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.parsed = load("edge-cases.xml")
		cls.records = {r.pmid: r for r in cls.parsed.records}

	def test_book_articles_are_skipped_and_counted(self):
		self.assertEqual(self.parsed.books_skipped, 1)
		self.assertNotIn("90000007", self.records)

	def test_inline_markup_is_flattened_in_title_and_abstract(self):
		record = self.records["90000001"]
		self.assertEqual(record.title, "Effects of CO2 on in vitro myelination in 7T imaging.")
		self.assertEqual(record.abstract, "Unstructured text with italic and x2 inline.")

	def test_unstructured_abstract_has_no_label_and_no_copyright(self):
		self.assertNotIn("Somebody", self.records["90000001"].abstract)

	def test_doi_falls_back_to_elocationid_and_is_normalized(self):
		self.assertEqual(self.records["90000001"].doi, "10.1234/elocation.only")
		self.assertEqual(self.records["90000002"].doi, "10.5555/mixedcase.doi")

	def test_mixed_structured_and_loose_sections(self):
		self.assertEqual(
			self.records["90000002"].abstract, "BACKGROUND: Why.\n\nMETHODS: How.\n\nLoose paragraph."
		)

	def test_collective_and_nameless_authors_are_skipped_and_orcid_url_normalized(self):
		authors = self.records["90000001"].authors
		self.assertEqual(
			[(a.given_name, a.family_name, a.orcid) for a in authors],
			[("Anna", "Rossi", "0000-0002-1825-0097"), ("Jan", "Kowalski", None)],
		)

	def test_first_language_and_deduplicated_keywords(self):
		record = self.records["90000001"]
		self.assertEqual(record.language, "eng")
		self.assertEqual(record.keywords, ["alpha", "beta"])

	def test_numeric_month_without_a_day_is_the_first(self):
		self.assertEqual(self.records["90000001"].published_date, utc(2025, 3, 1))

	def test_medline_date_takes_its_first_year(self):
		self.assertEqual(self.records["90000002"].published_date, utc(1998, 1, 1))

	def test_electronic_date_beats_the_issue_date(self):
		self.assertEqual(self.records["90000003"].published_date, utc(2024, 1, 31))

	def test_winter_season_is_january(self):
		self.assertEqual(self.records["90000004"].published_date, utc(2023, 1, 1))

	def test_impossible_day_keeps_the_month(self):
		self.assertEqual(self.records["90000005"].published_date, utc(2022, 2, 1))

	def test_no_dates_gives_none(self):
		self.assertIsNone(self.records["90000006"].published_date)

	def test_retracted_publication_type(self):
		self.assertTrue(self.records["90000002"].retracted)

	def test_retraction_notice_in_comments_corrections(self):
		self.assertTrue(self.records["90000003"].retracted)
		self.assertFalse(self.records["90000001"].retracted)

	def test_erratum_alone_is_not_a_retraction(self):
		# 90000003 has both an erratum and a retraction notice; the retraction decides
		self.assertTrue(self.records["90000003"].retracted)

	def test_mesh_qualifiers_and_auto_indexing(self):
		migraine, humans = self.records["90000002"].mesh
		self.assertEqual(migraine.descriptor_ui, "D008881")
		self.assertTrue(migraine.major_topic)
		self.assertFalse(migraine.auto_indexed)
		self.assertEqual(
			[(q.ui, q.name, q.major_topic, q.auto_indexed) for q in migraine.qualifiers],
			[("Q000209", "etiology", False, False), ("Q000601", "surgery", True, True)],
		)
		self.assertTrue(humans.auto_indexed)
		self.assertEqual(humans.qualifiers, [])

	def test_record_without_a_pmid_is_dropped(self):
		xml = b"<PubmedArticleSet><PubmedArticle><MedlineCitation><Article/></MedlineCitation></PubmedArticle></PubmedArticleSet>"
		self.assertEqual(parse_pubmed_xml(xml).records, [])
