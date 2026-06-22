from django.test import TestCase

from api.filters import ArticleFilter
from gregory.models import Articles


class DoiFilterTestCase(TestCase):
	def setUp(self):
		self.a1 = Articles.objects.create(
			title="Article One", link="https://example.com/1", doi="10.1000/AAA"
		)
		self.a2 = Articles.objects.create(
			title="Article Two", link="https://example.com/2", doi="10.2000/bbb"
		)
		self.a3 = Articles.objects.create(
			title="Article Three", link="https://example.com/3", doi="10.3000/CCC"
		)
		self.no_doi = Articles.objects.create(
			title="Article No DOI", link="https://example.com/4"
		)

	def _filter(self, doi_param):
		f = ArticleFilter(
			data={"doi": doi_param},
			queryset=Articles.objects.all(),
		)
		return list(f.qs.values_list("article_id", flat=True))

	def test_single_doi_exact_case(self):
		ids = self._filter("10.1000/AAA")
		self.assertIn(self.a1.article_id, ids)
		self.assertEqual(len(ids), 1)

	def test_single_doi_case_insensitive(self):
		ids = self._filter("10.1000/aaa")
		self.assertIn(self.a1.article_id, ids)
		self.assertEqual(len(ids), 1)

	def test_multi_doi(self):
		ids = self._filter("10.1000/AAA,10.2000/bbb")
		self.assertIn(self.a1.article_id, ids)
		self.assertIn(self.a2.article_id, ids)
		self.assertNotIn(self.a3.article_id, ids)
		self.assertNotIn(self.no_doi.article_id, ids)

	def test_multi_doi_mixed_case(self):
		ids = self._filter("10.1000/aaa,10.3000/ccc")
		self.assertIn(self.a1.article_id, ids)
		self.assertIn(self.a3.article_id, ids)
		self.assertEqual(len(ids), 2)

	def test_multi_doi_with_spaces(self):
		ids = self._filter("10.1000/AAA , 10.2000/BBB")
		self.assertIn(self.a1.article_id, ids)
		self.assertIn(self.a2.article_id, ids)

	def test_nonexistent_doi_returns_empty(self):
		ids = self._filter("10.9999/nope")
		self.assertEqual(ids, [])

	def test_empty_value_returns_all(self):
		f = ArticleFilter(data={"doi": ""}, queryset=Articles.objects.all())
		self.assertEqual(f.qs.count(), Articles.objects.all().count())
