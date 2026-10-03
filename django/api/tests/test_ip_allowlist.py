"""
The per-key IP allowlist (APIAccessScheme.ip_addresses) must be checked
against the address nginx saw, not one the client can choose.

nginx sets ``X-Forwarded-For $proxy_add_x_forwarded_for``, which appends the
real address to whatever X-Forwarded-For the client sent, so the first entry
is client-chosen. ``X-Real-IP $remote_addr`` replaces any client-sent value.

Run with:
    docker exec gregory python manage.py test api.tests.test_ip_allowlist
"""

import json
from datetime import timedelta

from django.contrib.sites.models import Site
from django.test import Client, RequestFactory, TestCase
from django.utils.timezone import now
from organizations.models import Organization

from api.models import APIAccessScheme, APIAccessSchemeLog
from api.utils.utils import getIPAddress
from gregory.models import Articles, OrganizationApiSettings, OrganizationSite, Team
from gregory.visibility import _resolve_api_scheme

ALLOWED_IP = "203.0.113.5"
OTHER_IP = "198.51.100.7"
NGINX_IP = "172.18.0.1"


class GetIPAddressTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()

	def test_prefers_x_real_ip(self):
		request = self.factory.get("/", HTTP_X_REAL_IP=OTHER_IP, REMOTE_ADDR=NGINX_IP)
		self.assertEqual(getIPAddress(request), OTHER_IP)

	def test_falls_back_to_remote_addr(self):
		request = self.factory.get("/", REMOTE_ADDR=OTHER_IP)
		self.assertEqual(getIPAddress(request), OTHER_IP)

	def test_blank_x_real_ip_falls_back_to_remote_addr(self):
		request = self.factory.get("/", HTTP_X_REAL_IP="  ", REMOTE_ADDR=OTHER_IP)
		self.assertEqual(getIPAddress(request), OTHER_IP)

	def test_x_real_ip_is_stripped(self):
		request = self.factory.get("/", HTTP_X_REAL_IP=f" {OTHER_IP} ")
		self.assertEqual(getIPAddress(request), OTHER_IP)

	def test_ignores_x_forwarded_for(self):
		request = self.factory.get(
			"/", HTTP_X_FORWARDED_FOR=f"{ALLOWED_IP}, {OTHER_IP}", REMOTE_ADDR=OTHER_IP
		)
		self.assertEqual(getIPAddress(request), OTHER_IP)


class IPAllowlistEditArticleTest(TestCase):
	"""checkValidAccess path, via POST /articles/edit/."""

	def setUp(self):
		self.client = Client()
		org = Organization.objects.create(name="Allowlist Org", slug="allowlist-org")
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=False
		)
		team = Team.objects.create(organization=org, name="Allowlist Team", slug="allowlist-team")
		article = Articles.objects.create(
			title="Allowlist Article",
			link="https://example.com/allowlist",
			doi="10.1111/allowlist",
		)
		article.teams.add(team)
		# Editorial content is per site, so the key needs one to edit.
		site = Site.objects.create(domain="allowlist.example.com", name="allowlist")
		OrganizationSite.objects.create(organization=org, site=site, is_default=True)
		self.scheme = APIAccessScheme.objects.create(
			client_name="allowlist-key",
			client_contacts="allowlist@example.com",
			organization=org,
			site=site,
			ip_addresses=ALLOWED_IP,
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)

	def _edit(self, **meta):
		return self.client.post(
			"/articles/edit/",
			data=json.dumps({"doi": "10.1111/allowlist", "takeaways": "x"}),
			content_type="application/json",
			HTTP_AUTHORIZATION=self.scheme.api_key,
			**meta,
		)

	def _last_logged_ip(self):
		# Refusals are logged without a scheme (checkValidAccess raises before
		# returning one), so look at the latest row overall.
		return APIAccessSchemeLog.objects.latest("access_date").ip_addr

	def test_spoofed_forwarded_for_is_refused_direct(self):
		"""No proxy: a client-sent X-Forwarded-For naming an allowlisted IP
		must not stand in for the socket address."""
		resp = self._edit(HTTP_X_FORWARDED_FOR=ALLOWED_IP, REMOTE_ADDR=OTHER_IP)
		self.assertEqual(resp.status_code, 401)
		self.assertEqual(self._last_logged_ip(), OTHER_IP)

	def test_spoofed_forwarded_for_is_refused_behind_nginx(self):
		"""Behind nginx: X-Forwarded-For is "<spoofed>, <real>" and X-Real-IP
		is the real address."""
		resp = self._edit(
			HTTP_X_FORWARDED_FOR=f"{ALLOWED_IP}, {OTHER_IP}",
			HTTP_X_REAL_IP=OTHER_IP,
			REMOTE_ADDR=NGINX_IP,
		)
		self.assertEqual(resp.status_code, 401)
		self.assertEqual(self._last_logged_ip(), OTHER_IP)

	def test_allowlisted_x_real_ip_is_accepted(self):
		resp = self._edit(HTTP_X_REAL_IP=ALLOWED_IP, REMOTE_ADDR=NGINX_IP)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(self._last_logged_ip(), ALLOWED_IP)

	def test_allowlisted_remote_addr_is_accepted(self):
		resp = self._edit(REMOTE_ADDR=ALLOWED_IP)
		self.assertEqual(resp.status_code, 200)

	def test_resolve_api_scheme_ignores_spoofed_forwarded_for(self):
		"""The read-side visibility check (_resolve_api_scheme) uses the same
		helper and must refuse the spoofed address too."""
		factory = RequestFactory()
		spoofed = factory.get(
			"/",
			HTTP_AUTHORIZATION=self.scheme.api_key,
			HTTP_X_FORWARDED_FOR=ALLOWED_IP,
			REMOTE_ADDR=OTHER_IP,
		)
		self.assertIsNone(_resolve_api_scheme(spoofed))
		allowed = factory.get(
			"/",
			HTTP_AUTHORIZATION=self.scheme.api_key,
			HTTP_X_REAL_IP=ALLOWED_IP,
			REMOTE_ADDR=NGINX_IP,
		)
		self.assertEqual(_resolve_api_scheme(allowed), self.scheme)
