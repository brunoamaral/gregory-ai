"""PubMed fields on the articles API: serializer output, ?pmid= filter, query count."""

from django.contrib.sites.models import Site
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from api.tests.test_article_query_efficiency import _build_articles
from gregory.models import (
	ArticleMeshHeading,
	Articles,
	MeshTerm,
	PublicationType,
)


def add_pubmed_data(article, n):
	"""Give *article* distinct PubMed rows so an un-prefetched relation scales with n."""
	article.pmid = f"1000{n}"
	article.pmcid = f"PMC200{n}"
	article.keywords = ["alpha", "beta"]
	article.save()
	ptype, _ = PublicationType.objects.get_or_create(
		ui=f"D0{n:05d}", defaults={"name": f"Type {n}"}
	)
	article.publication_types.add(ptype)
	descriptor = MeshTerm.objects.create(ui=f"D9{n:05d}", name=f"Descriptor {n}", kind="descriptor")
	qualifier = MeshTerm.objects.create(ui=f"Q9{n:05d}", name=f"Qualifier {n}", kind="qualifier")
	ArticleMeshHeading.objects.create(article=article, descriptor=descriptor, major_topic=True)
	ArticleMeshHeading.objects.create(
		article=article, descriptor=descriptor, qualifier=qualifier, major_topic=False
	)


def get_articles(site, **params):
	return APIClient().get(
		"/articles/", {"page_size": 100, **params}, HTTP_ORIGIN=f"https://{site.domain}"
	)


class ArticlePubmedFieldsTests(TestCase):
	def test_serializer_exposes_the_pubmed_fields(self):
		site = _build_articles(1, "p")
		add_pubmed_data(Articles.objects.get(), 1)
		result = get_articles(site).data["results"][0]
		self.assertEqual(result["pmid"], "10001")
		self.assertEqual(result["pmcid"], "PMC2001")
		self.assertEqual(result["keywords"], ["alpha", "beta"])
		self.assertEqual(result["publication_types"], ["Type 1"])
		self.assertEqual(
			[dict(m) for m in result["mesh"]],
			[
				{"descriptor": "Descriptor 1", "qualifier": None, "major_topic": True},
				{"descriptor": "Descriptor 1", "qualifier": "Qualifier 1", "major_topic": False},
			],
		)

	def test_article_without_pubmed_data_serializes_empty_values(self):
		site = _build_articles(1, "q")
		result = get_articles(site).data["results"][0]
		self.assertIsNone(result["pmid"])
		self.assertEqual(result["publication_types"], [])
		self.assertEqual(result["mesh"], [])
		self.assertEqual(result["keywords"], [])

	def test_pmid_filter_is_an_exact_match(self):
		site = _build_articles(3, "r")
		for n, article in enumerate(Articles.objects.order_by("article_id"), start=1):
			add_pubmed_data(article, n)
		hit = get_articles(site, pmid="10002").data["results"]
		self.assertEqual([a["pmid"] for a in hit], ["10002"])
		self.assertEqual(get_articles(site, pmid="1000").data["results"], [])

	def test_query_count_does_not_scale_with_pubmed_rows(self):
		Site.objects.get_current()
		site_a = _build_articles(3, "s")
		for n, article in enumerate(Articles.objects.all(), start=1):
			add_pubmed_data(article, n)
		with CaptureQueriesContext(connection) as small:
			self.assertEqual(len(get_articles(site_a).data["results"]), 3)

		Articles.objects.all().delete()
		site_b = _build_articles(9, "t")
		for n, article in enumerate(Articles.objects.all(), start=10):
			add_pubmed_data(article, n)
		with CaptureQueriesContext(connection) as large:
			self.assertEqual(len(get_articles(site_b).data["results"]), 9)

		self.assertLessEqual(len(large), len(small) + 2)
