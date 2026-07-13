"""
Tests for gregory.utils.doi_utils: URL-based DOI extraction and the PubMed
PMID -> DOI resolver.

Run:
	docker exec gregory python manage.py test gregory.tests.test_doi_utils
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from unittest.mock import MagicMock, patch

from django.test import TestCase

from gregory.utils.doi_utils import (
	extract_doi_from_url,
	extract_pmid_from_url,
	resolve_doi_from_pmid,
	resolve_doi_from_pubmed_url,
)


class ExtractDoiFromUrlTests(TestCase):
	"""Positive cases: URLs that should yield a DOI."""

	def test_springer_article_url(self):
		url = "https://link.springer.com/article/10.1007/s10484-026-09800-x"
		self.assertEqual(
			extract_doi_from_url(url), "10.1007/s10484-026-09800-x"
		)

	def test_doi_org_resolver(self):
		url = "https://doi.org/10.3109/some.identifier"
		self.assertEqual(
			extract_doi_from_url(url), "10.3109/some.identifier"
		)

	def test_dx_doi_org_resolver(self):
		url = "https://dx.doi.org/10.3109/another.identifier"
		self.assertEqual(
			extract_doi_from_url(url), "10.3109/another.identifier"
		)

	def test_doi_org_resolver_with_www(self):
		url = "https://www.doi.org/10.1000/xyz"
		self.assertEqual(extract_doi_from_url(url), "10.1000/xyz")

	def test_pnas_doi_abs_with_query_string(self):
		url = "https://www.pnas.org/doi/abs/10.1073/pnas.2422928122?af=R"
		self.assertEqual(
			extract_doi_from_url(url), "10.1073/pnas.2422928122"
		)

	def test_doi_with_trailing_slash(self):
		url = "https://doi.org/10.1007/s00332-025-10234-8/"
		self.assertEqual(
			extract_doi_from_url(url), "10.1007/s00332-025-10234-8"
		)

	def test_doi_with_trailing_fragment(self):
		url = "https://link.springer.com/article/10.1007/s10484-026-09800-x#section"
		self.assertEqual(
			extract_doi_from_url(url), "10.1007/s10484-026-09800-x"
		)

	def test_doi_is_lowercased(self):
		url = "https://doi.org/10.1007/S10484-026-09800-X"
		self.assertEqual(
			extract_doi_from_url(url), "10.1007/s10484-026-09800-x"
		)

	def test_doi_embedded_with_other_query_params(self):
		url = "https://example.com/content/10.1177/21582440251334940?ai=2b4&mi=x"
		self.assertEqual(
			extract_doi_from_url(url), "10.1177/21582440251334940"
		)


class ExtractDoiFromUrlNegativeTests(TestCase):
	"""Negative cases: repository URLs with no DOI must return None."""

	def test_hdl_handle_net(self):
		url = "https://hdl.handle.net/10520/EJC-1234abcd"
		self.assertIsNone(extract_doi_from_url(url))

	def test_arxiv(self):
		url = "https://arxiv.org/abs/2501.12345"
		self.assertIsNone(extract_doi_from_url(url))

	def test_hal_science(self):
		url = "https://hal.science/hal-04123456"
		self.assertIsNone(extract_doi_from_url(url))

	def test_escholarship(self):
		url = "https://escholarship.org/uc/item/1a2b3c4d"
		self.assertIsNone(extract_doi_from_url(url))

	def test_empty_url(self):
		self.assertIsNone(extract_doi_from_url(""))

	def test_none_url(self):
		self.assertIsNone(extract_doi_from_url(None))

	def test_plain_url_no_doi(self):
		self.assertIsNone(extract_doi_from_url("https://example.com/some/page"))


class ExtractPmidFromUrlTests(TestCase):
	def test_extracts_pmid(self):
		self.assertEqual(
			extract_pmid_from_url("https://pubmed.ncbi.nlm.nih.gov/38812345/"),
			"38812345",
		)

	def test_no_trailing_slash(self):
		self.assertEqual(
			extract_pmid_from_url("https://pubmed.ncbi.nlm.nih.gov/38812345"),
			"38812345",
		)

	def test_no_pmid_in_non_pubmed_url(self):
		self.assertIsNone(extract_pmid_from_url("https://example.com/article/1"))

	def test_empty_url(self):
		self.assertIsNone(extract_pmid_from_url(""))

	def test_none_url(self):
		self.assertIsNone(extract_pmid_from_url(None))


class ResolveDoiFromPmidTests(TestCase):
	"""All network calls mocked -- never hit the real NCBI API in tests."""

	def _mock_response(self, payload, status_ok=True):
		response = MagicMock()
		response.json.return_value = payload
		if not status_ok:
			response.raise_for_status.side_effect = Exception("HTTP error")
		return response

	def test_resolves_doi_from_esummary_payload(self):
		payload = {
			"result": {
				"38812345": {
					"articleids": [
						{"idtype": "pubmed", "value": "38812345"},
						{"idtype": "doi", "value": "10.1234/example.doi"},
					]
				}
			}
		}
		with patch(
			"gregory.utils.doi_utils.requests.get",
			return_value=self._mock_response(payload),
		) as mock_get:
			doi = resolve_doi_from_pmid("38812345")
		self.assertEqual(doi, "10.1234/example.doi")
		mock_get.assert_called_once()
		_, kwargs = mock_get.call_args
		self.assertEqual(kwargs["params"]["id"], "38812345")
		self.assertEqual(kwargs["params"]["db"], "pubmed")

	def test_no_doi_in_articleids(self):
		payload = {
			"result": {
				"38812345": {
					"articleids": [{"idtype": "pubmed", "value": "38812345"}]
				}
			}
		}
		with patch(
			"gregory.utils.doi_utils.requests.get",
			return_value=self._mock_response(payload),
		):
			doi = resolve_doi_from_pmid("38812345")
		self.assertIsNone(doi)

	def test_network_failure_returns_none(self):
		with patch(
			"gregory.utils.doi_utils.requests.get",
			side_effect=ConnectionError("network down"),
		):
			doi = resolve_doi_from_pmid("38812345")
		self.assertIsNone(doi)

	def test_http_error_returns_none(self):
		with patch(
			"gregory.utils.doi_utils.requests.get",
			return_value=self._mock_response({}, status_ok=False),
		):
			doi = resolve_doi_from_pmid("38812345")
		self.assertIsNone(doi)

	def test_malformed_payload_returns_none(self):
		with patch(
			"gregory.utils.doi_utils.requests.get",
			return_value=self._mock_response({"unexpected": "shape"}),
		):
			doi = resolve_doi_from_pmid("38812345")
		self.assertIsNone(doi)

	def test_empty_pmid_short_circuits_without_network_call(self):
		with patch("gregory.utils.doi_utils.requests.get") as mock_get:
			doi = resolve_doi_from_pmid("")
		self.assertIsNone(doi)
		mock_get.assert_not_called()


class ResolveDoiFromPubmedUrlTests(TestCase):
	def test_full_pipeline(self):
		payload = {
			"result": {
				"38812345": {
					"articleids": [{"idtype": "doi", "value": "10.1234/example.doi"}]
				}
			}
		}
		response = MagicMock()
		response.json.return_value = payload
		with patch(
			"gregory.utils.doi_utils.requests.get", return_value=response
		):
			doi = resolve_doi_from_pubmed_url(
				"https://pubmed.ncbi.nlm.nih.gov/38812345/"
			)
		self.assertEqual(doi, "10.1234/example.doi")

	def test_non_pubmed_url_short_circuits_without_network_call(self):
		with patch("gregory.utils.doi_utils.requests.get") as mock_get:
			doi = resolve_doi_from_pubmed_url("https://example.com/article/1")
		self.assertIsNone(doi)
		mock_get.assert_not_called()
