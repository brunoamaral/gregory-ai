from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework import status
from gregory.models import Authors, Articles, Team, OrganizationApiSettings
from api.serializers import AuthorSerializer
from django.contrib.sites.models import Site
from organizations.models import Organization


class AuthorURLFormatTests(TestCase):
	"""Test cases specifically for verifying the correct URL format in author responses"""

	def setUp(self):
		# Public org so anonymous API calls can see this author
		org = Organization.objects.create(name="URL Format Org", slug="url-fmt-org")
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		team = Team.objects.create(
			name="URL Format Team", slug="url-fmt-team", organization=org
		)

		# Create test author
		self.author = Authors.objects.create(
			family_name="Smith", given_name="John", full_name="John Smith"
		)

		# Link author through an article in the public team
		article = Articles.objects.create(
			title="URL Format Article",
			link="https://example.com/url-fmt",
		)
		article.teams.add(team)
		article.authors.add(self.author)

		# Set up site for URL generation
		site = Site.objects.get_current()
		site.domain = "api.brain-regeneration.com"
		site.save()

		self.client = APIClient()

	def test_author_serializer_articles_list_url_format(self):
		"""Test that AuthorSerializer generates the new URL format for articles_list"""
		serializer = AuthorSerializer(self.author)
		data = serializer.data

		expected_url = (
			f"https://api.brain-regeneration.com/articles/?author_id={self.author.author_id}"
		)
		self.assertEqual(data["articles_list"], expected_url)

		# Verify it's not using the old format
		old_url = f"https://api.brain-regeneration.com/articles/author/{self.author.author_id}"
		self.assertNotEqual(data["articles_list"], old_url)

	def test_authors_endpoint_response_url_format(self):
		"""Test that the /authors/ endpoint returns the correct articles_list URL format"""
		response = self.client.get("/authors/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)

		results = response.data["results"]
		self.assertGreater(len(results), 0)

		# Check the first author's articles_list URL
		author_data = results[0]
		self.assertIn("articles_list", author_data)

		# Should use the new format with query parameters
		articles_list_url = author_data["articles_list"]
		self.assertIn("articles/?author_id=", articles_list_url)
		self.assertNotIn("articles/author/", articles_list_url)

	def test_single_author_endpoint_response_url_format(self):
		"""Test that the /authors/{id}/ endpoint returns the correct articles_list URL format"""
		response = self.client.get(f"/authors/{self.author.author_id}/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)

		# Check the articles_list URL
		self.assertIn("articles_list", response.data)
		articles_list_url = response.data["articles_list"]

		# Should use the new format with query parameters, on the host the
		# request actually used (testserver here) rather than the configured
		# Site's domain — see AuthorSerializer.get_articles_list.
		expected_url = (
			f"http://testserver/articles/?author_id={self.author.author_id}"
		)
		self.assertEqual(articles_list_url, expected_url)

		# Verify it's not using the old format
		old_url = f"http://testserver/articles/author/{self.author.author_id}"
		self.assertNotEqual(articles_list_url, old_url)

	@override_settings(ALLOWED_HOSTS=["api.brain-regeneration.com", "testserver"])
	def test_articles_list_follows_the_requested_host(self):
		"""One instance serves several frontends: a request to one site's API
		host must not come back with another site's domain."""
		site = Site.objects.get_current()
		site.domain = "api.gregory-ms.com"
		site.save()

		response = self.client.get(
			f"/authors/{self.author.author_id}/", HTTP_HOST="api.brain-regeneration.com"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		url = response.data["articles_list"]
		self.assertIn("api.brain-regeneration.com", url)
		self.assertNotIn("gregory-ms.com", url)
		self.assertIn(f"/articles/?author_id={self.author.author_id}", url)

	def test_articles_list_falls_back_to_the_site_without_a_request(self):
		"""No request in context (a command, or a serializer used directly) —
		the configured Site, with its 'api.' prefixing rule, still applies."""
		site = Site.objects.get_current()
		site.domain = "gregory-ms.com"
		site.save()

		serializer = AuthorSerializer(self.author)
		self.assertEqual(
			serializer.data["articles_list"],
			f"https://api.gregory-ms.com/articles/?author_id={self.author.author_id}",
		)

