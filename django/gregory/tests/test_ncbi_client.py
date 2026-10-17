"""
Tests for gregory.utils.ncbi.NcbiClient and get_ncbi_credentials.

Never calls NCBI: requests is mocked at the NcbiClient boundary.
"""

import logging
import os
import tempfile
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase

from organizations.models import Organization

from gregory.models import OrganizationCredentials
from gregory.utils import ncbi
from gregory.utils.ncbi import (
	NcbiClient,
	NcbiError,
	NcbiQueryError,
	NcbiResultCapError,
)
from subscriptions.management.commands.utils.get_credentials import (
	get_ncbi_credentials,
)


def _response(payload=None, content=b"", status=200):
	response = MagicMock()
	response.status_code = status
	response.json.return_value = payload
	response.content = content
	if status >= 400:
		response.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
	return response


def _esearch_payload(**overrides):
	result = {
		"count": "2",
		"idlist": ["1", "2"],
		"querytranslation": "ms[All Fields]",
		"errorlist": {},
		"warninglist": {},
	}
	result.update(overrides)
	return {"esearchresult": result}


class ThrottleTests(SimpleTestCase):
	def setUp(self):
		self.tmp = tempfile.TemporaryDirectory()
		patcher = patch("gregory.utils.ncbi.tempfile.gettempdir", return_value=self.tmp.name)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.addCleanup(self.tmp.cleanup)
		self.now = 100.0
		self.sleeps = []

		def fake_sleep(seconds):
			self.sleeps.append(seconds)
			self.now += seconds

		for target, value in (("_clock", lambda: self.now), ("_sleep", fake_sleep)):
			p = patch(f"gregory.utils.ncbi.{target}", value)
			p.start()
			self.addCleanup(p.stop)

	def test_first_call_does_not_wait(self):
		ncbi._throttle("bucket", 0.5)
		self.assertEqual(self.sleeps, [])

	def test_second_call_waits_out_the_interval(self):
		ncbi._throttle("bucket", 0.5)
		self.now += 0.2
		ncbi._throttle("bucket", 0.5)
		self.assertEqual(len(self.sleeps), 1)
		self.assertAlmostEqual(self.sleeps[0], 0.3)

	def test_no_wait_after_the_interval_has_passed(self):
		ncbi._throttle("bucket", 0.5)
		self.now += 1
		ncbi._throttle("bucket", 0.5)
		self.assertEqual(self.sleeps, [])

	def test_buckets_are_independent(self):
		ncbi._throttle("a", 0.5)
		ncbi._throttle("b", 0.5)
		self.assertEqual(self.sleeps, [])

	def test_bucket_file_name_never_contains_the_key(self):
		client = NcbiClient(api_key="SECRETKEY123")
		client._throttle()
		names = os.listdir(self.tmp.name)
		self.assertEqual(len(names), 1)
		self.assertNotIn("SECRETKEY123", names[0])
		self.assertTrue(names[0].startswith("ncbi-"))

	def test_keyless_and_keyed_use_different_buckets(self):
		NcbiClient()._throttle()
		NcbiClient(api_key="k")._throttle()
		self.assertEqual(len(os.listdir(self.tmp.name)), 2)


@patch("gregory.utils.ncbi._throttle")
class ClientRequestTests(SimpleTestCase):
	def test_every_request_is_identified(self, _throttle):
		client = NcbiClient(api_key="K", email="a@b.org", tool="mytool")
		with patch("gregory.utils.ncbi.requests.get", return_value=_response({})) as get:
			client.esummary("pubmed", ["1"])
		params = get.call_args.kwargs["params"]
		self.assertEqual(params["tool"], "mytool")
		self.assertEqual(params["email"], "a@b.org")
		self.assertEqual(params["api_key"], "K")

	def test_keyless_client_sends_no_key_or_email(self, _throttle):
		with patch("gregory.utils.ncbi.requests.get", return_value=_response({})) as get:
			NcbiClient().esummary("pubmed", ["1"])
		params = get.call_args.kwargs["params"]
		self.assertEqual(params["tool"], "gregory-ai")
		self.assertNotIn("api_key", params)
		self.assertNotIn("email", params)

	def test_keyed_client_throttles_per_key_bucket(self, throttle):
		with patch("gregory.utils.ncbi.requests.get", return_value=_response({})):
			NcbiClient(api_key="K").esummary("pubmed", ["1"])
			NcbiClient().esummary("pubmed", ["1"])
		(keyed_bucket, keyed_interval), _ = throttle.call_args_list[0]
		(keyless_bucket, keyless_interval), _ = throttle.call_args_list[1]
		self.assertNotEqual(keyed_bucket, keyless_bucket)
		self.assertEqual(keyless_bucket, "ncbi-keyless")
		self.assertAlmostEqual(keyed_interval, 0.1)
		self.assertAlmostEqual(keyless_interval, 1 / 3)

	def test_efetch_uses_get_up_to_200_ids_and_post_above(self, _throttle):
		client = NcbiClient()
		with patch("gregory.utils.ncbi.requests.get", return_value=_response(content=b"<x/>")) as get, patch(
			"gregory.utils.ncbi.requests.post"
		) as post:
			xml = client.efetch_pubmed([str(i) for i in range(200)])
			self.assertEqual(xml, b"<x/>")
			get.assert_called_once()
			post.assert_not_called()
		with patch("gregory.utils.ncbi.requests.get") as get, patch(
			"gregory.utils.ncbi.requests.post", return_value=_response(content=b"<y/>")
		) as post:
			xml = client.efetch_pubmed([str(i) for i in range(201)])
			self.assertEqual(xml, b"<y/>")
			get.assert_not_called()
			post.assert_called_once()
			self.assertEqual(post.call_args.kwargs["data"]["db"], "pubmed")

	def test_long_term_goes_as_post(self, _throttle):
		with patch("gregory.utils.ncbi.requests.post", return_value=_response(_esearch_payload())) as post:
			NcbiClient().esearch("a" * 2000)
		post.assert_called_once()

	def test_retries_429_then_succeeds(self, _throttle):
		responses = [_response(status=429), _response(status=503), _response({"ok": 1})]
		with patch("gregory.utils.ncbi.requests.get", side_effect=responses) as get, patch(
			"gregory.utils.ncbi._sleep"
		) as sleep:
			data = NcbiClient().esummary("pubmed", ["1"])
		self.assertEqual(data, {"ok": 1})
		self.assertEqual(get.call_count, 3)
		self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

	def test_retries_the_rate_limit_body(self, _throttle):
		limited = _response({"error": "API rate limit exceeded", "count": "11"})
		with patch(
			"gregory.utils.ncbi.requests.get", side_effect=[limited, _response({"ok": 1})]
		) as get, patch("gregory.utils.ncbi._sleep"):
			data = NcbiClient().esummary("pubmed", ["1"])
		self.assertEqual(data, {"ok": 1})
		self.assertEqual(get.call_count, 2)

	def test_gives_up_after_three_retries(self, _throttle):
		with patch(
			"gregory.utils.ncbi.requests.get", return_value=_response(status=429)
		) as get, patch("gregory.utils.ncbi._sleep") as sleep:
			with self.assertRaises(NcbiError):
				NcbiClient().esummary("pubmed", ["1"])
		self.assertEqual(get.call_count, 4)
		self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2, 4])

	def test_client_error_is_not_retried(self, _throttle):
		with patch(
			"gregory.utils.ncbi.requests.get", return_value=_response(status=400)
		) as get:
			with self.assertRaises(NcbiError):
				NcbiClient().esummary("pubmed", ["1"])
		self.assertEqual(get.call_count, 1)

	def test_errors_never_leak_the_api_key(self, _throttle):
		url_with_key = "https://eutils.ncbi.nlm.nih.gov/x?api_key=SECRETKEY123"
		with patch(
			"gregory.utils.ncbi.requests.get",
			side_effect=requests.ConnectionError(f"failed for {url_with_key}"),
		):
			with self.assertRaises(NcbiError) as ctx:
				NcbiClient(api_key="SECRETKEY123").esummary("pubmed", ["1"])
		self.assertNotIn("SECRETKEY123", str(ctx.exception))
		self.assertIsNone(ctx.exception.__cause__)
		self.assertTrue(ctx.exception.__suppress_context__)

	def test_logs_never_contain_the_api_key(self, _throttle):
		with patch(
			"gregory.utils.ncbi.requests.get", side_effect=[_response(status=429), _response({})]
		), patch("gregory.utils.ncbi._sleep"):
			with self.assertLogs("gregory.utils.ncbi", level=logging.DEBUG) as logs:
				NcbiClient(api_key="SECRETKEY123").esummary("pubmed", ["1"])
		self.assertNotIn("SECRETKEY123", "\n".join(logs.output))


@patch("gregory.utils.ncbi._throttle")
class ESearchTests(SimpleTestCase):
	def _search(self, payload, **params):
		with patch("gregory.utils.ncbi.requests.get", return_value=_response(payload)):
			return NcbiClient().esearch("ms", **params)

	def test_parses_a_result(self, _throttle):
		result = self._search(_esearch_payload(webenv="W", querykey="1"))
		self.assertEqual(result.count, 2)
		self.assertEqual(result.ids, ["1", "2"])
		self.assertEqual(result.querytranslation, "ms[All Fields]")
		self.assertEqual((result.webenv, result.querykey), ("W", "1"))

	def test_error_field_raises_query_error(self, _throttle):
		with self.assertRaises(NcbiQueryError) as ctx:
			self._search({"esearchresult": {"ERROR": "Invalid db name"}})
		self.assertIn("Invalid db name", str(ctx.exception))

	def test_fields_not_found_raises_query_error(self, _throttle):
		payload = _esearch_payload(errorlist={"fieldsnotfound": ["bogus"], "phrasesnotfound": []})
		with self.assertRaises(NcbiQueryError) as ctx:
			self._search(payload)
		self.assertIn("bogus", str(ctx.exception))

	def test_empty_errorlist_lists_are_fine(self, _throttle):
		payload = _esearch_payload(errorlist={"fieldsnotfound": [], "phrasesnotfound": []})
		self.assertEqual(self._search(payload).count, 2)

	def test_warnings_are_kept(self, _throttle):
		payload = _esearch_payload(warninglist={"outputmessages": ["Quoted phrase not found"]})
		self.assertIn("Quoted phrase not found", self._search(payload).warnings[0])

	def test_capped_result_raises_when_ids_are_wanted(self, _throttle):
		with self.assertRaises(NcbiResultCapError):
			self._search(_esearch_payload(count="9999"), retmax=9999)

	def test_capped_count_is_fine_for_a_count_only_search(self, _throttle):
		result = self._search(_esearch_payload(count="12000", idlist=[]), rettype="count")
		self.assertEqual(result.count, 12000)

	def test_just_under_the_cap_is_fine(self, _throttle):
		self.assertEqual(self._search(_esearch_payload(count="9998"), retmax=9999).count, 9998)

	def test_unexpected_shape_raises(self, _throttle):
		with self.assertRaises(NcbiError):
			self._search({"nope": 1})


class GetNcbiCredentialsTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org", slug="org")

	def test_no_credentials_row(self):
		self.assertEqual(get_ncbi_credentials(self.org), (None, None, None))

	def test_partial_row_returns_what_is_set(self):
		OrganizationCredentials.objects.create(organization=self.org, ncbi_email="a@b.org")
		self.assertEqual(get_ncbi_credentials(self.org), (None, "a@b.org", "gregory-ai"))

	def test_full_row(self):
		OrganizationCredentials.objects.create(
			organization=self.org, ncbi_api_key="K", ncbi_email="a@b.org", ncbi_tool="t"
		)
		self.assertEqual(get_ncbi_credentials(self.org), ("K", "a@b.org", "t"))

	def test_for_organization_builds_a_client(self):
		OrganizationCredentials.objects.create(
			organization=self.org, ncbi_api_key="K", ncbi_email="a@b.org"
		)
		client = NcbiClient.for_organization(self.org)
		self.assertEqual((client.api_key, client.email, client.tool), ("K", "a@b.org", "gregory-ai"))

	def test_for_organization_without_credentials_is_keyless(self):
		with patch("gregory.utils.ncbi._site_admin_email", return_value="site@b.org"):
			client = NcbiClient.for_organization(self.org)
		self.assertIsNone(client.api_key)
		self.assertEqual(client.email, "site@b.org")
