from django.core.cache import cache
from django.core.cache.backends.base import BaseCache
from django.core.cache.backends.locmem import LocMemCache
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from mcpauth.service import service_credential_valid
from mcpauth.tests.helpers import (
	SERVICE_KEY,
	authorize_params,
	get_token,
	grant,
	introspect,
	make_org,
	make_site,
	make_user,
	password_for,
	register_client,
	resource_for,
)


class LoginPageTest(TestCase):
	def setUp(self):
		cache.clear()
		self.user = make_user("login-user", make_org("Login Org"))

	def test_login_page_is_branded_and_has_a_csrf_token(self):
		response = Client(enforce_csrf_checks=True).get("/o/login/?next=/o/authorize/")

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Sign in to GregoryAI")
		self.assertContains(response, "csrfmiddlewaretoken")

	def test_login_page_cannot_be_framed(self):
		response = Client().get("/o/login/")
		self.assertEqual(response["X-Frame-Options"], "DENY")

	def test_post_without_csrf_token_is_refused(self):
		response = Client(enforce_csrf_checks=True).post(
			"/o/login/", {"username": "login-user", "password": password_for("login-user")}
		)
		self.assertEqual(response.status_code, 403)

	def test_successful_login_follows_next(self):
		response = Client().post(
			"/o/login/", {"username": "login-user", "password": password_for("login-user"), "next": "/o/authorize/?x=1"}
		)
		self.assertRedirects(response, "/o/authorize/?x=1", fetch_redirect_response=False)

	def test_next_to_another_host_is_ignored(self):
		response = Client().post(
			"/o/login/",
			{"username": "login-user", "password": password_for("login-user"), "next": "https://evil.example.net/"},
		)
		self.assertNotIn("evil.example.net", response["Location"])

	@override_settings(OAUTH_LOGIN_MAX_FAILURES=3)
	def test_repeated_failures_lock_the_username_even_for_the_right_password(self):
		for _ in range(3):
			Client().post("/o/login/", {"username": "login-user", "password": "wrong"})

		response = Client().post("/o/login/", {"username": "login-user", "password": password_for("login-user")})

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Too many failed sign-in attempts")
		self.assertNotIn("_auth_user_id", response.wsgi_request.session)

	@override_settings(OAUTH_LOGIN_MAX_FAILURES=3)
	def test_repeated_failures_lock_the_client_address_across_usernames(self):
		for index in range(3):
			Client().post(
				"/o/login/", {"username": f"nobody-{index}", "password": "x"}, REMOTE_ADDR="203.0.113.9"
			)

		response = Client().post(
			"/o/login/",
			{"username": "login-user", "password": password_for("login-user")},
			REMOTE_ADDR="203.0.113.9",
		)

		self.assertContains(response, "Too many failed sign-in attempts")

	@override_settings(OAUTH_LOGIN_MAX_FAILURES=3)
	def test_a_spoofed_forwarded_for_does_not_escape_the_address_limit(self):
		# nginx appends the real address to whatever X-Forwarded-For the client
		# sent and sets X-Real-IP itself, so only X-Real-IP is the client's address.
		for index in range(3):
			Client().post(
				"/o/login/",
				{"username": f"nobody-{index}", "password": "x"},
				HTTP_X_REAL_IP="203.0.113.30",
				HTTP_X_FORWARDED_FOR=f"10.9.9.{index}, 203.0.113.30",
			)

		response = Client().post(
			"/o/login/",
			{"username": "login-user", "password": password_for("login-user")},
			HTTP_X_REAL_IP="203.0.113.30",
			HTTP_X_FORWARDED_FOR="10.9.9.99, 203.0.113.30",
		)

		self.assertContains(response, "Too many failed sign-in attempts")

	@override_settings(OAUTH_LOGIN_MAX_FAILURES=3)
	def test_another_address_is_not_locked_out(self):
		for _ in range(3):
			Client().post("/o/login/", {"username": "someone", "password": "x"}, REMOTE_ADDR="203.0.113.9")

		response = Client().post(
			"/o/login/",
			{"username": "login-user", "password": password_for("login-user")},
			REMOTE_ADDR="203.0.113.10",
		)

		self.assertEqual(response.status_code, 302)

	@override_settings(OAUTH_LOGIN_MAX_FAILURES=3)
	def test_success_resets_the_username_counter(self):
		Client().post("/o/login/", {"username": "login-user", "password": "wrong"}, REMOTE_ADDR="203.0.113.20")
		Client().post("/o/login/", {"username": "login-user", "password": password_for("login-user")}, REMOTE_ADDR="203.0.113.21")
		for _ in range(2):
			Client().post("/o/login/", {"username": "login-user", "password": "wrong"}, REMOTE_ADDR="203.0.113.22")

		response = Client().post(
			"/o/login/", {"username": "login-user", "password": password_for("login-user")}, REMOTE_ADDR="203.0.113.23"
		)

		self.assertEqual(response.status_code, 302)

	def test_signed_in_session_goes_straight_to_consent(self):
		org = make_org("Session Org")
		site = make_site(org, "session.example.com")
		user = make_user("session-user", org)
		grant(user, site)
		web = Client()
		web.force_login(user)

		response = web.get("/o/authorize/", authorize_params(register_client(), resource_for(site)))

		self.assertEqual(response.status_code, 200)


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class IntrospectionTest(TestCase):
	def setUp(self):
		self.org = make_org("Introspect Org")
		self.site = make_site(self.org, "introspect.example.com")
		self.user = make_user("introspected", self.org)
		grant(self.user, self.site)

	def test_requires_the_service_credential(self):
		token = get_token(self.user, self.site).json()["access_token"]

		self.assertEqual(introspect(token, key=None).status_code, 401)
		self.assertEqual(introspect(token, key="wrong-key").status_code, 401)
		self.assertEqual(introspect(token, key=SERVICE_KEY).status_code, 200)

	def test_the_users_own_token_is_not_a_credential(self):
		token = get_token(self.user, self.site).json()["access_token"]

		response = Client().post(
			"/o/introspect/", {"token": token}, HTTP_AUTHORIZATION=f"Bearer {token}"
		)

		self.assertEqual(response.status_code, 401)

	@override_settings(GREGORY_MCP_SERVICE_KEY="")
	def test_unconfigured_key_refuses_everything_including_an_empty_bearer(self):
		self.assertEqual(introspect("anything", key="").status_code, 401)
		response = Client().post("/o/introspect/", {"token": "x"}, HTTP_AUTHORIZATION="Bearer ")
		self.assertEqual(response.status_code, 401)

	def test_get_is_not_allowed(self):
		response = Client().get("/o/introspect/", HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}")
		self.assertEqual(response.status_code, 405)

	def test_missing_token_parameter_is_a_bad_request(self):
		response = Client().post("/o/introspect/", {}, HTTP_AUTHORIZATION=f"Bearer {SERVICE_KEY}")
		self.assertEqual(response.status_code, 400)

	def test_unknown_expired_and_garbage_tokens_are_inactive(self):
		from datetime import timedelta

		from django.utils import timezone

		from mcpauth.models import AccessToken

		token = get_token(self.user, self.site).json()["access_token"]
		AccessToken.objects.exclude(site=None).update(expires=timezone.now() - timedelta(seconds=1))

		self.assertEqual(introspect(token).json(), {"active": False})
		self.assertEqual(introspect("not-a-token").json(), {"active": False})

	def test_response_is_not_cacheable_and_has_no_secrets(self):
		token = get_token(self.user, self.site).json()["access_token"]

		response = introspect(token)

		self.assertEqual(response["Cache-Control"], "no-store")
		self.assertNotIn(token, response.content.decode())

	def test_deactivated_user_or_switched_off_site_is_inactive(self):
		from sitesettings.models import CustomSetting

		token = get_token(self.user, self.site).json()["access_token"]
		self.assertTrue(introspect(token).json()["active"])

		CustomSetting.objects.filter(site=self.site).update(mcp_enabled=False)
		self.assertFalse(introspect(token).json()["active"])
		CustomSetting.objects.filter(site=self.site).update(mcp_enabled=True)

		self.user.is_active = False
		self.user.save()
		self.assertFalse(introspect(token).json()["active"])

	def test_public_tier_token_stops_working_when_the_site_goes_private(self):
		from sitesettings.models import CustomSetting

		outsider = make_user("public-visitor")
		token = get_token(outsider, self.site).json()["access_token"]
		self.assertEqual(introspect(token).json()["tier"], "public")

		CustomSetting.objects.filter(site=self.site).update(api_public=False)

		self.assertFalse(introspect(token).json()["active"])


class ServiceCredentialTest(SimpleTestCase):
	factory = RequestFactory()

	@override_settings(GREGORY_MCP_SERVICE_KEY="secret")
	def test_bearer_scheme_is_case_insensitive_and_the_value_exact(self):
		ok = self.factory.get("/", HTTP_AUTHORIZATION="bearer secret")
		self.assertTrue(service_credential_valid(ok))
		for header in ("Bearer secret2", "Bearer Secret", "Basic secret", "secret", "Bearer"):
			with self.subTest(header=header):
				self.assertFalse(service_credential_valid(self.factory.get("/", HTTP_AUTHORIZATION=header)))
		self.assertFalse(service_credential_valid(self.factory.get("/")))


class BaseIncrCache(LocMemCache):
	"""LocMem storage with the base ``incr()`` that DatabaseCache inherits,
	which rewrites the value with the default timeout. LocMem's own incr()
	keeps the expiry, which hid the bug."""

	incr = BaseCache.incr


@override_settings(
	CACHES={
		"default": {
			"BACKEND": "mcpauth.tests.test_login_introspect.BaseIncrCache",
			"TIMEOUT": 300,
		}
	}
)
class ThrottleWindowTest(SimpleTestCase):
	"""Production runs on DatabaseCache. A counter must outlive the cache's
	default 300-second timeout for its whole window."""

	def test_a_count_lasts_the_whole_window_not_the_cache_default(self):
		from unittest import mock

		from mcpauth import throttle

		start = 1_900_000_000.0
		with mock.patch("time.time", return_value=start):
			throttle.hit("login-ip", "203.0.113.50", 15 * 60)
			throttle.hit("login-ip", "203.0.113.50", 15 * 60)
		with mock.patch("time.time", return_value=start + 400):
			self.assertEqual(throttle.count("login-ip", "203.0.113.50"), 2)
			self.assertEqual(throttle.hit("login-ip", "203.0.113.50", 15 * 60), 3)
		with mock.patch("time.time", return_value=start + 15 * 60 + 1):
			self.assertEqual(throttle.count("login-ip", "203.0.113.50"), 0)

