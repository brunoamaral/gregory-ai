"""
Regression tests for CategoryViewSet's switch from prefetching every
article/trial row to Count(..., distinct=True) annotations.

Run with:
    docker exec gregory python manage.py test api.tests.test_category_count_annotations
"""

from django.db import connection
from django.db.models import Count
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.test import APIClient

from api.serializers import CategorySerializer
from gregory.models import (
	Articles,
	Organization,
	OrganizationApiSettings,
	Subject,
	Team,
	TeamCategory,
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
				article_count_annotated=Count("articles", distinct=True),
				trials_count_annotated=Count("trials", distinct=True),
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
