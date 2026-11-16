"""Tests for gregory.services.openalex_authorships and the author merge's authorship handling."""

import json
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from gregory.models import ArticleAuthorship, Articles, Authors, Institution
from gregory.services.author_merge import merge_authors
from gregory.services.openalex_authorships import (
	apply_openalex_work,
	match_authorships,
	recompute_author_openalex_ids,
)
from gregory.utils.openalex import OpenAlexAuthorship, parse_work

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "openalex" / "works-sample.json").read_text())


def person(author_id, given, family, orcid=None):
	return SimpleNamespace(author_id=author_id, given_name=given, family_name=family, ORCID=orcid)


def oa(name, index=0, orcid="", position="middle", author_id="A1", institutions=()):
	return OpenAlexAuthorship(
		position=position,
		index=index,
		is_corresponding=False,
		openalex_author_id=author_id,
		name=name,
		orcid=orcid,
		institutions=list(institutions),
	)


class MatchingTests(SimpleTestCase):
	def pairs(self, authors, authorships):
		return [(a.author_id, s.index) for a, s, _ in match_authorships(authors, authorships)]

	def test_orcid_match_wins_over_a_different_name(self):
		authors = [person(1, "Jane", "Smith", "https://orcid.org/0000-0001-2345-6789")]
		self.assertEqual(
			self.pairs(authors, [oa("J. Q. Public", orcid="0000-0001-2345-6789")]), [(1, 0)]
		)

	def test_orcid_comparison_ignores_the_url_form(self):
		authors = [person(1, "Jane", "Smith", "http://orcid.org/0000-0001-234X-6789")]
		self.assertEqual(self.pairs(authors, [oa("Someone Else", orcid="0000-0001-234X-6789")]), [(1, 0)])

	def test_name_match_ignores_accents_and_hyphens(self):
		authors = [person(1, "Jean-Luc", "Müller"), person(2, "Zoë", "Žižek-Smith")]
		matches = self.pairs(
			authors, [oa("Jean Luc Muller", 0), oa("Zoe Zizek Smith", 1)]
		)
		# "Zizek Smith" is two words: the last-word rule fails, the compound rule finds it.
		self.assertEqual(matches, [(1, 0), (2, 1)])

	def test_compound_family_name(self):
		authors = [person(1, "Ana Maria", "De Souza")]
		self.assertEqual(self.pairs(authors, [oa("Ana Maria De Souza")]), [(1, 0)])

	def test_two_authors_with_the_same_family_name_and_initial_match_neither(self):
		authors = [person(1, "John", "Smith"), person(2, "Jane", "Smith")]
		self.assertEqual(self.pairs(authors, [oa("J Smith")]), [])

	def test_a_different_initial_is_not_a_match(self):
		authors = [person(1, "John", "Smith"), person(2, "Mary", "Smith")]
		self.assertEqual(self.pairs(authors, [oa("Mary Smith")]), [(2, 0)])

	def test_each_author_is_used_once(self):
		authors = [person(1, "John", "Smith")]
		self.assertEqual(self.pairs(authors, [oa("John Smith", 0), oa("J. Smith", 1)]), [(1, 0)])

	def test_an_orcid_match_used_once_does_not_match_twice(self):
		authors = [person(1, "John", "Smith", "0000-0001-0000-0001")]
		matches = self.pairs(
			authors,
			[oa("John Smith", 0, orcid="0000-0001-0000-0001"), oa("John Smith", 1, orcid="0000-0001-0000-0001")],
		)
		self.assertEqual(matches, [(1, 0)])

	def test_an_openalex_author_with_no_counterpart_is_left_out(self):
		authors = [person(1, "John", "Smith")]
		self.assertEqual(self.pairs(authors, [oa("Nobody Known", 0), oa("John Smith", 1)]), [(1, 1)])

	def test_the_index_is_the_authorships_place(self):
		authors = [person(1, "John", "Smith")]
		(author, authorship, index), = match_authorships(authors, [oa("John Smith", 7)])
		self.assertEqual(index, 7)


def make_article(doi="10.1/a", title="A", **kwargs):
	return Articles.objects.create(title=title, link=f"https://ex.org/{title}", doi=doi, **kwargs)


def make_author(given, family, orcid=None):
	return Authors.objects.create(given_name=given, family_name=family, ORCID=orcid)


class ApplyTests(TestCase):
	def sample_article(self):
		"""The first sample work with a database author for four of its six authors."""
		article = make_article(doi="10.1186/s12974-023-02861-3")
		self.proust = make_author("Alizé", "Proust", "https://orcid.org/0000-0003-2809-2835")
		self.queval = make_author("Christophe", "Queval")
		self.harvey = make_author("Ruth", "Harvey")
		self.wilkinson = make_author("Robert John", "Wilkinson")
		for author in (self.proust, self.queval, self.harvey, self.wilkinson):
			article.authors.add(author)
		return article

	def test_applies_rows_institutions_and_article_fields(self):
		article = self.sample_article()
		result = apply_openalex_work(article, parse_work(SAMPLE["results"][0]))

		self.assertEqual(result.matched, 4)
		self.assertEqual(result.unmatched, 2)
		rows = {r.author_id: r for r in ArticleAuthorship.objects.filter(article=article)}
		self.assertEqual(rows[self.proust.pk].position, "first")
		self.assertEqual(rows[self.proust.pk].author_index, 0)
		self.assertTrue(rows[self.proust.pk].is_corresponding)
		self.assertEqual(rows[self.proust.pk].openalex_author_id, "A5086435829")
		self.assertEqual(rows[self.wilkinson.pk].position, "last")
		self.assertEqual(rows[self.wilkinson.pk].author_index, 5)
		self.assertIn("Francis Crick Institute", rows[self.proust.pk].raw_affiliation)
		institution = Institution.objects.get(openalex_id="I2801081054")
		self.assertEqual(institution.name, "The Francis Crick Institute")
		self.assertEqual(institution.ror, "04tnbqb63")
		self.assertEqual(institution.country_code, "GB")
		self.assertEqual(list(rows[self.proust.pk].institutions.all()), [institution])
		# Crick is shared by all four; Wilkinson has four more.
		self.assertEqual(result.institutions_created, 5)

		article.refresh_from_db()
		self.assertEqual(article.openalex_id, "W4385549237")
		self.assertIsNotNone(article.openalex_checked_at)
		self.assertEqual(sorted(result.author_ids), sorted(rows))

	def test_the_articles_authors_link_is_untouched(self):
		article = self.sample_article()
		before = sorted(article.authors.values_list("pk", flat=True))
		apply_openalex_work(article, parse_work(SAMPLE["results"][0]))
		self.assertEqual(sorted(article.authors.values_list("pk", flat=True)), before)

	def test_rows_are_replaced_and_no_stale_ones_remain(self):
		article = self.sample_article()
		stale = make_author("Old", "Person")
		article.authors.add(stale)
		ArticleAuthorship.objects.create(
			article=article, author=stale, position="middle", author_index=3, openalex_author_id="A9"
		)
		result = apply_openalex_work(article, parse_work(SAMPLE["results"][0]))
		self.assertFalse(ArticleAuthorship.objects.filter(article=article, author=stale).exists())
		self.assertEqual(ArticleAuthorship.objects.filter(article=article).count(), 4)
		self.assertIn(stale.pk, result.author_ids)  # its OpenAlex ID needs recomputing

		apply_openalex_work(article, parse_work(SAMPLE["results"][0]))
		self.assertEqual(ArticleAuthorship.objects.filter(article=article).count(), 4)

	def test_institutions_are_upserted_and_renamed(self):
		Institution.objects.create(openalex_id="I2801081054", name="Old name", country_code="US", type="other")
		article = self.sample_article()
		result = apply_openalex_work(article, parse_work(SAMPLE["results"][0]))
		self.assertEqual(Institution.objects.filter(openalex_id="I2801081054").count(), 1)
		institution = Institution.objects.get(openalex_id="I2801081054")
		self.assertEqual(institution.name, "The Francis Crick Institute")
		self.assertEqual(institution.country_code, "GB")
		self.assertEqual(institution.type, "facility")
		self.assertEqual(result.institutions_created, 4)  # the renamed one is not counted

	def test_markers_are_cleared_without_an_article_history_row(self):
		article = self.sample_article()
		Articles.objects.filter(pk=article.pk).update(
			openalex_attempts=3, openalex_next_check=datetime(2030, 1, 1, tzinfo=dt_timezone.utc)
		)
		article.refresh_from_db()
		history_before = article.history.count()
		apply_openalex_work(article, parse_work(SAMPLE["results"][0]))
		article.refresh_from_db()
		self.assertEqual(article.openalex_attempts, 0)
		self.assertIsNone(article.openalex_next_check)
		self.assertEqual(article.history.count(), history_before)

	def test_an_institution_shared_by_two_authors_is_stored_once(self):
		article = self.sample_article()
		apply_openalex_work(article, parse_work(SAMPLE["results"][0]))
		self.assertEqual(Institution.objects.count(), 5)
		through = ArticleAuthorship.institutions.through
		# Three authors with Crick alone, and Wilkinson with Crick and four others.
		self.assertEqual(through.objects.count(), 3 + 5)

	def test_authors_default_to_the_articles_own(self):
		article = self.sample_article()
		result = apply_openalex_work(article, parse_work(SAMPLE["results"][0]), authors=None)
		self.assertEqual(result.matched, 4)


class RecomputeTests(TestCase):
	def link(self, article, author, oa_id, index=0):
		return ArticleAuthorship.objects.create(
			article=article, author=author, position="middle", author_index=index, openalex_author_id=oa_id
		)

	def test_the_most_frequent_openalex_id_wins(self):
		author = make_author("A", "One")
		for n, oa_id in enumerate(["A1", "A2", "A2"]):
			self.link(make_article(doi=f"10.1/m{n}", title=f"M{n}"), author, oa_id)
		self.assertEqual(recompute_author_openalex_ids([author.pk]), 1)
		author.refresh_from_db()
		self.assertEqual(author.openalex_id, "A2")

	def test_a_tie_goes_to_the_most_recent_article(self):
		author = make_author("A", "One")
		old = make_article(doi="10.1/old", title="Old", published_date=datetime(2015, 1, 1, tzinfo=dt_timezone.utc))
		new = make_article(doi="10.1/new", title="New", published_date=datetime(2024, 1, 1, tzinfo=dt_timezone.utc))
		self.link(old, author, "A_OLD")
		self.link(new, author, "A_NEW")
		recompute_author_openalex_ids([author.pk])
		author.refresh_from_db()
		self.assertEqual(author.openalex_id, "A_NEW")

	def test_blank_ids_are_ignored_and_authors_without_rows_get_null(self):
		author = make_author("A", "One")
		other = make_author("B", "Two")
		other.openalex_id = "A_STALE"
		other.save()
		Authors.objects.filter(pk=other.pk).update(openalex_id="A_STALE")
		self.link(make_article(), author, "")
		recompute_author_openalex_ids([author.pk, other.pk])
		author.refresh_from_db()
		other.refresh_from_db()
		self.assertIsNone(author.openalex_id)
		self.assertIsNone(other.openalex_id)

	def test_unchanged_authors_are_not_rewritten(self):
		author = make_author("A", "One")
		self.link(make_article(), author, "A1")
		self.assertEqual(recompute_author_openalex_ids([author.pk]), 1)
		self.assertEqual(recompute_author_openalex_ids([author.pk]), 0)

	def test_no_author_history_row_is_written(self):
		author = make_author("A", "One")
		self.link(make_article(), author, "A1")
		before = author.history.count()
		recompute_author_openalex_ids([author.pk])
		self.assertEqual(author.history.count(), before)


class MergeAuthorshipTests(TestCase):
	def setUp(self):
		self.keep = make_author("Jane", "Smith")
		self.remove = make_author("J", "Smith")

	def authorship(self, article, author, oa_id, position="middle"):
		return ArticleAuthorship.objects.create(
			article=article, author=author, position=position, author_index=1, openalex_author_id=oa_id
		)

	def test_authorships_move_to_the_kept_author(self):
		article = make_article()
		article.authors.add(self.remove)
		row = self.authorship(article, self.remove, "A7", "last")
		merge_authors(self.keep, [self.remove])
		row.refresh_from_db()
		self.assertEqual(row.author_id, self.keep.pk)
		self.keep.refresh_from_db()
		self.assertEqual(self.keep.openalex_id, "A7")

	def test_a_collision_keeps_the_kept_authors_row(self):
		article = make_article()
		article.authors.add(self.keep, self.remove)
		kept_row = self.authorship(article, self.keep, "A1", "first")
		self.authorship(article, self.remove, "A2", "last")
		other = make_article(doi="10.1/b", title="B")
		other.authors.add(self.remove)
		moved_row = self.authorship(other, self.remove, "A1", "last")

		merge_authors(self.keep, [self.remove])

		rows = ArticleAuthorship.objects.filter(author=self.keep)
		self.assertEqual(rows.count(), 2)
		kept_row.refresh_from_db()
		self.assertEqual((kept_row.position, kept_row.openalex_author_id), ("first", "A1"))
		moved_row.refresh_from_db()
		self.assertEqual(moved_row.author_id, self.keep.pk)
		self.assertFalse(ArticleAuthorship.objects.filter(openalex_author_id="A2").exists())
		self.keep.refresh_from_db()
		self.assertEqual(self.keep.openalex_id, "A1")
