"""Behind nginx the API is reached over plain HTTP, with X-Forwarded-Proto saying
what the client used. settings.SECURE_PROXY_SSL_HEADER trusts it, so absolute
URLs the API builds (pagination links, hyperlinked fields) are https for a
client that spoke https, and unchanged for one that did not.
"""

from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from api.tests.visibility_helpers import publish_subjects
from gregory.models import Subject, Team, Trials
from mcpauth.tests.helpers import make_org


class ForwardedProtoTest(TestCase):
	@classmethod
	def setUpTestData(cls):
		org = make_org("Proxy Org")
		team = Team.objects.create(organization=org, name="Proxy team", slug="proxy-team")
		subject = Subject.objects.create(team=team, subject_name="Proxy subject", subject_slug="proxy-subject")
		cls.site = publish_subjects(subject, organization=org, domain="proxy.example.com")
		for n in range(3):
			trial = Trials.objects.create(title=f"Trial {n}", link=f"https://t.test/{n}")
			trial.subjects.add(subject)

	# A real request carries a Host header with no port; the test client would
	# otherwise build the host from SERVER_NAME and port 80 and print ":80" once
	# the scheme is https.
	HOST = "localhost"

	def setUp(self):
		self.client = APIClient()
		self.url = f"/trials/?site_id={self.site.pk}&page_size=1&page=2"

	def get(self, **headers):
		return self.client.get(self.url, HTTP_HOST=self.HOST, **headers)

	def test_the_setting_trusts_exactly_x_forwarded_proto_https(self):
		self.assertEqual(settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))

	def test_pagination_links_are_https_when_the_proxy_says_so(self):
		data = self.get(HTTP_X_FORWARDED_PROTO="https").data

		self.assertTrue(data["next"].startswith("https://localhost/trials/"), data["next"])
		self.assertTrue(data["previous"].startswith("https://localhost/trials/"), data["previous"])

	def test_pagination_links_are_unchanged_without_the_header(self):
		data = self.get().data

		self.assertTrue(data["next"].startswith("http://localhost/trials/"), data["next"])
		self.assertTrue(data["previous"].startswith("http://localhost/trials/"), data["previous"])

	def test_the_proxy_saying_http_keeps_http(self):
		data = self.get(HTTP_X_FORWARDED_PROTO="http").data

		self.assertTrue(data["next"].startswith("http://localhost/"), data["next"])

	def test_a_chain_of_proxies_counts_by_its_first_entry(self):
		"""Django reads the first entry of a comma-separated list: the scheme the
		original client used."""
		for value, expected in (("https, https", "https"), ("https, http", "https"), ("http, https", "http")):
			with self.subTest(value=value):
				data = self.get(HTTP_X_FORWARDED_PROTO=value).data
				self.assertTrue(data["next"].startswith(f"{expected}://localhost/"), (value, data["next"]))

	def test_any_other_value_is_not_trusted(self):
		"""Only the exact value nginx's $scheme produces counts; anything else (a
		made-up scheme, the wrong case) leaves the request as plain HTTP."""
		for value in ("HTTPS", "wss", "1", "", "yes"):
			with self.subTest(value=value):
				data = self.get(HTTP_X_FORWARDED_PROTO=value).data
				self.assertTrue(data["next"].startswith("http://localhost/"), (value, data["next"]))

	def test_the_other_links_in_the_response_follow_the_same_scheme(self):
		"""Not only `next`: nothing in the page may mix schemes."""
		body = self.get(HTTP_X_FORWARDED_PROTO="https").content.decode()

		self.assertNotIn("http://localhost", body)

	def test_the_request_reports_itself_secure_only_through_the_header(self):
		from django.test import RequestFactory

		request = RequestFactory().get("/", HTTP_X_FORWARDED_PROTO="https")
		self.assertTrue(request.is_secure())
		self.assertEqual(request.scheme, "https")
		plain = RequestFactory().get("/")
		self.assertFalse(plain.is_secure())
		self.assertEqual(plain.scheme, "http")
