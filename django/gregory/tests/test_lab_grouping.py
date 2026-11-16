"""Tests for the pure lab grouping rules. No database: hand-built tuples and dicts."""

from datetime import date
from unittest import TestCase

from gregory.services.lab_grouping import compute_labs

TODAY = date(2026, 11, 16)  # the recency window starts after 2016
OLD = 2010  # no recency bonus
NEW = 2025


class World:
	"""Builds the inputs of compute_labs from a list of papers.

	``paper(last=H, others=[...])`` adds a paper whose last author is H; the others are
	first or middle. ``insts`` maps an author id to the institution ids printed for them
	on every paper of the world (override per paper with ``paper(insts={...})``).
	"""

	def __init__(self, **options):
		self.rows = []
		self.years = {}
		self.counts = {}
		self.info = {}  # author -> [openalex_id, orcid]
		self.total = {}  # author -> articles linked, when it differs from the papers added
		self.manual_heads = set()
		self.existing_heads = set()
		self.not_heads = set()
		self.insts = options.get("insts", {})
		self._next = 1

	def author(self, author, openalex_id=None, orcid=None, total=None):
		self.info[author] = [openalex_id, orcid]
		if total is not None:
			self.total[author] = total
		return author

	def paper(self, last, others=(), year=OLD, insts=None, n_authors=None):
		article = self._next
		self._next += 1
		insts = self.insts if insts is None else insts
		people = list(others) + [last]
		self.years[article] = year
		self.counts[article] = n_authors or len(people)
		for index, author in enumerate(people):
			position = "last" if author == last else "first" if index == 0 else "middle"
			self.rows.append((article, author, position, tuple(insts.get(author, ()))))
			self.info.setdefault(author, [None, None])
		return article

	def papers(self, n, last, others=(), **kwargs):
		return [self.paper(last, others, **kwargs) for _ in range(n)]

	def compute(self):
		linked = {}
		for _, author, _, _ in self.rows:
			linked[author] = linked.get(author, 0) + 1
		authors = {
			a: (oa, orcid, self.total.get(a, linked.get(a, 0)))
			for a, (oa, orcid) in self.info.items()
		}
		return compute_labs(
			self.rows,
			authors=authors,
			article_years=self.years,
			article_author_counts=self.counts,
			manual_heads=self.manual_heads,
			existing_heads=self.existing_heads,
			not_heads=self.not_heads,
			today=TODAY,
		)


class HeadTests(TestCase):
	def test_three_last_author_papers_make_a_head(self):
		w = World()
		w.papers(3, last=1, others=[2])
		self.assertEqual(set(w.compute().labs), {1})

	def test_two_last_author_papers_are_not_enough(self):
		w = World()
		w.papers(2, last=1, others=[2])
		self.assertEqual(w.compute().labs, {})

	def test_a_paper_with_31_authors_does_not_count(self):
		w = World()
		w.papers(2, last=1, others=[2])
		w.paper(last=1, others=[2], n_authors=31)
		self.assertEqual(w.compute().labs, {})

	def test_a_paper_with_30_authors_counts(self):
		w = World()
		w.papers(2, last=1, others=[2])
		w.paper(last=1, others=[2], n_authors=30)
		self.assertEqual(set(w.compute().labs), {1})

	def test_a_sole_author_paper_does_not_count(self):
		w = World()
		w.papers(2, last=1, others=[2])
		w.paper(last=1)
		self.assertEqual(w.compute().labs, {})

	def test_a_head_row_gets_the_head_role_and_their_paper_count(self):
		w = World()
		w.papers(3, last=1, others=[2], year=2024)
		w.paper(last=1, others=[2], year=2025)
		a = w.compute().assignments[1]
		self.assertEqual((a.role, a.head_id, a.shared_articles, a.last_shared_year), ("head", 1, 4, 2025))
		self.assertEqual(w.compute().labs[1].last_author_articles, 4)


class MembershipTests(TestCase):
	def lab_world(self, shared, extra_head_papers=3):
		w = World()
		w.papers(extra_head_papers, last=1, others=[9])
		w.papers(shared, last=1, others=[2])
		return w

	def test_two_shared_papers_make_a_member(self):
		a = self.lab_world(2).compute().assignments[2]
		self.assertEqual((a.role, a.head_id, a.shared_articles), ("member", 1, 2))

	def test_exactly_one_shared_paper_makes_a_collaborator(self):
		w = self.lab_world(1)
		w.author(2, total=2)  # one more article, not by a head
		a = w.compute().assignments[2]
		self.assertEqual((a.role, a.shared_articles), ("collaborator", 1))
		self.assertEqual(w.compute().labs[1].collaborator_count, 1)

	def test_no_shared_paper_gives_no_assignment(self):
		w = self.lab_world(0)
		w.paper(last=5, others=[2])  # their other paper has a non-head last author
		w.paper(last=5, others=[2])
		self.assertNotIn(2, w.compute().assignments)

	def test_a_person_with_one_article_in_total_is_left_out(self):
		w = self.lab_world(2)
		w.author(2, total=1)
		self.assertNotIn(2, w.compute().assignments)

	def test_the_counts_are_cached_on_the_lab(self):
		w = self.lab_world(2)
		w.papers(2, last=1, others=[3])
		w.paper(last=1, others=[4])
		w.paper(last=1, others=[4])
		w.papers(1, last=1, others=[7, 8])  # 7 and 8: one shared paper, but also elsewhere
		w.paper(last=6, others=[7])
		w.paper(last=6, others=[8])
		lab = w.compute().labs[1]
		self.assertEqual((lab.member_count, lab.collaborator_count), (4, 2))
		self.assertEqual(lab.head_rows, [1])


class RecencyTests(TestCase):
	def test_recent_papers_beat_more_old_ones(self):
		w = World()
		w.papers(3, last=1, others=[7], year=OLD)  # head A: three old shared papers
		w.papers(3, last=8, others=[7], year=NEW)  # head B: three recent shared papers
		# A needs its own head count: the three shared papers already make 3.
		w.papers(3, last=1, others=[9], year=OLD)
		w.papers(3, last=8, others=[9], year=NEW)
		# Person 5 shares 3 old papers with head 1 and 2 recent ones with head 8.
		w.papers(3, last=1, others=[5], year=OLD)
		w.papers(2, last=8, others=[5], year=NEW)
		self.assertEqual(w.compute().assignments[5].head_id, 8)

	def test_the_window_is_relative_to_today(self):
		w = World()
		w.papers(3, last=1, others=[9], year=2020)
		w.papers(3, last=8, others=[9], year=2020)
		# 2 papers in 2016 (the base year: no bonus) with head 1, 2 in 2017 with head 8.
		w.papers(2, last=1, others=[5], year=2016)
		w.papers(2, last=8, others=[5], year=2017)
		self.assertEqual(w.compute().assignments[5].head_id, 8)


class TieBreakTests(TestCase):
	def two_heads(self, w):
		w.papers(3, last=1, others=[91])
		w.papers(3, last=2, others=[92])

	def test_more_shared_papers_win_on_equal_score(self):
		w = World()
		self.two_heads(w)
		# Head 1: 2 papers in 2021 (weight 25 each = 50). Head 2: 5 old papers (10 each = 50).
		w.papers(2, last=1, others=[5], year=2021)
		w.papers(5, last=2, others=[5], year=OLD)
		self.assertEqual(w.compute().assignments[5].head_id, 2)

	def test_the_more_recent_paper_wins_on_equal_score_and_count(self):
		w = World()
		self.two_heads(w)
		# Head 1: 2026 (40) + 2016 (10) = 50. Head 2: two in 2021 (25 + 25) = 50.
		w.paper(last=1, others=[5], year=2026)
		w.paper(last=1, others=[5], year=2016)
		w.papers(2, last=2, others=[5], year=2021)
		self.assertEqual(w.compute().assignments[5].head_id, 1)

	def test_the_lowest_head_id_wins_a_complete_tie(self):
		w = World()
		self.two_heads(w)
		w.papers(2, last=2, others=[5])
		w.papers(2, last=1, others=[5])
		self.assertEqual(w.compute().assignments[5].head_id, 1)

	def test_two_shared_papers_beat_one_even_when_the_one_scores_more(self):
		w = World()
		self.two_heads(w)
		w.papers(2, last=1, others=[5], year=OLD)
		w.paper(last=2, others=[5], year=2026)  # 40 beats 20, but is only one paper
		self.assertEqual(w.compute().assignments[5].role, "member")
		self.assertEqual(w.compute().assignments[5].head_id, 1)


class InstitutionTests(TestCase):
	def world(self, insts):
		w = World(insts=insts)
		w.papers(3, last=1, others=[9])
		w.papers(2, last=1, others=[2])
		return w

	def test_same_institution_true_when_they_overlap(self):
		a = self.world({1: [10], 2: [10, 11]}).compute().assignments[2]
		self.assertIs(a.same_institution, True)

	def test_same_institution_false_when_they_do_not(self):
		a = self.world({1: [10], 2: [11]}).compute().assignments[2]
		self.assertIs(a.same_institution, False)

	def test_same_institution_is_null_when_one_side_has_none(self):
		a = self.world({1: [10]}).compute().assignments[2]
		self.assertIsNone(a.same_institution)

	def test_half_of_the_shared_papers_is_enough_for_core(self):
		w = World()
		w.papers(3, last=1, others=[9])
		w.paper(last=1, others=[2], insts={1: [10], 2: [10]})
		w.paper(last=1, others=[2], insts={1: [10], 2: [11]})
		self.assertIs(w.compute().assignments[2].same_institution, True)

	def test_core_is_counted_on_the_lab(self):
		w = World(insts={1: [10], 2: [10], 3: [11]})
		w.papers(3, last=1, others=[9])
		w.papers(2, last=1, others=[2, 3])
		lab = w.compute().labs[1]
		self.assertEqual((lab.member_count, lab.core_count), (3, 1))

	def test_the_labs_institution_is_the_heads_highest_weighted(self):
		w = World()
		# Three old papers at institution 10 (10 each) against one recent one at 11 (40).
		w.papers(3, last=1, others=[2], year=OLD, insts={1: [10]})
		w.paper(last=1, others=[2], year=2026, insts={1: [11]})
		self.assertEqual(w.compute().labs[1].institution_id, 11)

	def test_a_head_with_no_institutions_gives_a_lab_with_none(self):
		w = World()
		w.papers(3, last=1, others=[2])
		self.assertIsNone(w.compute().labs[1].institution_id)


class PersonTests(TestCase):
	def test_last_author_papers_over_two_rows_add_up_to_a_head(self):
		w = World()
		w.author(1, openalex_id="A1")
		w.author(2, openalex_id="A1")
		w.papers(2, last=1, others=[9])
		w.paper(last=2, others=[9])
		result = w.compute()
		self.assertEqual(set(result.labs), {1})
		self.assertEqual(result.labs[1].last_author_articles, 3)
		self.assertEqual(result.labs[1].head_rows, [1, 2])

	def test_both_rows_land_in_the_lab_with_the_head_role(self):
		w = World()
		w.author(1, openalex_id="A1")
		w.author(2, openalex_id="A1")
		w.papers(2, last=1, others=[9])
		w.paper(last=2, others=[9])
		result = w.compute()
		for row in (1, 2):
			self.assertEqual((result.assignments[row].role, result.assignments[row].head_id), ("head", 1))

	def test_the_head_row_is_the_one_with_more_last_author_papers(self):
		w = World()
		w.author(5, openalex_id="A1")
		w.author(6, openalex_id="A1")
		w.paper(last=5, others=[9])
		w.papers(2, last=6, others=[9])
		self.assertEqual(set(w.compute().labs), {6})

	def test_on_equal_papers_the_row_with_an_orcid_is_the_head_row(self):
		w = World()
		w.author(5, openalex_id="A1")
		w.author(6, openalex_id="A1", orcid="0000-0001-2345-6789")
		w.paper(last=5, others=[9])
		w.paper(last=6, others=[9])
		w.paper(last=5, others=[9])
		w.paper(last=6, others=[9])
		self.assertEqual(set(w.compute().labs), {6})

	def test_then_the_lowest_author_id(self):
		w = World()
		w.author(8, openalex_id="A1")
		w.author(7, openalex_id="A1")
		w.papers(2, last=8, others=[9])
		w.papers(2, last=7, others=[9])
		self.assertEqual(set(w.compute().labs), {7})

	def test_an_existing_head_row_is_kept(self):
		w = World()
		w.author(5, openalex_id="A1")
		w.author(6, openalex_id="A1")
		w.paper(last=5, others=[9])
		w.papers(3, last=6, others=[9])
		w.existing_heads = {5}  # fewer papers, but already the head
		self.assertEqual(set(w.compute().labs), {5})

	def test_a_member_with_two_rows_puts_both_in_the_lab(self):
		w = World()
		w.author(2, openalex_id="A2")
		w.author(3, openalex_id="A2")
		w.papers(3, last=1, others=[9])
		w.paper(last=1, others=[2])
		w.paper(last=1, others=[3])
		result = w.compute()
		self.assertEqual({(a.role, a.head_id) for k, a in result.assignments.items() if k in (2, 3)}, {("member", 1)})
		self.assertEqual(result.labs[1].member_count, 2)  # 9, and the one person behind rows 2 and 3

	def test_authors_without_an_openalex_id_are_their_own_person(self):
		w = World()
		w.papers(2, last=1, others=[9])
		w.paper(last=2, others=[9])
		self.assertEqual(w.compute().labs, {})


class ManualHeadTests(TestCase):
	def test_a_manual_heads_one_paper_still_makes_a_lab_with_computed_members(self):
		w = World()
		w.paper(last=1, others=[2])
		w.paper(last=1, others=[2])
		w.manual_heads = {1}
		w.existing_heads = {1}
		result = w.compute()
		self.assertEqual(set(result.labs), {1})
		self.assertEqual(result.assignments[2].role, "member")
		self.assertEqual(result.assignments[1].role, "head")

	def test_a_manual_head_with_no_papers_still_heads_a_lab(self):
		w = World()
		w.author(1)
		w.manual_heads = {1}
		self.assertEqual(set(w.compute().labs), {1})


class NotHeadTests(TestCase):
	"""An author a superuser placed by hand as a non-head never heads a computed lab."""

	def world(self):
		w = World()
		w.papers(3, last=1, others=[2])  # 1 would be a head, 2 their member
		w.papers(3, last=5, others=[1, 2])  # 5 is a head too; 1 and 2 publish under them
		return w

	def test_without_the_bar_they_head_their_own_lab(self):
		self.assertEqual(set(self.world().compute().labs), {1, 5})

	def test_a_hand_placed_member_does_not_head_a_lab(self):
		w = self.world()
		w.not_heads = {1}
		result = w.compute()
		self.assertEqual(set(result.labs), {5})
		self.assertEqual((result.assignments[2].head_id, result.assignments[2].role), (5, "member"))

	def test_any_row_of_the_person_bars_the_person(self):
		w = self.world()
		w.author(1, openalex_id="A1")
		w.author(7, openalex_id="A1")
		w.not_heads = {7}
		self.assertEqual(set(w.compute().labs), {5})

	def test_a_manual_labs_head_stays_a_head(self):
		w = self.world()
		w.not_heads = {1}
		w.manual_heads = {1}
		self.assertEqual(set(w.compute().labs), {1, 5})
