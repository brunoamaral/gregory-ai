"""The Articles admin shows PubMed data read-only and without per-row queries."""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from gregory.admin import ArticleAdmin, MeshTermAdmin, PublicationTypeAdmin
from gregory.models import ArticleMeshHeading, Articles, MeshTerm, PublicationType

User = get_user_model()


def add_headings(article, n):
	for i in range(n):
		descriptor = MeshTerm.objects.create(ui=f"D{article.pk}{i:03d}", name=f"D {i}", kind="descriptor")
		qualifier = MeshTerm.objects.create(ui=f"Q{article.pk}{i:03d}", name=f"Q {i}", kind="qualifier")
		ArticleMeshHeading.objects.create(article=article, descriptor=descriptor, qualifier=qualifier)
		ptype = PublicationType.objects.create(ui=f"D8{article.pk}{i:03d}", name=f"Type {i}")
		article.publication_types.add(ptype)


class ArticleAdminPubmedTests(TestCase):
	def setUp(self):
		self.admin_user = User.objects.create_superuser("pm-root", "r@example.com", "x")
		self.client.force_login(self.admin_user)

	def change_page(self, article):
		return self.client.get(reverse("admin:gregory_articles_change", args=[article.pk]))

	def test_change_page_shows_pmid_link_and_pubmed_rows(self):
		article = Articles.objects.create(
			title="A", link="https://x.org/a", pmid="38812345", pmcid="PMC1"
		)
		add_headings(article, 2)
		response = self.change_page(article)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "https://pubmed.ncbi.nlm.nih.gov/38812345/")
		self.assertContains(response, "PMC1")
		self.assertContains(response, "Type 1")
		self.assertContains(response, "D 1")

	def test_pubmed_fields_are_read_only(self):
		for name in ("pmcid", "pubmed_status", "pubmed_checked_at", "language", "keywords", "pmid_link"):
			self.assertIn(name, ArticleAdmin.readonly_fields)

	def test_query_count_does_not_scale_with_headings(self):
		few = Articles.objects.create(title="Few", link="https://x.org/few")
		add_headings(few, 1)
		self.change_page(few)  # warm caches
		with CaptureQueriesContext(connection) as small:
			self.change_page(few)

		many = Articles.objects.create(title="Many", link="https://x.org/many")
		add_headings(many, 12)
		with CaptureQueriesContext(connection) as large:
			self.change_page(many)

		self.assertLessEqual(len(large), len(small) + 1)

	def test_article_search_finds_by_pmid(self):
		Articles.objects.create(title="Findable", link="https://x.org/f", pmid="5550001")
		Articles.objects.create(title="Other", link="https://x.org/o", pmid="9990002")
		response = self.client.get(reverse("admin:gregory_articles_changelist"), {"q": "5550001"})
		self.assertContains(response, "Findable")
		self.assertNotContains(response, "Other")


class ReferenceAdminTests(TestCase):
	def test_reference_tables_are_read_only_and_searchable(self):
		root = User.objects.create_superuser("ref-root", "r@example.com", "x")
		request = type("R", (), {"user": root})()
		for admin_cls, model in ((PublicationTypeAdmin, PublicationType), (MeshTermAdmin, MeshTerm)):
			model_admin = admin_cls(model, None)
			self.assertFalse(model_admin.has_add_permission(request))
			self.assertFalse(model_admin.has_change_permission(request))
			self.assertFalse(model_admin.has_delete_permission(request))
			self.assertEqual(model_admin.search_fields, ["name", "ui"])
