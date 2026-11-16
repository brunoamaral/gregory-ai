"""The OpenAlex columns and authorship rows are admin-only: the author and article API
payloads must have exactly the keys they had before the OpenAlex fields were added."""

from django.test import TestCase
from rest_framework.test import APIClient

from api.tests.visibility_helpers import publish_subjects
from gregory.models import (
	ArticleAuthorship,
	Articles,
	Authors,
	Institution,
	OrganizationApiSettings,
	Subject,
	Team,
)
from organizations.models import Organization

AUTHOR_KEYS = {
	"ORCID", "articles_count", "articles_list", "author_id", "biography", "country",
	"credit_name", "current_affiliation", "external_ids", "family_name", "full_name",
	"given_name", "orcid_claimed", "orcid_keywords", "orcid_verified_email",
	"relevant_articles_count", "researcher_urls",
}
ARTICLE_KEYS = {
	"access", "article_id", "article_subject_relevances", "authors", "categories",
	"clinical_trials", "container_title", "discovery_date", "doi", "keywords", "link",
	"links", "mesh", "ml_predictions", "ml_score", "pmcid", "pmid", "publication_types",
	"published_date", "publisher", "sources", "subjects", "summary", "teams", "title",
}


class OpenAlexFieldsNotExposedTests(TestCase):
	def setUp(self):
		organization = Organization.objects.create(name="Test Org")
		OrganizationApiSettings.objects.filter(organization=organization).update(make_api_public=True)
		team = Team.objects.create(organization=organization, name="Team", slug="team")
		subject = Subject.objects.create(subject_name="Subject", subject_slug="subject", team=team)
		publish_subjects(subject, organization=organization)
		self.author = Authors.objects.create(
			given_name="Jane", family_name="Smith", ORCID="0000-0002-3456-7890", openalex_id="A123"
		)
		self.article = Articles.objects.create(
			title="Paper",
			link="https://example.com/paper",
			doi="10.1/paper",
			openalex_id="W123",
		)
		self.article.authors.add(self.author)
		self.article.teams.add(team)
		self.article.subjects.add(subject)
		row = ArticleAuthorship.objects.create(
			article=self.article,
			author=self.author,
			position="last",
			author_index=0,
			openalex_author_id="A123",
			raw_affiliation="Lab, University. someone@example.org",
		)
		row.institutions.add(Institution.objects.create(openalex_id="I1", name="University"))
		self.client = APIClient()

	def test_author_payload_keys_are_unchanged(self):
		data = self.client.get(f"/authors/{self.author.author_id}/").data
		self.assertEqual(set(data), AUTHOR_KEYS)

	def test_article_payload_keys_are_unchanged(self):
		data = self.client.get("/articles/").data["results"][0]
		self.assertEqual(set(data), ARTICLE_KEYS)

	def test_nothing_openalex_shaped_leaks_into_either_payload(self):
		for url in (f"/authors/{self.author.author_id}/", "/articles/", "/authors/"):
			body = self.client.get(url).content.decode()
			for needle in ("openalex", "A123", "W123", "someone@example.org", "authorship"):
				self.assertNotIn(needle, body, f"{needle!r} in {url}")
