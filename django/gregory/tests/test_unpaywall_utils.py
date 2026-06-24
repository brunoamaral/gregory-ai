import unittest
from unittest.mock import patch

from django.test import TestCase

from gregory.unpaywall import unpaywall_utils


VALID_RESPONSE = {
	"is_oa": True,
	"best_oa_location": {"url": "https://example.org/paper.pdf"},
}

PATCH_GET_JSON = "gregory.unpaywall.unpaywall_utils.Unpywall.get_json"


class UnpaywallUtilsTests(TestCase):
	@patch(PATCH_GET_JSON)
	def test_getDataByDOI_returns_empty_when_not_found(self, mock_get):
		mock_get.return_value = None  # Unpaywall returns None for unknown DOIs
		result = unpaywall_utils.getDataByDOI("10.1186/not-found", "user@test.org")
		self.assertEqual(result, {})

	@patch(PATCH_GET_JSON)
	def test_getDataByDOI_returns_empty_on_exception(self, mock_get):
		mock_get.side_effect = Exception("connection error")
		result = unpaywall_utils.getDataByDOI("10.1186/error-doi", "user@test.org")
		self.assertEqual(result, {})

	@patch(PATCH_GET_JSON)
	def test_getDataByDOI_returns_data_on_success(self, mock_get):
		mock_get.return_value = VALID_RESPONSE
		result = unpaywall_utils.getDataByDOI("10.1186/valid-doi", "user@test.org")
		self.assertEqual(result["is_oa"], True)
		self.assertEqual(result["best_oa_location"]["url"], "https://example.org/paper.pdf")

	@patch(PATCH_GET_JSON)
	def test_checkIfDOIIsOpenAccess_true(self, mock_get):
		mock_get.return_value = {"is_oa": True}
		self.assertTrue(
			unpaywall_utils.checkIfDOIIsOpenAccess("10.1186/open", "user@test.org")
		)

	@patch(PATCH_GET_JSON)
	def test_checkIfDOIIsOpenAccess_false(self, mock_get):
		mock_get.return_value = {"is_oa": False}
		self.assertFalse(
			unpaywall_utils.checkIfDOIIsOpenAccess("10.1186/closed", "user@test.org")
		)

	@patch(PATCH_GET_JSON)
	def test_checkIfDOIIsOpenAccess_no_data(self, mock_get):
		mock_get.return_value = None
		self.assertFalse(
			unpaywall_utils.checkIfDOIIsOpenAccess("10.1186/missing", "user@test.org")
		)

	@patch(PATCH_GET_JSON)
	def test_getOpenAccessURLForDOI_returns_url(self, mock_get):
		mock_get.return_value = VALID_RESPONSE
		url = unpaywall_utils.getOpenAccessURLForDOI("10.1186/valid-doi", "user@test.org")
		self.assertEqual(url, "https://example.org/paper.pdf")

	@patch(PATCH_GET_JSON)
	def test_getOpenAccessURLForDOI_returns_none_when_no_location(self, mock_get):
		mock_get.return_value = {"is_oa": False, "best_oa_location": None}
		url = unpaywall_utils.getOpenAccessURLForDOI("10.1186/closed-doi", "user@test.org")
		self.assertIsNone(url)

	def test_getDataByDOI_raises_on_empty_args(self):
		with self.assertRaises(ValueError):
			unpaywall_utils.getDataByDOI("", "user@test.org")
		with self.assertRaises(ValueError):
			unpaywall_utils.getDataByDOI("10.1186/x", "")


if __name__ == "__main__":
	unittest.main()
