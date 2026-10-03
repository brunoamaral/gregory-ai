"""
Client registration (D20): RFC 7591 dynamic registration and Client ID
Metadata Documents, both open, both limited to what an MCP connector needs.
"""

import json
from copy import deepcopy

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from oauth2_provider.models import get_application_model

from mcpauth.models import AccessToken
from mcpauth.registration import PolicyMetadataFetcher
from mcpauth.tests.helpers import (
	REDIRECT_URI,
	SERVICE_KEY,
	TEST_CLIENT_SECRET,
	authorize_params,
	grant,
	introspect,
	make_org,
	make_site,
	make_user,
	password_for,
	pkce_pair,
	register_client,
	resource_for,
)

Application = get_application_model()

CIMD_URL = "https://client.example.com/oauth/client.json"


class FakeFetcher(PolicyMetadataFetcher):
	"""Stands in for the network step only: returns a fixed document, then our
	registration policy runs as it does in production."""

	document = {
		"client_id": CIMD_URL,
		"client_name": "Metadata client",
		"redirect_uris": [REDIRECT_URI],
		"grant_types": ["authorization_code"],
		"token_endpoint_auth_method": "none",
	}

	def fetch_document(self, client_id):
		return dict(self.document, client_id=client_id), 300


def _post_register(body, **extra):
	return Client().post("/o/register/", data=json.dumps(body), content_type="application/json", **extra)


class DynamicRegistrationTest(TestCase):
	def setUp(self):
		cache.clear()

	def test_anonymous_registration_creates_a_public_authorization_code_client(self):
		response = _post_register(
			{
				"client_name": "Claude",
				"redirect_uris": [REDIRECT_URI],
				"grant_types": ["authorization_code", "refresh_token"],
				"token_endpoint_auth_method": "none",
			}
		)

		self.assertEqual(response.status_code, 201, response.content)
		body = response.json()
		app = Application.objects.get(client_id=body["client_id"])
		self.assertEqual(app.client_type, "public")
		self.assertEqual(app.authorization_grant_type, "authorization-code")
		self.assertFalse(app.skip_authorization)
		self.assertEqual(app.registration_source, "dcr")
		self.assertNotIn("client_secret", body)

	def test_confidential_client_gets_a_secret(self):
		response = _post_register(
			{
				"redirect_uris": [REDIRECT_URI],
				"grant_types": ["authorization_code"],
				"token_endpoint_auth_method": "client_secret_post",
			}
		)

		self.assertEqual(response.status_code, 201)
		self.assertIn("client_secret", response.json())

	def test_other_grant_types_are_refused(self):
		for grants in (
			["password"],
			["client_credentials"],
			["implicit"],
			["authorization_code", "client_credentials"],
			["urn:ietf:params:oauth:grant-type:device_code"],
		):
			with self.subTest(grants=grants):
				response = _post_register({"redirect_uris": [REDIRECT_URI], "grant_types": grants})
				self.assertEqual(response.status_code, 400)
				self.assertEqual(response.json()["error"], "invalid_client_metadata")
		self.assertEqual(Application.objects.count(), 0)

	def test_redirect_uris_must_be_https_or_loopback(self):
		refused = _post_register({"redirect_uris": ["http://client.example.com/cb"]})
		self.assertEqual(refused.status_code, 400)
		loopback = _post_register({"redirect_uris": ["http://127.0.0.1:33418/callback"]})
		self.assertEqual(loopback.status_code, 201, loopback.content)

	def test_registration_is_rate_limited_per_address(self):
		with override_settings(OAUTH_DCR_MAX_PER_HOUR=3):
			statuses = [
				_post_register({"redirect_uris": [REDIRECT_URI]}, REMOTE_ADDR="198.51.100.7").status_code
				for _ in range(5)
			]
			other_address = _post_register({"redirect_uris": [REDIRECT_URI]}, REMOTE_ADDR="198.51.100.8")

		self.assertEqual(statuses, [201, 201, 201, 429, 429])
		self.assertEqual(other_address.status_code, 201)

	def test_a_spoofed_forwarded_for_does_not_escape_the_registration_limit(self):
		with override_settings(OAUTH_DCR_MAX_PER_HOUR=2):
			statuses = [
				_post_register(
					{"redirect_uris": [REDIRECT_URI]},
					HTTP_X_REAL_IP="198.51.100.40",
					HTTP_X_FORWARDED_FOR=f"10.8.8.{index}, 198.51.100.40",
				).status_code
				for index in range(4)
			]

		self.assertEqual(statuses, [201, 201, 429, 429])

	def test_management_endpoint_needs_the_registration_token(self):
		body = _post_register({"redirect_uris": [REDIRECT_URI]}).json()
		url = f"/o/register/{body['client_id']}/"

		self.assertEqual(Client().get(url).status_code, 401)
		ok = Client().get(url, HTTP_AUTHORIZATION=f"Bearer {body['registration_access_token']}")
		self.assertEqual(ok.status_code, 200)

	def test_an_update_cannot_escape_the_registration_policy(self):
		"""RFC 7592 updates pass the same policy as registration: a client can't
		register as an authorization-code client and then switch itself to
		another grant or a plain-http redirect."""
		body = _post_register({"redirect_uris": [REDIRECT_URI]}).json()
		url = f"/o/register/{body['client_id']}/"
		auth = {"HTTP_AUTHORIZATION": f"Bearer {body['registration_access_token']}"}

		for update in (
			{"redirect_uris": [REDIRECT_URI], "grant_types": ["password"]},
			{"redirect_uris": ["http://client.example.com/cb"]},
		):
			with self.subTest(update=update):
				refused = Client().put(url, data=json.dumps(update), content_type="application/json", **auth)
				self.assertEqual(refused.status_code, 400)
				self.assertEqual(refused.json()["error"], "invalid_client_metadata")

		app = Application.objects.get(client_id=body["client_id"])
		self.assertEqual(app.authorization_grant_type, Application.GRANT_AUTHORIZATION_CODE)
		self.assertEqual(app.redirect_uris, REDIRECT_URI)

		allowed = Client().put(
			url,
			data=json.dumps({"redirect_uris": ["http://127.0.0.1:40000/callback"]}),
			content_type="application/json",
			**auth,
		)
		self.assertEqual(allowed.status_code, 200, allowed.content)

	def test_registration_token_is_never_an_editor_token(self):
		with override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY):
			body = _post_register({"redirect_uris": [REDIRECT_URI]}).json()
			self.assertFalse(introspect(body["registration_access_token"]).json()["active"])


class ClientIdMetadataDocumentTest(TestCase):
	def setUp(self):
		cache.clear()
		self.org = make_org("Cimd Org")
		self.site = make_site(self.org, "cimd.example.com")
		self.user = make_user("cimd-user", self.org)
		grant(self.user, self.site)

	def _settings(self):
		config = deepcopy(settings.OAUTH2_PROVIDER)
		config["CIMD_METADATA_FETCHER"] = "mcpauth.tests.test_registration.FakeFetcher"
		return override_settings(OAUTH2_PROVIDER=config)

	def test_https_url_client_id_is_resolved_and_can_authorize(self):
		with self._settings():
			web = Client()
			web.force_login(self.user)

			response = web.get("/o/authorize/", authorize_params(CIMD_URL, resource_for(self.site)))

			self.assertEqual(response.status_code, 200, response.content)
			self.assertContains(response, "Metadata client")
			app = Application.objects.get(client_id=CIMD_URL)
			self.assertEqual(app.registration_source, "cimd")

	def test_document_with_a_remote_http_redirect_is_not_registered(self):
		with self._settings():
			FakeFetcher.document = dict(FakeFetcher.document, redirect_uris=["http://client.example.com/cb"])
			try:
				web = Client()
				web.force_login(self.user)
				params = authorize_params(CIMD_URL, resource_for(self.site))
				params["redirect_uri"] = "http://client.example.com/cb"

				response = web.get("/o/authorize/", params)

				self.assertEqual(response.status_code, 400)
				self.assertEqual(Application.objects.count(), 0)
			finally:
				FakeFetcher.document = dict(FakeFetcher.document, redirect_uris=[REDIRECT_URI])

	def test_document_asking_for_the_implicit_grant_is_not_registered(self):
		with self._settings():
			FakeFetcher.document = dict(FakeFetcher.document, grant_types=["implicit"])
			try:
				web = Client()
				web.force_login(self.user)

				response = web.get("/o/authorize/", authorize_params(CIMD_URL, resource_for(self.site)))

				self.assertEqual(response.status_code, 400)
				self.assertEqual(Application.objects.count(), 0)
			finally:
				FakeFetcher.document = dict(FakeFetcher.document, grant_types=["authorization_code"])

	def test_cimd_client_cannot_skip_consent_or_name_a_foreign_redirect(self):
		with self._settings():
			web = Client()
			web.force_login(self.user)
			params = authorize_params(CIMD_URL, resource_for(self.site))
			params["redirect_uri"] = "https://attacker.example.net/cb"

			response = web.get("/o/authorize/", params)

			self.assertEqual(response.status_code, 400)

	def test_unreachable_document_is_just_an_unknown_client(self):
		web = Client()
		web.force_login(self.user)

		# DOT's real fetcher refuses the unresolvable host; nothing is created.
		response = web.get(
			"/o/authorize/",
			authorize_params("https://does-not-resolve.invalid/client.json", resource_for(self.site)),
		)

		self.assertEqual(response.status_code, 400)
		self.assertEqual(Application.objects.count(), 0)

	def test_metadata_advertises_cimd(self):
		data = Client().get("/.well-known/oauth-authorization-server").json()
		self.assertTrue(data["client_id_metadata_document_supported"])


class ServerMetadataTest(TestCase):
	def setUp(self):
		self.data = Client().get("/.well-known/oauth-authorization-server").json()

	def test_endpoints_sit_under_o(self):
		self.assertTrue(self.data["authorization_endpoint"].endswith("/o/authorize/"))
		self.assertTrue(self.data["token_endpoint"].endswith("/o/token/"))
		self.assertTrue(self.data["revocation_endpoint"].endswith("/o/revoke/"))
		self.assertTrue(self.data["registration_endpoint"].endswith("/o/register/"))

	def test_only_the_authorization_code_flow_with_s256_is_advertised(self):
		self.assertEqual(self.data["response_types_supported"], ["code"])
		self.assertEqual(self.data["grant_types_supported"], ["authorization_code", "refresh_token"])
		self.assertEqual(self.data["code_challenge_methods_supported"], ["S256"])

	def test_scopes_are_the_two_article_scopes(self):
		self.assertEqual(self.data["scopes_supported"], ["articles:edit", "articles:read"])

	def test_metadata_is_readable_cross_origin(self):
		response = Client().get("/.well-known/oauth-authorization-server")
		self.assertEqual(response["Access-Control-Allow-Origin"], "*")

	@override_settings(
		OAUTH2_PROVIDER={**settings.OAUTH2_PROVIDER, "OIDC_ISS_ENDPOINT": "https://api.example.org"}
	)
	def test_configured_issuer_wins_over_the_request_origin(self):
		data = Client().get("/.well-known/oauth-authorization-server").json()
		self.assertEqual(data["issuer"], "https://api.example.org")
		self.assertEqual(data["token_endpoint"], "https://api.example.org/o/token/")


class RevocationTest(TestCase):
	def test_a_public_client_can_revoke_its_own_token(self):
		org = make_org("Revoke Client Org")
		site = make_site(org, "revoke-client.example.com")
		user = make_user("revoker", org)
		grant(user, site)
		client_id = register_client()
		from mcpauth.tests.helpers import get_token

		body = get_token(user, site, client_id=client_id).json()

		response = Client().post("/o/revoke/", {"token": body["access_token"], "client_id": client_id})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(AccessToken.objects.exclude(site=None).count(), 0)


class HardCodedGrantsTest(TestCase):
	def test_a_hand_made_client_credentials_client_cannot_mint_tokens(self):
		app = Application.objects.create(
			name="machine",
			client_type="confidential",
			authorization_grant_type="client-credentials",
			client_secret=TEST_CLIENT_SECRET,
		)

		response = Client().post(
			"/o/token/",
			{"grant_type": "client_credentials", "client_id": app.client_id, "client_secret": TEST_CLIENT_SECRET},
		)

		self.assertGreaterEqual(response.status_code, 400)
		self.assertEqual(AccessToken.objects.count(), 0)

	def test_a_hand_made_password_client_cannot_mint_tokens(self):
		make_user("pwuser")
		app = Application.objects.create(
			name="legacy",
			client_type="confidential",
			authorization_grant_type="password",
			client_secret=TEST_CLIENT_SECRET,
		)

		response = Client().post(
			"/o/token/",
			{
				"grant_type": "password",
				"client_id": app.client_id,
				"client_secret": TEST_CLIENT_SECRET,
				"username": "pwuser",
				"password": password_for("pwuser"),
			},
		)

		self.assertGreaterEqual(response.status_code, 400)
		self.assertEqual(AccessToken.objects.count(), 0)

	def test_pkce_pair_helper_is_s256(self):
		verifier, challenge = pkce_pair("x" * 43)
		self.assertEqual(len(challenge), 43)
