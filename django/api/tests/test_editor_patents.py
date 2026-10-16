"""
The ``/editor/patents/`` routes (api/editor_views.py): the editor's actions on
patent families, mirroring the article routes.

Setup: one organisation with a public site A (subject A, patents ON), a private
MCP-enabled site B (subject B, patents OFF) and another organisation's site.
Editors hold grants on one site each. Patents are reachable only through a site
with ``patents_enabled`` on, so every route is 404 for an editor of site B.
"""

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils.text import slugify
from rest_framework.test import APIClient

from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.models import (
	PatentCategoryAssignment,
	PatentPublication,
	PatentSiteContent,
	PatentTrialLink,
	Patents,
	Sponsor,
	Subject,
	Team,
	TeamCategory,
	Trials,
)
from mcpauth.tests.helpers import SERVICE_KEY, grant, make_org, make_user
from sitesettings.models import CustomSetting


def _subject(org, name):
	team = Team.objects.create(organization=org, name=f"{name} team", slug=slugify(f"{name} team"))
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def _patent(title, *subjects, **fields):
	patent = Patents.objects.create(title=title, **fields)
	patent.subjects.add(*subjects)
	return patent


def _publication(patent, number):
	country, rest = number[:2], number[2:]
	kind = rest[-2:] if rest[-1].isdigit() else rest[-1]
	return PatentPublication.objects.create(
		patent=patent,
		publication_number=number,
		country=country,
		doc_number=rest[: len(rest) - len(kind)],
		kind=kind,
	)


def headers(user, site, key=SERVICE_KEY):
	"""What the MCP server sends for a verified editor."""
	return {
		"HTTP_X_GREGORY_EDITOR_USER": str(user.pk),
		"HTTP_X_GREGORY_EDITOR_SITE": str(site.pk),
		"HTTP_AUTHORIZATION": f"Bearer {key}",
	}


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class PatentEditorFixture(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = make_org("Patent Editor Org")
		cls.other_org = make_org("Patent Editor Other Org")
		cls.subject_a = _subject(cls.org, "Patent Subject A")
		cls.subject_b = _subject(cls.org, "Patent Subject B")
		cls.subject_other = _subject(cls.other_org, "Patent Subject Other")
		cls.site_a = publish_subjects(
			cls.subject_a, organization=cls.org, domain="pe-a.example.com", patents_enabled=True
		)
		# Patents OFF: the editor of this site has no patent scope at all.
		cls.site_b = private_site_publishing(
			cls.subject_b, organization=cls.org, domain="pe-b.example.com", patents_enabled=False
		)
		cls.site_other = publish_subjects(
			cls.subject_other, organization=cls.other_org, domain="pe-o.example.com", patents_enabled=True
		)
		CustomSetting.objects.filter(site__in=[cls.site_a, cls.site_b, cls.site_other]).update(
			mcp_enabled=True
		)

		cls.ana = make_user("pe-ana", cls.org)
		cls.ben = make_user("pe-ben", cls.org)
		grant(cls.ana, cls.site_a)
		grant(cls.ana, cls.site_b)
		grant(cls.ben, cls.site_a, can_edit=False)

		cls.pat_a = _patent("Patent A", cls.subject_a, family_id="9001", representative_publication="EP4123456A1")
		_publication(cls.pat_a, "EP4123456A1")
		_publication(cls.pat_a, "EP4123456B1")
		_publication(cls.pat_a, "WO2020123456A1")
		cls.pat_b = _patent("Patent B", cls.subject_b, family_id="9002")
		_publication(cls.pat_b, "EP7000000A1")
		cls.pat_other = _patent("Patent Other", cls.subject_other, family_id="9003")
		_publication(cls.pat_other, "EP8000000A1")

		cls.trial_a = Trials.objects.create(title="Trial A", link="https://t.test/a")
		cls.trial_a.subjects.add(cls.subject_a)
		cls.trial_b = Trials.objects.create(title="Trial B", link="https://t.test/b")
		cls.trial_b.subjects.add(cls.subject_b)
		cls.trial_other = Trials.objects.create(title="Trial Other", link="https://t.test/o")
		cls.trial_other.subjects.add(cls.subject_other)
		cls.sponsor = Sponsor.objects.create(name="Editor Sponsor", slug="editor-sponsor")

		cls.cat_a = TeamCategory.objects.create(
			team=cls.subject_a.team, category_name="Cat A", category_slug="pe-cat-a", category_terms=["zzzz"]
		)
		cls.cat_a.subjects.add(cls.subject_a)
		cls.cat_other = TeamCategory.objects.create(
			team=cls.subject_other.team,
			category_name="Cat Other",
			category_slug="pe-cat-other",
			category_terms=["zzzz"],
		)
		cls.cat_other.subjects.add(cls.subject_other)

	def setUp(self):
		cache.clear()
		self.client = APIClient()

	def get(self, path, user=None, site=None):
		return self.client.get(path, **headers(user or self.ana, site or self.site_a))

	def send(self, method, path, data=None, user=None, site=None):
		return getattr(self.client, method)(
			path, data, format="json", **headers(user or self.ana, site or self.site_a)
		)

	def link(self, patent, trial, **fields):
		defaults = {"sponsor": self.sponsor, "timing": "during", "basis": "category"}
		defaults.update(fields)
		return PatentTrialLink.objects.create(patent=patent, trial=trial, **defaults)


class AuthorisationTest(PatentEditorFixture):
	def test_no_credential_is_401(self):
		for path in ("/editor/patents/resolve/?publication_number=EP4123456", f"/editor/patents/{self.pat_a.pk}/history/"):
			with self.subTest(path=path):
				self.assertEqual(self.client.get(path).status_code, 401)

	def test_read_only_editor_reads_but_cannot_write(self):
		self.assertEqual(self.get(f"/editor/patents/{self.pat_a.pk}/history/", user=self.ben).status_code, 200)
		writes = (
			("patch", f"/editor/patents/{self.pat_a.pk}/editorial/", {"takeaways": "x"}),
			("put", f"/editor/patents/{self.pat_a.pk}/categories/{self.cat_a.pk}/", None),
			("delete", f"/editor/patents/{self.pat_a.pk}/categories/{self.cat_a.pk}/", None),
			("put", f"/editor/patents/{self.pat_a.pk}/trials/{self.trial_a.pk}/", None),
			("delete", f"/editor/patents/{self.pat_a.pk}/trials/{self.trial_a.pk}/", None),
		)
		for method, path, body in writes:
			with self.subTest(method=method, path=path):
				self.assertEqual(self.send(method, path, body, user=self.ben).status_code, 403)

	def test_user_without_a_grant_is_403(self):
		stranger = make_user("pe-stranger", self.org)
		self.assertEqual(self.get(f"/editor/patents/{self.pat_a.pk}/history/", user=stranger).status_code, 403)


class SiteWithoutPatentsTest(PatentEditorFixture):
	"""Ana holds a grant on site B, where patents are off: nothing about
	patents exists for her there, not even an in-scope subject's family."""

	def all_routes(self, patent):
		return (
			("get", f"/editor/patents/resolve/?publication_number=EP7000000A1", None),
			("get", f"/editor/patents/{patent.pk}/", None),
			("get", f"/editor/patents/{patent.pk}/history/", None),
			("patch", f"/editor/patents/{patent.pk}/editorial/", {"takeaways": "x"}),
			("put", f"/editor/patents/{patent.pk}/categories/{self.cat_a.pk}/", None),
			("delete", f"/editor/patents/{patent.pk}/categories/{self.cat_a.pk}/", None),
			("put", f"/editor/patents/{patent.pk}/trials/{self.trial_b.pk}/", None),
			("delete", f"/editor/patents/{patent.pk}/trials/{self.trial_b.pk}/", None),
		)

	def test_every_route_is_404(self):
		for method, path, body in self.all_routes(self.pat_b):
			with self.subTest(method=method, path=path):
				response = self.send(method, path, body, site=self.site_b)
				self.assertEqual(response.status_code, 404, response.content)

	def test_the_list_is_empty(self):
		response = self.get("/editor/patents/", site=self.site_b)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["results"], [])

	def test_nothing_was_written(self):
		self.send("patch", f"/editor/patents/{self.pat_b.pk}/editorial/", {"takeaways": "x"}, site=self.site_b)
		self.assertFalse(PatentSiteContent.objects.exists())


class ScopeTest(PatentEditorFixture):
	def test_a_patent_of_another_organisation_is_404_on_every_route(self):
		pat = self.pat_other
		for method, path, body in (
			("get", f"/editor/patents/{pat.pk}/history/", None),
			("patch", f"/editor/patents/{pat.pk}/editorial/", {"takeaways": "x"}),
			("put", f"/editor/patents/{pat.pk}/categories/{self.cat_a.pk}/", None),
			("put", f"/editor/patents/{pat.pk}/trials/{self.trial_a.pk}/", None),
			("get", "/editor/patents/resolve/?publication_number=EP8000000A1", None),
		):
			with self.subTest(method=method, path=path):
				self.assertEqual(self.send(method, path, body).status_code, 404)

	def test_a_missing_patent_gets_the_same_answer(self):
		self.assertEqual(self.get("/editor/patents/999999/history/").status_code, 404)


class ResolveTest(PatentEditorFixture):
	url = "/editor/patents/resolve/?publication_number="

	def test_resolves_in_any_format(self):
		for raw in ("EP4123456A1", "EP%204123456%20A1", "ep-4123456-a1", "WO2020123456A1", "EP4123456B1"):
			with self.subTest(raw=raw):
				response = self.get(self.url + raw)
				self.assertEqual(response.status_code, 200, response.content)
				self.assertEqual(response.json()["patent_id"], self.pat_a.pk)

	def test_response_fields(self):
		body = self.get(self.url + "EP4123456A1").json()
		self.assertEqual(
			body,
			{
				"patent_id": self.pat_a.pk,
				"family_id": "9001",
				"title": "Patent A",
				"representative_publication": "EP4123456A1",
			},
		)

	def test_without_a_kind_code_every_kind_matches_one_family(self):
		response = self.get(self.url + "EP4123456")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["patent_id"], self.pat_a.pk)

	def test_a_letter_alone_matches_the_kind_with_its_digit(self):
		self.assertEqual(self.get(self.url + "EP4123456B").json()["patent_id"], self.pat_a.pk)

	def test_unknown_number_is_404(self):
		self.assertEqual(self.get(self.url + "EP1111111A1").status_code, 404)

	def test_unparseable_number_is_404(self):
		self.assertEqual(self.get(self.url + "!!!").status_code, 404)

	def test_missing_parameter_is_400(self):
		self.assertEqual(self.get("/editor/patents/resolve/").status_code, 400)
		self.assertEqual(self.get(self.url + "%20").status_code, 400)

	def test_several_families_are_a_409_listing_their_ids(self):
		# OPS can place two kinds of one number in different families.
		twin = _patent("Patent A twin", self.subject_a, family_id="9004")
		_publication(twin, "EP4123456A2")
		response = self.get(self.url + "EP4123456")
		self.assertEqual(response.status_code, 409)
		body = response.json()
		self.assertEqual(sorted(body["patent_ids"]), sorted([self.pat_a.pk, twin.pk]))
		self.assertIn("2 patents match", body["detail"])
		# The full number still resolves.
		self.assertEqual(self.get(self.url + "EP4123456A2").json()["patent_id"], twin.pk)

	def test_families_outside_the_scope_do_not_cause_a_conflict(self):
		twin = _patent("Foreign twin", self.subject_other, family_id="9005")
		_publication(twin, "EP4123456A2")
		self.assertEqual(self.get(self.url + "EP4123456").status_code, 200)


class ResolveListTest(PatentEditorFixture):
	def test_a_list_is_a_400_not_a_misleading_404(self):
		for value in ("EP4123456A1,WO2020123456A1", "EP4123456A1;WO2020123456A1", "EP4123456,"):
			with self.subTest(value=value):
				response = self.get(f"/editor/patents/resolve/?publication_number={value}")
				self.assertEqual(response.status_code, 400, response.content)
				self.assertIn("Send one publication number", str(response.json()))

	def test_spaces_inside_one_number_are_still_fine(self):
		self.assertEqual(
			self.get("/editor/patents/resolve/?publication_number=EP%204123456%20A1").status_code, 200
		)


class EditorialRespectsThePatentSwitchTest(PatentEditorFixture):
	"""Patents are shown only through sites with patents_enabled on, and the
	editor API treats every other site as having none. The editorial text the
	read API offers, and the admin inline, must agree."""

	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		# A patent visible through site A, with text on A and (stale, e.g. from
		# before the switch was turned off) on site B.
		cls.pat_both = _patent("Visible via A", cls.subject_a, cls.subject_b)
		PatentSiteContent.objects.create(patent=cls.pat_both, site=cls.site_a, takeaways="Text A")
		PatentSiteContent.objects.create(patent=cls.pat_both, site=cls.site_b, takeaways="Text B")

	def sites_in(self, entries):
		return [entry["site"]["id"] for entry in entries]

	def test_a_member_of_both_sites_gets_only_the_site_with_patents(self):
		self.client.force_login(make_user("pe-member", self.org))
		detail = self.client.get(f"/patents/{self.pat_both.pk}/?include=editorial")
		self.assertEqual(detail.status_code, 200)
		self.assertEqual(self.sites_in(detail.data["editorial"]), [self.site_a.pk])
		self.assertEqual(detail.data["editorial"][0]["takeaways"], "Text A")
		listing = self.client.get("/patents/?include=editorial")
		row = next(r for r in listing.data["results"] if r["patent_id"] == self.pat_both.pk)
		self.assertEqual(self.sites_in(row["editorial"]), [self.site_a.pk])

	def test_the_same_member_still_sees_both_sites_on_an_article(self):
		from gregory.models import Articles, ArticleSiteContent

		article = Articles.objects.create(title="Both sites", link="https://a.test/both")
		article.subjects.add(self.subject_a, self.subject_b)
		ArticleSiteContent.objects.create(article=article, site=self.site_b, takeaways="B")
		self.client.force_login(make_user("pe-member-art", self.org))
		detail = self.client.get(f"/articles/{article.pk}/?include=editorial")
		self.assertEqual(sorted(self.sites_in(detail.data["editorial"])), sorted([self.site_a.pk, self.site_b.pk]))

	def test_the_editor_of_the_site_with_patents_gets_only_that_site(self):
		body = self.get(f"/editor/patents/{self.pat_both.pk}/?include=editorial").json()
		self.assertEqual(self.sites_in(body["editorial"]), [self.site_a.pk])

	def test_a_key_bound_to_a_site_without_patents_sees_no_patent_at_all(self):
		from datetime import timedelta
		from django.utils.timezone import now
		from api.models import APIAccessScheme

		key = APIAccessScheme.objects.create(
			client_name="b-key",
			client_contacts="k@example.com",
			organization=self.org,
			site=self.site_b,
			ip_addresses="",
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)
		response = self.client.get("/patents/?include=editorial", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(response.data["results"], [])

	def test_admin_inline_offers_only_sites_with_patents(self):
		from django.test import RequestFactory

		from gregory.admin import ArticleSiteContentInline, PatentSiteContentInline

		member = make_user("pe-staff", self.org)
		request = RequestFactory().get("/admin/")
		request.user = member
		self.assertEqual(
			list(PatentSiteContentInline._user_sites(request).values_list("pk", flat=True)), [self.site_a.pk]
		)
		# The article inline is untouched.
		self.assertEqual(
			sorted(ArticleSiteContentInline._user_sites(request).values_list("pk", flat=True)),
			sorted([self.site_a.pk, self.site_b.pk]),
		)

	def test_admin_inline_narrows_superusers_too(self):
		from django.contrib.auth import get_user_model
		from django.test import RequestFactory

		from gregory.admin import PatentSiteContentInline

		root = get_user_model().objects.create_superuser("pe-root", "root@example.com", "pw")
		request = RequestFactory().get("/admin/")
		request.user = root
		offered = set(PatentSiteContentInline._user_sites(request).values_list("pk", flat=True))
		self.assertEqual(offered, {self.site_a.pk, self.site_other.pk})
		self.assertNotIn(self.site_b.pk, offered)

	def test_admin_history_follows_the_same_sites(self):
		from django.contrib import admin
		from django.test import RequestFactory

		from gregory.admin import PatentSiteContentAdmin, PatentSiteContentInline

		self.assertIs(PatentSiteContentAdmin.inline_class, PatentSiteContentInline)
		model_admin = PatentSiteContentAdmin(PatentSiteContent, admin.site)
		request = RequestFactory().get("/admin/")
		request.user = make_user("pe-staff-2", self.org)
		on_a = PatentSiteContent.objects.get(patent=self.pat_both, site=self.site_a)
		on_b = PatentSiteContent.objects.get(patent=self.pat_both, site=self.site_b)
		history = PatentSiteContent.history
		self.assertTrue(model_admin.get_history_queryset(request, history, "id", on_a.pk).exists())
		self.assertFalse(model_admin.get_history_queryset(request, history, "id", on_b.pk).exists())


class EditorialTest(PatentEditorFixture):
	def url(self, patent=None):
		return f"/editor/patents/{(patent or self.pat_a).pk}/editorial/"

	def test_sets_both_fields(self):
		response = self.send("patch", self.url(), {"takeaways": "T", "summary_plain_english": "S"})
		self.assertEqual(response.status_code, 200, response.content)
		body = response.json()
		self.assertEqual(body["patent_id"], self.pat_a.pk)
		self.assertEqual(body["site_id"], self.site_a.pk)
		self.assertEqual((body["takeaways"], body["summary_plain_english"]), ("T", "S"))
		self.assertEqual(body["updated_by"], "pe-ana <pe-ana@example.com>")
		content = PatentSiteContent.objects.get(patent=self.pat_a, site=self.site_a)
		self.assertEqual(content.takeaways, "T")

	def test_omitted_fields_are_left_alone(self):
		self.send("patch", self.url(), {"takeaways": "T", "summary_plain_english": "S"})
		body = self.send("patch", self.url(), {"takeaways": "T2"}).json()
		self.assertEqual((body["takeaways"], body["summary_plain_english"]), ("T2", "S"))

	def test_an_empty_string_clears_a_field(self):
		self.send("patch", self.url(), {"takeaways": "T", "summary_plain_english": "S"})
		body = self.send("patch", self.url(), {"takeaways": ""}).json()
		self.assertIsNone(body["takeaways"])
		self.assertEqual(body["summary_plain_english"], "S")

	def test_an_empty_body_is_400(self):
		self.assertEqual(self.send("patch", self.url(), {}).status_code, 400)

	def test_content_is_per_site(self):
		PatentSiteContent.objects.create(patent=self.pat_a, site=self.site_other, takeaways="Other site")
		self.send("patch", self.url(), {"takeaways": "Mine"})
		self.assertEqual(PatentSiteContent.objects.get(patent=self.pat_a, site=self.site_other).takeaways, "Other site")

	def test_the_change_is_recorded_under_the_editor(self):
		self.send("patch", self.url(), {"takeaways": "T"})
		row = PatentSiteContent.history.get(patent_id=self.pat_a.pk)
		self.assertEqual(row.editor_label, "pe-ana <pe-ana@example.com>")
		self.assertEqual(row.via, "mcp")

	def test_editor_read_includes_editorial_with_audit_fields(self):
		self.send("patch", self.url(), {"takeaways": "T", "summary_plain_english": "S"})
		detail = self.get(f"/editor/patents/{self.pat_a.pk}/?include=editorial").json()
		self.assertEqual(len(detail["editorial"]), 1)
		entry = detail["editorial"][0]
		self.assertEqual(entry["site"]["id"], self.site_a.pk)
		self.assertEqual((entry["takeaways"], entry["summary_plain_english"]), ("T", "S"))
		self.assertEqual(entry["updated_by"], "pe-ana <pe-ana@example.com>")
		self.assertIsNotNone(entry["updated_at"])

	def test_editorial_is_absent_without_the_include(self):
		self.send("patch", self.url(), {"takeaways": "T"})
		self.assertNotIn("editorial", self.get(f"/editor/patents/{self.pat_a.pk}/").json())
		listing = self.get("/editor/patents/").json()["results"]
		self.assertTrue(listing)
		for row in listing:
			self.assertNotIn("editorial", row)

	def test_editorial_on_the_list_without_a_query_per_patent(self):
		for n in range(6):
			patent = _patent(f"Extra {n}", self.subject_a)
			PatentSiteContent.objects.create(patent=patent, site=self.site_a, takeaways=f"t{n}")
		from django.db import connection
		from django.test.utils import CaptureQueriesContext

		self.get("/editor/patents/?include=editorial")
		cache.clear()
		with CaptureQueriesContext(connection) as small:
			self.get("/editor/patents/?include=editorial&page_size=2")
		cache.clear()
		with CaptureQueriesContext(connection) as large:
			self.get("/editor/patents/?include=editorial&page_size=7")
		self.assertEqual(len(small), len(large))

	def test_anonymous_callers_get_the_site_text_without_audit_fields(self):
		self.send("patch", self.url(), {"takeaways": "T"})
		response = self.client.get(
			f"/patents/{self.pat_a.pk}/?include=editorial&site_id={self.site_a.pk}"
		)
		self.assertEqual(response.status_code, 200)
		entry = response.data["editorial"][0]
		self.assertEqual(entry["takeaways"], "T")
		self.assertNotIn("updated_by", entry)
		self.assertNotIn("updated_at", entry)
		# And without the parameter nothing is added.
		plain = self.client.get(f"/patents/{self.pat_a.pk}/?site_id={self.site_a.pk}")
		self.assertNotIn("editorial", plain.data)

	def test_editorial_and_claims_combine(self):
		self.send("patch", self.url(), {"takeaways": "T"})
		self.pat_a.claims = "1. A claim."
		self.pat_a.save()
		body = self.get(f"/editor/patents/{self.pat_a.pk}/?include=claims,editorial").json()
		self.assertEqual(body["claims"], "1. A claim.")
		self.assertEqual(body["editorial"][0]["takeaways"], "T")


class CategoryTest(PatentEditorFixture):
	def url(self, patent=None, category=None):
		return f"/editor/patents/{(patent or self.pat_a).pk}/categories/{(category or self.cat_a).pk}/"

	def test_assigns_by_hand(self):
		response = self.send("put", self.url())
		self.assertEqual(response.status_code, 201, response.content)
		body = response.json()
		self.assertEqual((body["patent_id"], body["category_id"], body["source"]), (self.pat_a.pk, self.cat_a.pk, "manual"))
		self.assertEqual(body["updated_by"], "pe-ana <pe-ana@example.com>")

	def test_assigning_again_is_unchanged(self):
		self.send("put", self.url())
		self.assertEqual(self.send("put", self.url()).status_code, 200)
		self.assertEqual(PatentCategoryAssignment.objects.filter(patents=self.pat_a).count(), 1)

	def test_an_automatic_assignment_becomes_manual(self):
		PatentCategoryAssignment.objects.create(patents=self.pat_a, teamcategory=self.cat_a, source="automatic")
		response = self.send("put", self.url())
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["source"], "manual")
		self.assertEqual(PatentCategoryAssignment.objects.get(patents=self.pat_a).source, "manual")

	def test_a_category_outside_the_scope_is_404(self):
		self.assertEqual(self.send("put", self.url(category=self.cat_other)).status_code, 404)

	def test_unassigns_a_hand_assignment(self):
		self.send("put", self.url())
		response = self.send("delete", self.url())
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.json(), {"patent_id": self.pat_a.pk, "category_id": self.cat_a.pk, "removed": "manual"})
		self.assertFalse(PatentCategoryAssignment.objects.filter(patents=self.pat_a).exists())

	def test_unassigning_an_automatic_assignment_is_refused_with_the_reason(self):
		PatentCategoryAssignment.objects.create(patents=self.pat_a, teamcategory=self.cat_a, source="automatic")
		response = self.send("delete", self.url())
		self.assertEqual(response.status_code, 409)
		self.assertIn("Change the category's terms", response.json()["detail"])
		self.assertTrue(PatentCategoryAssignment.objects.filter(patents=self.pat_a).exists())

	def test_unassigning_a_patent_that_is_not_in_the_category_is_404(self):
		self.assertEqual(self.send("delete", self.url()).status_code, 404)

	def test_the_change_is_recorded(self):
		self.send("put", self.url())
		self.send("delete", self.url())
		rows = list(PatentCategoryAssignment.history.filter(patents_id=self.pat_a.pk).order_by("history_date"))
		self.assertEqual([r.history_type for r in rows], ["+", "-"])
		self.assertEqual({r.editor_label for r in rows}, {"pe-ana <pe-ana@example.com>"})

	def test_a_hand_assignment_survives_rebuild_categories(self):
		"""The pipeline removes only automatic rows: a hand assignment of a
		patent that does not match the category's terms stays, a stale
		automatic one goes."""
		stale = _patent("Stale automatic", self.subject_a)
		PatentCategoryAssignment.objects.create(patents=stale, teamcategory=self.cat_a, source="automatic")
		self.send("put", self.url())

		call_command("rebuild_categories", patents_only=True, verbosity=0)

		assigned = set(
			PatentCategoryAssignment.objects.filter(teamcategory=self.cat_a).values_list("patents_id", "source")
		)
		self.assertIn((self.pat_a.pk, "manual"), assigned)
		self.assertNotIn((stale.pk, "automatic"), assigned)


class TrialLinkTest(PatentEditorFixture):
	def url(self, patent=None, trial=None):
		return f"/editor/patents/{(patent or self.pat_a).pk}/trials/{(trial or self.trial_a).pk}/"

	def shown(self):
		response = self.client.get(f"/patents/{self.pat_a.pk}/?site_id={self.site_a.pk}")
		return [link["trial_id"] for link in response.data["trial_links"]]

	def test_delete_suppresses_the_link(self):
		self.link(self.pat_a, self.trial_a)
		self.assertEqual(self.shown(), [self.trial_a.pk])
		response = self.send("delete", self.url())
		self.assertEqual(response.status_code, 200, response.content)
		body = response.json()
		self.assertIs(body["suppressed"], True)
		self.assertEqual((body["patent_id"], body["trial_id"]), (self.pat_a.pk, self.trial_a.pk))
		self.assertEqual(body["updated_by"], "pe-ana <pe-ana@example.com>")
		self.assertEqual(self.shown(), [])
		# Hidden, not deleted: detection leaves it alone.
		self.assertTrue(PatentTrialLink.objects.get(patent=self.pat_a, trial=self.trial_a).suppressed)

	def test_put_restores_a_suppressed_link(self):
		self.link(self.pat_a, self.trial_a, suppressed=True)
		response = self.send("put", self.url())
		self.assertEqual(response.status_code, 200, response.content)
		self.assertIs(response.json()["suppressed"], False)
		self.assertEqual(self.shown(), [self.trial_a.pk])

	def test_suppressing_works_on_a_subject_basis_link_too(self):
		self.link(self.pat_a, self.trial_a, basis="subject")
		self.assertEqual(self.send("delete", self.url()).status_code, 200)
		self.assertTrue(PatentTrialLink.objects.get(patent=self.pat_a).suppressed)

	def test_both_directions_are_idempotent_and_record_nothing_new(self):
		link = self.link(self.pat_a, self.trial_a)
		self.assertEqual(self.send("put", self.url()).status_code, 200)  # not suppressed: no-op
		self.assertEqual(PatentTrialLink.history.filter(id=link.id).count(), 1)  # only the creation
		self.send("delete", self.url())
		count = PatentTrialLink.history.filter(id=link.id).count()
		again = self.send("delete", self.url())  # already suppressed: no-op
		self.assertEqual(again.status_code, 200)
		self.assertIs(again.json()["suppressed"], True)
		self.assertEqual(PatentTrialLink.history.filter(id=link.id).count(), count)

	def test_put_cannot_create_a_link(self):
		response = self.send("put", self.url())
		self.assertEqual(response.status_code, 404)
		self.assertIn("can't create patent-trial links", response.json()["detail"])
		self.assertIn("only restore suppressed ones", response.json()["detail"])
		self.assertFalse(PatentTrialLink.objects.exists())

	def test_delete_of_a_pair_that_is_not_linked_is_404(self):
		response = self.send("delete", self.url())
		self.assertEqual(response.status_code, 404)
		self.assertIn("not linked", response.json()["detail"])

	def test_a_trial_outside_the_scope_is_404(self):
		self.link(self.pat_a, self.trial_other)
		for method in ("put", "delete"):
			with self.subTest(method=method):
				self.assertEqual(self.send(method, self.url(trial=self.trial_other)).status_code, 404)
		self.assertFalse(PatentTrialLink.objects.get(trial=self.trial_other).suppressed)

	def test_the_suppression_is_recorded_under_the_editor(self):
		link = self.link(self.pat_a, self.trial_a)
		self.send("delete", self.url())
		newest = PatentTrialLink.history.filter(id=link.id).order_by("-history_date").first()
		self.assertTrue(newest.suppressed)
		self.assertEqual(newest.editor_label, "pe-ana <pe-ana@example.com>")
		self.assertEqual(newest.via, "mcp")


class HistoryTest(PatentEditorFixture):
	def url(self, patent=None, query=""):
		return f"/editor/patents/{(patent or self.pat_a).pk}/history/{query}"

	def test_lists_editorial_category_and_link_changes_newest_first(self):
		self.link(self.pat_a, self.trial_a)
		self.send("patch", f"/editor/patents/{self.pat_a.pk}/editorial/", {"takeaways": "First"})
		self.send("put", f"/editor/patents/{self.pat_a.pk}/categories/{self.cat_a.pk}/")
		self.send("delete", f"/editor/patents/{self.pat_a.pk}/trials/{self.trial_a.pk}/")

		body = self.get(self.url()).json()

		self.assertEqual(body["patent_id"], self.pat_a.pk)
		# The link's creation (no editor) is the oldest entry.
		self.assertEqual(
			[e["kind"] for e in body["entries"]],
			["trial_link", "category", "editorial", "trial_link"],
		)
		suppress = body["entries"][0]
		self.assertEqual(suppress["change"], "updated")
		self.assertEqual(suppress["changed_by"], "pe-ana <pe-ana@example.com>")
		self.assertEqual(suppress["via"], "mcp")
		self.assertEqual(
			suppress["details"],
			{"trial_id": self.trial_a.pk, "timing": "during", "basis": "category", "suppressed": True},
		)
		editorial = next(e for e in body["entries"] if e["kind"] == "editorial")
		self.assertEqual(editorial["details"]["takeaways"], "First")
		self.assertEqual(editorial["change"], "created")
		category = next(e for e in body["entries"] if e["kind"] == "category")
		self.assertEqual(category["details"], {"category_id": self.cat_a.pk, "source": "manual"})
		creation = body["entries"][-1]
		self.assertEqual(creation["change"], "created")
		self.assertIsNone(creation["changed_by"])

	def test_restore_shows_up_too(self):
		self.link(self.pat_a, self.trial_a)
		self.send("delete", f"/editor/patents/{self.pat_a.pk}/trials/{self.trial_a.pk}/")
		self.send("put", f"/editor/patents/{self.pat_a.pk}/trials/{self.trial_a.pk}/")
		flags = [e["details"]["suppressed"] for e in self.get(self.url()).json()["entries"] if e["kind"] == "trial_link"]
		self.assertEqual(flags, [False, True, False])

	def test_there_are_no_relevance_entries(self):
		self.send("patch", f"/editor/patents/{self.pat_a.pk}/editorial/", {"takeaways": "x"})
		kinds = {e["kind"] for e in self.get(self.url()).json()["entries"]}
		self.assertNotIn("relevance", kinds)

	def test_the_limit_counts_only_in_scope_link_changes(self):
		self.link(self.pat_a, self.trial_a)
		for index in range(3):
			other = Trials.objects.create(title=f"Foreign {index}", link=f"https://t.test/f{index}")
			other.subjects.add(self.subject_other)
			self.link(self.pat_a, other)
		entries = self.get(self.url(query="?limit=1")).json()["entries"]
		self.assertEqual([(e["kind"], e["details"]["trial_id"]) for e in entries], [("trial_link", self.trial_a.pk)])

	def test_only_this_sites_editorial_history_is_shown(self):
		PatentSiteContent.objects.create(patent=self.pat_a, site=self.site_other, takeaways="Other site text")
		self.send("patch", f"/editor/patents/{self.pat_a.pk}/editorial/", {"takeaways": "Site A text"})
		texts = [e["details"]["takeaways"] for e in self.get(self.url()).json()["entries"] if e["kind"] == "editorial"]
		self.assertEqual(texts, ["Site A text"])

	def test_categories_outside_the_scope_are_left_out(self):
		PatentCategoryAssignment.objects.create(patents=self.pat_a, teamcategory=self.cat_other, source="manual")
		self.assertEqual(self.get(self.url()).json()["entries"], [])

	def test_changes_by_an_api_key_name_the_key(self):
		content = PatentSiteContent.objects.create(patent=self.pat_a, site=self.site_a, takeaways="x")
		PatentSiteContent.history.filter(id=content.id).update(api_access_scheme_label="Frontend key", editor_label="")
		entry = self.get(self.url()).json()["entries"][0]
		self.assertEqual(entry["changed_by"], "Frontend key")

	def test_limit_validation(self):
		for query in ("?limit=0", "?limit=201", "?limit=abc", "?limit=-1"):
			with self.subTest(query=query):
				self.assertEqual(self.get(self.url(query=query)).status_code, 400)
		self.assertEqual(self.get(self.url(query="?limit=200")).status_code, 200)

	def test_limit_trims_the_result(self):
		for index in range(4):
			self.send("patch", f"/editor/patents/{self.pat_a.pk}/editorial/", {"takeaways": str(index)})
		self.assertEqual(len(self.get(self.url(query="?limit=2")).json()["entries"]), 2)


class ThrottleTest(PatentEditorFixture):
	def patch_patent(self, text="x", patent=None):
		return self.send("patch", f"/editor/patents/{(patent or self.pat_a).pk}/editorial/", {"takeaways": text})

	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 3, "day": 500})
	def test_patent_writes_past_the_hourly_limit_are_429(self):
		statuses = [self.patch_patent(text=str(i)).status_code for i in range(5)]
		self.assertEqual(statuses, [200, 200, 200, 429, 429])
		self.assertIn("Retry-After", self.patch_patent())

	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 2, "day": 500})
	def test_patent_and_article_edits_share_one_limit(self):
		from gregory.models import Articles

		article = Articles.objects.create(title="Throttle article", link="https://a.test/t", doi="10.1/thr")
		article.subjects.add(self.subject_a)
		first = self.send("patch", f"/editor/articles/{article.pk}/editorial/", {"takeaways": "x"})
		second = self.patch_patent()
		third = self.send("put", f"/editor/patents/{self.pat_a.pk}/categories/{self.cat_a.pk}/")
		self.assertEqual([first.status_code, second.status_code, third.status_code], [200, 200, 429])

	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 1, "day": 500})
	def test_reads_are_never_throttled(self):
		self.patch_patent()
		for _ in range(4):
			self.assertEqual(self.get(f"/editor/patents/{self.pat_a.pk}/history/").status_code, 200)
			self.assertEqual(self.get("/editor/patents/resolve/?publication_number=EP4123456A1").status_code, 200)
