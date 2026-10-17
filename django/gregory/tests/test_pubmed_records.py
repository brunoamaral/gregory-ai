"""Tests for apply_pubmed_record, the shared author helper and the truncation helper."""

from pathlib import Path

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from gregory.models import (
	ArticleMeshHeading,
	Articles,
	Authors,
	MeshTerm,
	PublicationType,
)
from gregory.services.pubmed_records import apply_pubmed_record, pubmed_link
from gregory.utils.authors import AMBIGUOUS, CREATED, EXISTING, UPDATED, get_or_create_author
from gregory.utils.pubmed_xml import parse_pubmed_xml
from gregory.utils.text_cleaning import is_summary_truncated

FIXTURES = Path(__file__).parent / "fixtures" / "pubmed"


def records(name):
	return {r.pmid: r for r in parse_pubmed_xml((FIXTURES / name).read_bytes()).records}


SAMPLE = records("efetch-sample-3-records.xml")
EDGE = records("edge-cases.xml")


def article(**kwargs):
	kwargs.setdefault("title", "Existing title")
	kwargs.setdefault("link", f"https://ex.org/{Articles.objects.count()}")
	return Articles.objects.create(**kwargs)


class IsSummaryTruncatedTests(SimpleTestCase):
	def test_endings(self):
		for text in ("cut off...", "cut off [...]", "cut off […]", "cut off…", "  cut off ...  "):
			self.assertTrue(is_summary_truncated(text), text)

	def test_complete_or_empty(self):
		for text in ("A full sentence.", "", None, "Mid ... dle text."):
			self.assertFalse(is_summary_truncated(text), text)


class GetOrCreateAuthorTests(TestCase):
	def test_orcid_creates_then_matches(self):
		author, status = get_or_create_author("Ana", "Silva", "0000-0001-0000-0001")
		self.assertEqual(status, CREATED)
		again, status = get_or_create_author("Ana", "Silva", "0000-0001-0000-0001")
		self.assertEqual((again.pk, status), (author.pk, EXISTING))

	def test_orcid_match_updates_a_changed_name(self):
		author, _ = get_or_create_author("Ana", "Silva", "0000-0001-0000-0002")
		renamed, status = get_or_create_author("Ana Maria", "Silva", "0000-0001-0000-0002")
		self.assertEqual((renamed.pk, status), (author.pk, UPDATED))
		author.refresh_from_db()
		self.assertEqual(author.given_name, "Ana Maria")

	def test_name_only_creates_then_matches(self):
		author, status = get_or_create_author("Jo", "Doe", None)
		self.assertEqual(status, CREATED)
		self.assertEqual(get_or_create_author("Jo", "Doe", None), (author, EXISTING))

	def test_duplicate_names_are_ambiguous(self):
		Authors.objects.create(given_name="Jo", family_name="Doe")
		Authors.objects.create(given_name="Jo", family_name="Doe")
		self.assertEqual(get_or_create_author("Jo", "Doe", None), (None, AMBIGUOUS))


class ApplyNewArticleTests(TestCase):
	def test_pubmed_data_is_authoritative_for_a_new_article(self):
		record = SAMPLE["42834886"]
		new = Articles.objects.create(title="placeholder", link=pubmed_link(record.pmid))
		result = apply_pubmed_record(new, record, is_new=True)
		new.refresh_from_db()
		self.assertEqual(new.title, record.title)
		self.assertEqual(new.summary, record.abstract)
		self.assertEqual((new.pmid, new.pmcid, new.doi), ("42834886", "PMC13634772", "10.1016/j.lana.2026.101640"))
		self.assertEqual(new.published_date, record.published_date)
		self.assertEqual(new.link, "https://pubmed.ncbi.nlm.nih.gov/42834886/")
		self.assertIn("pubmed.ncbi.nlm.nih.gov", new.links)
		self.assertEqual(new.pubmed_status, "PubMed-not-MEDLINE")
		self.assertIsNotNone(new.pubmed_checked_at)
		self.assertEqual(new.language, "eng")
		self.assertEqual(new.keywords, record.keywords)
		self.assertEqual(result.authors_added, len(record.authors))
		self.assertEqual(new.authors.count(), len(record.authors))

	def test_the_callers_placeholder_link_becomes_the_pubmed_page(self):
		record = SAMPLE["42834886"]
		new = Articles.objects.create(title="placeholder", link="https://elsewhere.org/x")
		apply_pubmed_record(new, record, is_new=True)
		new.refresh_from_db()
		self.assertEqual(new.link, "https://pubmed.ncbi.nlm.nih.gov/42834886/")

	def test_a_doi_held_by_another_article_is_not_taken(self):
		article(doi="10.1016/j.lana.2026.101640")
		record = SAMPLE["42834886"]
		new = Articles.objects.create(title="placeholder", link="https://ex.org/new")
		apply_pubmed_record(new, record, is_new=True)
		new.refresh_from_db()
		self.assertIsNone(new.doi)
		self.assertEqual(new.pmid, "42834886")


class ApplyExistingArticleTests(TestCase):
	record = SAMPLE["42830590"]

	def test_an_empty_summary_is_replaced(self):
		a = article(summary="")
		result = apply_pubmed_record(a, self.record, is_new=False)
		a.refresh_from_db()
		self.assertEqual(a.summary, self.record.abstract)
		self.assertTrue(result.summary_replaced)

	def test_a_truncated_summary_is_replaced(self):
		a = article(summary="Peer support for people with acquired neuro...")
		apply_pubmed_record(a, self.record, is_new=False)
		a.refresh_from_db()
		self.assertEqual(a.summary, self.record.abstract)

	def test_last_updated_moves_when_content_changes_and_not_otherwise(self):
		a = article(summary="")
		before = a.last_updated
		apply_pubmed_record(a, self.record, is_new=False)
		a.refresh_from_db()
		self.assertGreater(a.last_updated, before)
		settled = a.last_updated
		apply_pubmed_record(a, self.record, is_new=False)  # nothing left to fill
		a.refresh_from_db()
		self.assertEqual(a.last_updated, settled)

	def test_a_failure_part_way_rolls_the_whole_record_back(self):
		a = article(summary="")
		with patch("gregory.services.pubmed_records._replace_mesh", side_effect=RuntimeError("boom")):
			with self.assertRaises(RuntimeError):
				apply_pubmed_record(a, self.record, is_new=False)
		a.refresh_from_db()
		self.assertEqual(a.summary, "")
		self.assertIsNone(a.pmid)
		self.assertIsNone(a.pubmed_checked_at)
		self.assertEqual(a.publication_types.count(), 0)

	def test_a_stale_instance_is_re_read_before_deciding(self):
		a = article(summary="")
		stale = Articles.objects.get(pk=a.pk)
		Articles.objects.filter(pk=a.pk).update(summary="Written meanwhile by CrossRef.")
		result = apply_pubmed_record(stale, self.record, is_new=False)
		a.refresh_from_db()
		self.assertEqual(a.summary, "Written meanwhile by CrossRef.")
		self.assertFalse(result.summary_replaced)

	def test_a_full_summary_is_kept(self):
		a = article(summary="A complete abstract from CrossRef.")
		result = apply_pubmed_record(a, self.record, is_new=False)
		a.refresh_from_db()
		self.assertEqual(a.summary, "A complete abstract from CrossRef.")
		self.assertFalse(result.summary_replaced)

	def test_title_date_and_identifiers_only_fill_gaps(self):
		a = article(title="Kept title", pmid="1", pmcid="PMC1", doi="10.1/kept")
		a.published_date = EDGE["90000001"].published_date
		a.save()
		apply_pubmed_record(a, self.record, is_new=False)
		a.refresh_from_db()
		self.assertEqual((a.title, a.pmid, a.pmcid, a.doi), ("Kept title", "1", "PMC1", "10.1/kept"))
		self.assertEqual(a.published_date, EDGE["90000001"].published_date)

	def test_gaps_are_filled_and_doi_is_left_alone(self):
		a = article(title="")
		apply_pubmed_record(a, SAMPLE["42834886"], is_new=False)
		a.refresh_from_db()
		self.assertEqual(a.title, SAMPLE["42834886"].title)
		self.assertEqual(a.pmid, "42834886")
		self.assertEqual(a.pmcid, "PMC13634772")
		self.assertIsNone(a.doi)
		self.assertEqual(a.published_date, SAMPLE["42834886"].published_date)

	def test_a_pmid_held_by_another_article_is_logged_and_left_alone(self):
		article(pmid="42830590")
		a = article()
		with self.assertLogs("gregory.services.pubmed_records", level="WARNING"):
			result = apply_pubmed_record(a, self.record, is_new=False)
		a.refresh_from_db()
		self.assertIsNone(a.pmid)
		self.assertTrue(result.pmid_conflict)


class RetractionTests(TestCase):
	def test_retraction_is_set_and_never_cleared(self):
		a = article()
		result = apply_pubmed_record(a, EDGE["90000002"], is_new=False)
		a.refresh_from_db()
		self.assertTrue(a.retracted)
		self.assertTrue(result.retracted_set)
		# a later, clean record for the same article does not un-retract it
		apply_pubmed_record(a, SAMPLE["42830590"], is_new=False)
		a.refresh_from_db()
		self.assertTrue(a.retracted)

	def test_clean_record_leaves_retracted_false(self):
		a = article()
		apply_pubmed_record(a, SAMPLE["42830590"], is_new=False)
		a.refresh_from_db()
		self.assertFalse(a.retracted)


class ReplaceRelationsTests(TestCase):
	def test_publication_types_are_replaced_and_renamed_by_id(self):
		a = article()
		stale = PublicationType.objects.create(ui="D999", name="Stale Type")
		a.publication_types.add(stale)
		PublicationType.objects.create(ui="D016428", name="Old Name For Journal Article")
		apply_pubmed_record(a, SAMPLE["42830590"], is_new=False)
		self.assertEqual(
			set(a.publication_types.values_list("ui", flat=True)),
			{ui for ui, _ in SAMPLE["42830590"].publication_types},
		)
		self.assertEqual(PublicationType.objects.get(ui="D016428").name, "Journal Article")
		self.assertTrue(PublicationType.objects.filter(ui="D999").exists())

	def test_mesh_is_replaced_in_full_and_leaves_no_stale_rows(self):
		a = article()
		old = MeshTerm.objects.create(ui="D777", name="Stale", kind="descriptor")
		ArticleMeshHeading.objects.create(article=a, descriptor=old)
		apply_pubmed_record(a, EDGE["90000002"], is_new=False)
		rows = list(a.mesh_headings.select_related("descriptor", "qualifier").order_by("id"))
		self.assertNotIn("D777", [r.descriptor.ui for r in rows])
		self.assertEqual(
			[(r.descriptor.ui, r.qualifier.ui if r.qualifier else None, r.major_topic, r.auto_indexed) for r in rows],
			[
				("D008881", None, True, False),
				("D008881", "Q000209", False, False),
				("D008881", "Q000601", True, True),
				("D006801", None, False, True),
			],
		)
		# applying the same record again replaces, never duplicates
		apply_pubmed_record(a, EDGE["90000002"], is_new=False)
		self.assertEqual(a.mesh_headings.count(), 4)
		self.assertEqual(MeshTerm.objects.get(ui="Q000601").kind, "qualifier")

	def test_a_record_without_mesh_clears_old_headings(self):
		a = article()
		apply_pubmed_record(a, EDGE["90000002"], is_new=False)
		apply_pubmed_record(a, EDGE["90000004"], is_new=False)
		self.assertEqual(a.mesh_headings.count(), 0)
		self.assertEqual(a.publication_types.count(), 0)

	def test_mesh_term_names_are_overwritten_by_id(self):
		MeshTerm.objects.create(ui="D006801", name="Human beings", kind="descriptor")
		apply_pubmed_record(article(), EDGE["90000002"], is_new=False)
		self.assertEqual(MeshTerm.objects.get(ui="D006801").name, "Humans")


class AuthorsTests(TestCase):
	def test_authors_are_added_only_when_the_article_has_none(self):
		a = article()
		apply_pubmed_record(a, EDGE["90000001"], is_new=False)
		self.assertEqual(
			sorted(a.authors.values_list("family_name", flat=True)), ["Kowalski", "Rossi"]
		)

		b = article()
		existing = Authors.objects.create(given_name="Crossref", family_name="Author")
		b.authors.add(existing)
		result = apply_pubmed_record(b, EDGE["90000001"], is_new=False)
		self.assertEqual(list(b.authors.all()), [existing])
		self.assertEqual(result.authors_added, 0)

	def test_existing_author_is_matched_by_orcid(self):
		known = Authors.objects.create(given_name="Anna", family_name="Rossi", ORCID="0000-0002-1825-0097")
		a = article()
		apply_pubmed_record(a, EDGE["90000001"], is_new=False)
		self.assertIn(known, a.authors.all())
		self.assertEqual(Authors.objects.filter(ORCID="0000-0002-1825-0097").count(), 1)


class BookkeepingHistoryTests(TestCase):
	def test_a_refresh_with_no_content_change_adds_no_history_row(self):
		a = article(summary="Complete summary.", pmid="42830590", pmcid=None)
		apply_pubmed_record(a, SAMPLE["42830590"], is_new=False)  # fills gaps once
		before = a.history.count()
		apply_pubmed_record(a, SAMPLE["42830590"], is_new=False)
		self.assertEqual(a.history.count(), before)
