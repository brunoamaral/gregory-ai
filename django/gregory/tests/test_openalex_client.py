"""
Tests for gregory.utils.openalex.

Never calls OpenAlex: requests is mocked at the client boundary. The fixture
fixtures/openalex/works-sample.json is trimmed from a real OpenAlex response (the three
records are real; email addresses in affiliation strings are replaced).
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from gregory.utils import openalex
from gregory.utils.openalex import (
	OpenAlexBudgetError,
	OpenAlexClient,
	OpenAlexError,
	bare_doi,
	parse_work,
	usable_doi,
)

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "openalex" / "works-sample.json").read_text())
DOI_A = "10.1186/s12974-023-02861-3"
DOI_B = "10.1016/j.crmeth.2023.100533"
DOI_C = "10.1186/s44280-023-00019-7"


def _response(payload=None, status=200):
	response = MagicMock()
	response.status_code = status
	response.json.return_value = payload
	return response


class ClientTests(SimpleTestCase):
	def setUp(self):
		patcher = patch.object(openalex, "_sleep")
		self.sleep = patcher.start()
		self.addCleanup(patcher.stop)

	def test_url_is_quoted_but_keeps_the_filter_syntax(self):
		client = OpenAlexClient(api_key="")
		url = client._url([DOI_A, "10.1002/(sici)1097-4687(199802)239:2<1::aid-jmor1>3.0.co;2-w"])
		self.assertIn("filter=doi:", url)
		self.assertIn("10.1186/s12974-023-02861-3|10.1002/", url)
		self.assertNotIn("<", url)
		self.assertIn("&per-page=100", url)
		self.assertIn("&select=id,doi,publication_year,authorships", url)
		self.assertNotIn("api_key", url)
		self.assertNotIn("mailto", url)

	def test_api_key_is_sent_when_set(self):
		self.assertIn("&api_key=secret-key", OpenAlexClient(api_key="secret-key")._url([DOI_A]))

	def test_setting_is_the_default_key(self):
		with self.settings(OPENALEX_API_KEY="from-settings"):
			self.assertEqual(OpenAlexClient().api_key, "from-settings")
		with self.settings(OPENALEX_API_KEY=""):
			self.assertIsNone(OpenAlexClient().api_key)

	@patch("gregory.utils.openalex.requests.get")
	def test_results_are_keyed_by_bare_lower_case_doi(self, get):
		get.return_value = _response(SAMPLE)
		found = OpenAlexClient(api_key="").works_by_doi([DOI_A, DOI_B, DOI_C.upper()])
		self.assertEqual(set(found), {DOI_A, DOI_B, DOI_C})
		self.assertEqual(found[DOI_A]["id"], "https://openalex.org/W4385549237")
		self.assertEqual(get.call_args.kwargs["headers"], {"User-Agent": "gregory-ai"})

	@patch("gregory.utils.openalex.requests.get")
	def test_a_result_that_was_not_asked_for_is_dropped(self, get):
		get.return_value = _response(SAMPLE)
		found = OpenAlexClient(api_key="").works_by_doi([DOI_A])
		self.assertEqual(set(found), {DOI_A})

	@patch("gregory.utils.openalex.requests.get")
	def test_an_unknown_doi_is_simply_absent(self, get):
		get.return_value = _response({"meta": {"cost_usd": 0.0001}, "results": []})
		self.assertEqual(OpenAlexClient(api_key="").works_by_doi([DOI_A]), {})

	def test_more_than_100_dois_is_refused(self):
		with self.assertRaises(ValueError):
			OpenAlexClient(api_key="").works_by_doi([f"10.1/x{i}" for i in range(101)])

	@patch("gregory.utils.openalex.requests.get")
	def test_5xx_is_retried_with_backoff(self, get):
		get.side_effect = [_response(status=503), _response(status=500), _response(SAMPLE)]
		found = OpenAlexClient(api_key="").works_by_doi([DOI_A])
		self.assertEqual(get.call_count, 3)
		self.assertEqual(set(found), {DOI_A})
		slept = [c.args[0] for c in self.sleep.call_args_list]
		self.assertIn(1, slept)
		self.assertIn(2, slept)

	@patch("gregory.utils.openalex.requests.get")
	def test_connection_errors_are_retried_three_times_then_raise(self, get):
		get.side_effect = requests.ConnectionError("boom")
		with self.assertRaises(OpenAlexError) as ctx:
			OpenAlexClient(api_key="").works_by_doi([DOI_A])
		self.assertNotIsInstance(ctx.exception, OpenAlexBudgetError)
		self.assertEqual(get.call_count, 4)  # the first try and three retries

	@patch("gregory.utils.openalex.requests.get")
	def test_429_raises_budget_error_without_retry(self, get):
		get.return_value = _response(status=429)
		with self.assertRaises(OpenAlexBudgetError):
			OpenAlexClient(api_key="").works_by_doi([DOI_A])
		self.assertEqual(get.call_count, 1)

	@patch("gregory.utils.openalex.requests.get")
	def test_any_other_4xx_raises_budget_error_without_retry(self, get):
		get.return_value = _response(status=403)
		with self.assertRaises(OpenAlexBudgetError):
			OpenAlexClient(api_key="").works_by_doi([DOI_A])
		self.assertEqual(get.call_count, 1)

	@patch("gregory.utils.openalex.requests.get")
	def test_cost_is_totalled(self, get):
		get.return_value = _response(SAMPLE)
		client = OpenAlexClient(api_key="")
		client.works_by_doi([DOI_A])
		client.works_by_doi([DOI_B])
		self.assertAlmostEqual(client.total_cost_usd, 0.0002)

	@patch("gregory.utils.openalex.requests.get")
	def test_the_key_and_the_notice_stay_out_of_the_logs(self, get):
		get.side_effect = requests.ConnectionError(
			"https://api.openalex.org/works?filter=doi:x&api_key=secret-key failed"
		)
		with self.assertLogs("gregory.utils.openalex", level=logging.DEBUG) as logs:
			with self.assertRaises(OpenAlexError) as ctx:
				OpenAlexClient(api_key="secret-key").works_by_doi([DOI_A])
		self.assertNotIn("secret-key", "\n".join(logs.output))
		self.assertNotIn("secret-key", str(ctx.exception))

		get.side_effect = None
		get.return_value = _response(
			{"meta": {"notice": "NOTICE-TEXT-FROM-OPENALEX", "cost_usd": 0.0001}, "results": SAMPLE["results"]}
		)
		with self.assertNoLogs("gregory.utils.openalex"):
			OpenAlexClient(api_key="secret-key").works_by_doi([DOI_A])

	@patch("gregory.utils.openalex.time.monotonic", return_value=100.0)
	@patch("gregory.utils.openalex.requests.get")
	def test_calls_are_paused(self, get, _clock):
		get.return_value = _response(SAMPLE)
		client = OpenAlexClient(api_key="")
		client.works_by_doi([DOI_A])
		self.sleep.assert_not_called()  # nothing to wait for before the first call
		client.works_by_doi([DOI_B])
		self.sleep.assert_called_once_with(openalex.REQUEST_PAUSE_SECONDS)


class ParsingTests(SimpleTestCase):
	def test_usable_doi(self):
		self.assertTrue(usable_doi(DOI_A))
		for bad in ("10.1/a|b", "10.1/a,b", "10.1/a b", "10.1/a\tb", "", None):
			self.assertFalse(usable_doi(bad), bad)

	def test_bare_doi(self):
		self.assertEqual(bare_doi("https://doi.org/10.1186/ABC"), "10.1186/abc")
		self.assertEqual(bare_doi(" 10.1186/ABC "), "10.1186/abc")

	def test_parse_work_gives_bare_ids(self):
		work = parse_work(SAMPLE["results"][0])
		self.assertEqual(work.openalex_id, "W4385549237")
		self.assertEqual(work.doi, DOI_A)
		self.assertEqual(work.year, 2023)
		first = work.authorships[0]
		self.assertEqual(first.position, "first")
		self.assertEqual(first.index, 0)
		self.assertTrue(first.is_corresponding)
		self.assertEqual(first.openalex_author_id, "A5086435829")
		self.assertEqual(first.name, "Alize M Proust")
		self.assertEqual(first.orcid, "0000-0003-2809-2835")
		inst = first.institutions[0]
		self.assertEqual(inst.openalex_id, "I2801081054")
		self.assertEqual(inst.ror, "04tnbqb63")
		self.assertEqual(inst.country_code, "GB")
		self.assertEqual(inst.type, "facility")
		self.assertIn("Francis Crick Institute", first.raw_affiliation)
		self.assertEqual(work.authorships[1].orcid, "")
		self.assertEqual([a.index for a in work.authorships], list(range(6)))
		self.assertEqual(work.authorships[-1].position, "last")

	def test_parse_work_tolerates_missing_parts(self):
		work = parse_work(
			{"id": "https://openalex.org/W1", "doi": None, "authorships": [{"author": {}, "institutions": []}]}
		)
		self.assertEqual(work.doi, "")
		self.assertEqual(work.authorships[0].openalex_author_id, "")
		self.assertEqual(work.authorships[0].position, "middle")
