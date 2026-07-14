"""API-level tests for the country/region normalization fields and filters on /trials/.

Fixture/org-visibility setup mirrors api/tests/test_trial_phase_normalization.py — a
public organization is required for the default (anonymous) APIClient to see any trials
at all. See docs/TRIAL-COUNTRY-NORMALIZATION-PLAN.md for the design.
"""

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers import TrialSerializer
from gregory.models import (
	Organization,
	OrganizationApiSettings,
	Subject,
	Team,
	Trials,
)


class TrialCountryAndRegionFilterTests(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Country Filter Org", slug="country-filter-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Country Filter Team",
			slug="country-filter-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Country Filter Subject",
			subject_slug="country-filter-subject",
			team=self.team,
		)

		self.germany_trial = Trials.objects.create(
			title="Germany Trial",
			link="https://example.com/country-filter-de",
			published_date=timezone.now(),
			countries_by_source={"ctgov": "Germany"},
		)
		self.germany_trial.teams.add(self.team)
		self.germany_trial.subjects.add(self.subject)

		self.us_trial = Trials.objects.create(
			title="US Trial",
			link="https://example.com/country-filter-us",
			published_date=timezone.now(),
			countries_by_source={"ctgov": "United States"},
		)
		self.us_trial.teams.add(self.team)
		self.us_trial.subjects.add(self.subject)

		self.client = APIClient()

	def test_country_filter_returns_only_matching_trials(self):
		response = self.client.get("/trials/?country=DE")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.germany_trial.trial_id
		)

	def test_country_filter_is_case_insensitive(self):
		response = self.client.get("/trials/?country=de")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)

	def test_region_filter_returns_only_matching_trials(self):
		response = self.client.get("/trials/?region=europe")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.germany_trial.trial_id
		)

		response = self.client.get("/trials/?region=north_america")
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(
			response.data["results"][0]["trial_id"], self.us_trial.trial_id
		)

	def test_region_filter_invalid_choice_errors_cleanly(self):
		response = self.client.get("/trials/?region=not-a-real-region")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_response_body_includes_country_fields(self):
		response = self.client.get(f"/trials/?trial_id={self.germany_trial.trial_id}")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		result = response.data["results"][0]
		self.assertEqual(result["countries_normalized"], ["DE"])
		self.assertEqual(result["regions_normalized"], ["europe"])
		self.assertEqual(len(result["trial_countries"]), 1)
		self.assertEqual(result["trial_countries"][0]["country"], "DE")
		self.assertIsNone(result["trial_countries"][0]["status"])
		self.assertEqual(result["trial_countries"][0]["sources"], ["ctgov"])
		# Legacy field kept as-is for API compatibility.
		self.assertIn("countries", result)

	def test_regions_normalized_field_is_read_only_on_serializer(self):
		"""editable=False on the model field makes DRF mark it read-only automatically,
		so it can never be set via a POST body."""
		serializer = TrialSerializer()
		self.assertTrue(serializer.fields["regions_normalized"].read_only)

	def test_country_filter_matches_no_countries_gracefully(self):
		response = self.client.get("/trials/?country=ZZ")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 0)

	def test_trial_countries_query_count_is_bounded_on_list_endpoint(self):
		"""TrialViewSet.get_queryset() must prefetch trial_countries — otherwise every
		row in a list response issues its own query (see
		docs/TRIAL-COUNTRY-NORMALIZATION-PLAN.md 'API perf')."""
		from django.db import connection
		from django.test.utils import CaptureQueriesContext

		for i in range(5):
			trial = Trials.objects.create(
				title=f"Bulk trial {i}",
				link=f"https://example.com/country-filter-bulk-{i}",
				published_date=timezone.now(),
				countries_by_source={"ctgov": "France"},
			)
			trial.teams.add(self.team)
			trial.subjects.add(self.subject)

		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get("/trials/?page_size=50")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		# A handful of fixed-cost queries (count, org visibility, main select, a few
		# prefetches) — NOT one additional query per trial row.
		self.assertLess(len(ctx.captured_queries), 20)
