"""
Tests for the boolean search parser (api.utils.search.build_search_q) and its
integration with ArticleFilter and TrialFilter via the ?search= parameter.
"""

from django.db.models import Q
from django.test import TestCase, RequestFactory
from django.utils import timezone

from api.filters import ArticleFilter, TrialFilter
from api.utils.search import build_search_q, _tokenize
from gregory.models import Articles, Trials


# ---------------------------------------------------------------------------
# Unit tests: parser internals
# ---------------------------------------------------------------------------

class TokenizeTests(TestCase):
	def test_single_word(self):
		self.assertEqual(_tokenize("myelin"), [("WORD", "myelin")])

	def test_or_keyword(self):
		self.assertEqual(
			_tokenize("myelin OR parkinson"),
			[("WORD", "myelin"), ("OR", "OR"), ("WORD", "parkinson")],
		)

	def test_and_keyword(self):
		self.assertEqual(
			_tokenize("a AND b"),
			[("WORD", "a"), ("AND", "AND"), ("WORD", "b")],
		)

	def test_not_keyword(self):
		self.assertEqual(_tokenize("NOT cancer"), [("NOT", "NOT"), ("WORD", "cancer")])

	def test_dash_negation(self):
		self.assertEqual(_tokenize("-cancer"), [("NOT", "-"), ("WORD", "cancer")])

	def test_quoted_phrase(self):
		self.assertEqual(_tokenize('"myelin repair"'), [("PHRASE", "myelin repair")])

	def test_parens(self):
		self.assertEqual(
			_tokenize("(a OR b)"),
			[("LP", "("), ("WORD", "a"), ("OR", "OR"), ("WORD", "b"), ("RP", ")")],
		)

	def test_lowercase_or_is_word(self):
		# lowercase 'or' must NOT be treated as the OR operator
		tokens = _tokenize("stem or cells")
		self.assertNotIn(("OR", "or"), tokens)
		self.assertIn(("WORD", "or"), tokens)

	def test_unbalanced_quote(self):
		# unbalanced opening quote: rest of string becomes a PHRASE token
		toks = _tokenize('"myelin repair')
		self.assertEqual(len(toks), 1)
		self.assertEqual(toks[0][0], "PHRASE")


class BuildSearchQTests(TestCase):
	def test_empty_string_returns_none(self):
		self.assertIsNone(build_search_q(""))
		self.assertIsNone(build_search_q("   "))
		self.assertIsNone(build_search_q(None))

	def test_single_term_produces_q(self):
		q = build_search_q("myelin")
		self.assertIsInstance(q, Q)
		# Should filter on utitle and usummary
		self.assertIn("utitle__contains", str(q))
		self.assertIn("usummary__contains", str(q))

	def test_single_term_is_uppercased(self):
		q = build_search_q("myelin")
		self.assertIn("MYELIN", str(q))

	def test_two_bare_terms_are_anded(self):
		q_both = build_search_q("myelin repair")
		q_a = build_search_q("myelin")
		q_b = build_search_q("repair")
		# AND means the combined Q is stricter than either alone
		self.assertEqual(str(q_both), str(q_a & q_b))

	def test_or_operator(self):
		q = build_search_q("myelin OR parkinson")
		q_a = build_search_q("myelin")
		q_b = build_search_q("parkinson")
		self.assertEqual(str(q), str(q_a | q_b))

	def test_not_operator(self):
		q = build_search_q("myelin NOT cancer")
		self.assertIn("NOT", str(q))

	def test_dash_negation(self):
		q_dash = build_search_q("myelin -cancer")
		q_not = build_search_q("myelin NOT cancer")
		self.assertEqual(str(q_dash), str(q_not))

	def test_quoted_phrase_different_from_bare(self):
		q_phrase = build_search_q('"myelin repair"')
		q_and = build_search_q("myelin repair")
		# Phrase match on "MYELIN REPAIR" vs AND of individual terms
		self.assertNotEqual(str(q_phrase), str(q_and))
		self.assertIn("MYELIN REPAIR", str(q_phrase))

	def test_grouping(self):
		q = build_search_q("(myelin OR repair) -tumor")
		self.assertIsInstance(q, Q)
		self.assertIn("NOT", str(q))

	def test_malformed_deep_parens_does_not_raise(self):
		# Should not raise even with pathological input
		q = build_search_q("((((((((((((((((((((")
		self.assertIsInstance(q, Q)

	def test_stray_operators_do_not_raise(self):
		q = build_search_q("a OR OR b")
		self.assertIsInstance(q, Q)

	def test_long_junk_does_not_raise(self):
		q = build_search_q("x" * 500)
		self.assertIsInstance(q, Q)

	def test_all_operators_junk(self):
		q = build_search_q("OR OR NOT AND")
		# Falls back to phrase match or returns something; must not raise
		# (may be None if parse returns nothing — but fallback ensures Q)
		self.assertIsInstance(q, Q)


# ---------------------------------------------------------------------------
# Integration tests: ArticleFilter ?search= via the filter class directly
# ---------------------------------------------------------------------------

class ArticleFilterSearchTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.article_myelin = Articles.objects.create(
			title="Myelin repair mechanisms",
			summary="Study of remyelination pathways.",
			link="https://example.com/a1",
		)
		self.article_parkinson = Articles.objects.create(
			title="Parkinson disease progression",
			summary="Dopamine pathways in Parkinson.",
			link="https://example.com/a2",
		)
		self.article_both = Articles.objects.create(
			title="Myelin loss in Parkinson disease",
			summary="Relationship between myelin and parkinson.",
			link="https://example.com/a3",
		)
		self.article_cancer = Articles.objects.create(
			title="Cancer treatment advances",
			summary="New approaches to tumor treatment.",
			link="https://example.com/a4",
		)

	def _filter(self, search):
		qs = Articles.objects.all()
		req = self.factory.get("/articles/", {"search": search})
		f = ArticleFilter(req.GET, queryset=qs, request=req)
		return f.qs

	def test_single_term_matches_substring(self):
		# "myelin" must match both article_myelin and article_both, not the others
		result = self._filter("myelin")
		ids = set(result.values_list("article_id", flat=True))
		self.assertIn(self.article_myelin.article_id, ids)
		self.assertIn(self.article_both.article_id, ids)
		self.assertNotIn(self.article_parkinson.article_id, ids)
		self.assertNotIn(self.article_cancer.article_id, ids)

	def test_two_bare_terms_are_anded(self):
		# "myelin parkinson" → must contain BOTH; only article_both qualifies
		result = self._filter("myelin parkinson")
		ids = set(result.values_list("article_id", flat=True))
		self.assertIn(self.article_both.article_id, ids)
		self.assertNotIn(self.article_myelin.article_id, ids)
		self.assertNotIn(self.article_parkinson.article_id, ids)

	def test_or_broadens_results(self):
		# "myelin OR parkinson" → union; all three should appear
		result = self._filter("myelin OR parkinson")
		ids = set(result.values_list("article_id", flat=True))
		self.assertIn(self.article_myelin.article_id, ids)
		self.assertIn(self.article_parkinson.article_id, ids)
		self.assertIn(self.article_both.article_id, ids)
		self.assertNotIn(self.article_cancer.article_id, ids)

	def test_not_excludes(self):
		# "myelin -cancer" → myelin articles, cancer excluded (none here overlap, but verify myelin present)
		result = self._filter("myelin -cancer")
		ids = set(result.values_list("article_id", flat=True))
		self.assertIn(self.article_myelin.article_id, ids)
		self.assertNotIn(self.article_cancer.article_id, ids)

	def test_quoted_phrase_contiguous(self):
		# "myelin repair" as phrase → only article_myelin (title: "Myelin repair mechanisms")
		result = self._filter('"myelin repair"')
		ids = set(result.values_list("article_id", flat=True))
		self.assertIn(self.article_myelin.article_id, ids)
		# article_both has "myelin" and "parkinson" but not the phrase "myelin repair"
		self.assertNotIn(self.article_both.article_id, ids)

	def test_empty_search_returns_all(self):
		result = self._filter("")
		self.assertEqual(result.count(), Articles.objects.count())

	def test_malformed_input_returns_200_not_500(self):
		# These must not raise
		self._filter("(((")
		self._filter("OR")
		self._filter("a OR OR b")
		self._filter('"unbalanced')


# ---------------------------------------------------------------------------
# Integration tests: TrialFilter ?search=
# ---------------------------------------------------------------------------

class TrialFilterSearchTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.trial_myelin = Trials.objects.create(
			title="Myelin sheath restoration trial",
			summary="Testing remyelination agents.",
			link="https://example.com/t1",
			published_date=timezone.now(),
		)
		self.trial_parkinson = Trials.objects.create(
			title="Parkinson motor function study",
			summary="Dopamine replacement therapy.",
			link="https://example.com/t2",
			published_date=timezone.now(),
		)
		self.trial_both = Trials.objects.create(
			title="Parkinson and myelin research",
			summary="Combined neurodegeneration study.",
			link="https://example.com/t3",
			published_date=timezone.now(),
		)

	def _filter(self, search):
		qs = Trials.objects.all()
		req = self.factory.get("/trials/", {"search": search})
		f = TrialFilter(req.GET, queryset=qs, request=req)
		return f.qs

	def test_single_term(self):
		result = self._filter("myelin")
		ids = set(result.values_list("trial_id", flat=True))
		self.assertIn(self.trial_myelin.trial_id, ids)
		self.assertIn(self.trial_both.trial_id, ids)
		self.assertNotIn(self.trial_parkinson.trial_id, ids)

	def test_and_semantics(self):
		result = self._filter("myelin parkinson")
		ids = set(result.values_list("trial_id", flat=True))
		self.assertIn(self.trial_both.trial_id, ids)
		self.assertNotIn(self.trial_myelin.trial_id, ids)
		self.assertNotIn(self.trial_parkinson.trial_id, ids)

	def test_or_semantics(self):
		result = self._filter("myelin OR parkinson")
		ids = set(result.values_list("trial_id", flat=True))
		self.assertIn(self.trial_myelin.trial_id, ids)
		self.assertIn(self.trial_parkinson.trial_id, ids)
		self.assertIn(self.trial_both.trial_id, ids)

	def test_malformed_does_not_raise(self):
		self._filter("(((")
		self._filter("OR NOT")
