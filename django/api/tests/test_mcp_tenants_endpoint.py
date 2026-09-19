"""
Tests for GET /tenants/ (api.views.McpTenantsView) -- the tenant config
endpoint for MCP multi-tenancy Phase 2. See
MCP-MULTI-TENANCY-PHASE-2-PLAN.md, task C6.

Sites are built with publish_subjects()/private_site_publishing()
(api/tests/visibility_helpers.py), same as
test_site_resolution_visibility.py. Neither helper sets mcp_enabled, so
every test that wants a real tenant flips it on the returned Site's
CustomSetting row explicitly.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.models import OrganizationSite, Subject, Team
from sitesettings.models import CustomSetting, SiteMcpDocument, SiteMcpPrompt

User = get_user_model()


def _enable_mcp(site):
	"""Fetch site's (lowest-setting_id) CustomSetting row and turn
	mcp_enabled on, returning the row."""
	setting = CustomSetting.objects.filter(site=site).order_by("setting_id").first()
	setting.mcp_enabled = True
	setting.save()
	return setting


class McpTenantsAnonymousTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.org = Organization.objects.create(name="Tenants Org", slug="tenants-org")
		self.team = Team.objects.create(
			organization=self.org, name="Tenants Team", slug="tenants-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Tenants Subject", subject_slug="tenants-subj", team=self.team
		)

	def test_eligible_public_site_is_listed_with_exactly_the_c3_keys(self):
		site = publish_subjects(self.subject, organization=self.org, name="Eligible Site")
		setting = _enable_mcp(site)
		setting.mcp_description = "A test tenant."
		setting.save()

		resp = self.client.get(reverse("mcp_tenants"))
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(len(resp.data), 1)
		row = resp.data[0]
		self.assertEqual(
			set(row.keys()),
			{
				"site_id",
				"domain",
				"name",
				"title",
				"api_public",
				"mcp_description",
				"subjects",
				"prompts",
				"documents",
			},
		)
		self.assertEqual(row["site_id"], site.pk)
		self.assertEqual(row["domain"], site.domain)
		self.assertEqual(row["name"], site.name)
		self.assertEqual(row["title"], setting.title)
		self.assertTrue(row["api_public"])
		self.assertEqual(row["mcp_description"], "A test tenant.")

	def test_site_with_mcp_enabled_off_is_absent(self):
		publish_subjects(self.subject, organization=self.org, name="Not MCP Site")
		# mcp_enabled left at its default (False).
		resp = self.client.get(reverse("mcp_tenants"))
		self.assertEqual(resp.data, [])

	def test_public_site_with_empty_scope_is_absent(self):
		site = publish_subjects(organization=self.org, name="Empty Scope Site")
		_enable_mcp(site)
		resp = self.client.get(reverse("mcp_tenants"))
		self.assertEqual(resp.data, [])

	def test_private_site_with_mcp_enabled_is_absent(self):
		site = private_site_publishing(
			self.subject, organization=self.org, name="Private MCP Site"
		)
		_enable_mcp(site)
		resp = self.client.get(reverse("mcp_tenants"))
		self.assertEqual(resp.data, [])


class McpTenantsContentTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.org = Organization.objects.create(name="Content Org", slug="content-org")
		self.team = Team.objects.create(
			organization=self.org, name="Content Team", slug="content-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Content Subject", subject_slug="content-subj", team=self.team
		)
		self.site = publish_subjects(
			self.subject, organization=self.org, name="Content Site"
		)
		_enable_mcp(self.site)

	def test_inactive_prompts_and_documents_are_left_out(self):
		SiteMcpPrompt.objects.create(
			site=self.site,
			name="active_prompt",
			title="Active",
			template="Hello",
			arguments=[],
			is_active=True,
			ordering=1,
		)
		SiteMcpPrompt.objects.create(
			site=self.site,
			name="inactive_prompt",
			title="Inactive",
			template="Hello",
			arguments=[],
			is_active=False,
			ordering=2,
		)
		SiteMcpDocument.objects.create(
			site=self.site,
			slug="active-doc",
			title="Active Doc",
			body="Body",
			is_active=True,
			ordering=1,
		)
		SiteMcpDocument.objects.create(
			site=self.site,
			slug="inactive-doc",
			title="Inactive Doc",
			body="Body",
			is_active=False,
			ordering=2,
		)

		resp = self.client.get(reverse("mcp_tenants"))
		row = resp.data[0]
		self.assertEqual([p["name"] for p in row["prompts"]], ["active_prompt"])
		self.assertEqual([d["slug"] for d in row["documents"]], ["active-doc"])

	def test_ordering_is_respected(self):
		SiteMcpPrompt.objects.create(
			site=self.site, name="second", title="Second", template="Hi", arguments=[], ordering=20
		)
		SiteMcpPrompt.objects.create(
			site=self.site, name="first", title="First", template="Hi", arguments=[], ordering=10
		)
		SiteMcpDocument.objects.create(
			site=self.site, slug="z-doc", title="Z", body="B", ordering=20
		)
		SiteMcpDocument.objects.create(
			site=self.site, slug="a-doc", title="A", body="B", ordering=10
		)

		resp = self.client.get(reverse("mcp_tenants"))
		row = resp.data[0]
		self.assertEqual([p["name"] for p in row["prompts"]], ["first", "second"])
		self.assertEqual([d["slug"] for d in row["documents"]], ["a-doc", "z-doc"])

	def test_arguments_come_back_in_canonical_form(self):
		SiteMcpPrompt.objects.create(
			site=self.site,
			name="with_args",
			title="With Args",
			template="Hello $topic",
			arguments=[{"name": "topic", "description": "A topic.", "required": True}],
		)
		resp = self.client.get(reverse("mcp_tenants"))
		prompt = resp.data[0]["prompts"][0]
		self.assertEqual(
			prompt["arguments"],
			[{"name": "topic", "description": "A topic.", "required": True}],
		)

	def test_no_team_id_anywhere_in_the_response(self):
		SiteMcpPrompt.objects.create(
			site=self.site, name="p", title="P", template="Hello", arguments=[]
		)
		SiteMcpDocument.objects.create(site=self.site, slug="d", title="D", body="B")
		resp = self.client.get(reverse("mcp_tenants"))
		self.assertNotIn("team_id", resp.content.decode())

	def test_subjects_agree_with_the_api(self):
		other_subject = Subject.objects.create(
			subject_name="Other Content Subject",
			subject_slug="other-content-subj",
			team=self.team,
		)
		self.site.customsetting_set.first().scope_subjects.add(other_subject)

		tenants_resp = self.client.get(reverse("mcp_tenants"))
		tenant_subject_ids = {s["id"] for s in tenants_resp.data[0]["subjects"]}

		subjects_resp = self.client.get("/subjects/", {"site_id": self.site.pk})
		api_subject_ids = {s["id"] for s in subjects_resp.data["results"]}

		self.assertEqual(tenant_subject_ids, api_subject_ids)
		self.assertEqual(tenant_subject_ids, {self.subject.pk, other_subject.pk})


class McpTenantsKeyTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.org = Organization.objects.create(name="Key Org", slug="key-org")
		self.other_org = Organization.objects.create(name="Key Other Org", slug="key-other-org")
		self.team = Team.objects.create(
			organization=self.org, name="Key Team", slug="key-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Key Subject", subject_slug="key-subj", team=self.team
		)
		self.private_site = private_site_publishing(
			self.subject, organization=self.org, name="Key Private Site"
		)
		_enable_mcp(self.private_site)

	def _make_key(self, *, organization, site, begin=None, end=None):
		return APIAccessScheme.objects.create(
			client_name="Tenants Test Key",
			client_contacts="a@b.com",
			organization=organization,
			site=site,
			ip_addresses="",
			begin_date=begin or now() - timedelta(days=1),
			end_date=end or now() + timedelta(days=30),
		)

	def test_valid_key_sees_its_own_private_tenant(self):
		key = self._make_key(organization=self.org, site=self.private_site)
		self.client.defaults["HTTP_AUTHORIZATION"] = key.api_key

		tenants_resp = self.client.get(reverse("mcp_tenants"))
		site_ids = {row["site_id"] for row in tenants_resp.data}
		self.assertIn(self.private_site.pk, site_ids)

		own_row = next(
			row for row in tenants_resp.data if row["site_id"] == self.private_site.pk
		)
		tenant_subject_ids = {s["id"] for s in own_row["subjects"]}

		subjects_resp = self.client.get("/subjects/")
		api_subject_ids = {s["id"] for s in subjects_resp.data["results"]}
		self.assertEqual(tenant_subject_ids, api_subject_ids)

	def test_key_for_a_different_private_site_does_not_see_it(self):
		other_subject = Subject.objects.create(
			subject_name="Other Private Subject",
			subject_slug="other-private-subj",
			team=self.team,
		)
		other_private_site = private_site_publishing(
			other_subject, organization=self.other_org, name="Other Private Site"
		)
		_enable_mcp(other_private_site)

		key = self._make_key(organization=self.org, site=self.private_site)
		self.client.defaults["HTTP_AUTHORIZATION"] = key.api_key

		resp = self.client.get(reverse("mcp_tenants"))
		site_ids = {row["site_id"] for row in resp.data}
		self.assertIn(self.private_site.pk, site_ids)
		self.assertNotIn(other_private_site.pk, site_ids)

	def test_key_whose_site_is_outside_its_organisation_gets_the_anonymous_answer(self):
		# self.private_site belongs to self.org via OrganizationSite, but the
		# key below claims self.other_org -- no such link exists for that pair.
		self.assertFalse(
			OrganizationSite.objects.filter(
				organization=self.other_org, site=self.private_site
			).exists()
		)
		key = self._make_key(organization=self.other_org, site=self.private_site)
		self.client.defaults["HTTP_AUTHORIZATION"] = key.api_key

		with_key_resp = self.client.get(reverse("mcp_tenants"))
		# A genuinely separate, credential-free client -- self.client still
		# carries the header set above, so reusing it here would compare the
		# keyed response with itself rather than with a real anonymous one.
		anon_resp = APIClient().get(reverse("mcp_tenants"))
		self.assertEqual(with_key_resp.data, anon_resp.data)
		site_ids = {row["site_id"] for row in with_key_resp.data}
		self.assertNotIn(self.private_site.pk, site_ids)

	def test_expired_key_gets_the_anonymous_answer(self):
		key = self._make_key(
			organization=self.org,
			site=self.private_site,
			begin=now() - timedelta(days=30),
			end=now() - timedelta(days=1),
		)
		self.client.defaults["HTTP_AUTHORIZATION"] = key.api_key

		resp = self.client.get(reverse("mcp_tenants"))
		site_ids = {row["site_id"] for row in resp.data}
		self.assertNotIn(self.private_site.pk, site_ids)

	def test_signed_in_user_gets_the_anonymous_answer(self):
		user = User.objects.create_user(username="tenants-member", password="pw")
		OrganizationUser.objects.create(organization=self.org, user=user)
		self.client.force_authenticate(user=user)

		resp = self.client.get(reverse("mcp_tenants"))
		site_ids = {row["site_id"] for row in resp.data}
		self.assertNotIn(self.private_site.pk, site_ids)


class McpTenantsNotGatedBySiteResolutionTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.org_a = Organization.objects.create(name="Gate Org A", slug="gate-org-a")
		self.org_b = Organization.objects.create(name="Gate Org B", slug="gate-org-b")
		self.team_a = Team.objects.create(
			organization=self.org_a, name="Gate Team A", slug="gate-team-a"
		)
		self.team_b = Team.objects.create(
			organization=self.org_b, name="Gate Team B", slug="gate-team-b"
		)
		self.subject_a = Subject.objects.create(
			subject_name="Gate Subject A", subject_slug="gate-subj-a", team=self.team_a
		)
		self.subject_b = Subject.objects.create(
			subject_name="Gate Subject B", subject_slug="gate-subj-b", team=self.team_b
		)
		self.site_a = publish_subjects(
			self.subject_a, organization=self.org_a, name="Gate Site A"
		)
		self.site_b = publish_subjects(
			self.subject_b, organization=self.org_b, name="Gate Site B"
		)
		_enable_mcp(self.site_a)
		_enable_mcp(self.site_b)

	def test_tenants_returns_200_while_articles_returns_400(self):
		tenants_resp = self.client.get(reverse("mcp_tenants"))
		self.assertEqual(tenants_resp.status_code, 200)
		site_ids = {row["site_id"] for row in tenants_resp.data}
		self.assertEqual(site_ids, {self.site_a.pk, self.site_b.pk})

		articles_resp = self.client.get("/articles/")
		self.assertEqual(articles_resp.status_code, 400)


class McpTenantsHeadersAndQueriesTests(TestCase):
	def setUp(self):
		self.client = APIClient()
		self.org = Organization.objects.create(name="Headers Org", slug="headers-org")
		self.team = Team.objects.create(
			organization=self.org, name="Headers Team", slug="headers-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Headers Subject", subject_slug="headers-subj", team=self.team
		)
		self.site = publish_subjects(
			self.subject, organization=self.org, name="Headers Site"
		)
		_enable_mcp(self.site)

	def test_vary_includes_authorization(self):
		resp = self.client.get(reverse("mcp_tenants"))
		self.assertIn("Authorization", resp.headers.get("Vary", ""))

	def test_prompt_count_does_not_change_query_count(self):
		# Warm the process-level Site cache (django.contrib.sites) first --
		# its first-ever lookup costs one query that has nothing to do with
		# prompt count, and would otherwise make the "one prompt" call look
		# one query heavier than the "five prompts" call.
		self.client.get(reverse("mcp_tenants"))

		SiteMcpPrompt.objects.create(
			site=self.site, name="p1", title="P1", template="Hello", arguments=[]
		)
		with CaptureQueriesContext(connection) as one_prompt:
			resp = self.client.get(reverse("mcp_tenants"))
		self.assertEqual(resp.status_code, 200)

		for i in range(2, 6):
			SiteMcpPrompt.objects.create(
				site=self.site, name=f"p{i}", title=f"P{i}", template="Hello", arguments=[]
			)
		with CaptureQueriesContext(connection) as five_prompts:
			resp = self.client.get(reverse("mcp_tenants"))
		self.assertEqual(resp.status_code, 200)

		self.assertEqual(len(one_prompt.captured_queries), len(five_prompts.captured_queries))
