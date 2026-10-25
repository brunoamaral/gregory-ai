"""
visible_medicine_subject_ids(): which subjects a caller may see EMA medicines for.

Mirrors the patent scope tests in api/tests/test_patents_api.py. Fixture:

  - site A: public, medicines ON,  scope {S_on}
  - site B: public, medicines OFF, scope {S_off}
  - site P: private, medicines ON, scope {S_priv}
  - site Q: public, PATENTS on but medicines OFF, scope {S_pat}
  - all of them belong to one organisation

The two switches are independent: a site with patents on and medicines off
shows no medicines, and the other way round.
"""

from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.utils.text import slugify
from django.utils.timezone import now

from api.models import APIAccessScheme
from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.middleware.visibility import VisibleOrgMiddleware
from gregory.models import Subject, Team
from gregory.site_resolution import NoSiteResolvedError
from gregory.visibility import (
	_feature_switch,
	_settings_rows,
	visible_medicine_subject_ids,
	visible_patent_subject_ids,
	visible_subject_ids,
)
from mcpauth.editor_auth import EditorAuth
from mcpauth.tests.helpers import make_org, make_user
from sitesettings.models import CustomSetting


def _subject(org, name):
	team = Team.objects.create(
		organization=org, name=f"{name} team", slug=slugify(f"{name} team")
	)
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


class MedicineScopeTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = make_org("Medicine Scope Org")
		cls.s_on = _subject(cls.org, "Med On")
		cls.s_off = _subject(cls.org, "Med Off")
		cls.s_priv = _subject(cls.org, "Med Priv")
		cls.s_pat = _subject(cls.org, "Med Patents Only")
		cls.site_a = publish_subjects(
			cls.s_on, organization=cls.org, domain="med-a.example.com", medicines_enabled=True
		)
		cls.site_b = publish_subjects(cls.s_off, organization=cls.org, domain="med-b.example.com")
		cls.site_p = private_site_publishing(
			cls.s_priv, organization=cls.org, domain="med-p.example.com", medicines_enabled=True
		)
		cls.site_q = publish_subjects(
			cls.s_pat, organization=cls.org, domain="med-q.example.com", patents_enabled=True
		)

	def request(self, query="", user=None, **headers):
		request = RequestFactory().get("/" + query, **headers)
		request.user = user or AnonymousUser()
		return request

	def key_for(self, site):
		return APIAccessScheme.objects.create(
			client_name=f"key-{site.pk}",
			client_contacts="k@example.com",
			organization=self.org,
			site=site,
			ip_addresses="",
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)

	def test_anonymous_on_a_site_with_medicines_sees_its_scope(self):
		request = self.request(f"?site_id={self.site_a.pk}")
		self.assertEqual(visible_medicine_subject_ids(request), {self.s_on.pk})

	def test_anonymous_on_a_site_without_medicines_sees_none(self):
		request = self.request(f"?site_id={self.site_b.pk}")
		self.assertEqual(visible_medicine_subject_ids(request), set())
		# The ordinary scope is untouched: the subject is still visible.
		self.assertEqual(visible_subject_ids(request), {self.s_off.pk})

	def test_the_patents_switch_does_not_turn_medicines_on(self):
		request = self.request(f"?site_id={self.site_q.pk}")
		self.assertEqual(visible_patent_subject_ids(request), {self.s_pat.pk})
		self.assertEqual(visible_medicine_subject_ids(request), set())

	def test_the_medicines_switch_does_not_turn_patents_on(self):
		request = self.request(f"?site_id={self.site_a.pk}")
		self.assertEqual(visible_medicine_subject_ids(request), {self.s_on.pk})
		self.assertEqual(visible_patent_subject_ids(request), set())

	def test_site_bound_key_on_a_private_site_with_medicines(self):
		key = self.key_for(self.site_p)
		request = self.request(HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(visible_medicine_subject_ids(request), {self.s_priv.pk})

	def test_site_bound_key_on_a_site_without_medicines_sees_none(self):
		key = self.key_for(self.site_b)
		request = self.request(HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(visible_medicine_subject_ids(request), set())

	def test_include_public_adds_only_public_sites_with_medicines(self):
		key = self.key_for(self.site_p)
		request = self.request("?include_public=true", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(
			visible_medicine_subject_ids(request), {self.s_on.pk, self.s_priv.pk}
		)

	def test_include_public_does_not_apply_to_an_anonymous_caller(self):
		request = self.request(f"?site_id={self.site_b.pk}&include_public=true")
		self.assertEqual(visible_medicine_subject_ids(request), set())

	def test_authenticated_member_sees_the_union_of_their_sites_with_medicines(self):
		user = make_user("med-member", self.org)
		request = self.request(user=user)
		self.assertEqual(
			visible_medicine_subject_ids(request), {self.s_on.pk, self.s_priv.pk}
		)

	def test_authenticated_user_outside_the_organisation_sees_none(self):
		request = self.request(user=make_user("med-outsider"))
		self.assertEqual(visible_medicine_subject_ids(request), set())

	def test_mcp_editor_sees_only_their_one_site(self):
		request = self.request()
		request.auth = EditorAuth(user_id=1, site_id=self.site_p.pk, can_edit=True)
		self.assertEqual(visible_medicine_subject_ids(request), {self.s_priv.pk})
		request.auth = EditorAuth(user_id=1, site_id=self.site_b.pk, can_edit=True)
		self.assertEqual(visible_medicine_subject_ids(request), set())

	def test_ambiguous_anonymous_site_fails_closed_like_the_other_scopes(self):
		request = self.request()
		with self.assertRaises(NoSiteResolvedError):
			visible_medicine_subject_ids(request)
		with self.assertRaises(NoSiteResolvedError):
			visible_patent_subject_ids(request)

	def test_a_second_private_settings_row_does_not_leak_into_the_anonymous_scope(self):
		CustomSetting.objects.create(
			site=self.site_a, title="private twin", api_public=False, medicines_enabled=True
		)
		twin = CustomSetting.objects.get(site=self.site_a, title="private twin")
		twin.scope_subjects.add(self.s_priv)
		request = self.request(f"?site_id={self.site_a.pk}")
		self.assertEqual(visible_medicine_subject_ids(request), {self.s_on.pk})


class MiddlewareTests(TestCase):
	def test_the_middleware_attaches_the_medicine_scope_lazily(self):
		seen = {}

		def view(request):
			seen["value"] = request.visible_medicine_subject_ids
			return SimpleNamespace(headers={}, get=lambda *a, **k: None)

		request = RequestFactory().get("/")
		request.user = AnonymousUser()
		VisibleOrgMiddleware(view)(request)
		# No public site exists: an unambiguous, empty scope.
		self.assertEqual(set(seen["value"]), set())


class SettingsRowsTests(TestCase):
	def setUp(self):
		self.org = make_org("Rows Org")
		self.subject = _subject(self.org, "Rows Subject")
		self.on = publish_subjects(
			self.subject, domain="rows-on.example.com", medicines_enabled=True
		)
		self.off = publish_subjects(self.subject, domain="rows-off.example.com")

	def row_sites(self, **kwargs):
		return {row.site_id for row in _settings_rows(**kwargs)}

	def test_no_feature_keeps_every_row(self):
		self.assertEqual(self.row_sites(), {self.on.pk, self.off.pk})

	def test_a_feature_keeps_only_rows_with_the_switch_on(self):
		self.assertEqual(self.row_sites(feature="medicines_enabled"), {self.on.pk})
		self.assertEqual(self.row_sites(feature="patents_enabled"), set())

	def test_an_unknown_feature_is_refused(self):
		with self.assertRaises(ValueError):
			_settings_rows(feature="rss_enabled")

	def test_feature_switch_names(self):
		self.assertIsNone(_feature_switch())
		self.assertEqual(_feature_switch(patents=True), "patents_enabled")
		self.assertEqual(_feature_switch(medicines=True), "medicines_enabled")
		with self.assertRaises(ValueError):
			_feature_switch(patents=True, medicines=True)
