from django.test import TestCase
from django.urls import reverse
from django.contrib.sites.models import Site
from rest_framework.test import APIClient
from rest_framework import status
from gregory.models import (
	Authors,
	Articles,
	Team,
	Subject,
	OrganizationApiSettings,
)
from organizations.models import Organization


class AuthorCoauthorsTest(TestCase):
	"""Test cases for the /authors/<pk>/coauthors/ endpoint."""

	def setUp(self):
		self.organization = Organization.objects.create(name="Coauthors Org")
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=self.organization, name="Coauthors Team", slug="coauthors-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Coauthors Subject",
			subject_slug="coauthors-subject",
			team=self.team,
		)

		self.target = Authors.objects.create(given_name="Target", family_name="Author")
		self.coauthor_a = Authors.objects.create(given_name="Alpha", family_name="Coauthor")
		self.coauthor_b = Authors.objects.create(given_name="Beta", family_name="Coauthor")
		self.stranger = Authors.objects.create(given_name="Stranger", family_name="Author")

		# 3 articles shared between target and coauthor_a (highest shared count)
		for i in range(3):
			article = Articles.objects.create(
				title=f"Shared A article {i}",
				link=f"http://example.com/shared-a-{i}",
			)
			article.authors.add(self.target, self.coauthor_a)
			article.teams.add(self.team)
			article.subjects.add(self.subject)

		# 1 article shared between target and coauthor_b
		shared_b_article = Articles.objects.create(
			title="Shared B article",
			link="http://example.com/shared-b-0",
		)
		shared_b_article.authors.add(self.target, self.coauthor_b)
		shared_b_article.teams.add(self.team)
		shared_b_article.subjects.add(self.subject)

		# coauthor_b has 4 more articles NOT shared with target, 2 of which are relevant.
		# This is the join-reuse regression guard: relevant_articles_count must count
		# these even though they aren't part of the "shared" set.
		for i in range(4):
			extra_article = Articles.objects.create(
				title=f"Coauthor B solo article {i}",
				link=f"http://example.com/coauthor-b-solo-{i}",
			)
			extra_article.authors.add(self.coauthor_b)
			extra_article.teams.add(self.team)
			extra_article.subjects.add(self.subject)
			if i < 2:
				Articles.objects.filter(pk=extra_article.pk).update(relevant=True)

		# A stranger has no articles with the target at all.
		stranger_article = Articles.objects.create(
			title="Stranger solo article",
			link="http://example.com/stranger-solo",
		)
		stranger_article.authors.add(self.stranger)
		stranger_article.teams.add(self.team)
		stranger_article.subjects.add(self.subject)

		self.client = APIClient()

	def _url(self, author):
		return reverse("authors-coauthors", kwargs={"pk": author.author_id})

	def test_target_author_absent_from_results(self):
		response = self.client.get(self._url(self.target))
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [r["author_id"] for r in response.data["results"]]
		self.assertNotIn(self.target.author_id, ids)

	def test_stranger_absent_from_results(self):
		response = self.client.get(self._url(self.target))
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = [r["author_id"] for r in response.data["results"]]
		self.assertNotIn(self.stranger.author_id, ids)

	def test_correct_members_present(self):
		response = self.client.get(self._url(self.target))
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["author_id"] for r in response.data["results"]}
		self.assertEqual(ids, {self.coauthor_a.author_id, self.coauthor_b.author_id})

	def test_ordering_by_shared_articles_desc(self):
		response = self.client.get(self._url(self.target))
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		results = response.data["results"]
		self.assertEqual(results[0]["author_id"], self.coauthor_a.author_id)
		self.assertEqual(results[0]["shared_articles"], 3)
		self.assertEqual(results[1]["author_id"], self.coauthor_b.author_id)
		self.assertEqual(results[1]["shared_articles"], 1)

	def test_join_reuse_regression_guard(self):
		"""coauthor_b's relevant_articles_count must include relevant articles
		that are NOT shared with the target author (2 solo relevant + 0 shared
		relevant = 2), not be capped by shared_articles (which is 1)."""
		response = self.client.get(self._url(self.target))
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		by_id = {r["author_id"]: r for r in response.data["results"]}
		coauthor_b_data = by_id[self.coauthor_b.author_id]
		self.assertEqual(coauthor_b_data["shared_articles"], 1)
		self.assertEqual(coauthor_b_data["articles_count"], 5)
		self.assertEqual(coauthor_b_data["relevant_articles_count"], 2)
		self.assertGreater(
			coauthor_b_data["relevant_articles_count"],
			coauthor_b_data["shared_articles"],
			"relevant_articles_count must not be limited to the shared-articles join",
		)

	def test_coauthor_a_counts(self):
		response = self.client.get(self._url(self.target))
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		by_id = {r["author_id"]: r for r in response.data["results"]}
		coauthor_a_data = by_id[self.coauthor_a.author_id]
		self.assertEqual(coauthor_a_data["articles_count"], 3)
		self.assertEqual(coauthor_a_data["relevant_articles_count"], 0)

	def test_pagination_works(self):
		response = self.client.get(self._url(self.target))
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("count", response.data)
		self.assertIn("results", response.data)
		self.assertIn("next", response.data)
		self.assertIn("previous", response.data)
		self.assertEqual(response.data["count"], 2)

	def test_serializer_fields(self):
		response = self.client.get(self._url(self.target))
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		expected_fields = [
			"author_id",
			"given_name",
			"family_name",
			"full_name",
			"ORCID",
			"country",
			"shared_articles",
			"articles_count",
			"relevant_articles_count",
		]
		for result in response.data["results"]:
			for field in expected_fields:
				self.assertIn(field, result, f"Field '{field}' missing from coauthors payload")

	def test_unknown_author_returns_404(self):
		response = self.client.get(
			reverse("authors-coauthors", kwargs={"pk": 9999999})
		)
		self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AuthorCoauthorsQueryCountTest(TestCase):
	"""assertNumQueries pins the query budget so join-reuse regressions can't creep back in."""

	def setUp(self):
		Site.objects.clear_cache()  # ensure consistent query count regardless of run order
		self.organization = Organization.objects.create(name="Coauthors Query Org")
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=self.organization, name="Coauthors Query Team", slug="coauthors-query-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Coauthors Query Subject",
			subject_slug="coauthors-query-subject",
			team=self.team,
		)

		self.target = Authors.objects.create(given_name="Query", family_name="Target")

		self.coauthors = []
		for i in range(6):
			coauthor = Authors.objects.create(given_name=f"Co{i}", family_name="Author")
			self.coauthors.append(coauthor)
			article = Articles.objects.create(
				title=f"Query shared article {i}",
				link=f"http://example.com/query-shared-{i}",
			)
			article.authors.add(self.target, coauthor)
			article.teams.add(self.team)
			article.subjects.add(self.subject)
			if i % 2 == 0:
				Articles.objects.filter(pk=article.pk).update(relevant=True)

		self.client = APIClient()

	def test_coauthors_query_budget_flat_with_page_content(self):
		"""shared_articles, articles_count and relevant_articles_count are all
		computed via annotations, so the query count must not grow with the
		number of coauthors on the page."""
		url = reverse("authors-coauthors", kwargs={"pk": self.target.author_id})
		with self.assertNumQueries(5):
			response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertGreater(len(response.data["results"]), 0)

	def test_relevant_articles_count_method_is_zero_query_when_annotated(self):
		"""get_relevant_articles_count must trust the annotation and never issue
		a query of its own — VisibleOrgMiddleware attaches visible_org_ids to
		every request, so this is the path taken on every real request."""
		from api.serializers import AuthorSerializer

		author = self.target
		author.relevant_articles_count = 3
		serializer = AuthorSerializer(author)
		with self.assertNumQueries(0):
			self.assertEqual(serializer.get_relevant_articles_count(author), 3)

	def test_author_list_query_budget_is_pinned(self):
		"""Pins the query count for /authors/ under org-visibility scope so a
		real regression is caught. article_count and relevant_articles_count
		are both computed via get_queryset annotations, and get_site() uses
		Django's cached Site.objects.get_current(), so the query count must
		stay flat regardless of how many authors are returned."""
		with self.assertNumQueries(4):
			response = self.client.get("/authors/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertGreater(len(response.data["results"]), 0)
