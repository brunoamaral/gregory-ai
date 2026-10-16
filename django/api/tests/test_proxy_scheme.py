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
		cls.subject = subject
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

	def test_an_authors_articles_list_agrees_with_next_on_the_scheme(self):
		"""`articles_list` used to read the header by hand; it now shares
		request.scheme with `next`, so the two cannot disagree in one response."""
		from gregory.models import Articles, Authors

		# An author is listed only when an article in the site's scope has them, and
		# /authors/ has a fixed page size of 10, so 11 are needed for a `next`.
		for n in range(11):
			author = Authors.objects.create(
				given_name="A", family_name=f"Author{n}", full_name=f"A Author{n}", ORCID=f"0000-0001-0000-{n:04d}"
			)
			article = Articles.objects.create(title=f"Article {n}", link=f"https://a.test/{n}")
			article.subjects.add(self.subject)
			article.authors.add(author)
		for value, expected in (("https", "https"), ("HTTPS", "http"), ("https, http", "https"), ("javascript", "http"), ("", "http")):
			with self.subTest(value=value):
				response = self.client.get(
					f"/authors/?site_id={self.site.pk}", HTTP_HOST=self.HOST, HTTP_X_FORWARDED_PROTO=value
				)
				self.assertEqual(response.status_code, 200)
				self.assertTrue(response.data["next"].startswith(f"{expected}://"), (value, response.data["next"]))
				self.assertTrue(
					response.data["results"][0]["articles_list"].startswith(f"{expected}://"),
					(value, response.data["results"][0]["articles_list"]),
				)

	def test_the_rss_feeds_self_link_is_https_behind_a_tls_proxy(self):
		"""Django's syndication framework picks the scheme of the feed's own link
		from request.is_secure()."""
		from django.urls import reverse
		from sitesettings.models import CustomSetting

		CustomSetting.objects.filter(site=self.site).update(rss_enabled=True)
		url = reverse(
			"site_trials_by_subject_feed",
			kwargs={"site_id": self.site.pk, "subject_slug": "proxy-subject"},
		)

		plain = self.client.get(url, HTTP_HOST=self.HOST)
		proxied = self.client.get(url, HTTP_HOST=self.HOST, HTTP_X_FORWARDED_PROTO="https")

		self.assertEqual(plain.status_code, 200, plain.content[:200])
		self.assertRegex(plain.content.decode(), r'<atom:link href="http://[^"]+" rel="self"')
		self.assertRegex(proxied.content.decode(), r'<atom:link href="https://[^"]+" rel="self"')

	def test_the_request_reports_itself_secure_only_through_the_header(self):
		from django.test import RequestFactory

		request = RequestFactory().get("/", HTTP_X_FORWARDED_PROTO="https")
		self.assertTrue(request.is_secure())
		self.assertEqual(request.scheme, "https")
		plain = RequestFactory().get("/")
		self.assertFalse(plain.is_secure())
		self.assertEqual(plain.scheme, "http")
