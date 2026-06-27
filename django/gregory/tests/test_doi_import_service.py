"""
Tests for gregory.services.doi_import and the admin view that uses it.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_doi_import_service
"""

from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from django.urls import reverse
from organizations.models import Organization, OrganizationUser

from gregory.models import Articles, Sources, Subject, Team
from gregory.services.doi_import import (
	ImportStatus,
	create_article_from_doi,
	normalize_doi,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# normalize_doi
# ---------------------------------------------------------------------------

class NormalizeDoiTests(TestCase):
	def test_strips_https_prefix(self):
		self.assertEqual(normalize_doi("https://doi.org/10.1000/xyz"), "10.1000/xyz")

	def test_strips_http_prefix(self):
		self.assertEqual(normalize_doi("http://doi.org/10.1000/xyz"), "10.1000/xyz")

	def test_strips_dx_prefix(self):
		self.assertEqual(normalize_doi("https://dx.doi.org/10.1000/xyz"), "10.1000/xyz")

	def test_strips_doi_colon_prefix(self):
		self.assertEqual(normalize_doi("doi:10.1000/xyz"), "10.1000/xyz")

	def test_bare_doi_unchanged(self):
		self.assertEqual(normalize_doi("10.1000/xyz"), "10.1000/xyz")

	def test_strips_whitespace(self):
		self.assertEqual(normalize_doi("  10.1000/xyz  "), "10.1000/xyz")


# ---------------------------------------------------------------------------
# create_article_from_doi
# ---------------------------------------------------------------------------

def _make_org_stack():
	"""Return (org, team, subject, source) for use in tests."""
	org = Organization.objects.create(name="Test Org", slug="test-org-doi")
	team = Team.objects.create(name="Test Team", slug="test-team-doi", organization=org)
	subject = Subject.objects.create(subject_name="Test Subject", team=team)
	source = Sources.objects.create(
		name="Test Source",
		source_for="science paper",
		method="manual",
		team=team,
		subject=subject,
	)
	return org, team, subject, source


def _mock_paper(title="Test Article Title", doi="10.9999/test", **kwargs):
	"""Build a SciencePaper mock with sensible defaults."""
	paper = MagicMock()
	paper.title = title
	paper.doi = doi
	paper.link = f"https://doi.org/{doi}"
	paper.abstract = "Test abstract."
	paper.published_date = None
	paper.access = "open"
	paper.publisher = "Test Publisher"
	paper.journal = "Test Journal"
	paper.pdf_link = None
	paper.authors = []
	paper.retracted = None
	paper.clean_abstract.return_value = "Test abstract."
	paper.refresh.return_value = None
	for k, v in kwargs.items():
		setattr(paper, k, v)
	return paper


class CreateArticleFromDoiTests(TestCase):
	def setUp(self):
		self.org, self.team, self.subject, self.source = _make_org_stack()

	@patch("gregory.services.doi_import.SciencePaper")
	def test_creates_article(self, MockSciencePaper):
		MockSciencePaper.return_value = _mock_paper()

		result = create_article_from_doi("10.9999/test", self.source)

		self.assertEqual(result.status, ImportStatus.CREATED)
		self.assertIsNotNone(result.article)
		article = result.article
		self.assertEqual(article.title, "Test Article Title")
		self.assertEqual(article.doi, "10.9999/test")
		self.assertIn(self.source, article.sources.all())
		self.assertIn(self.team, article.teams.all())
		self.assertIn(self.subject, article.subjects.all())

	@patch("gregory.services.doi_import.SciencePaper")
	def test_normalizes_doi_url(self, MockSciencePaper):
		mock_paper = _mock_paper(doi="10.9999/test")
		MockSciencePaper.return_value = mock_paper

		result = create_article_from_doi("https://doi.org/10.9999/test", self.source)

		self.assertEqual(result.status, ImportStatus.CREATED)
		# SciencePaper was instantiated with the normalised DOI
		MockSciencePaper.assert_called_once_with(doi="10.9999/test")

	@patch("gregory.services.doi_import.SciencePaper")
	def test_dedup_by_doi(self, MockSciencePaper):
		Articles.objects.create(
			title="Existing Article",
			doi="10.9999/existing",
			link="https://doi.org/10.9999/existing",
		)

		result = create_article_from_doi("10.9999/existing", self.source)

		self.assertEqual(result.status, ImportStatus.EXISTS_BY_DOI)
		self.assertIsNotNone(result.article)
		MockSciencePaper.assert_not_called()

	@patch("gregory.services.doi_import.SciencePaper")
	def test_dedup_by_title(self, MockSciencePaper):
		Articles.objects.create(
			title="Duplicate Title Article",
			link="https://example.com/dup",
		)
		MockSciencePaper.return_value = _mock_paper(
			title="Duplicate Title Article", doi="10.9999/new"
		)

		result = create_article_from_doi("10.9999/new", self.source)

		self.assertEqual(result.status, ImportStatus.EXISTS_BY_TITLE)

	@patch("gregory.services.doi_import.SciencePaper")
	def test_crossref_failure_no_title(self, MockSciencePaper):
		mock_paper = _mock_paper(title=None)
		mock_paper.refresh.return_value = "DOI not found"
		MockSciencePaper.return_value = mock_paper

		result = create_article_from_doi("10.9999/missing", self.source)

		self.assertEqual(result.status, ImportStatus.CROSSREF_FAILURE)
		self.assertIsNone(result.article)

	@patch("gregory.services.doi_import.SciencePaper")
	def test_existing_doi_gets_source_added(self, MockSciencePaper):
		"""If the article already exists, the source should be associated."""
		existing = Articles.objects.create(
			title="Already There",
			doi="10.9999/already",
			link="https://doi.org/10.9999/already",
		)
		result = create_article_from_doi("10.9999/already", self.source)

		self.assertEqual(result.status, ImportStatus.EXISTS_BY_DOI)
		existing.refresh_from_db()
		self.assertIn(self.source, existing.sources.all())

	@patch("gregory.services.doi_import.SciencePaper")
	def test_authors_attached(self, MockSciencePaper):
		mock_paper = _mock_paper(
			authors=[
				{"given": "Jane", "family": "Doe", "ORCID": None},
				{"given": "John", "family": "Smith", "ORCID": "0000-0001-2345-6789"},
			]
		)
		MockSciencePaper.return_value = mock_paper

		result = create_article_from_doi("10.9999/authors", self.source)

		self.assertEqual(result.status, ImportStatus.CREATED)
		self.assertEqual(result.authors_added, 2)
		self.assertEqual(result.article.authors.count(), 2)

	@patch("gregory.services.doi_import.SciencePaper")
	def test_skip_authors(self, MockSciencePaper):
		mock_paper = _mock_paper(
			authors=[{"given": "Jane", "family": "Doe", "ORCID": None}]
		)
		MockSciencePaper.return_value = mock_paper

		result = create_article_from_doi("10.9999/noauth", self.source, skip_authors=True)

		self.assertEqual(result.status, ImportStatus.CREATED)
		self.assertEqual(result.authors_added, 0)
		self.assertEqual(result.article.authors.count(), 0)


# ---------------------------------------------------------------------------
# Admin view
# ---------------------------------------------------------------------------

class AddByDoiAdminViewTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.org, self.team, self.subject, self.source = _make_org_stack()

		self.superuser = User.objects.create_superuser(
			username="admin_doi", password="pass", email="admin@example.com"
		)
		self.staff_user = User.objects.create_user(
			username="staff_doi",
			password="pass",
			email="staff@example.com",
			is_staff=True,
		)
		self.staff_user.user_permissions.add(
			*User.objects.none().model._meta.app_label
			and []  # placeholder; permissions added below
		)
		# Grant add_articles permission
		from django.contrib.contenttypes.models import ContentType
		from django.contrib.auth.models import Permission

		ct = ContentType.objects.get_for_model(Articles)
		perm = Permission.objects.get(codename="add_articles", content_type=ct)
		self.staff_user.user_permissions.add(perm)

		OrganizationUser.objects.create(
			organization=self.org, user=self.staff_user, is_admin=False
		)

	def _get_url(self):
		return reverse("admin:article_add_by_doi")

	def test_requires_login(self):
		from django.test import Client
		client = Client()
		resp = client.get(self._get_url())
		self.assertRedirects(resp, f"/admin/login/?next={self._get_url()}", fetch_redirect_response=False)

	def test_permission_denied_without_add_articles(self):
		from django.test import Client

		no_perm_user = User.objects.create_user(
			username="noperm_doi", password="pass", email="noperm@example.com", is_staff=True
		)
		client = Client()
		client.force_login(no_perm_user)
		resp = client.get(self._get_url())
		# Should redirect to changelist with error
		self.assertRedirects(resp, reverse("admin:gregory_articles_changelist"), fetch_redirect_response=False)

	def test_get_renders_form(self):
		from django.test import Client
		client = Client()
		client.force_login(self.superuser)
		resp = client.get(self._get_url())
		self.assertEqual(resp.status_code, 200)
		self.assertContains(resp, "Add Article by DOI")
		self.assertContains(resp, "DOI")

	@patch("gregory.services.doi_import.SciencePaper")
	def test_post_creates_article_and_redirects(self, MockSciencePaper):
		MockSciencePaper.return_value = _mock_paper(doi="10.9999/viewtest")

		from django.test import Client
		client = Client()
		client.force_login(self.superuser)
		resp = client.post(self._get_url(), {
			"doi": "10.9999/viewtest",
			"source": self.source.pk,
		})

		article = Articles.objects.filter(doi="10.9999/viewtest").first()
		self.assertIsNotNone(article)
		self.assertRedirects(
			resp,
			reverse("admin:gregory_articles_change", args=[article.pk]),
			fetch_redirect_response=False,
		)

	@patch("gregory.services.doi_import.SciencePaper")
	def test_pipeline_commands_not_called(self, MockSciencePaper):
		"""Enrichment commands must NOT run during the admin view."""
		MockSciencePaper.return_value = _mock_paper(doi="10.9999/nopipeline")

		from django.test import Client
		from unittest.mock import patch as _patch

		client = Client()
		client.force_login(self.superuser)
		# Patch at the django.core.management level — if any enrichment command
		# is inadvertently called from the service, this will catch it.
		with _patch("django.core.management.call_command") as mock_cmd:
			client.post(self._get_url(), {
				"doi": "10.9999/nopipeline",
				"source": self.source.pk,
			})
			mock_cmd.assert_not_called()

	@patch("gregory.services.doi_import.SciencePaper")
	def test_staff_source_queryset_scoped_to_org(self, MockSciencePaper):
		"""Staff users should only see sources from their own organisation."""
		other_org = Organization.objects.create(name="Other Org", slug="other-org-doi")
		other_team = Team.objects.create(
			name="Other Team", slug="other-team-doi", organization=other_org
		)
		other_source = Sources.objects.create(
			name="Other Source",
			source_for="science paper",
			method="manual",
			team=other_team,
		)

		from django.test import Client
		client = Client()
		client.force_login(self.staff_user)
		resp = client.get(self._get_url())
		self.assertEqual(resp.status_code, 200)
		form = resp.context["form"]
		source_ids = list(form.fields["source"].queryset.values_list("pk", flat=True))
		self.assertIn(self.source.pk, source_ids)
		self.assertNotIn(other_source.pk, source_ids)
