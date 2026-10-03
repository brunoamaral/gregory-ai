"""
The OAuth flow end to end: a client registers, a person signs in and approves,
the code is exchanged, and what the token says about site and tier is what the
rules in MCP-AUTH-PLAN.md require.
"""

import json
from urllib.parse import parse_qs, urlsplit

from django.test import Client, TestCase, override_settings

from mcpauth.models import AccessToken, SiteEditor
from mcpauth.tests.helpers import (
	REDIRECT_URI,
	SERVICE_KEY,
	authorize_params,
	get_token,
	grant,
	introspect,
	make_org,
	make_site,
	make_user,
	pkce_pair,
	register_client,
	resource_for,
)


def issued():
	"""Access tokens issued through the flow. Registering a client also stores a
	registration token (no site), which is DOT's bookkeeping, not an editor session."""
	return AccessToken.objects.exclude(site=None)


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class FlowTestCase(TestCase):
	def setUp(self):
		self.org = make_org("Flow Org")
		self.site = make_site(self.org, "flow-one.example.com", admin_email="admin@flow-one.example.com")
		self.second_site = make_site(self.org, "flow-two.example.com")
		self.private_site = make_site(self.org, "flow-private.example.com", api_public=False)
		self.editor = make_user("ana", self.org)
		self.outsider = make_user("outsider")
		grant(self.editor, self.site)
		grant(self.editor, self.second_site)


class EditorTierTest(FlowTestCase):
	def test_editor_gets_an_editor_tier_token_bound_to_the_site(self):
		response = get_token(self.editor, self.site)

		self.assertEqual(response.status_code, 200, response.content)
		body = response.json()
		self.assertEqual(sorted(body["scope"].split()), ["articles:edit", "articles:read"])
		token = issued().get()
		self.assertEqual(token.site, self.site)
		self.assertEqual(token.tier, "editor")
		self.assertEqual(token.user, self.editor)
		self.assertEqual(token.resource, [resource_for(self.site)])
		self.assertEqual(body["expires_in"], 3600)

	def test_introspection_reports_user_site_tier_scope_and_audience(self):
		body = get_token(self.editor, self.site).json()

		info = introspect(body["access_token"]).json()

		self.assertTrue(info["active"])
		self.assertEqual(info["user_id"], self.editor.pk)
		self.assertEqual(info["site_id"], self.site.pk)
		self.assertEqual(info["tier"], "editor")
		self.assertEqual(info["aud"], [resource_for(self.site)])
		self.assertEqual(sorted(info["scope"].split()), ["articles:edit", "articles:read"])
		self.assertIn("exp", info)

	def test_read_only_grant_gets_editor_tier_without_the_edit_scope(self):
		reader = make_user("reader", self.org)
		grant(reader, self.site, can_edit=False)

		body = get_token(reader, self.site).json()

		self.assertEqual(body["scope"], "articles:read")
		info = introspect(body["access_token"]).json()
		self.assertEqual(info["tier"], "editor")
		self.assertEqual(info["scope"], "articles:read")


class MultiSiteEditorTest(FlowTestCase):
	def test_two_sites_give_two_tokens_each_bound_to_its_own_address(self):
		first = get_token(self.editor, self.site).json()["access_token"]
		second = get_token(self.editor, self.second_site).json()["access_token"]

		first_info = introspect(first).json()
		second_info = introspect(second).json()

		self.assertEqual(first_info["site_id"], self.site.pk)
		self.assertEqual(second_info["site_id"], self.second_site.pk)
		self.assertEqual(first_info["aud"], [resource_for(self.site)])
		self.assertEqual(second_info["aud"], [resource_for(self.second_site)])

	def test_revoking_one_grant_leaves_the_other_connector_working(self):
		first = get_token(self.editor, self.site).json()["access_token"]
		second = get_token(self.editor, self.second_site).json()["access_token"]

		SiteEditor.objects.get(user=self.editor, site=self.site).revoke()

		self.assertFalse(introspect(first).json()["active"])
		self.assertTrue(introspect(second).json()["active"])

	def test_user_without_a_grant_on_the_second_site_gets_the_public_tier(self):
		single = make_user("single", self.org)
		grant(single, self.site)

		body = get_token(single, self.second_site).json()

		self.assertEqual(body["scope"], "articles:read")
		self.assertEqual(issued().get().tier, "public")
		info = introspect(body["access_token"]).json()
		self.assertEqual(info["tier"], "public")
		self.assertEqual(info["site_id"], self.second_site.pk)

	def test_user_without_a_grant_is_refused_at_consent_on_a_private_site(self):
		web = Client()
		web.force_login(self.outsider)
		client_id = register_client()

		response = web.get("/o/authorize/", authorize_params(client_id, resource_for(self.private_site)))

		self.assertEqual(response.status_code, 403)
		self.assertContains(response, "No access to", status_code=403)
		self.assertNotContains(response, 'name="allow"', status_code=403)
		self.assertEqual(issued().count(), 0)


class PublicTierTest(FlowTestCase):
	def test_signed_in_user_without_a_grant_gets_a_read_only_public_token(self):
		body = get_token(self.outsider, self.site).json()

		self.assertEqual(body["scope"], "articles:read")
		token = issued().get()
		self.assertEqual(token.tier, "public")
		self.assertNotIn("articles:edit", token.scope)

	def test_public_consent_names_the_contact_for_editor_access(self):
		web = Client()
		web.force_login(self.outsider)
		client_id = register_client()

		response = web.get("/o/authorize/", authorize_params(client_id, resource_for(self.site)))

		self.assertContains(response, "public data only")
		self.assertContains(response, "admin@flow-one.example.com")
		self.assertNotContains(response, "Edit this site")

	def test_public_user_forging_the_scope_field_still_gets_no_edit_scope(self):
		web = Client()
		web.force_login(self.outsider)
		client_id = register_client()
		verifier, challenge = pkce_pair()
		resource = resource_for(self.site)

		approved = web.post(
			"/o/authorize/",
			{
				"client_id": client_id,
				"redirect_uri": REDIRECT_URI,
				"scope": "articles:read articles:edit",
				"response_type": "code",
				"state": "s",
				"code_challenge": challenge,
				"code_challenge_method": "S256",
				"resource": resource,
				"allow": "Authorize",
			},
		)
		code = parse_qs(urlsplit(approved["Location"]).query)["code"][0]
		token = Client().post(
			"/o/token/",
			{
				"grant_type": "authorization_code",
				"code": code,
				"redirect_uri": REDIRECT_URI,
				"client_id": client_id,
				"code_verifier": verifier,
			},
		).json()

		self.assertEqual(token["scope"], "articles:read")

	def test_revoking_a_grant_turns_the_next_sign_in_into_the_public_tier(self):
		self.assertEqual(issued().count(), 0)
		get_token(self.editor, self.site)
		SiteEditor.objects.get(user=self.editor, site=self.site).revoke()
		self.assertEqual(AccessToken.objects.filter(site=self.site).count(), 0)

		body = get_token(self.editor, self.site).json()

		self.assertEqual(body["scope"], "articles:read")
		self.assertEqual(issued().get().tier, "public")


class ConsentTest(FlowTestCase):
	def test_consent_names_site_application_and_actions(self):
		web = Client()
		web.force_login(self.editor)
		client_id = register_client()

		response = web.get("/o/authorize/", authorize_params(client_id, resource_for(self.site)))

		self.assertContains(response, "flow-one.example.com")
		self.assertContains(response, "Test connector")
		self.assertContains(response, "Edit this site")
		self.assertContains(response, "recorded under your name")

	def test_consent_screen_cannot_be_framed_or_cached(self):
		web = Client()
		web.force_login(self.editor)

		response = web.get("/o/authorize/", authorize_params(register_client(), resource_for(self.site)))

		self.assertEqual(response["X-Frame-Options"], "DENY")
		self.assertIn("no-cache", response["Cache-Control"])
		self.assertContains(response, "csrfmiddlewaretoken")

	def test_anonymous_authorize_redirects_to_the_branded_login(self):
		client_id = register_client()

		response = Client().get("/o/authorize/", authorize_params(client_id, resource_for(self.site)))

		self.assertEqual(response.status_code, 302)
		self.assertTrue(response["Location"].startswith("/o/login/?next="))

	def test_approval_prompt_auto_does_not_skip_consent(self):
		get_token(self.editor, self.site)
		web = Client()
		web.force_login(self.editor)
		client_id = register_client()
		params = authorize_params(client_id, resource_for(self.site))
		params["approval_prompt"] = "auto"

		response = web.get("/o/authorize/", params)

		self.assertEqual(response.status_code, 200)

	def test_post_with_missing_fields_is_a_bad_request_not_a_crash(self):
		web = Client()
		web.force_login(self.editor)

		response = web.post("/o/authorize/", {"allow": "Authorize"})

		self.assertEqual(response.status_code, 400)
		self.assertEqual(issued().count(), 0)

	def test_post_naming_a_site_the_person_cannot_use_is_refused(self):
		web = Client()
		web.force_login(self.outsider)
		client_id = register_client()
		_, challenge = pkce_pair()

		response = web.post(
			"/o/authorize/",
			{
				"client_id": client_id,
				"redirect_uri": REDIRECT_URI,
				"scope": "articles:read",
				"response_type": "code",
				"state": "s",
				"code_challenge": challenge,
				"code_challenge_method": "S256",
				"resource": resource_for(self.private_site),
				"allow": "Authorize",
			},
		)

		self.assertEqual(response.status_code, 302)
		self.assertIn("error=access_denied", response["Location"])
		self.assertEqual(issued().count(), 0)

	def test_declining_returns_access_denied_and_issues_nothing(self):
		web = Client()
		web.force_login(self.editor)
		client_id = register_client()
		_, challenge = pkce_pair()

		response = web.post(
			"/o/authorize/",
			{
				"client_id": client_id,
				"redirect_uri": REDIRECT_URI,
				"scope": "articles:read",
				"response_type": "code",
				"state": "s",
				"code_challenge": challenge,
				"code_challenge_method": "S256",
				"resource": resource_for(self.site),
			},
		)

		self.assertEqual(response.status_code, 302)
		self.assertIn("error=access_denied", response["Location"])
		self.assertEqual(issued().count(), 0)


class ResourceBindingTest(FlowTestCase):
	def _authorize(self, resource):
		web = Client()
		web.force_login(self.editor)
		return web.get("/o/authorize/", authorize_params(register_client(), resource))

	def test_missing_resource_is_refused_with_invalid_target(self):
		response = self._authorize(None)

		self.assertEqual(response.status_code, 302)
		self.assertIn("error=invalid_target", response["Location"])

	def test_resource_that_is_not_an_editor_address_is_refused(self):
		for resource in (
			"https://gregory-ai.flow-one.example.com/mcp",
			"https://gregory-ai.flow-one.example.com/mcp/editor/extra",
			"https://gregory-ai.flow-one.example.com/other",
			"http://gregory-ai.flow-one.example.com/mcp/editor",
			"https://unknown-host.example.org/mcp/editor",
		):
			with self.subTest(resource=resource):
				response = self._authorize(resource)
				self.assertEqual(response.status_code, 302)
				self.assertIn("error=invalid_target", response["Location"])

	def test_site_with_the_assistant_switched_off_is_refused(self):
		off = make_site(self.org, "flow-off.example.com", mcp_enabled=False)
		grant(self.editor, off)

		response = self._authorize(resource_for(off))

		self.assertIn("error=invalid_target", response["Location"])

	def test_two_resources_in_one_request_are_refused(self):
		web = Client()
		web.force_login(self.editor)
		params = authorize_params(register_client(), resource_for(self.site))
		query = "&".join(f"{k}={v}" for k, v in params.items())
		query += "&resource=" + resource_for(self.second_site)

		response = web.get("/o/authorize/?" + query)

		self.assertEqual(response.status_code, 302)
		self.assertIn("error=invalid_target", response["Location"])

	def test_host_with_the_tenant_prefix_resolves_to_the_site(self):
		response = self._authorize(resource_for(self.site, prefix="gregory-ai"))

		self.assertEqual(response.status_code, 200)

	def test_pkce_is_required(self):
		web = Client()
		web.force_login(self.editor)
		params = authorize_params(register_client(), resource_for(self.site))
		del params["code_challenge"], params["code_challenge_method"]

		response = web.get("/o/authorize/", params)

		self.assertEqual(response.status_code, 302)
		self.assertIn("error=invalid_request", response["Location"])


class RefreshTest(FlowTestCase):
	def _refresh(self, client_id, refresh_token, **extra):
		return Client().post(
			"/o/token/",
			{
				"grant_type": "refresh_token",
				"refresh_token": refresh_token,
				"client_id": client_id,
				**extra,
			},
		)

	def test_refresh_rotates_and_keeps_site_and_tier(self):
		client_id = register_client()
		body = get_token(self.editor, self.site, client_id=client_id).json()

		refreshed = self._refresh(client_id, body["refresh_token"])

		self.assertEqual(refreshed.status_code, 200, refreshed.content)
		new = refreshed.json()
		self.assertNotEqual(new["refresh_token"], body["refresh_token"])
		info = introspect(new["access_token"]).json()
		self.assertEqual(info["site_id"], self.site.pk)
		self.assertEqual(info["tier"], "editor")
		self.assertEqual(info["aud"], [resource_for(self.site)])
		# The old refresh token is spent.
		self.assertEqual(self._refresh(client_id, body["refresh_token"]).status_code, 400)

	def test_refresh_cannot_widen_the_resource_to_another_site(self):
		client_id = register_client()
		body = get_token(self.editor, self.site, client_id=client_id).json()

		response = self._refresh(
			client_id, body["refresh_token"], resource=resource_for(self.second_site)
		)

		self.assertEqual(response.status_code, 400)
		# DOT sends its error bodies as text/html; the payload is still JSON.
		self.assertEqual(json.loads(response.content)["error"], "invalid_target")

	def test_refresh_does_not_upgrade_a_public_token_to_the_editor_tier(self):
		client_id = register_client()
		body = get_token(self.outsider, self.site, client_id=client_id).json()
		grant(self.outsider, self.site)

		refreshed = self._refresh(client_id, body["refresh_token"]).json()

		self.assertEqual(refreshed["scope"], "articles:read")
		self.assertEqual(introspect(refreshed["access_token"]).json()["tier"], "public")


class TokenEndpointHardeningTest(FlowTestCase):
	def test_only_authorization_code_and_refresh_grants_are_accepted(self):
		client_id = register_client()
		for grant_type in ("password", "client_credentials"):
			with self.subTest(grant_type=grant_type):
				response = Client().post(
					"/o/token/",
					{"grant_type": grant_type, "client_id": client_id, "username": "ana", "password": "x"},
				)
				self.assertIn(response.status_code, (400, 401))
				self.assertEqual(issued().count(), 0)

	def test_code_exchange_without_the_verifier_fails(self):
		web = Client()
		web.force_login(self.editor)
		client_id = register_client()
		verifier, challenge = pkce_pair()
		approved = web.post(
			"/o/authorize/",
			{
				"client_id": client_id,
				"redirect_uri": REDIRECT_URI,
				"scope": "articles:read",
				"response_type": "code",
				"state": "s",
				"code_challenge": challenge,
				"code_challenge_method": "S256",
				"resource": resource_for(self.site),
				"allow": "Authorize",
			},
		)
		code = parse_qs(urlsplit(approved["Location"]).query)["code"][0]

		response = Client().post(
			"/o/token/",
			{"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI, "client_id": client_id},
		)

		self.assertEqual(response.status_code, 400)
		self.assertEqual(issued().count(), 0)

	def test_grant_revoked_between_consent_and_exchange_drops_to_public(self):
		web = Client()
		web.force_login(self.editor)
		client_id = register_client()
		verifier, challenge = pkce_pair()
		approved = web.post(
			"/o/authorize/",
			{
				"client_id": client_id,
				"redirect_uri": REDIRECT_URI,
				"scope": "articles:read articles:edit",
				"response_type": "code",
				"state": "s",
				"code_challenge": challenge,
				"code_challenge_method": "S256",
				"resource": resource_for(self.site),
				"allow": "Authorize",
			},
		)
		code = parse_qs(urlsplit(approved["Location"]).query)["code"][0]
		SiteEditor.objects.get(user=self.editor, site=self.site).revoke()

		token = Client().post(
			"/o/token/",
			{
				"grant_type": "authorization_code",
				"code": code,
				"redirect_uri": REDIRECT_URI,
				"client_id": client_id,
				"code_verifier": verifier,
			},
		).json()

		self.assertEqual(token["scope"], "articles:read")
		self.assertEqual(issued().get().tier, "public")
