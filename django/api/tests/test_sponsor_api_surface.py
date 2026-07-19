"""Tests for the sponsor API surface (PR S): the nested ``sponsor`` object on
TrialSerializer, the ``sponsor_id``/``sponsor_slug`` trial filters, the
``by_sponsor``/``by_sponsor_type`` facets on ``/trials/stats/``, and the
``/sponsors/`` endpoint.

Trials auto-resolve their canonical Sponsor on save() (see
Trials._resolve_primary_sponsor() / docs/trials-field-normalization.md) — these
tests lean on that rather than constructing Sponsor/SponsorAlias rows by hand.

Trials with no team are invisible to OrgVisibilityMixin (Exists() over an empty
teams M2M is always false), so every trial here is attached to a public-org team —
same fixture shape as api/tests/test_trials_stats.py.

Run with:
    docker exec gregory python manage.py test api.tests.test_sponsor_api_surface
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from organizations.models import Organization
from rest_framework.test import APIClient

from gregory.models import OrganizationApiSettings, Sponsor, Team, Trials
from gregory.utils.trial_field_normalizers import SponsorType


class SponsorAPITestCase(TestCase):
	def setUp(self):
		self.client = APIClient()
		org = Organization.objects.create(name="Sponsor API Org", slug="sponsor-api-org")
		OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=True)
		self.team = Team.objects.create(organization=org, name="Sponsor API Org", slug="sponsor-api-org")

	def _make_trial(self, title, link, primary_sponsor=None):
		trial = Trials.objects.create(title=title, link=link, primary_sponsor=primary_sponsor)
		trial.teams.add(self.team)
		return trial


class SponsorSerializerTests(SponsorAPITestCase):
	def test_sponsor_field_populated_when_resolved(self):
		trial = self._make_trial(
			"Sponsored Trial", "https://example.com/sponsor-ser-1", "Acme Research Corp"
		)
		resp = self.client.get(f"/trials/{trial.trial_id}/")
		self.assertEqual(resp.status_code, 200)
		sponsor = resp.data["sponsor"]
		self.assertIsNotNone(sponsor)
		self.assertEqual(sponsor["name"], "Acme Research Corp")
		self.assertIn("id", sponsor)
		self.assertIn("slug", sponsor)
		self.assertIn("sponsor_type", sponsor)
		# Raw fields stay untouched alongside the new nested object.
		self.assertEqual(resp.data["primary_sponsor"], "Acme Research Corp")

	def test_sponsor_field_null_when_unresolved(self):
		trial = self._make_trial(
			"Unsponsored Trial", "https://example.com/sponsor-ser-2", None
		)
		resp = self.client.get(f"/trials/{trial.trial_id}/")
		self.assertEqual(resp.status_code, 200)
		self.assertIsNone(resp.data["sponsor"])


class SponsorFilterRoundTripTests(SponsorAPITestCase):
	def setUp(self):
		super().setUp()
		self.trial = self._make_trial(
			"Filter Trial", "https://example.com/sponsor-filter-1", "Round Trip Corp"
		)
		self.sponsor = self.trial.primary_sponsor_normalized
		self.other_trial = self._make_trial(
			"Other Filter Trial", "https://example.com/sponsor-filter-2", "Other Corp"
		)

	def test_sponsor_id_filter(self):
		resp = self.client.get("/trials/", {"sponsor_id": self.sponsor.pk})
		self.assertEqual(resp.status_code, 200)
		ids = [row["trial_id"] for row in resp.data["results"]]
		self.assertIn(self.trial.trial_id, ids)
		self.assertNotIn(self.other_trial.trial_id, ids)

	def test_sponsor_slug_filter(self):
		resp = self.client.get("/trials/", {"sponsor_slug": self.sponsor.slug})
		self.assertEqual(resp.status_code, 200)
		ids = [row["trial_id"] for row in resp.data["results"]]
		self.assertIn(self.trial.trial_id, ids)
		self.assertNotIn(self.other_trial.trial_id, ids)

	def test_legacy_primary_sponsor_filter_still_works(self):
		resp = self.client.get("/trials/", {"primary_sponsor": "Round Trip"})
		self.assertEqual(resp.status_code, 200)
		ids = [row["trial_id"] for row in resp.data["results"]]
		self.assertIn(self.trial.trial_id, ids)


class SponsorFacetsTests(SponsorAPITestCase):
	def test_merged_family_returns_one_row(self):
		# Two raw spellings that normalize to the same key resolve to the same
		# Sponsor — the facet must report them as a single row with count=2, not
		# two separate rows.
		self._make_trial(
			"Merge A", "https://example.com/sponsor-facet-merge-1", "Merge Family Corp"
		)
		self._make_trial(
			"Merge B",
			"https://example.com/sponsor-facet-merge-2",
			"  merge family corp  ",
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		rows = [
			row for row in resp.data["by_sponsor"] if row["name"] == "Merge Family Corp"
		]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["count"], 2)

	def test_by_sponsor_capped_at_25(self):
		for i in range(30):
			self._make_trial(
				f"Cap Trial {i:02d}",
				f"https://example.com/sponsor-facet-cap-{i:02d}",
				f"Cap Sponsor {i:02d}",
			)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		cap_rows = [
			row for row in resp.data["by_sponsor"] if row["name"].startswith("Cap Sponsor")
		]
		self.assertEqual(len(cap_rows), 25)

	def test_no_sponsor_counts_unresolved_trials(self):
		self._make_trial(
			"Resolved", "https://example.com/sponsor-facet-nosponsor-1", "Resolved Corp"
		)
		self._make_trial(
			"Unresolved 1", "https://example.com/sponsor-facet-nosponsor-2", None
		)
		self._make_trial(
			"Unresolved 2", "https://example.com/sponsor-facet-nosponsor-3", None
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["no_sponsor"], 2)

	def test_by_sponsor_type_all_keys_present_incl_no_type(self):
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		by_type = resp.data["by_sponsor_type"]
		self.assertEqual(set(by_type.keys()), set(SponsorType.values) | {"no_type"})

	def test_by_sponsor_type_no_type_excludes_unresolved(self):
		# A resolved-but-untyped sponsor lands in no_type; an unresolved trial
		# (no sponsor FK at all) must NOT also land in no_type — it's reported
		# separately via no_sponsor. "Xylo Research Alpha" deliberately avoids
		# every keyword in the rules classifier (industry/academic/government/
		# nonprofit), so save() leaves sponsor_type unset rather than guessing one.
		self._make_trial(
			"Untyped", "https://example.com/sponsor-facet-notype-1", "Xylo Research Alpha"
		)
		self._make_trial(
			"No Sponsor At All", "https://example.com/sponsor-facet-notype-2", None
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertGreaterEqual(resp.data["by_sponsor_type"]["no_type"], 1)
		self.assertGreaterEqual(resp.data["no_sponsor"], 1)

	def test_by_sponsor_type_scoped_by_filter(self):
		industry_trial = self._make_trial(
			"Industry Trial", "https://example.com/sponsor-facet-scope-1", "Scoped Pharma Inc."
		)
		resp = self.client.get(
			"/trials/stats/", {"sponsor_id": industry_trial.primary_sponsor_normalized_id}
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["total"], 1)


class SponsorViewSetTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.industry = Sponsor.objects.create(
			name="Industry Sponsor Co", slug="industry-sponsor-co", sponsor_type="industry"
		)
		self.nonprofit = Sponsor.objects.create(
			name="Nonprofit Sponsor Org", slug="nonprofit-sponsor-org", sponsor_type="nonprofit"
		)
		for i in range(3):
			Trials.objects.create(
				title=f"Industry Trial {i}",
				link=f"https://example.com/sponsor-viewset-{i}",
				primary_sponsor=None,
			)
		# Attach trials to self.industry without going through resolution, to
		# control the exact trials_count independent of the save() hook.
		Trials.objects.filter(
			link__startswith="https://example.com/sponsor-viewset-"
		).update(primary_sponsor_normalized=self.industry)

	def test_list_and_detail_routing(self):
		resp = self.client.get("/sponsors/")
		self.assertEqual(resp.status_code, 200)
		names = [row["name"] for row in resp.data["results"]]
		self.assertIn("Industry Sponsor Co", names)

		detail = self.client.get(f"/sponsors/{self.industry.pk}/")
		self.assertEqual(detail.status_code, 200)
		self.assertEqual(detail.data["name"], "Industry Sponsor Co")
		self.assertEqual(detail.data["trials_count"], 3)

	def test_sponsor_type_filter(self):
		resp = self.client.get("/sponsors/", {"sponsor_type": "nonprofit"})
		self.assertEqual(resp.status_code, 200)
		names = [row["name"] for row in resp.data["results"]]
		self.assertIn("Nonprofit Sponsor Org", names)
		self.assertNotIn("Industry Sponsor Co", names)

	def test_search_by_name(self):
		resp = self.client.get("/sponsors/", {"search": "Nonprofit Sponsor"})
		self.assertEqual(resp.status_code, 200)
		names = [row["name"] for row in resp.data["results"]]
		self.assertIn("Nonprofit Sponsor Org", names)
		self.assertNotIn("Industry Sponsor Co", names)

	def test_ordering_by_trials_count_desc(self):
		resp = self.client.get("/sponsors/", {"ordering": "-trials_count"})
		self.assertEqual(resp.status_code, 200)
		names = [row["name"] for row in resp.data["results"]]
		self.assertEqual(names[0], "Industry Sponsor Co")

	def test_page_size_capped_at_100(self):
		resp = self.client.get("/sponsors/", {"page_size": "500"})
		self.assertEqual(resp.status_code, 200)
		self.assertLessEqual(resp.data["page_size"], 100)


class SponsorQueryCountTests(SponsorAPITestCase):
	"""N+1 guards: query count must stay flat as row count grows."""

	def test_trials_list_query_count_flat_vs_page_size(self):
		for i in range(8):
			self._make_trial(
				f"N+1 Trial {i}",
				f"https://example.com/sponsor-n1-trial-{i}",
				f"N1 Sponsor {i % 3}",  # a few repeated sponsors, a few distinct
			)

		with CaptureQueriesContext(connection) as small:
			resp_small = self.client.get("/trials/?page_size=2")
		self.assertEqual(resp_small.status_code, 200)

		with CaptureQueriesContext(connection) as large:
			resp_large = self.client.get("/trials/?page_size=8")
		self.assertEqual(resp_large.status_code, 200)

		self.assertEqual(
			len(small.captured_queries),
			len(large.captured_queries),
			msg="Query count must not scale with page size — check select_related('primary_sponsor_normalized')",
		)

	def test_sponsors_list_query_count_flat_vs_page_size(self):
		for i in range(8):
			Sponsor.objects.create(name=f"N1 Sponsor List {i}", slug=f"n1-sponsor-list-{i}")

		with CaptureQueriesContext(connection) as small:
			resp_small = self.client.get("/sponsors/?page_size=2")
		self.assertEqual(resp_small.status_code, 200)

		with CaptureQueriesContext(connection) as large:
			resp_large = self.client.get("/sponsors/?page_size=8")
		self.assertEqual(resp_large.status_code, 200)

		self.assertEqual(
			len(small.captured_queries),
			len(large.captured_queries),
			msg="Query count must not scale with page size on /sponsors/",
		)
