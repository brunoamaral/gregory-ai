"""
The "can add sources" flag and the ``sources:add`` scope: a superuser-granted
power on top of ``can_edit`` that rides on the editor tier, is clamped on every
token issue, refresh and introspection, and is read live by ``authenticate_editor``.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase, override_settings

from mcpauth.access import SiteAccess, evaluate_access
from mcpauth.admin import SiteEditorForm, SiteEditorInline
from mcpauth.editor_auth import (
	HEADER_SITE,
	HEADER_USER,
	authenticate_editor,
)
from mcpauth.models import AccessToken, RefreshToken, SiteEditor
from mcpauth.tests.helpers import (
	SERVICE_KEY,
	authorize_params,
	get_token,
	grant,
	introspect,
	make_org,
	make_site,
	make_user,
	register_client,
	resource_for,
)

ALL_SCOPES = "articles:read articles:edit sources:add"


class SourceFlagRulesTest(TestCase):
	def setUp(self):
		self.org = make_org("Flag Org")
		self.site = make_site(self.org, "flag.example.com")
		self.user = make_user("flagged", self.org)

	def test_defaults_to_off(self):
		self.assertFalse(grant(self.user, self.site).can_manage_sources)

	def test_the_flag_without_can_edit_is_refused_by_clean(self):
		editor = SiteEditor(user=self.user, site=self.site, can_edit=False, can_manage_sources=True)

		with self.assertRaises(ValidationError) as caught:
			editor.full_clean()

		self.assertIn("can_manage_sources", caught.exception.message_dict)
		self.assertIn("Tick can edit", caught.exception.message_dict["can_manage_sources"][0])

	def test_the_flag_with_can_edit_is_accepted(self):
		SiteEditor(user=self.user, site=self.site, can_edit=True, can_manage_sources=True).full_clean()

	def test_the_database_refuses_the_flag_without_can_edit(self):
		editor = grant(self.user, self.site, can_manage_sources=True)

		with self.assertRaises(IntegrityError), transaction.atomic():
			SiteEditor.objects.filter(pk=editor.pk).update(can_edit=False)

	def test_the_database_refuses_a_new_row_with_the_flag_and_no_can_edit(self):
		with self.assertRaises(IntegrityError), transaction.atomic():
			SiteEditor.objects.create(user=self.user, site=self.site, can_edit=False, can_manage_sources=True)


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class SourceFlagSessionsTest(TestCase):
	def setUp(self):
		self.org = make_org("Flag Session Org")
		self.site = make_site(self.org, "flag-session.example.com")
		self.other = make_site(self.org, "flag-session-other.example.com")
		self.user = make_user("flag-session", self.org)
		self.editor = grant(self.user, self.site, can_manage_sources=True)
		grant(self.user, self.other, can_manage_sources=True)

	def _live(self, site):
		return AccessToken.objects.filter(user=self.user, site=site).count()

	def test_unticking_the_flag_ends_that_sites_sessions_and_only_that_site(self):
		get_token(self.user, self.site, scope=ALL_SCOPES)
		get_token(self.user, self.other, scope=ALL_SCOPES)
		self.assertEqual((self._live(self.site), self._live(self.other)), (1, 1))

		self.editor.can_manage_sources = False
		self.editor.save()

		self.assertEqual((self._live(self.site), self._live(self.other)), (0, 1))
		self.assertEqual(RefreshToken.objects.filter(user=self.user).count(), 1)

	def test_ticking_the_flag_keeps_existing_sessions(self):
		reader = make_user("flag-ticked", self.org)
		editor = grant(reader, self.site)
		body = get_token(reader, self.site, scope=ALL_SCOPES).json()

		editor.can_manage_sources = True
		editor.save()

		info = introspect(body["access_token"]).json()
		self.assertTrue(info["active"])
		# A token is never widened after the fact: signing in again is how
		# the person gets the new scope.
		self.assertEqual(sorted(info["scope"].split()), ["articles:edit", "articles:read"])

	def test_saving_an_unchanged_flag_keeps_the_sessions(self):
		get_token(self.user, self.site, scope=ALL_SCOPES)

		self.editor.save()

		self.assertEqual(self._live(self.site), 1)

	def test_a_new_sign_in_after_ticking_the_flag_gets_the_scope(self):
		later = make_user("flag-later", self.org)
		editor = grant(later, self.site)
		self.assertNotIn("sources:add", get_token(later, self.site, scope=ALL_SCOPES).json()["scope"])
		editor.can_manage_sources = True
		editor.save()

		body = get_token(later, self.site, scope=ALL_SCOPES).json()

		self.assertEqual(sorted(body["scope"].split()), ["articles:edit", "articles:read", "sources:add"])


class SiteAccessScopesTest(TestCase):
	def setUp(self):
		self.site = Site(domain="scopes.example.com", name="scopes")

	def _scopes(self, tier, can_edit, can_manage_sources):
		return SiteAccess(
			self.site, tier, can_edit, can_manage_sources=can_manage_sources
		).scopes

	def test_every_combination_of_tier_can_edit_and_flag(self):
		read = ["articles:read"]
		edit = ["articles:read", "articles:edit"]
		sources = ["articles:read", "articles:edit", "sources:add"]
		cases = [
			("editor", True, True, sources),
			("editor", True, False, edit),
			("editor", False, True, read),
			("editor", False, False, read),
			("public", True, True, read),
			("public", True, False, read),
			("public", False, True, read),
			("public", False, False, read),
		]
		for tier, can_edit, flag, expected in cases:
			with self.subTest(tier=tier, can_edit=can_edit, can_manage_sources=flag):
				self.assertEqual(self._scopes(tier, can_edit, flag), expected)

	def test_clamp_scope_drops_sources_add_without_the_flag(self):
		access = SiteAccess(self.site, "editor", True)
		self.assertEqual(access.clamp_scope(ALL_SCOPES), "articles:read articles:edit")

	def test_evaluate_access_passes_the_flag_from_the_grant(self):
		org = make_org("Eval Flag Org")
		site = make_site(org, "eval-flag.example.com")
		user = make_user("eval-flag", org)
		grant(user, site, can_manage_sources=True)

		access = evaluate_access(user, site)

		self.assertTrue(access.can_manage_sources)
		self.assertIn("sources:add", access.scopes)


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class SourcesScopeFlowTest(TestCase):
	def setUp(self):
		self.org = make_org("Scope Flow Org")
		self.site = make_site(self.org, "scope-flow.example.com")
		self.plain = make_user("scope-plain", self.org)
		self.manager = make_user("scope-manager", self.org)
		self.reader = make_user("scope-reader", self.org)
		self.outsider = make_user("scope-outsider")
		grant(self.plain, self.site)
		self.manager_grant = grant(self.manager, self.site, can_manage_sources=True)
		grant(self.reader, self.site, can_edit=False)

	def test_asking_for_the_scope_without_the_flag_gets_a_token_without_it(self):
		for user in (self.plain, self.reader, self.outsider):
			with self.subTest(user=user.username):
				body = get_token(user, self.site, scope=ALL_SCOPES).json()
				self.assertNotIn("sources:add", body["scope"].split())
				info = introspect(body["access_token"]).json()
				self.assertNotIn("sources:add", info["scope"].split())

	def test_with_the_flag_the_token_has_the_scope(self):
		body = get_token(self.manager, self.site, scope=ALL_SCOPES).json()

		self.assertEqual(sorted(body["scope"].split()), ["articles:edit", "articles:read", "sources:add"])
		info = introspect(body["access_token"]).json()
		self.assertEqual(sorted(info["scope"].split()), ["articles:edit", "articles:read", "sources:add"])

	def test_the_scope_is_only_there_if_it_was_asked_for(self):
		body = get_token(self.manager, self.site, scope="articles:read articles:edit").json()

		self.assertNotIn("sources:add", body["scope"].split())

	def test_consent_lists_the_scope_and_the_note_only_for_a_source_manager(self):
		for user, shown in ((self.manager, True), (self.plain, False)):
			with self.subTest(user=user.username):
				web = Client()
				web.force_login(user)
				client_id = register_client()

				response = web.get(
					"/o/authorize/", authorize_params(client_id, resource_for(self.site), scope=ALL_SCOPES)
				)

				self.assertEqual(response.status_code, 200)
				for text in ("Add RSS feeds and PubMed or ClinicalTrials.gov searches", "feed every site that covers the subject"):
					if shown:
						self.assertContains(response, text)
					else:
						self.assertNotContains(response, text)

	def test_a_refresh_after_the_flag_is_removed_gets_a_token_without_it(self):
		client_id = register_client()
		body = get_token(self.manager, self.site, scope=ALL_SCOPES, client_id=client_id).json()
		self.assertIn("sources:add", body["scope"].split())
		# Bypass save(): what a bulk update would do, so the refresh token survives.
		SiteEditor.objects.filter(pk=self.manager_grant.pk).update(can_manage_sources=False)

		refreshed = Client().post(
			"/o/token/",
			{"grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": client_id},
		)

		self.assertEqual(refreshed.status_code, 200, refreshed.content)
		self.assertNotIn("sources:add", refreshed.json()["scope"].split())
		self.assertEqual(sorted(refreshed.json()["scope"].split()), ["articles:edit", "articles:read"])

	def test_a_refresh_keeps_the_scope_while_the_flag_stands(self):
		client_id = register_client()
		body = get_token(self.manager, self.site, scope=ALL_SCOPES, client_id=client_id).json()

		refreshed = Client().post(
			"/o/token/",
			{"grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": client_id},
		)

		self.assertIn("sources:add", refreshed.json()["scope"].split())

	def test_introspecting_an_old_token_after_the_flag_is_removed_does_not_return_the_scope(self):
		body = get_token(self.manager, self.site, scope=ALL_SCOPES).json()
		SiteEditor.objects.filter(pk=self.manager_grant.pk).update(can_manage_sources=False)

		info = introspect(body["access_token"]).json()

		self.assertTrue(info["active"])
		self.assertEqual(sorted(info["scope"].split()), ["articles:edit", "articles:read"])

	def test_introspecting_after_can_edit_is_lost_drops_both_scopes(self):
		body = get_token(self.manager, self.site, scope=ALL_SCOPES).json()
		# Both columns move together, as the check constraint requires.
		SiteEditor.objects.filter(pk=self.manager_grant.pk).update(can_edit=False, can_manage_sources=False)

		info = introspect(body["access_token"]).json()

		self.assertEqual(info["scope"], "articles:read")


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class AuthenticateEditorTest(TestCase):
	def setUp(self):
		self.org = make_org("Auth Flag Org")
		self.site = make_site(self.org, "auth-flag.example.com")
		self.user = make_user("auth-flag", self.org)

	def _auth(self):
		request = RequestFactory().get(
			"/editor/articles/",
			HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}",
			**{
				"HTTP_" + HEADER_USER.upper().replace("-", "_"): str(self.user.pk),
				"HTTP_" + HEADER_SITE.upper().replace("-", "_"): str(self.site.pk),
			},
		)
		return authenticate_editor(request)[1]

	def test_a_plain_editor_cannot_manage_sources(self):
		grant(self.user, self.site)

		auth = self._auth()

		self.assertTrue(auth.can_edit)
		self.assertFalse(auth.can_manage_sources)

	def test_a_source_manager_can(self):
		grant(self.user, self.site, can_manage_sources=True)

		auth = self._auth()

		self.assertTrue(auth.can_edit)
		self.assertTrue(auth.can_manage_sources)

	def test_it_reads_the_live_grant_on_every_request(self):
		editor = grant(self.user, self.site, can_manage_sources=True)
		self.assertTrue(self._auth().can_manage_sources)

		SiteEditor.objects.filter(pk=editor.pk).update(can_manage_sources=False)

		self.assertFalse(self._auth().can_manage_sources)

	def test_a_read_only_grant_never_manages_sources(self):
		grant(self.user, self.site, can_edit=False)

		auth = self._auth()

		self.assertFalse(auth.can_edit)
		self.assertFalse(auth.can_manage_sources)

	def test_the_flag_needs_can_edit_even_if_the_row_says_otherwise(self):
		# The constraint makes this row impossible; the read still does not trust the flag alone.
		editor = grant(self.user, self.site, can_manage_sources=True)
		from unittest import mock

		with mock.patch("mcpauth.editor_auth.active_grant") as active:
			active.return_value = SiteEditor(user=self.user, site=self.site, can_edit=False, can_manage_sources=True)
			self.assertFalse(self._auth().can_manage_sources)
		self.assertTrue(editor.can_manage_sources)


class SourceFlagAdminTest(TestCase):
	def setUp(self):
		self.org = make_org("Flag Admin Org")
		self.site = make_site(self.org, "flag-admin.example.com")
		self.root = get_user_model().objects.create_superuser("flag-root", "flag-root@example.com", "x")
		self.user = make_user("flag-admin-editor", self.org)
		self.staff = make_user("flag-admin-staff", is_staff=True)

	def _inline(self, user):
		request = RequestFactory().get("/")
		request.user = user
		return SiteEditorInline(Site, admin.site), request

	def test_the_field_is_in_the_form_and_the_inline_after_can_edit(self):
		self.assertIn("can_manage_sources", SiteEditorForm.Meta.fields)
		inline, _ = self._inline(self.root)
		self.assertEqual(inline.fields[1:3], ["can_edit", "can_manage_sources"])

	def test_the_form_has_the_admin_label(self):
		form = SiteEditorForm()
		self.assertEqual(form.fields["can_manage_sources"].label, "Can add sources")

	def test_the_form_refuses_the_flag_without_can_edit(self):
		form = SiteEditorForm({"user": self.user.pk, "can_manage_sources": "on"}, instance=SiteEditor(site=self.site))

		self.assertFalse(form.is_valid())
		self.assertIn("can_manage_sources", form.errors)

	def test_the_form_accepts_the_flag_with_can_edit(self):
		form = SiteEditorForm(
			{"user": self.user.pk, "can_edit": "on", "can_manage_sources": "on"}, instance=SiteEditor(site=self.site)
		)

		self.assertTrue(form.is_valid(), form.errors)

	def test_a_non_superuser_can_neither_see_nor_change_the_field(self):
		inline, request = self._inline(self.staff)

		self.assertFalse(inline.has_view_permission(request))
		self.assertFalse(inline.has_change_permission(request))
		self.assertFalse(inline.has_add_permission(request))

	def test_a_staff_member_with_model_permissions_still_cannot(self):
		from django.contrib.auth.models import Permission

		self.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label="mcpauth", content_type__model="siteeditor"))
		staff = get_user_model().objects.get(pk=self.staff.pk)
		inline, request = self._inline(staff)

		self.assertFalse(inline.has_view_permission(request))
		self.assertFalse(inline.has_change_permission(request))
		self.assertFalse(inline.has_add_permission(request))
