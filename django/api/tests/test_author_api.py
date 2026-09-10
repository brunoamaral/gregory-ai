from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from gregory.models import (
	Authors,
	Articles,
	Team,
	Subject,
	TeamCategory,
	Sources,
	OrganizationApiSettings,
)
from organizations.models import Organization
from django_countries.fields import Country


class AuthorAPITest(TestCase):
	"""Test cases for Authors API endpoints"""

	def setUp(self):
		"""Set up test data"""
		# Create test organization and team
		self.organization = Organization.objects.create(name="Test Org")
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=self.organization, name="Test Team", slug="test-team"
		)

		# Create test subject
		self.subject = Subject.objects.create(
			subject_name="Test Subject", subject_slug="test-subject", team=self.team
		)

		# Create test category
		self.category = TeamCategory.objects.create(
			team=self.team, category_name="Test Category", category_slug="test-category"
		)
		self.category.subjects.add(self.subject)

		# Create test source
		self.source = Sources.objects.create(
			name="Test Source", team=self.team, subject=self.subject
		)

		# Create test authors
		self.author1 = Authors.objects.create(
			given_name="Jane",
			family_name="Smith",
			ORCID="0000-0002-3456-7890",
			country=Country("US"),
		)

		self.author2 = Authors.objects.create(
			given_name="John", family_name="Doe", ORCID="0000-0001-2345-6789"
		)

		# Create test articles
		self.article1 = Articles.objects.create(
			title="Test Article 1",
			link="http://example.com/article1",
			summary="Test summary 1",
		)
		self.article1.authors.add(self.author1)
		self.article1.teams.add(self.team)
		self.article1.subjects.add(self.subject)
		self.article1.sources.add(self.source)
		self.article1.team_categories.add(self.category)

		self.article2 = Articles.objects.create(
			title="Test Article 2",
			link="http://example.com/article2",
			summary="Test summary 2",
		)
		self.article2.authors.add(self.author1)
		self.article2.teams.add(self.team)
		self.article2.subjects.add(self.subject)
		self.article2.sources.add(self.source)
		self.article2.team_categories.add(self.category)

		# One article for author2
		self.article3 = Articles.objects.create(
			title="Test Article 3",
			link="http://example.com/article3",
			summary="Test summary 3",
		)
		self.article3.authors.add(self.author2)
		self.article3.teams.add(self.team)
		self.article3.subjects.add(self.subject)
		self.article3.sources.add(self.source)

		self.client = APIClient()

	def test_authors_list_endpoint(self):
		"""Test the authors list endpoint"""
		response = self.client.get("/authors/")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("results", response.data)
		self.assertIn("count", response.data)
		self.assertEqual(response.data["count"], 2)

	def test_author_detail_endpoint(self):
		"""Test the author detail endpoint"""
		response = self.client.get(f"/authors/{self.author1.author_id}/")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("full_name", response.data)
		self.assertEqual(response.data["full_name"], "Jane Smith")
		self.assertEqual(response.data["author_id"], self.author1.author_id)

	def test_authors_sorting_by_article_count(self):
		"""Test sorting authors by article count"""
		response = self.client.get("/authors/?sort_by=article_count&order=desc")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		results = response.data["results"]

		# author1 should come first (2 articles vs 1)
		self.assertEqual(results[0]["author_id"], self.author1.author_id)
		self.assertEqual(results[0]["articles_count"], 2)
		self.assertEqual(results[1]["author_id"], self.author2.author_id)
		self.assertEqual(results[1]["articles_count"], 1)

	def test_authors_filter_by_orcid_case_insensitive(self):
		"""ORCID filtering should be case-insensitive end to end.

		Regression test: the viewset used to apply a manual case-sensitive
		`ORCID__contains` filter in addition to the FilterSet's case-insensitive
		`icontains` filter, ANDing the two together. Since ORCID checksum
		characters are only ever generated as uppercase 'X' (never lowercase),
		a lowercase 'x' in the query string silently returned zero results.
		"""
		orcid_with_checksum_x = "0000-0002-9999-000X"
		author = Authors.objects.create(
			given_name="Ada",
			family_name="Checksum",
			ORCID=orcid_with_checksum_x,
		)
		article = Articles.objects.create(
			title="Checksum Article",
			link="http://example.com/checksum-article",
		)
		article.authors.add(author)
		article.teams.add(self.team)
		article.subjects.add(self.subject)
		article.sources.add(self.source)

		# Exact case match
		response = self.client.get(f"/authors/?orcid={orcid_with_checksum_x}")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 1)
		self.assertEqual(response.data["results"][0]["author_id"], author.author_id)

		# Lowercase checksum character should still match (case-insensitive)
		response = self.client.get("/authors/?orcid=0000-0002-9999-000x")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 1)
		self.assertEqual(response.data["results"][0]["author_id"], author.author_id)

		# Lowercase partial/substring match should also work
		response = self.client.get("/authors/?orcid=9999-000x")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 1)
		self.assertEqual(response.data["results"][0]["author_id"], author.author_id)

	def test_authors_filtering_by_subject_without_team_works(self):
		"""
		subject_id alone (no team_id) must work -- site-scoped API visibility,
		Phase 2 item 2. A caller scoped to a site knows subjects, not team_ids
		(Subject.team is a plain FK, so subject_id already resolves to exactly
		one team with no ambiguity), so requiring team_id here would make
		subject filtering unusable for that caller.
		"""
		response = self.client.get(
			f"/authors/?subject_id={self.subject.id}&sort_by=article_count"
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		# Both author1 (2 articles) and author2 (1 article) are tagged with
		# this subject.
		self.assertEqual(response.data["count"], 2)
		results = response.data["results"]
		self.assertEqual(results[0]["author_id"], self.author1.author_id)
		self.assertEqual(results[1]["author_id"], self.author2.author_id)

	def test_authors_filtering_by_subjects_any_without_team_works(self):
		"""?subjects_any=<id> (no team_id) is the OR-list equivalent of subject_id."""
		response = self.client.get(
			f"/authors/?subjects_any={self.subject.id}&sort_by=article_count"
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 2)

	def test_authors_filtering_by_subjects_any_multiple_ids(self):
		"""?subjects_any=A,B returns authors tagged with either subject."""
		other_subject = Subject.objects.create(
			subject_name="Other Subject", subject_slug="other-subject", team=self.team
		)
		other_author = Authors.objects.create(
			given_name="Extra", family_name="Author"
		)
		other_article = Articles.objects.create(
			title="Other subject article",
			link="http://example.com/other-subject-article",
		)
		other_article.authors.add(other_author)
		other_article.teams.add(self.team)
		other_article.subjects.add(other_subject)

		response = self.client.get(
			f"/authors/?subjects_any={self.subject.id},{other_subject.id}"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = {r["author_id"] for r in response.data["results"]}
		self.assertIn(self.author1.author_id, ids)
		self.assertIn(self.author2.author_id, ids)
		self.assertIn(other_author.author_id, ids)

	def test_authors_filtering_by_subjects_any_all_invalid_returns_empty(self):
		"""?subjects_any=foo,bar (all non-numeric) matches nothing, not an error."""
		response = self.client.get("/authors/?subjects_any=foo,bar")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	def test_authors_filtering_validation_category_without_team(self):
		"""Test that filtering by category_slug without team_id returns empty results"""
		response = self.client.get(
			f"/authors/?category_slug={self.category.category_slug}&sort_by=article_count"
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)
		self.assertEqual(len(response.data["results"]), 0)

	def test_authors_filtering_with_valid_team_and_subject(self):
		"""Test filtering by team_id and subject_id works correctly"""
		response = self.client.get(
			f"/authors/?team_id={self.team.id}&subject_id={self.subject.id}&sort_by=article_count"
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 2)

		# Should be sorted by article count (desc)
		results = response.data["results"]
		self.assertEqual(results[0]["author_id"], self.author1.author_id)
		self.assertEqual(results[1]["author_id"], self.author2.author_id)

	def test_authors_filtering_with_valid_team_and_category(self):
		"""Test filtering by team_id and category_slug works correctly"""
		response = self.client.get(
			f"/authors/?team_id={self.team.id}&category_slug={self.category.category_slug}&sort_by=article_count"
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		# Only author1 has articles in this category
		self.assertEqual(response.data["count"], 1)
		self.assertEqual(
			response.data["results"][0]["author_id"], self.author1.author_id
		)

	def test_authors_timeframe_filtering_year(self):
		"""Test filtering by timeframe=year"""
		response = self.client.get("/authors/?sort_by=article_count&timeframe=year")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("results", response.data)

	def test_authors_timeframe_filtering_month(self):
		"""Test filtering by timeframe=month"""
		response = self.client.get("/authors/?sort_by=article_count&timeframe=month")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("results", response.data)

	def test_authors_timeframe_filtering_week(self):
		"""Test filtering by timeframe=week"""
		response = self.client.get("/authors/?sort_by=article_count&timeframe=week")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("results", response.data)

	def test_authors_custom_date_range_filtering(self):
		"""Test filtering by custom date range"""
		response = self.client.get(
			"/authors/?sort_by=article_count&date_from=2024-01-01&date_to=2024-12-31"
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("results", response.data)

	def test_authors_action_by_team_subject_validation(self):
		"""Test by_team_subject action requires both team_id and subject_id"""
		# Missing both parameters
		response = self.client.get("/authors/by_team_subject/")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("error", response.data)

		# Missing subject_id
		response = self.client.get(f"/authors/by_team_subject/?team_id={self.team.id}")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

		# Missing team_id
		response = self.client.get(
			f"/authors/by_team_subject/?subject_id={self.subject.id}"
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

		# Valid parameters
		response = self.client.get(
			f"/authors/by_team_subject/?team_id={self.team.id}&subject_id={self.subject.id}"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_authors_action_by_team_category_validation(self):
		"""Test by_team_category action requires both team_id and category_slug"""
		# Missing both parameters
		response = self.client.get("/authors/by_team_category/")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("error", response.data)

		# Missing category_slug
		response = self.client.get(f"/authors/by_team_category/?team_id={self.team.id}")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

		# Missing team_id
		response = self.client.get(
			f"/authors/by_team_category/?category_slug={self.category.category_slug}"
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

		# Valid parameters
		response = self.client.get(
			f"/authors/by_team_category/?team_id={self.team.id}&category_slug={self.category.category_slug}"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_authors_serializer_fields(self):
		"""Test that author serializer includes all expected fields"""
		response = self.client.get("/authors/")

		self.assertEqual(response.status_code, status.HTTP_200_OK)

		if response.data["results"]:
			author_data = response.data["results"][0]
			expected_fields = [
				"author_id",
				"given_name",
				"family_name",
				"full_name",
				"ORCID",
				"country",
				"articles_count",
				"relevant_articles_count",
				"articles_list",
			]

			for field in expected_fields:
				self.assertIn(
					field, author_data, f"Field '{field}' missing from API response"
				)

	def test_authors_ordering_by_different_fields(self):
		"""Test ordering by different fields"""
		# Test ordering by given_name
		response = self.client.get("/authors/?sort_by=given_name&order=asc")
		self.assertEqual(response.status_code, status.HTTP_200_OK)

		# Test ordering by family_name
		response = self.client.get("/authors/?sort_by=family_name&order=desc")
		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_authors_pagination(self):
		"""Test that pagination works correctly"""
		response = self.client.get("/authors/")

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("count", response.data)
		self.assertIn("results", response.data)
		self.assertIn("next", response.data)
		self.assertIn("previous", response.data)

	def test_team_id_requirement_validation(self):
		"""
		Test that team_id is required when filtering by category_slug, but NOT
		when filtering by subject_id (site-scoped API visibility, Phase 2 item
		2 -- see AuthorsViewSet.get_queryset for why the two are no longer
		symmetric).
		"""
		# Test 1: Basic endpoint without filters (should work)
		response = self.client.get("/authors/?sort_by=article_count")
		self.assertEqual(response.status_code, status.HTTP_200_OK)

		# Test 2: With team_id only (should work)
		response = self.client.get(
			f"/authors/?team_id={self.team.id}&sort_by=article_count"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)

		# Test 3: With subject_id but no team_id (should now work, and return
		# both authors tagged with this subject)
		response = self.client.get(
			f"/authors/?subject_id={self.subject.id}&sort_by=article_count"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data["count"],
			2,
			"subject_id without team_id should work, not return empty results",
		)

		# Test 4: With category_slug but no team_id (should return empty results)
		response = self.client.get(
			f"/authors/?category_slug={self.category.category_slug}&sort_by=article_count"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data["count"],
			0,
			"Should return empty results when category_slug is used without team_id",
		)
		self.assertEqual(len(response.data["results"]), 0)

		# Test 5: With team_id and subject_id (should work and potentially return results)
		response = self.client.get(
			f"/authors/?team_id={self.team.id}&subject_id={self.subject.id}&sort_by=article_count"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		# Note: Results depend on test data, but request should be valid

		# Test 6: With team_id and category_slug (should work and potentially return results)
		response = self.client.get(
			f"/authors/?team_id={self.team.id}&category_slug={self.category.category_slug}&sort_by=article_count"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		# Note: Results depend on test data, but request should be valid

		# Test 7: subject_id + category_slug without team_id -- still empty,
		# but now solely because of category_slug's requirement (subject_id
		# alone would not trigger it; see Test 3).
		response = self.client.get(
			f"/authors/?subject_id={self.subject.id}&category_slug={self.category.category_slug}&sort_by=article_count"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data["count"],
			0,
			"category_slug without team_id should still return empty results",
		)
		self.assertEqual(len(response.data["results"]), 0)

		# Test 8: Invalid team_id with valid subject_id (should return empty but not error)
		invalid_team_id = 99999
		response = self.client.get(
			f"/authors/?team_id={invalid_team_id}&subject_id={self.subject.id}&sort_by=article_count"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

		# Test 9: Valid team_id with invalid subject_id (should return empty but not error)
		invalid_subject_id = 99999
		response = self.client.get(
			f"/authors/?team_id={self.team.id}&subject_id={invalid_subject_id}&sort_by=article_count"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	def test_team_id_requirement_with_timeframe_filters(self):
		"""
		category_slug still requires team_id even alongside a date/timeframe
		filter; subject_id does not (Phase 2 item 2).
		"""
		from datetime import datetime, timedelta

		from django.utils import timezone as django_timezone

		# Give article1/article2 a published_date inside the date window so
		# the subject_id case below has something to actually find -- without
		# this, an all-NULL published_date would return empty regardless of
		# the team_id coupling being tested here, masking the behaviour.
		# One day back, deliberately: /authors/ parses date_to into a plain
		# date, so `published_date__lte` compares against midnight. An
		# article published at any time TODAY sorts after that boundary and
		# falls outside its own window -- which would make this test fail
		# for a reason unrelated to the team_id coupling it exists to check.
		# (Note /articles/ documents published_date_before as including the
		# full day; /authors/ does not. Pre-existing inconsistency.)
		now = django_timezone.now() - timedelta(days=1)
		Articles.objects.filter(
			pk__in=[self.article1.pk, self.article2.pk]
		).update(published_date=now)

		date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
		date_to = datetime.now().strftime("%Y-%m-%d")

		# subject_id + date filters, no team_id -- now works, since team_id is
		# not required for subject-based filtering.
		response = self.client.get(
			f"/authors/?subject_id={self.subject.id}&date_from={date_from}&date_to={date_to}"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data["count"],
			1,
			"subject_id + date filters without team_id should find author1 "
			"(the only author with articles published in the window)",
		)
		self.assertEqual(
			response.data["results"][0]["author_id"], self.author1.author_id
		)

		# category_slug + timeframe, no team_id -- still empty; category_slug
		# keeps the team_id requirement.
		response = self.client.get(
			f"/authors/?category_slug={self.category.category_slug}&timeframe=last_month"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			response.data["count"],
			0,
			"Should return empty results when category_slug is used with timeframe but without team_id",
		)

		# Test with all filters including team_id (should work)
		response = self.client.get(
			f"/authors/?team_id={self.team.id}&subject_id={self.subject.id}&timeframe=last_month&sort_by=article_count"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		# Should not error, regardless of whether there are results

	def test_validation_error_messages_clarity(self):
		"""
		Test that the API provides clear behavior for team_id requirement violations.
		While we return empty results instead of errors for the main endpoint,
		the action endpoints should provide clear error messages.
		"""
		# Test action endpoint validation messages
		response = self.client.get("/authors/by_team_subject/")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("error", response.data)
		self.assertTrue(
			"team_id" in str(response.data["error"]).lower()
			or "subject_id" in str(response.data["error"]).lower(),
			"Error message should mention required parameters",
		)

		response = self.client.get("/authors/by_team_category/")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("error", response.data)
		self.assertTrue(
			"team_id" in str(response.data["error"]).lower()
			or "category_slug" in str(response.data["error"]).lower(),
			"Error message should mention required parameters",
		)


class AuthorRelevantArticlesCountTest(TestCase):
	"""relevant_articles_count on list/detail payloads."""

	def setUp(self):
		self.organization = Organization.objects.create(name="Relevant Count Org")
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=self.organization, name="Relevant Count Team", slug="relevant-count-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Relevant Count Subject",
			subject_slug="relevant-count-subject",
			team=self.team,
		)

		self.author = Authors.objects.create(given_name="Alice", family_name="Relevant")

		self.relevant_article = Articles.objects.create(
			title="Relevant article",
			link="http://example.com/relevant-count-1",
		)
		self.relevant_article.authors.add(self.author)
		self.relevant_article.teams.add(self.team)
		self.relevant_article.subjects.add(self.subject)
		Articles.objects.filter(pk=self.relevant_article.pk).update(relevant=True)

		self.not_relevant_article = Articles.objects.create(
			title="Not relevant article",
			link="http://example.com/relevant-count-2",
		)
		self.not_relevant_article.authors.add(self.author)
		self.not_relevant_article.teams.add(self.team)
		self.not_relevant_article.subjects.add(self.subject)

		self.client = APIClient()

	def test_relevant_articles_count_le_articles_count_in_list(self):
		response = self.client.get("/authors/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		by_id = {r["author_id"]: r for r in response.data["results"]}
		author_data = by_id[self.author.author_id]
		self.assertEqual(author_data["articles_count"], 2)
		self.assertEqual(author_data["relevant_articles_count"], 1)
		self.assertLessEqual(
			author_data["relevant_articles_count"], author_data["articles_count"]
		)

	def test_relevant_articles_count_in_detail(self):
		response = self.client.get(f"/authors/{self.author.author_id}/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["articles_count"], 2)
		self.assertEqual(response.data["relevant_articles_count"], 1)
