from datetime import datetime

from django.test import TestCase, RequestFactory
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.filters import ArticleFilter
from gregory.models import Articles, Organization, OrganizationApiSettings, Subject, Team


class ArticleDateRangeFilterTests(TestCase):
	"""Tests for published_date_after / published_date_before on ArticleFilter."""

	def setUp(self):
		self.factory = RequestFactory()

		self.org = Organization.objects.create(name="Date Test Org", slug="date-test-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Date Test Team", slug="date-test-team", organization=self.org
		)
		self.subject = Subject.objects.create(
			subject_name="Date Subject",
			subject_slug="date-subject",
			team=self.team,
		)

		def make_article(title, link, pub_date):
			a = Articles.objects.create(title=title, link=link)
			a.published_date = pub_date
			a.save(update_fields=["published_date"])
			a.teams.add(self.team)
			a.subjects.add(self.subject)
			return a

		# Spread articles across 2022-2024 including boundary edge cases
		self.a_2022 = make_article(
			"Article 2022-06-01",
			"https://example.com/1",
			timezone.make_aware(datetime(2022, 6, 1, 9, 0)),
		)
		self.a_2023_start = make_article(
			"Article 2023-01-01",
			"https://example.com/2",
			timezone.make_aware(datetime(2023, 1, 1, 0, 0)),
		)
		# Late on the end-boundary day — the key test for DateTimeField inclusive-end
		self.a_2023_end = make_article(
			"Article 2023-12-31 late",
			"https://example.com/3",
			timezone.make_aware(datetime(2023, 12, 31, 23, 30)),
		)
		self.a_2024 = make_article(
			"Article 2024-02-01",
			"https://example.com/4",
			timezone.make_aware(datetime(2024, 2, 1, 12, 0)),
		)
		# Article with no published_date (NULL)
		self.a_null = Articles.objects.create(
			title="No pub date",
			link="https://example.com/null",
		)
		self.a_null.teams.add(self.team)

	def _filter(self, params):
		request = self.factory.get("/articles/", params)
		qs = Articles.objects.all()
		return ArticleFilter(request.GET, queryset=qs, request=request).qs

	# --- after only ---

	def test_after_only(self):
		qs = self._filter({"published_date_after": "2023-01-01"})
		pks = set(qs.values_list("article_id", flat=True))
		self.assertIn(self.a_2023_start.article_id, pks)
		self.assertIn(self.a_2023_end.article_id, pks)
		self.assertIn(self.a_2024.article_id, pks)
		self.assertNotIn(self.a_2022.article_id, pks)

	def test_after_boundary_inclusive(self):
		qs = self._filter({"published_date_after": "2022-06-01"})
		pks = set(qs.values_list("article_id", flat=True))
		self.assertIn(self.a_2022.article_id, pks)

	# --- before only ---

	def test_before_only(self):
		qs = self._filter({"published_date_before": "2023-12-31"})
		pks = set(qs.values_list("article_id", flat=True))
		self.assertIn(self.a_2022.article_id, pks)
		self.assertIn(self.a_2023_start.article_id, pks)
		# Late on 2023-12-31 must be included (whole-day inclusive end via __date__lte)
		self.assertIn(self.a_2023_end.article_id, pks)
		self.assertNotIn(self.a_2024.article_id, pks)

	def test_before_boundary_inclusive(self):
		qs = self._filter({"published_date_before": "2024-02-01"})
		pks = set(qs.values_list("article_id", flat=True))
		self.assertIn(self.a_2024.article_id, pks)

	# --- closed range ---

	def test_closed_range(self):
		qs = self._filter({
			"published_date_after": "2023-01-01",
			"published_date_before": "2023-12-31",
		})
		pks = set(qs.values_list("article_id", flat=True))
		self.assertIn(self.a_2023_start.article_id, pks)
		self.assertIn(self.a_2023_end.article_id, pks)
		self.assertNotIn(self.a_2022.article_id, pks)
		self.assertNotIn(self.a_2024.article_id, pks)

	def test_closed_range_count(self):
		qs = self._filter({
			"published_date_after": "2023-01-01",
			"published_date_before": "2023-12-31",
		})
		self.assertEqual(qs.count(), 2)

	# --- no params is a no-op ---

	def test_no_date_params_returns_all(self):
		qs = self._filter({})
		self.assertEqual(qs.count(), Articles.objects.count())

	# --- NULL published_date rows are excluded by both filters ---

	def test_null_published_date_excluded(self):
		qs = self._filter({"published_date_after": "2020-01-01"})
		pks = set(qs.values_list("article_id", flat=True))
		self.assertNotIn(self.a_null.article_id, pks)

	# --- compose with subjects ---

	def test_compose_with_subjects(self):
		qs = self._filter({
			"published_date_after": "2023-01-01",
			"published_date_before": "2023-12-31",
			"subjects": str(self.subject.id),
		})
		self.assertEqual(qs.count(), 2)

	# --- compose with search ---

	def test_compose_with_search(self):
		qs = self._filter({
			"published_date_after": "2023-01-01",
			"published_date_before": "2023-12-31",
			"search": "2023-01-01",
		})
		self.assertEqual(qs.count(), 1)
		self.assertEqual(qs.first().article_id, self.a_2023_start.article_id)


class ArticleDateRangeAPITests(TestCase):
	"""HTTP-level tests: valid params return 200, invalid dates return 400."""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(
			name="API Date Org", slug="api-date-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="API Date Team", slug="api-date-team", organization=self.org
		)

	def test_valid_date_range_returns_200(self):
		response = self.client.get(
			"/articles/",
			{"published_date_after": "2023-01-01", "published_date_before": "2023-12-31"},
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_invalid_month_returns_400(self):
		response = self.client.get(
			"/articles/", {"published_date_after": "2023-13-40"}
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_non_date_string_returns_400(self):
		response = self.client.get(
			"/articles/", {"published_date_after": "not-a-date"}
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_non_iso_format_returns_400(self):
		response = self.client.get(
			"/articles/", {"published_date_before": "31/12/2023"}
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
