"""API-level tests for TrialSite exposure (TRIAL-GEOGRAPHY-PLAN.md PR G3):
detail-only `trial_sites` on `GET /trials/{id}/`, and the flat, filterable,
paginated `GET /trials/sites/` listing.

Fixture/org-visibility setup mirrors api/tests/test_trial_country_normalization.py —
a public organization is required for the default (anonymous) APIClient to see any
trials at all.
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from gregory.models import (
	Organization,
	OrganizationApiSettings,
	Subject,
	Team,
	Trials,
	TrialSite,
)


class TrialSiteAPITests(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Trial Site API Org", slug="trial-site-api-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Trial Site API Team",
			slug="trial-site-api-team",
			organization=self.organization,
		)
		self.subject = Subject.objects.create(
			subject_name="Trial Site API Subject",
			subject_slug="trial-site-api-subject",
			team=self.team,
		)
		self.other_subject = Subject.objects.create(
			subject_name="Other Subject",
			subject_slug="trial-site-api-other-subject",
			team=self.team,
		)

		self.trial = Trials.objects.create(
			title="Site API Test Trial",
			link="https://example.com/trial-site-api",
			published_date=timezone.now(),
		)
		self.trial.teams.add(self.team)
		self.trial.subjects.add(self.subject)

		self.site_de = TrialSite.objects.create(
			trial=self.trial,
			name="Site A",
			city="Berlin",
			country="DE",
			latitude=52.52,
			longitude=13.405,
			sources=["ctgov"],
		)
		self.site_ctis = TrialSite.objects.create(
			trial=self.trial,
			name="Site B",
			city="Rome",
			country="IT",
			sources=["ctis"],
		)

		self.client = APIClient()

	def test_list_response_has_no_trial_sites_key(self):
		response = self.client.get("/trials/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		result = next(
			r for r in response.data["results"] if r["trial_id"] == self.trial.trial_id
		)
		self.assertNotIn("trial_sites", result)

	def test_list_query_count_is_not_affected_by_number_of_sites(self):
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get("/trials/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		baseline_queries = len(ctx.captured_queries)

		for i in range(10):
			TrialSite.objects.create(
				trial=self.trial, name=f"Extra {i}", city="X", sources=["ctgov"]
			)

		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get("/trials/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(ctx.captured_queries), baseline_queries)

	def test_detail_response_includes_trial_sites(self):
		response = self.client.get(f"/trials/{self.trial.trial_id}/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		sites = response.data["trial_sites"]
		self.assertEqual(len(sites), 2)
		names = {s["name"] for s in sites}
		self.assertEqual(names, {"Site A", "Site B"})
		by_name = {s["name"]: s for s in sites}
		self.assertEqual(by_name["Site A"]["country"], "DE")
		self.assertEqual(by_name["Site A"]["latitude"], 52.52)
		self.assertEqual(by_name["Site A"]["sources"], ["ctgov"])
		self.assertIsNone(by_name["Site B"]["latitude"])

	def test_sites_endpoint_returns_flat_rows(self):
		response = self.client.get("/trials/sites/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		rows = response.data["results"]
		self.assertEqual(len(rows), 2)
		row = rows[0]
		self.assertEqual(
			set(row.keys()),
			{"trial_id", "name", "city", "country", "latitude", "longitude"},
		)

	def test_sites_endpoint_honours_country_filter(self):
		response = self.client.get("/trials/sites/", {"country": "DE"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		# country filters Trials (via TrialCountry), not TrialSite rows directly —
		# this trial has no TrialCountry data, so it should not match.
		self.assertEqual(len(response.data["results"]), 0)

	def test_sites_endpoint_honours_subject_id_filter(self):
		response = self.client.get(
			"/trials/sites/", {"subject_id": self.subject.pk}
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 2)

		response = self.client.get(
			"/trials/sites/", {"subject_id": self.other_subject.pk}
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 0)

	def test_sites_endpoint_latitude_isnull_filter(self):
		response = self.client.get("/trials/sites/", {"latitude__isnull": "false"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(response.data["results"][0]["name"], "Site A")

		response = self.client.get("/trials/sites/", {"latitude__isnull": "true"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 1)
		self.assertEqual(response.data["results"][0]["name"], "Site B")

	def test_sites_endpoint_rejects_all_results(self):
		response = self.client.get("/trials/sites/", {"all_results": "true"})
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_sites_endpoint_all_results_false_is_not_a_bypass(self):
		"""all_results=false is not a pagination-bypass value (see
		request_bypasses_pagination) — must paginate normally, not 400."""
		response = self.client.get("/trials/sites/", {"all_results": "false"})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn("count", response.data)

	def test_sites_endpoint_rejects_invalid_latitude_isnull_value(self):
		"""Regression guard (Copilot review on PR #791): an unrecognized
		latitude__isnull value must 400, not silently fall through to False."""
		response = self.client.get("/trials/sites/", {"latitude__isnull": "banana"})
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_sites_endpoint_accepts_numeric_and_word_latitude_isnull_values(self):
		# Site A has coordinates (latitude not null); Site B does not.
		for value, expected_name in (("1", "Site B"), ("0", "Site A"), ("yes", "Site B"), ("no", "Site A")):
			with self.subTest(value=value):
				response = self.client.get(
					"/trials/sites/", {"latitude__isnull": value}
				)
				self.assertEqual(response.status_code, status.HTTP_200_OK)
				self.assertEqual(len(response.data["results"]), 1)
				self.assertEqual(response.data["results"][0]["name"], expected_name)

	def test_sites_endpoint_paginates(self):
		for i in range(5):
			TrialSite.objects.create(
				trial=self.trial, name=f"Bulk {i}", city="X", sources=["ctgov"]
			)
		response = self.client.get("/trials/sites/", {"page_size": 3})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data["results"]), 3)
		self.assertEqual(response.data["count"], 7)

	def test_sites_endpoint_query_count_is_flat_vs_page_size(self):
		for i in range(20):
			TrialSite.objects.create(
				trial=self.trial, name=f"Flat {i}", city="X", sources=["ctgov"]
			)

		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get("/trials/sites/", {"page_size": 5})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		small_page_queries = len(ctx.captured_queries)

		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get("/trials/sites/", {"page_size": 22})
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(ctx.captured_queries), small_page_queries)
