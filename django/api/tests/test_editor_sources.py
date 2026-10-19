"""
The source routes on ``/editor/`` (api/editor_views.py): list, preview and add a
source. Only a grant with ``can_manage_sources`` reaches them. Same fixture as
test_editor_endpoints (ana edits sites A and B, ben reads site A only), plus cara,
who can add sources on site A.

Nothing here reaches PubMed, ClinicalTrials.gov or a real feed: the feed fetch, DNS,
``NcbiClient`` and ``ClinicalTrialsGovAPI`` are mocked in ``gregory.source_checks``.
"""

import socket
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from api.tests.test_editor_endpoints import EditorFixture
from gregory.management.commands.feedreader_trials_ctgov import Command as CtgovCommand
from gregory.models import PublicationType, Sources, Subject, Team
from gregory.utils.ncbi import ESearchResult, NcbiError, NcbiQueryError
from mcpauth.tests.helpers import grant, make_user
from sitesettings.models import CustomSetting

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<link>https://feeds.example.org/</link><description>x</description>
<item><title>Remyelination in mice</title><link>https://feeds.example.org/1</link>
<description>A paper about multiple sclerosis.</description><pubDate>Mon, 12 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Parkinson's cohort</title><link>https://feeds.example.org/2</link>
<description>Another topic.</description><pubDate>Tue, 13 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Third</title><link>https://feeds.example.org/3</link><description>Nothing.</description></item>
</channel></rss>""".encode()

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


class FakeResponse:
	def __init__(self, status=200, body=RSS, headers=None):
		self.status_code = status
		self._body = body
		self.headers = headers or {}
		self.closed = False

	def iter_content(self, size):
		for start in range(0, len(self._body), size):
			yield self._body[start : start + size]

	def close(self):
		self.closed = True


def redirect(location, status=302):
	return FakeResponse(status, b"", {"Location": location})


def dns(table):
	"""A getaddrinfo that answers from ``{host: ip}`` (anything else is public)."""

	def lookup(host, port, *args, **kwargs):
		ip = table.get(host, "93.184.216.34")
		family = socket.AF_INET6 if ":" in ip else socket.AF_INET
		return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

	return lookup


def pubmed_client(count=42, ids=("111", "222"), reject=None, down=False):
	"""A stand-in ``NcbiClient``: one search (count, translation, sample ids), then summaries."""
	client = MagicMock()

	def esearch(term, **params):
		if reject:
			raise NcbiQueryError(reject)
		if down:
			raise NcbiError("down")
		if params.get("rettype") == "count":
			# What NCBI really does: a count-only answer has no translation.
			return ESearchResult(count=count)
		return ESearchResult(
			count=count,
			ids=list(ids)[: params.get("retmax", 0)],
			querytranslation=f"translated: {term}",
			warnings=["Phrase not found: zzz"],
		)

	client.esearch.side_effect = esearch
	client.esummary.return_value = {
		"result": {
			"111": {"title": "First  paper", "sortpubdate": "2026/10/15 00:00"},
			"222": {"title": "Second paper", "sortpubdate": "2026/10/14 00:00", "pubdate": "2026 Oct 14"},
		}
	}
	return client


def ctgov_api(total=57, recent=9, down=False, status=None):
	api = MagicMock()

	def search(**kwargs):
		if status:
			response = MagicMock(status_code=status)
			raise requests.exceptions.HTTPError(response=response)
		if down:
			raise requests.exceptions.ConnectionError("down")
		if "filter_advanced" in kwargs:
			return {"totalCount": recent, "studies": [{}]}
		return {
			"totalCount": total,
			"studies": [
				{
					"protocolSection": {
						"identificationModule": {"nctId": "NCT01234567", "briefTitle": "A trial"},
						"statusModule": {"lastUpdatePostDateStruct": {"date": "2026-10-19"}},
					}
				}
			],
		}

	api.search.side_effect = search
	return api


class SourceFixture(EditorFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.cara = make_user("cara", cls.org)
		grant(cls.cara, cls.site_a, can_manage_sources=True)
		PublicationType.objects.create(ui="D016454", name="Review")
		PublicationType.objects.create(ui="D016428", name="Journal Article")

	def setUp(self):
		super().setUp()
		for target, new in (
			("gregory.source_checks.socket.getaddrinfo", dns({})),
			("gregory.source_checks.requests.get", MagicMock(return_value=FakeResponse())),
		):
			patcher = patch(target, new)
			patcher.start()
			self.addCleanup(patcher.stop)
		self.http = requests.get  # the patched mock
		self.ncbi = pubmed_client()
		self.api = ctgov_api()
		for target, new in (
			("gregory.source_checks.NcbiClient", MagicMock(for_site=MagicMock(return_value=self.ncbi))),
			("gregory.source_checks.ClinicalTrialsGovAPI", MagicMock(return_value=self.api)),
		):
			patcher = patch(target, new)
			patcher.start()
			self.addCleanup(patcher.stop)

	def as_cara(self, method, path, data=None, site=None):
		return self.send(method, path, data, user=self.cara, site=site)

	def create(self, body=None, **extra):
		return self.as_cara("post", "/editor/sources/", self.body(body, **extra))

	def preview(self, body=None, **extra):
		return self.as_cara("post", "/editor/sources/preview/", self.body(body, **extra))

	def body(self, body=None, **extra):
		if body:  # a whole body from pubmed_body() or ctgov_body()
			return {**body, **extra}
		return {
			"source_type": "rss_papers",
			"subject_id": self.subject_a.pk,
			"link": "https://feeds.example.org/ms.xml",
			**extra,
		}

	def pubmed_body(self, **extra):
		return {
			"source_type": "pubmed",
			"subject_id": self.subject_a.pk,
			"pubmed_query": '"multiple sclerosis"[MeSH Terms] AND remyelination',
			**extra,
		}

	def ctgov_body(self, **extra):
		return {
			"source_type": "ctgov",
			"subject_id": self.subject_a.pk,
			"ctgov_search_condition": "multiple sclerosis",
			**extra,
		}


class PermissionTest(SourceFixture):
	ROUTES = (
		("get", "/editor/sources/"),
		("post", "/editor/sources/"),
		("post", "/editor/sources/preview/"),
	)

	def test_an_editor_without_the_flag_is_403_on_every_route_reads_included(self):
		for method, path in self.ROUTES:
			with self.subTest(method=method, path=path):
				response = self.send(method, path, self.body() if method == "post" else None)
				self.assertEqual(response.status_code, 403)
		self.assertFalse(Sources.objects.exists())

	def test_a_read_only_grant_is_403_on_every_route(self):
		for method, path in self.ROUTES:
			with self.subTest(method=method, path=path):
				response = self.send(method, path, self.body() if method == "post" else None, user=self.ben)
				self.assertEqual(response.status_code, 403)

	def test_no_service_credential_is_401(self):
		for method, path in self.ROUTES:
			with self.subTest(method=method, path=path):
				response = getattr(self.client, method)(path, self.body() if method == "post" else None, format="json")
				self.assertEqual(response.status_code, 401)

	def test_the_flag_is_per_site(self):
		grant(self.cara, self.site_b)  # can edit site B, but not add sources there
		self.assertEqual(self.as_cara("get", "/editor/sources/", site=self.site_b).status_code, 403)
		self.assertEqual(self.as_cara("get", "/editor/sources/").status_code, 200)

	def test_a_source_manager_is_not_a_superuser_of_other_routes(self):
		# The flag adds the source routes; it does not widen anything else.
		response = self.as_cara("get", f"/editor/articles/{self.art_b.pk}/history/")
		self.assertEqual(response.status_code, 404)


class InputTest(SourceFixture):
	def test_a_subject_outside_the_scope_is_404(self):
		for subject in (self.subject_b, self.subject_other):
			for call in (self.create, self.preview):
				with self.subTest(subject=subject.subject_name, call=call.__name__):
					self.assertEqual(call(subject_id=subject.pk).status_code, 404)
		self.assertEqual(self.create(subject_id=999999).status_code, 404)
		self.assertFalse(Sources.objects.exists())

	def test_a_setting_of_another_type_is_400(self):
		cases = (
			(self.pubmed_body(link="https://x.example.org/feed"), "link"),
			(self.ctgov_body(pubmed_query="ms"), "pubmed_query"),
			(self.body(source_type="rss_trials", keyword_filter="ms"), "keyword_filter"),
			(self.body(ctgov_search_term="ms"), "ctgov_search_term"),
		)
		for body, field in cases:
			for call in (self.create, self.preview):
				with self.subTest(field=field, call=call.__name__):
					response = call(body)
					self.assertEqual(response.status_code, 400, response.content)
					self.assertIn(field, response.json())

	def test_a_field_the_server_sets_is_400_not_ignored(self):
		for field, value in (("ignore_ssl", True), ("ignore_ssl", False), ("team_id", 1), ("active", False)):
			with self.subTest(field=field, value=value):
				response = self.create(**{field: value})
				self.assertEqual(response.status_code, 400)
				self.assertEqual(response.json()[field], ["Unknown field."])
		self.assertFalse(Sources.objects.exists())

	def test_empty_values_of_another_types_settings_are_tolerated(self):
		response = self.create(pubmed_query="", ctgov_search_term=None, excluded_publication_types=[])
		self.assertEqual(response.status_code, 201, response.content)

	def test_unsupported_source_types_are_400(self):
		for key in ("ctis", "epo_ops", "scrape_papers", "manual_papers", "nonsense"):
			with self.subTest(key):
				self.assertEqual(self.create(source_type=key).status_code, 400)

	def test_the_admins_checks_run_with_the_admins_messages(self):
		cases = (
			(self.body(link=""), "link", "Enter the feed's URL."),
			(self.body(source_type="rss_trials", link="  "), "link", "Enter the feed's URL."),
			(self.ctgov_body(ctgov_search_condition=""), "ctgov_search_condition", "every trial in its registry"),
			(self.pubmed_body(pubmed_query=""), "pubmed_query", "Enter the PubMed search"),
			(self.pubmed_body(pubmed_query="ms AND 2020[dp]"), "pubmed_query", "Remove the date clause"),
			(self.pubmed_body(pubmed_query='"multiple sclerosis'), "pubmed_query", "Check the brackets and quotes."),
			(self.pubmed_body(pubmed_query="(ms"), "pubmed_query", "Check the brackets and quotes."),
		)
		for body, field, message in cases:
			with self.subTest(field=field, message=message):
				response = self.create(body)
				self.assertEqual(response.status_code, 400, response.content)
				self.assertIn(message, response.json()[field][0])
		self.assertFalse(Sources.objects.exists())

	def test_pubmed_rejecting_the_search_is_400_with_its_message(self):
		self.ncbi.esearch.side_effect = NcbiQueryError("Phrase not found: zzz")
		response = self.create(self.pubmed_body())
		self.assertEqual(response.status_code, 400)
		self.assertEqual(response.json()["pubmed_query"], ["PubMed rejected this search: Phrase not found: zzz"])
		self.assertFalse(Sources.objects.exists())

	def test_an_unknown_publication_type_is_400(self):
		response = self.create(self.pubmed_body(excluded_publication_types=["Review", "Made Up"]))
		self.assertEqual(response.status_code, 400)
		self.assertIn("Made Up", response.json()["excluded_publication_types"][0], response.content)

	def test_clinicaltrials_gov_rejecting_the_search_is_400(self):
		self.api.search.side_effect = ctgov_api(status=400).search.side_effect
		response = self.create(self.ctgov_body())
		self.assertEqual(response.status_code, 400)
		self.assertIn("ctgov_search_condition", response.json())


class RssAddressTest(SourceFixture):
	"""The server fetches an address someone typed in, so it only goes to public hosts."""

	def refused(self, **body):
		response = self.create(self.body(**body))
		self.assertEqual(response.status_code, 400, response.content)
		self.assertFalse(Sources.objects.exists())
		return response.json()["link"][0]

	def test_a_host_resolving_to_a_private_address_is_refused_without_a_request(self):
		for ip in ("127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "0.0.0.0", "100.64.0.1", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1"):
			with self.subTest(ip=ip), patch("gregory.source_checks.socket.getaddrinfo", dns({"feeds.example.org": ip})):
				self.assertIn("private or internal", self.refused())
		self.http.assert_not_called()

	def test_one_private_address_among_public_ones_is_refused(self):
		lookup = lambda host, port, *a, **k: PUBLIC + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))]
		with patch("gregory.source_checks.socket.getaddrinfo", lookup):
			self.assertIn("private or internal", self.refused())

	def test_a_host_that_does_not_resolve_is_refused(self):
		with patch("gregory.source_checks.socket.getaddrinfo", side_effect=socket.gaierror):
			self.assertIn("could not be found", self.refused())

	def test_only_http_and_https_are_fetched(self):
		for link in ("ftp://feeds.example.org/x", "file:///etc/passwd", "gopher://feeds.example.org", "feeds.example.org/x", "http://user:pw@feeds.example.org/x"):
			with self.subTest(link=link):
				self.refused(link=link)
		self.http.assert_not_called()

	def test_a_redirect_to_a_private_address_is_refused(self):
		with patch("gregory.source_checks.socket.getaddrinfo", dns({"internal.example.org": "10.1.2.3"})):
			self.http.side_effect = [redirect("http://internal.example.org/feed")]
			self.assertIn("private or internal", self.refused())
		self.assertEqual(self.http.call_count, 1)

	def test_a_relative_redirect_is_followed_and_the_final_address_is_checked(self):
		self.http.side_effect = [redirect("/real/feed.xml"), FakeResponse()]
		response = self.preview()
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(self.http.call_args_list[1].args[0], "https://feeds.example.org/real/feed.xml")

	def test_three_redirects_are_followed_and_a_fourth_is_refused(self):
		self.http.side_effect = [redirect("/a"), redirect("/b"), redirect("/c"), FakeResponse()]
		self.assertEqual(self.preview().status_code, 200)
		self.http.side_effect = [redirect("/a"), redirect("/b"), redirect("/c"), redirect("/d"), FakeResponse()]
		self.assertIn("redirects more than 3 times", self.refused())

	def test_redirects_are_never_followed_by_requests_itself(self):
		self.create()
		kwargs = self.http.call_args.kwargs
		self.assertIs(kwargs["allow_redirects"], False)
		self.assertTrue(kwargs["stream"])
		self.assertEqual(kwargs["timeout"], 10)

	def test_a_body_over_5_mb_is_refused(self):
		self.http.return_value = FakeResponse(body=b"<rss>" + b"x" * (5 * 1024 * 1024 + 1))
		self.assertIn("larger than 5 MB", self.refused())

	def test_a_declared_length_over_5_mb_is_refused_before_reading(self):
		response = FakeResponse(headers={"Content-Length": str(6 * 1024 * 1024)})
		response.iter_content = MagicMock()
		self.http.return_value = response
		self.assertIn("larger than 5 MB", self.refused())
		response.iter_content.assert_not_called()
		self.assertTrue(response.closed)

	def test_a_body_that_is_not_a_feed_is_refused(self):
		for body in (b"<html><body>Not a feed</body></html>", b"", b"plain text"):
			with self.subTest(body=body[:20]):
				self.http.return_value = FakeResponse(body=body)
				self.assertIn("did not return a feed", self.refused())

	def test_a_feed_with_no_entries_is_refused(self):
		self.http.return_value = FakeResponse(
			body=b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
		)
		self.assertIn("did not return a feed", self.refused())

	def test_a_non_200_answer_is_refused(self):
		self.http.return_value = FakeResponse(status=404, body=b"")
		self.assertIn("HTTP 404", self.refused())

	def test_network_failures_are_refused_with_a_reason(self):
		cases = (
			(requests.exceptions.SSLError("bad cert"), "security certificate"),
			(requests.exceptions.ConnectTimeout("slow"), "within 10 seconds"),
			(requests.exceptions.ConnectionError("refused"), "could not be reached"),
		)
		for error, message in cases:
			with self.subTest(message=message):
				self.http.side_effect = error
				self.assertIn(message, self.refused())

	def test_feedparser_gets_the_bytes_not_the_address(self):
		with patch("gregory.source_checks.feedparser.parse", wraps=__import__("feedparser").parse) as parse:
			self.create()
		self.assertIsInstance(parse.call_args.args[0], bytes)


class PreviewTest(SourceFixture):
	def test_an_rss_preview_counts_the_feed_and_the_keyword_filter(self):
		response = self.preview(keyword_filter="multiple sclerosis")
		self.assertEqual(response.status_code, 200, response.content)
		body = response.json()
		counts = {c["key"]: c for c in body["counts"]}
		self.assertEqual(counts["entries_in_feed"]["value"], 3)
		self.assertEqual(counts["entries_passing_keyword_filter"]["value"], 1)
		self.assertTrue(counts["entries_in_feed"]["label"])
		self.assertEqual([s["title"] for s in body["samples"]], ["Remyelination in mice"])
		self.assertEqual(body["samples"][0]["date"], "2026-10-12")
		self.assertEqual(body["samples"][0]["link"], "https://feeds.example.org/1")
		self.assertIsNone(body["refused"])
		self.assertIsNone(body["duplicate_of"])
		self.assertIsNone(body["pubmed"])

	def test_the_keyword_filter_uses_the_processor_of_the_given_link_not_the_redirect_target(self):
		from gregory.management.commands.feedreader_articles import Command as RssCommand

		self.http.side_effect = [redirect("https://publisher.example.net/feed.xml"), FakeResponse()]
		with patch.object(RssCommand, "get_feed_processor", autospec=True, side_effect=RssCommand.get_feed_processor) as pick:
			response = self.preview(link="https://www.biorxiv.org/rss/ms.xml", keyword_filter="multiple sclerosis")
		self.assertEqual(response.status_code, 200, response.content)
		pick.assert_called_once()
		self.assertEqual(pick.call_args.args[1], "https://www.biorxiv.org/rss/ms.xml")

	def test_an_rss_preview_without_a_filter_samples_the_first_entries(self):
		body = self.preview().json()
		self.assertEqual([c["key"] for c in body["counts"]], ["entries_in_feed"])
		self.assertEqual(len(body["samples"]), 3)

	def test_a_trials_feed_has_no_keyword_filter(self):
		body = self.preview(source_type="rss_trials").json()
		self.assertEqual([c["key"] for c in body["counts"]], ["entries_in_feed"])

	def test_a_pubmed_preview_has_the_count_the_translation_and_samples(self):
		response = self.preview(self.pubmed_body(excluded_publication_types=["review"]))
		self.assertEqual(response.status_code, 200, response.content)
		body = response.json()
		self.assertEqual(body["counts"], [{"key": "papers_last_30_days", "value": 42, "label": "Papers created in the last 30 days"}])
		self.assertIn("translated:", body["pubmed"]["querytranslation"])
		self.assertIn('NOT ("Review"[pt])', body["pubmed"]["querytranslation"])
		self.assertEqual(body["pubmed"]["warnings"], ["Phrase not found: zzz"])
		self.assertEqual(
			body["samples"],
			[
				{"title": "First paper", "date": "2026-10-15", "link": "https://pubmed.ncbi.nlm.nih.gov/111/"},
				{"title": "Second paper", "date": "2026-10-14", "link": "https://pubmed.ncbi.nlm.nih.gov/222/"},
			],
		)

	def test_a_pubmed_preview_is_one_search(self):
		self.preview(self.pubmed_body())
		self.ncbi.esearch.assert_called_once()
		params = self.ncbi.esearch.call_args.kwargs
		self.assertEqual(params, {"retmax": 5, "sort": "pub_date"})

	def test_a_pubmed_preview_over_the_id_cap_still_has_count_translation_and_samples(self):
		from gregory.utils.ncbi import NcbiResultCapError

		original = self.ncbi.esearch.side_effect

		def esearch(term, **params):
			capped = original(term, **params)
			capped.count = 12000
			raise NcbiResultCapError("capped", result=capped)

		self.ncbi.esearch.side_effect = esearch
		body = self.preview(self.pubmed_body()).json()
		self.assertEqual(body["counts"][0]["value"], 12000)
		self.assertIn("translated:", body["pubmed"]["querytranslation"])
		self.assertEqual(len(body["samples"]), 2)

	def test_pubmed_samples_are_left_out_when_their_titles_cannot_be_fetched(self):
		from gregory.utils.ncbi import NcbiError

		self.ncbi.esummary.side_effect = NcbiError("down")
		body = self.preview(self.pubmed_body()).json()
		self.assertEqual(body["counts"][0]["value"], 42)
		self.assertEqual(body["samples"], [])

	def test_a_ctgov_preview_has_the_total_the_recent_count_and_samples(self):
		response = self.preview(self.ctgov_body())
		self.assertEqual(response.status_code, 200, response.content)
		body = response.json()
		self.assertEqual(
			{c["key"]: c["value"] for c in body["counts"]},
			{"trials_matching": 57, "trials_updated_last_30_days": 9},
		)
		self.assertEqual(
			body["samples"],
			[{"title": "A trial", "date": "2026-10-19", "link": "https://clinicaltrials.gov/study/NCT01234567"}],
		)
		unfiltered, recent = (call.kwargs for call in self.api.search.call_args_list)
		self.assertNotIn("filter_advanced", unfiltered)
		self.assertEqual(unfiltered["query_cond"], "multiple sclerosis")
		start = (timezone.now() - timedelta(days=30)).strftime("%Y-%m-%d")
		self.assertEqual(recent["filter_advanced"], f"AREA[LastUpdatePostDate]RANGE[{start},MAX]")
		self.assertEqual(recent["query_cond"], "multiple sclerosis")

	def test_a_preview_writes_nothing(self):
		for body in (self.body(), self.pubmed_body(), self.ctgov_body()):
			self.assertEqual(self.preview(body).status_code, 200)
		self.assertFalse(Sources.objects.exists())
		self.assertFalse(Sources.history.exists())

	def test_a_preview_of_a_source_create_would_refuse_says_why_with_200(self):
		response = self.preview(self.pubmed_body(pubmed_query="ms AND 2020[dp]"))
		self.assertEqual(response.status_code, 200)
		body = response.json()
		self.assertIn("pubmed_query: Remove the date clause", body["refused"])
		self.assertEqual(body["counts"], [])
		self.ncbi.esearch.assert_not_called()

	def test_a_preview_of_a_private_feed_says_why_with_200(self):
		with patch("gregory.source_checks.socket.getaddrinfo", dns({"feeds.example.org": "10.0.0.1"})):
			body = self.preview().json()
		self.assertIn("private or internal", body["refused"])

	def test_a_preview_of_a_duplicate_names_it(self):
		existing = Sources.objects.create(
			name="Existing", source_for="science paper", method="rss", active=True,
			team=self.subject_a.team, subject=self.subject_a, link="https://feeds.example.org/ms.xml",
		)
		body = self.preview().json()
		self.assertIn("already does this", body["refused"])
		self.assertEqual(
			body["duplicate_of"],
			{"source_id": existing.pk, "name": "Existing", "subject_id": self.subject_a.pk, "subject_name": "Subject A"},
		)
		self.http.assert_not_called()

	def test_a_preview_ignores_name_and_description(self):
		self.assertEqual(self.preview(name="x", description="y").status_code, 200)

	def test_an_unreachable_service_is_503(self):
		self.ncbi.esearch.side_effect = NcbiError("down")
		self.assertEqual(self.preview(self.pubmed_body()).status_code, 503)
		self.api.search.side_effect = ctgov_api(down=True).search.side_effect
		self.assertEqual(self.preview(self.ctgov_body()).status_code, 503)


class CreateTest(SourceFixture):
	def test_an_rss_source_is_live_in_the_subjects_team_with_no_anchor(self):
		response = self.create(description="From the lab", keyword_filter="multiple sclerosis")
		self.assertEqual(response.status_code, 201, response.content)
		body = response.json()
		source = Sources.objects.get(pk=body["source"]["id"])
		self.assertEqual(source.team, self.subject_a.team)
		self.assertEqual(source.subject, self.subject_a)
		self.assertTrue(source.active)
		self.assertFalse(source.ignore_ssl)
		self.assertEqual((source.source_for, source.method), ("science paper", "rss"))
		self.assertEqual(source.name, "RSS | Subject A")
		self.assertEqual(source.description, "From the lab")
		self.assertEqual(source.link, "https://feeds.example.org/ms.xml")
		self.assertEqual(source.keyword_filter, "multiple sclerosis")
		self.assertIsNone(source.last_successful_fetch_at)
		self.assertEqual(body["source"]["source_type"], "rss_papers")
		self.assertEqual(body["source"]["settings"], {"link": "https://feeds.example.org/ms.xml", "keyword_filter": "multiple sclerosis"})
		self.assertEqual(body["first_fetch"], "next pipeline run (every 12 hours), from today on")
		self.assertEqual({c["key"] for c in body["counts"]}, {"entries_in_feed", "entries_passing_keyword_filter"})

	def test_a_trials_feed_is_a_trials_source(self):
		source = Sources.objects.get(pk=self.create(source_type="rss_trials").json()["source"]["id"])
		self.assertEqual((source.source_for, source.method), ("trials", "rss"))
		self.assertEqual(source.name, "RSS | Subject A")

	def test_a_pubmed_source_starts_from_now_and_keeps_its_excluded_types(self):
		before = timezone.now()
		response = self.create(self.pubmed_body(excluded_publication_types=["Review", "journal article"], name="MS remyelination"))
		self.assertEqual(response.status_code, 201, response.content)
		source = Sources.objects.get(pk=response.json()["source"]["id"])
		self.assertEqual((source.source_for, source.method), ("science paper", "pubmed_api"))
		self.assertEqual(source.name, "MS remyelination")
		self.assertEqual(source.pubmed_query, '"multiple sclerosis"[MeSH Terms] AND remyelination')
		self.assertEqual(
			sorted(source.excluded_publication_types.values_list("name", flat=True)), ["Journal Article", "Review"]
		)
		self.assertTrue(before <= source.last_successful_fetch_at <= timezone.now())
		self.assertEqual(response.json()["source"]["settings"]["excluded_publication_types"], ["Journal Article", "Review"])
		self.assertEqual(response.json()["pubmed"]["warnings"], ["Phrase not found: zzz"])

	def test_a_ctgov_source_starts_from_now(self):
		before = timezone.now()
		response = self.create(self.ctgov_body(ctgov_search_intervention="ocrelizumab"))
		self.assertEqual(response.status_code, 201, response.content)
		source = Sources.objects.get(pk=response.json()["source"]["id"])
		self.assertEqual((source.source_for, source.method), ("trials", "ctgov_api"))
		self.assertEqual(source.name, "CTGov | Subject A")
		self.assertEqual(source.ctgov_search_intervention, "ocrelizumab")
		self.assertIsNone(source.ctgov_search_term)
		self.assertTrue(before <= source.last_successful_fetch_at <= timezone.now())

	def test_settings_are_trimmed(self):
		source = Sources.objects.get(pk=self.create(self.pubmed_body(pubmed_query="  remyelination  ")).json()["source"]["id"])
		self.assertEqual(source.pubmed_query, "remyelination")

	def test_history_names_the_editor_and_the_mcp_door(self):
		source_id = self.create(self.pubmed_body(excluded_publication_types=["Review"])).json()["source"]["id"]
		rows = Sources.history.filter(source_id=source_id)
		self.assertEqual(rows.get(history_type="+").editor_user, self.cara)
		self.assertEqual({(r.editor_user_id, r.via) for r in rows}, {(self.cara.pk, "mcp")})

	def test_no_volume_limit_for_a_big_pubmed_search(self):
		self.ncbi.esearch.side_effect = lambda term, **p: ESearchResult(count=5000, ids=["111"], querytranslation="t")
		response = self.create(self.pubmed_body())
		self.assertEqual(response.status_code, 201, response.content)
		self.assertEqual(response.json()["counts"][0]["value"], 5000)

	def test_no_volume_limit_for_a_big_ctgov_search(self):
		self.api.search.side_effect = ctgov_api(total=10000, recent=4000).search.side_effect
		response = self.create(self.ctgov_body())
		self.assertEqual(response.status_code, 201, response.content)
		self.assertEqual({c["key"]: c["value"] for c in response.json()["counts"]}, {"trials_matching": 10000, "trials_updated_last_30_days": 4000})

	def test_create_never_trusts_that_a_preview_ran(self):
		# No preview first, and the checks still run: a private feed is refused.
		with patch("gregory.source_checks.socket.getaddrinfo", dns({"feeds.example.org": "127.0.0.1"})):
			self.assertEqual(self.create().status_code, 400)
		self.assertFalse(Sources.objects.exists())

	def test_an_unreachable_pubmed_is_503_and_creates_nothing(self):
		self.ncbi.esearch.side_effect = NcbiError("down")
		response = self.create(self.pubmed_body())
		self.assertEqual(response.status_code, 503)
		self.assertIn("PubMed could not be reached", response.json()["detail"])
		self.assertFalse(Sources.objects.exists())
		self.assertFalse(Sources.history.exists())

	def test_an_unreachable_clinicaltrials_gov_is_503_and_creates_nothing(self):
		self.api.search.side_effect = ctgov_api(down=True).search.side_effect
		response = self.create(self.ctgov_body())
		self.assertEqual(response.status_code, 503)
		self.assertIn("ClinicalTrials.gov could not be reached", response.json()["detail"])
		self.assertFalse(Sources.objects.exists())

	def test_server_errors_from_clinicaltrials_gov_are_503_not_a_refusal(self):
		self.api.search.side_effect = ctgov_api(status=500).search.side_effect
		self.assertEqual(self.create(self.ctgov_body()).status_code, 503)


class NoBackfillTest(SourceFixture):
	"""A source added here is incremental from its first run (spec decision 8)."""

	def test_a_new_pubmed_source_asks_for_the_last_two_days_not_thirty(self):
		source_id = self.create(self.pubmed_body()).json()["source"]["id"]
		anchor = Sources.objects.get(pk=source_id).last_successful_fetch_at
		client = MagicMock()
		client.esearch.return_value = ESearchResult(count=0, ids=[], querytranslation="t")
		with patch("gregory.management.commands.feedreader_articles_pubmed.NcbiClient") as klass:
			klass.for_organization.return_value = client
			klass.for_site.return_value = client
			call_command("feedreader_articles_pubmed", "--source-id", str(source_id), stdout=StringIO())
		term = client.esearch.call_args_list[0].args[0]
		span = term.rsplit(" AND ", 1)[1].replace("[crdt]", "")
		start = tuple(int(part) for part in span.split(":")[0].split("/"))
		expected = (anchor - timedelta(days=2)).date()
		self.assertEqual(start, (expected.year, expected.month, expected.day))

	def test_a_new_ctgov_source_asks_for_the_last_two_days_not_everything(self):
		source = Sources.objects.get(pk=self.create(self.ctgov_body()).json()["source"]["id"])
		command = CtgovCommand()
		params = command._build_search_params(source)
		start = (source.last_successful_fetch_at - timedelta(days=2)).strftime("%Y-%m-%d")
		self.assertEqual(params["filter_advanced"], f"AREA[LastUpdatePostDate]RANGE[{start},MAX]")

	def test_an_admin_added_ctgov_source_still_backfills(self):
		source = Sources.objects.create(
			name="CTGov | Admin", method="ctgov_api", source_for="trials", active=True, ctgov_search_condition="ms"
		)
		self.assertIsNone(source.last_successful_fetch_at)
		self.assertNotIn("filter_advanced", CtgovCommand()._build_search_params(source))

	def test_an_rss_source_has_no_anchor(self):
		source = Sources.objects.get(pk=self.create().json()["source"]["id"])
		self.assertIsNone(source.last_successful_fetch_at)


class DuplicateTest(SourceFixture):
	def existing(self, team=None, **fields):
		defaults = dict(
			name="Existing", source_for="science paper", method="rss", active=True,
			team=team or self.subject_a.team, subject=self.subject_a,
		)
		defaults.update(fields)
		return Sources.objects.create(**defaults)

	def assertDuplicate(self, response, existing):
		self.assertEqual(response.status_code, 409, response.content)
		body = response.json()
		self.assertEqual(body["duplicate_of"]["source_id"], existing.pk)
		self.assertEqual(body["duplicate_of"]["name"], existing.name)
		self.assertEqual(body["duplicate_of"]["subject_name"], existing.subject.subject_name)

	def test_rss_ignores_case_and_a_trailing_slash(self):
		existing = self.existing(link="HTTPS://Feeds.Example.org/MS.xml/")
		self.assertDuplicate(self.create(link="https://feeds.example.org/ms.xml"), existing)
		self.assertEqual(Sources.objects.count(), 1)

	def test_rss_compares_where_the_feed_redirects_to_as_well(self):
		existing = self.existing(link="http://feeds.example.org/real.xml")
		self.http.side_effect = [redirect("/real.xml"), FakeResponse()]
		self.assertDuplicate(self.create(link="http://feeds.example.org/old.xml"), existing)

	def test_pubmed_ignores_case_and_whitespace(self):
		existing = self.existing(
			method="pubmed_api", pubmed_query='"Multiple Sclerosis"[MeSH Terms]   AND\nremyelination'
		)
		self.assertDuplicate(self.create(self.pubmed_body(pubmed_query='"multiple sclerosis"[mesh terms] AND remyelination')), existing)

	def test_ctgov_compares_all_three_fields(self):
		existing = self.existing(
			source_for="trials", method="ctgov_api",
			ctgov_search_condition="Multiple  Sclerosis", ctgov_search_intervention="ocrelizumab",
		)
		self.assertDuplicate(
			self.create(self.ctgov_body(ctgov_search_intervention=" Ocrelizumab ")), existing
		)
		# A different intervention is a different search.
		self.assertEqual(self.create(self.ctgov_body(ctgov_search_intervention="rituximab")).status_code, 201)
		self.assertEqual(self.create(self.ctgov_body()).status_code, 201)

	def test_the_same_feed_as_another_type_is_not_a_duplicate(self):
		self.existing(link="https://feeds.example.org/ms.xml")
		self.assertEqual(self.create(source_type="rss_trials").status_code, 201)

	def test_an_inactive_source_is_not_a_duplicate(self):
		self.existing(link="https://feeds.example.org/ms.xml", active=False)
		self.assertEqual(self.create().status_code, 201)

	def test_a_duplicate_in_another_subject_of_the_same_team_is_named(self):
		other = Subject.objects.create(team=self.subject_a.team, subject_name="Elsewhere", subject_slug="elsewhere")
		existing = self.existing(link="https://feeds.example.org/ms.xml", subject=other)
		self.assertDuplicate(self.create(), existing)

	def test_another_teams_identical_source_is_not_a_duplicate_and_is_not_named(self):
		foreign = self.existing(team=self.subject_other.team, subject=self.subject_other, link="https://feeds.example.org/ms.xml", name="Secret feed")
		preview = self.preview()
		self.assertIsNone(preview.json()["duplicate_of"])
		self.assertNotIn("Secret feed", preview.content.decode())
		response = self.create()
		self.assertEqual(response.status_code, 201, response.content)
		self.assertNotIn("Secret feed", response.content.decode())
		self.assertNotEqual(response.json()["source"]["id"], foreign.pk)

	def test_a_duplicate_is_refused_before_anything_external_is_called(self):
		self.existing(method="pubmed_api", pubmed_query='"multiple sclerosis"[MeSH Terms] AND remyelination')
		self.assertEqual(self.create(self.pubmed_body()).status_code, 409)
		self.ncbi.esearch.assert_not_called()


class ListTest(SourceFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		team = cls.subject_a.team
		cls.rss = Sources.objects.create(
			name="RSS | A", source_for="science paper", method="rss", active=True, team=team,
			subject=cls.subject_a, link="https://a.example.org/feed", keyword_filter="ms",
		)
		cls.pubmed = Sources.objects.create(
			name="PubMed | A", source_for="science paper", method="pubmed_api", active=False, team=team,
			subject=cls.subject_a, pubmed_query="remyelination",
			last_successful_fetch_at=timezone.now(),
		)
		cls.pubmed.excluded_publication_types.add(PublicationType.objects.get(name="Review"))
		cls.ctis = Sources.objects.create(
			name="CTIS | A", source_for="trials", method="ctis_api", active=True, team=team,
			subject=cls.subject_a, ctis_search_criteria={"medicalCondition": "MS"},
		)
		cls.news = Sources.objects.create(
			name="News | A", source_for="news article", method="rss", active=True, team=team,
			subject=cls.subject_a, link="https://news.example.org/feed",
		)
		cls.out_of_scope = Sources.objects.create(
			name="RSS | B", source_for="science paper", method="rss", active=True,
			team=cls.subject_b.team, subject=cls.subject_b, link="https://b.example.org/feed",
		)
		cls.no_subject = Sources.objects.create(name="Orphan", source_for="science paper", method="rss", team=team)

	def listing(self, query=""):
		response = self.as_cara("get", f"/editor/sources/{query}")
		self.assertEqual(response.status_code, 200, response.content)
		return response.json()

	def test_lists_every_source_whose_subject_is_in_scope(self):
		body = self.listing()
		names = [s["name"] for s in body["results"]]
		self.assertEqual(names, ["CTIS | A", "News | A", "PubMed | A", "RSS | A"])
		self.assertEqual(body["count"], 4)

	def test_each_source_has_its_type_subject_team_and_settings(self):
		by_name = {s["name"]: s for s in self.listing()["results"]}
		rss = by_name["RSS | A"]
		self.assertEqual(
			(rss["id"], rss["source_type"], rss["subject_id"], rss["subject_name"], rss["team_id"], rss["active"]),
			(self.rss.pk, "rss_papers", self.subject_a.pk, "Subject A", self.subject_a.team_id, True),
		)
		self.assertEqual(rss["settings"], {"link": "https://a.example.org/feed", "keyword_filter": "ms"})
		pubmed = by_name["PubMed | A"]
		self.assertEqual(pubmed["settings"], {"pubmed_query": "remyelination", "excluded_publication_types": ["Review"]})
		self.assertIsNotNone(pubmed["last_successful_fetch_at"])
		self.assertEqual(by_name["CTIS | A"]["source_type"], "ctis")
		self.assertEqual(by_name["CTIS | A"]["settings"], {})
		self.assertEqual(by_name["News | A"]["source_type"], "legacy")

	def test_filters(self):
		self.assertEqual([s["name"] for s in self.listing("?active=false")["results"]], ["PubMed | A"])
		self.assertEqual(len(self.listing("?active=true")["results"]), 3)
		self.assertEqual([s["name"] for s in self.listing("?source_type=ctis")["results"]], ["CTIS | A"])
		self.assertEqual([s["name"] for s in self.listing("?source_type=rss_papers")["results"]], ["RSS | A"])
		self.assertEqual(len(self.listing(f"?subject_id={self.subject_a.pk}")["results"]), 4)
		self.assertEqual(self.listing(f"?subject_id={self.subject_b.pk}")["results"], [])

	def test_bad_filters_are_400(self):
		for query in ("?active=maybe", "?source_type=nope", "?subject_id=x"):
			with self.subTest(query=query):
				self.assertEqual(self.as_cara("get", f"/editor/sources/{query}").status_code, 400)

	def test_listing_is_not_throttled(self):
		with override_settings(MCP_SOURCE_RATE_LIMITS={"creates_per_day": 1, "previews_per_hour": 1}):
			self.assertEqual([self.as_cara("get", "/editor/sources/").status_code for _ in range(4)], [200] * 4)


class ThrottleTest(SourceFixture):
	@override_settings(MCP_SOURCE_RATE_LIMITS={"creates_per_day": 30, "previews_per_hour": 30})
	def test_the_31st_create_in_a_day_is_429(self):
		statuses = [
			self.create(self.ctgov_body(ctgov_search_term=f"term {i}")).status_code for i in range(31)
		]
		self.assertEqual(statuses, [201] * 30 + [429])
		self.assertEqual(Sources.objects.count(), 30)

	@override_settings(MCP_SOURCE_RATE_LIMITS={"creates_per_day": 30, "previews_per_hour": 30})
	def test_the_31st_preview_in_an_hour_is_429_with_retry_after(self):
		statuses = [self.preview().status_code for _ in range(31)]
		self.assertEqual(statuses, [200] * 30 + [429])
		self.assertIn("Retry-After", self.preview())

	def test_the_defaults_are_30_and_30(self):
		from django.conf import settings

		self.assertEqual(settings.MCP_SOURCE_RATE_LIMITS, {"creates_per_day": 30, "previews_per_hour": 30})

	@override_settings(MCP_SOURCE_RATE_LIMITS={"creates_per_day": 1, "previews_per_hour": 1})
	def test_creates_and_previews_have_separate_budgets(self):
		self.assertEqual(self.create().status_code, 201)
		self.assertEqual(self.preview(link="https://feeds.example.org/other.xml").status_code, 200)
		self.assertEqual(self.create(link="https://feeds.example.org/more.xml").status_code, 429)
		self.assertEqual(self.preview().status_code, 429)

	@override_settings(
		MCP_SOURCE_RATE_LIMITS={"creates_per_day": 2, "previews_per_hour": 2},
		MCP_EDITOR_RATE_LIMITS={"hour": 1, "day": 1},
	)
	def test_the_general_editor_write_limits_do_not_apply(self):
		# One general write per hour, yet two creates and two previews go through.
		self.assertEqual(self.create().status_code, 201)
		self.assertEqual(self.create(link="https://feeds.example.org/more.xml").status_code, 201)
		self.assertEqual(self.preview().status_code, 200)
		self.assertEqual(self.preview().status_code, 200)

	@override_settings(
		MCP_SOURCE_RATE_LIMITS={"creates_per_day": 30, "previews_per_hour": 30},
		MCP_EDITOR_RATE_LIMITS={"hour": 1, "day": 1},
	)
	def test_source_requests_do_not_spend_the_general_write_budget(self):
		self.create()
		self.preview()
		art = self.as_cara("patch", f"/editor/articles/{self.art_a.pk}/editorial/", {"takeaways": "x"})
		self.assertEqual(art.status_code, 200)

	@override_settings(MCP_SOURCE_RATE_LIMITS={"creates_per_day": 1, "previews_per_hour": 1})
	def test_the_limit_is_per_person_and_site(self):
		self.create()
		self.assertEqual(self.create(link="https://feeds.example.org/more.xml").status_code, 429)
		dana = make_user("dana", self.org)
		grant(dana, self.site_a, can_manage_sources=True)
		self.assertEqual(
			self.send("post", "/editor/sources/", self.body(link="https://feeds.example.org/dana.xml"), user=dana).status_code, 201
		)
		# Same person, another site she may add sources on.
		grant(self.cara, self.site_b, can_manage_sources=True)
		other_site = self.as_cara("post", "/editor/sources/", self.body(subject_id=self.subject_b.pk, link="https://feeds.example.org/b.xml"), site=self.site_b)
		self.assertEqual(other_site.status_code, 201, other_site.content)

	@override_settings(MCP_SOURCE_RATE_LIMITS={"creates_per_day": 1, "previews_per_hour": 1})
	def test_a_refused_create_still_counts(self):
		self.assertEqual(self.create(link="").status_code, 400)
		self.assertEqual(self.create().status_code, 429)


def _add_scope(subject, site):
	CustomSetting.objects.get(site=site).scope_subjects.add(subject)


class SourceTeamTest(SourceFixture):
	def test_the_team_comes_from_the_subject_not_the_caller(self):
		team_two = Team.objects.create(organization=self.org, name="Second", slug="second-team")
		subject_two = Subject.objects.create(team=team_two, subject_name="Second subject", subject_slug="second-subject")
		_add_scope(subject_two, self.site_a)
		source = Sources.objects.get(pk=self.create(subject_id=subject_two.pk).json()["source"]["id"])
		self.assertEqual(source.team, team_two)
		self.assertEqual(source.name, "RSS | Second subject")
