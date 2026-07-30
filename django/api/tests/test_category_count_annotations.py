"""
Regression tests for CategoryViewSet's switch from prefetching every
article/trial row to correlated subquery count annotations.

The counts must NOT be computed by annotating both the articles and trials
M2M relations with Count(..., distinct=True) in a single query: joining both
relations at once fans out to an articles x trials cross product per
category, which is cheap for small categories but spilled Postgres's hash
aggregate to disk in production for a category with thousands of rows on
each side. See api.views._category_through_count_subquery.

Run with:
    docker exec gregory python manage.py test api.tests.test_category_count_annotations
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.test import APIClient

from api.serializers import CategorySerializer
from api.views import CategoryViewSet, _category_through_count_subquery
from gregory.models import (
	ArticleCategoryAssignment,
	Articles,
	Authors,
	Organization,
	OrganizationApiSettings,
	Subject,
	Team,
	TeamCategory,
	TrialCategoryAssignment,
	Trials,
)


def _make_category(team, subject, name):
	cat = TeamCategory.objects.create(
		team=team, category_name=name, category_slug=f"{team.slug}-{slugify(name)}"
	)
	cat.subjects.add(subject)
	return cat


class CategoryCountAnnotationTests(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Category Count Org", slug="category-count-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Category Count Team",
			slug="category-count-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Category Count Subject",
			subject_slug="category-count-subject",
			team=self.team,
		)
		self.category = _make_category(self.team, self.subject, "Count Test Category")

		for i in range(5):
			article = Articles.objects.create(
				title=f"Category Count Article {i}",
				link=f"https://example.com/category-count-article-{i}",
				published_date=timezone.now(),
			)
			article.team_categories.add(self.category)

		for i in range(3):
			trial = Trials.objects.create(
				title=f"Category Count Trial {i}",
				link=f"https://example.com/category-count-trial-{i}",
				published_date=timezone.now(),
			)
			trial.team_categories.add(self.category)

		self.client = APIClient()

	def test_counts_correct_and_query_count_independent_of_row_volume(self):
		url = reverse("categories-list")
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(url, {"category_id": self.category.id})
		self.assertEqual(response.status_code, 200)

		results = response.data["results"] if "results" in response.data else response.data
		self.assertEqual(len(results), 1)
		payload = results[0]
		self.assertEqual(payload["article_count_total"], 5)
		self.assertEqual(payload["trials_count_total"], 3)

		# A handful of queries regardless of category size — materialising
		# every article/trial row here would scale with row count instead.
		self.assertLess(len(ctx.captured_queries), 20)

	def test_serializer_payload_matches_between_annotated_and_live_query(self):
		"""The annotated queryset (mirroring CategoryViewSet.get_queryset)
		and a plain (un-annotated) instance must produce identical
		article/trial counts."""
		annotated_obj = (
			TeamCategory.objects.annotate(
				article_count_annotated=_category_through_count_subquery(
					ArticleCategoryAssignment
				),
				trials_count_annotated=_category_through_count_subquery(
					TrialCategoryAssignment
				),
			)
			.get(pk=self.category.pk)
		)
		live_obj = TeamCategory.objects.get(pk=self.category.pk)

		context = {"author_params": {"include_authors": False}, "monthly_counts_params": {}}
		annotated_payload = CategorySerializer(annotated_obj, context=context).data
		live_payload = CategorySerializer(live_obj, context=context).data

		self.assertEqual(
			annotated_payload["article_count_total"], live_payload["article_count_total"]
		)
		self.assertEqual(
			annotated_payload["trials_count_total"], live_payload["trials_count_total"]
		)
		self.assertEqual(annotated_payload["article_count_total"], 5)
		self.assertEqual(annotated_payload["trials_count_total"], 3)

	def test_no_query_joins_both_through_tables(self):
		"""Guards against reintroducing the fan-out regression: no single
		query in the request should join both articles_team_categories and
		trials_team_categories, since that produces an articles x trials
		cross product per category (fine for a handful of rows, but spilled
		Postgres's hash aggregate to disk in production for a category with
		thousands of articles and trials)."""
		# Give the category enough rows on both sides that a cross product
		# would be detectable (25 x 15 = 375 vs. 25 + 15 = 40 if summed).
		for i in range(20):
			article = Articles.objects.create(
				title=f"Fanout Guard Article {i}",
				link=f"https://example.com/fanout-guard-article-{i}",
				published_date=timezone.now(),
			)
			article.team_categories.add(self.category)
		for i in range(12):
			trial = Trials.objects.create(
				title=f"Fanout Guard Trial {i}",
				link=f"https://example.com/fanout-guard-trial-{i}",
				published_date=timezone.now(),
			)
			trial.team_categories.add(self.category)

		url = reverse("categories-list")
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(url, {"category_id": self.category.id})
		self.assertEqual(response.status_code, 200)

		results = response.data["results"] if "results" in response.data else response.data
		payload = results[0]
		self.assertEqual(payload["article_count_total"], 25)
		self.assertEqual(payload["trials_count_total"], 15)

		# The fan-out bug's signature is a single query that JOINs BOTH
		# through tables at once (Count("articles"/"trials", distinct=True)
		# in one annotate() compiles to two JOINs into the same FROM
		# clause). Other queries in this request legitimately JOIN one of
		# these tables alone (e.g. authors_count), which is fine — only
		# joining both together produces the articles x trials cross
		# product. The fix references each through table via its own
		# independent correlated subquery (FROM ... WHERE teamcategory_id =
		# outer.id), never via JOIN at all.
		for query in ctx.captured_queries:
			sql = query["sql"]
			joins_articles = 'JOIN "articles_team_categories"' in sql
			joins_trials = 'JOIN "trials_team_categories"' in sql
			self.assertFalse(
				joins_articles and joins_trials,
				f"Query joins both through tables, reintroducing the fan-out: {sql}",
			)


class CategoryAuthorsCountOrderingTests(TestCase):
	"""`authors_count_annotated` is listed in CategoryViewSet.ordering_fields,
	so DRF's OrderingFilter passes it straight through to order_by() instead
	of discarding it as an unknown field. When the queryset doesn't annotate
	that name the request dies with
	`FieldError: Cannot resolve keyword 'authors_count_annotated' into field`
	— a 500 seen in production. The annotation is expensive (distinct authors
	across every article in the category), so it is added only for requests
	that actually sort by it; these tests pin both halves of that deal.
	"""

	def setUp(self):
		self.organization = Organization.objects.create(
			name="Authors Ordering Org", slug="authors-ordering-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Authors Ordering Team",
			slug="authors-ordering-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Authors Ordering Subject",
			subject_slug="authors-ordering-subject",
			team=self.team,
		)

		# Two categories with a known, different number of distinct authors.
		# The 3-author category shares one author across two articles, so a
		# count that forgets DISTINCT would report 4 and flip the sort order.
		self.few_authors = _make_category(self.team, self.subject, "Few Authors")
		self.many_authors = _make_category(self.team, self.subject, "Many Authors")

		authors = [
			Authors.objects.create(
				given_name=f"Author{i}", family_name="Test", full_name=f"Author{i} Test"
			)
			for i in range(5)
		]

		shared = authors[0]
		for i, extra in enumerate(authors[1:3]):
			article = Articles.objects.create(
				title=f"Many Authors Article {i}",
				link=f"https://example.com/many-authors-article-{i}",
				published_date=timezone.now(),
			)
			article.team_categories.add(self.many_authors)
			article.authors.add(shared, extra)

		article = Articles.objects.create(
			title="Few Authors Article",
			link="https://example.com/few-authors-article",
			published_date=timezone.now(),
		)
		article.team_categories.add(self.few_authors)
		article.authors.add(authors[3])

		self.client = APIClient()

	def _results(self, response):
		return response.data["results"] if "results" in response.data else response.data

	def test_ordering_by_authors_count_does_not_500(self):
		url = reverse("categories-list")
		response = self.client.get(
			url,
			{
				"team_id": self.team.id,
				"ordering": "-authors_count_annotated",
				"include_authors": "false",
			},
		)
		self.assertEqual(response.status_code, 200)

		results = self._results(response)
		names = [row["category_name"] for row in results]
		self.assertEqual(names, ["Many Authors", "Few Authors"])
		self.assertEqual(results[0]["authors_count"], 3)
		self.assertEqual(results[1]["authors_count"], 1)

	def test_ascending_ordering_by_authors_count_does_not_500(self):
		"""The `-` prefix must not be what makes the name resolvable."""
		url = reverse("categories-list")
		response = self.client.get(
			url,
			{
				"team_id": self.team.id,
				"ordering": "authors_count_annotated",
				"include_authors": "false",
			},
		)
		self.assertEqual(response.status_code, 200)
		names = [row["category_name"] for row in self._results(response)]
		self.assertEqual(names, ["Few Authors", "Many Authors"])

	def test_authors_count_ordering_works_alongside_another_term(self):
		"""DRF accepts a comma-separated ordering list; the annotation has to
		be added when the expensive term is anywhere in it, not just first."""
		url = reverse("categories-list")
		response = self.client.get(
			url,
			{
				"team_id": self.team.id,
				"ordering": "category_name,-authors_count_annotated",
				"include_authors": "false",
			},
		)
		self.assertEqual(response.status_code, 200)

	def test_authors_count_not_annotated_unless_sorted_by(self):
		"""The whole point of the conditional: an ordinary list request must
		not pay for the distinct-author count."""
		url = reverse("categories-list")
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(
				url, {"team_id": self.team.id, "include_authors": "false"}
			)
		self.assertEqual(response.status_code, 200)

		self.assertFalse(
			any("authors_count_annotated" in q["sql"] for q in ctx.captured_queries),
			"Default /categories/ request computed the expensive authors count",
		)

	def test_count_orderings_are_all_resolvable(self):
		"""Every name in ordering_fields must resolve against the default
		queryset (or be added to it on demand, as authors_count_annotated is).
		A name that is whitelisted but never annotated reaches order_by() and
		raises FieldError — the production 500 this module guards.
		"""
		url = reverse("categories-list")
		for field in CategoryViewSet.ordering_fields:
			for term in (field, f"-{field}"):
				with self.subTest(ordering=term):
					response = self.client.get(
						url,
						{
							"team_id": self.team.id,
							"ordering": term,
							"include_authors": "false",
						},
					)
					self.assertEqual(response.status_code, 200)

	def test_ordering_by_trials_count_uses_the_free_annotation(self):
		"""trials_count_annotated is always on the queryset, so sorting by it
		must not add the expensive authors count."""
		Trials.objects.create(
			title="Ordering Trial",
			link="https://example.com/ordering-trial",
			published_date=timezone.now(),
		).team_categories.add(self.many_authors)

		url = reverse("categories-list")
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(
				url,
				{
					"team_id": self.team.id,
					"ordering": "-trials_count_annotated",
					"include_authors": "false",
				},
			)
		self.assertEqual(response.status_code, 200)

		results = self._results(response)
		self.assertEqual(results[0]["category_name"], "Many Authors")
		self.assertEqual(results[0]["trials_count_total"], 1)
		self.assertEqual(results[1]["trials_count_total"], 0)
		self.assertFalse(
			any("authors_count_annotated" in q["sql"] for q in ctx.captured_queries),
			"Sorting by trials count computed the expensive authors count",
		)

	def test_unknown_ordering_field_still_falls_back_silently(self):
		"""Guards the assumption behind the fix: DRF drops ordering names that
		are NOT in ordering_fields, which is why only the whitelisted-but-
		unannotated name could ever reach order_by() and raise FieldError."""
		url = reverse("categories-list")
		response = self.client.get(
			url,
			{
				"team_id": self.team.id,
				"ordering": "no_such_field",
				"include_authors": "false",
			},
		)
		self.assertEqual(response.status_code, 200)
		names = [row["category_name"] for row in self._results(response)]
		self.assertEqual(names, sorted(names))
