"""Tests for gregory.utils.ema: reading EMA's export rows, and downloading it.

The rows in fixtures/ema/medicines-base.json are real rows cut from EMA's export of
2026-10-25. Nothing here calls EMA: the download is mocked at ``requests.get``.
"""

import json
from datetime import date
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import requests

from gregory.utils import ema

FIXTURES = Path(__file__).parent / "fixtures" / "ema"


def fixture_rows():
	with open(FIXTURES / "medicines-base.json") as handle:
		return {row["name_of_medicine"]: row for row in json.load(handle)["data"]}


ROWS = fixture_rows()


class ParseDateTests(TestCase):
	def test_valid(self):
		self.assertEqual(ema.parse_date("08/01/2018"), date(2018, 1, 8))

	def test_empty_is_none_without_a_warning(self):
		with self.assertNoLogs("gregory.utils.ema", level="WARNING"):
			self.assertIsNone(ema.parse_date(""))
			self.assertIsNone(ema.parse_date(None))

	def test_invalid_is_none_with_a_warning(self):
		with self.assertLogs("gregory.utils.ema", level="WARNING"):
			self.assertIsNone(ema.parse_date("2018-01-08"))
		with self.assertLogs("gregory.utils.ema", level="WARNING"):
			self.assertIsNone(ema.parse_date("31/02/2018"))


class ParseFlagTests(TestCase):
	def test_flags(self):
		self.assertTrue(ema.parse_flag("Yes"))
		self.assertFalse(ema.parse_flag("No"))
		self.assertFalse(ema.parse_flag(""))
		self.assertFalse(ema.parse_flag(None))


class CleanTextTests(TestCase):
	def test_kesimpta_indication_loses_its_nbsp(self):
		raw = ROWS["Kesimpta"]["therapeutic_indication"]
		self.assertIn("&nbsp;", raw)
		cleaned = ema.clean_text(raw)
		self.assertNotIn("&nbsp;", cleaned)
		self.assertNotIn("\xa0", cleaned)
		self.assertIn("(see section 5.1)", cleaned)

	def test_tags_are_stripped_and_paragraphs_do_not_run_together(self):
		self.assertEqual(ema.clean_text("<p>First.</p><p>Second <b>bold</b>.</p>"), "First. Second bold.")

	def test_escaped_tags_are_stripped_too(self):
		self.assertEqual(ema.clean_text("&lt;p&gt;Escaped&lt;/p&gt;"), "Escaped")

	def test_a_bare_less_than_sign_survives(self):
		self.assertEqual(ema.clean_text("patients aged &lt; 18 years"), "patients aged < 18 years")

	def test_whitespace_is_collapsed(self):
		self.assertEqual(ema.clean_text("  one \n\n two\t three  "), "one two three")

	def test_empty(self):
		self.assertEqual(ema.clean_text(""), "")
		self.assertEqual(ema.clean_text(None), "")


class SplitSubstancesTests(TestCase):
	def test_combination_product_splits_on_semicolon(self):
		row = ROWS["Synjardy"]
		self.assertEqual(
			ema.split_substances(
				row["international_non_proprietary_name_common_name"], row["active_substance"]
			),
			["empagliflozin", "metformin"],
		)

	def test_empty_inn_falls_back_to_the_active_substance(self):
		row = ROWS["Hydrocortisone Aguettant"]
		self.assertEqual(row["international_non_proprietary_name_common_name"], "")
		self.assertEqual(
			ema.split_substances("", row["active_substance"]), ["hydrocortisone"]
		)

	def test_lowercased_collapsed_and_deduplicated(self):
		self.assertEqual(
			ema.split_substances("Pixantrone  Dimaleate; pixantrone dimaleate ;", ""),
			["pixantrone dimaleate"],
		)

	def test_nothing_gives_an_empty_list(self):
		self.assertEqual(ema.split_substances("", ""), [])
		self.assertEqual(ema.split_substances(None, None), [])

	def test_a_substance_longer_than_the_column_is_cut(self):
		(substance,) = ema.split_substances("x" * 400, "")
		self.assertEqual(len(substance), 255)


class ParseRowTests(TestCase):
	def test_ocrevus(self):
		parsed = ema.parse_row(ROWS["Ocrevus"])
		self.assertEqual(parsed.ema_product_number, "EMEA/H/C/004043")
		self.assertEqual(parsed.name, "Ocrevus")
		self.assertEqual(parsed.substances, ["ocrelizumab"])
		self.assertFalse(parsed.is_combination)
		self.assertEqual(parsed.status, "Authorised")
		self.assertEqual(parsed.opinion_status, "")
		self.assertEqual(parsed.therapeutic_areas, ["Multiple Sclerosis"])
		self.assertEqual(parsed.atc_code, "L04AG08")
		self.assertEqual(parsed.opinion_date, date(2017, 11, 9))
		self.assertEqual(parsed.authorisation_date, date(2018, 1, 8))
		self.assertEqual(parsed.latest_ec_decision_date, date(2026, 10, 15))
		self.assertEqual(parsed.revision_number, 28)
		self.assertEqual(parsed.url, "https://www.ema.europa.eu/en/medicines/human/EPAR/ocrevus")
		self.assertFalse(parsed.orphan_medicine)
		self.assertEqual(parsed.bad_dates, [])
		self.assertIs(parsed.raw, ROWS["Ocrevus"])

	def test_combination_product(self):
		parsed = ema.parse_row(ROWS["Synjardy"])
		self.assertTrue(parsed.is_combination)

	def test_therapeutic_areas_split_on_semicolon(self):
		parsed = ema.parse_row(ROWS["Fovlussea"])
		self.assertEqual(
			parsed.therapeutic_areas,
			["Wet Macular Degeneration", "Macular Edema", "Diabetes Complications", "Choroidal Neovascularization"],
		)

	def test_a_bad_date_becomes_none_and_is_listed(self):
		row = dict(ROWS["Ocrevus"], marketing_authorisation_date="soon")
		with self.assertLogs("gregory.utils.ema", level="WARNING"):
			parsed = ema.parse_row(row)
		self.assertIsNone(parsed.authorisation_date)
		self.assertEqual(parsed.bad_dates, ["marketing_authorisation_date"])

	def test_an_odd_row_does_not_raise(self):
		parsed = ema.parse_row({"ema_product_number": "EMEA/H/C/1", "revision_number": "n/a"})
		self.assertEqual(parsed.name, "")
		self.assertIsNone(parsed.revision_number)
		self.assertEqual(parsed.substances, [])

	def test_a_new_status_is_kept_as_given(self):
		parsed = ema.parse_row(dict(ROWS["Ocrevus"], medicine_status="Some new wording"))
		self.assertEqual(parsed.status, "Some new wording")

	def test_model_values_exclude_the_bookkeeping_field(self):
		values = ema.parse_row(ROWS["Ocrevus"]).model_values()
		self.assertNotIn("bad_dates", values)
		self.assertIn("raw", values)

	def test_veterinary_rows_are_not_human(self):
		self.assertFalse(ema.is_human(ROWS["Stemyca"]))
		self.assertTrue(ema.is_human(ROWS["Ocrevus"]))


def response(status, body=b"", headers=None):
	mock = MagicMock()
	mock.status_code = status
	mock.content = body
	mock.headers = headers or {}
	return mock


@patch("gregory.utils.ema.time.sleep")
class DownloadExportTests(TestCase):
	@patch("gregory.utils.ema.requests.get")
	def test_ok_returns_body_and_last_modified(self, get, sleep):
		get.return_value = response(200, b"{}", {"Last-Modified": "Sun, 25 Oct 2026 06:00:00 GMT"})
		status, body, last_modified = ema.download_export()
		self.assertEqual((status, body, last_modified), (200, b"{}", "Sun, 25 Oct 2026 06:00:00 GMT"))
		headers = get.call_args.kwargs["headers"]
		self.assertNotIn("If-Modified-Since", headers)
		self.assertIn("gregory-ai", headers["User-Agent"])
		self.assertEqual(get.call_args.kwargs["timeout"], 60)

	@patch("gregory.utils.ema.requests.get")
	def test_sends_if_modified_since_and_passes_a_304_through(self, get, sleep):
		get.return_value = response(304)
		stamp = "Sun, 25 Oct 2026 06:00:00 GMT"
		self.assertEqual(ema.download_export(stamp), (304, b"", stamp))
		self.assertEqual(get.call_args.kwargs["headers"]["If-Modified-Since"], stamp)

	@patch("gregory.utils.ema.requests.get")
	def test_retries_a_server_error_twice_then_succeeds(self, get, sleep):
		get.side_effect = [response(503), response(502), response(200, b"ok")]
		self.assertEqual(ema.download_export()[:2], (200, b"ok"))
		self.assertEqual(get.call_count, 3)

	@patch("gregory.utils.ema.requests.get")
	def test_retries_a_connection_error(self, get, sleep):
		get.side_effect = [requests.ConnectionError("down"), response(200, b"ok")]
		self.assertEqual(ema.download_export()[:2], (200, b"ok"))

	@patch("gregory.utils.ema.requests.get")
	def test_gives_up_after_the_retries(self, get, sleep):
		get.return_value = response(500)
		with self.assertRaises(requests.RequestException):
			ema.download_export()
		self.assertEqual(get.call_count, 3)

	@patch("gregory.utils.ema.requests.get")
	def test_a_client_error_is_returned_not_retried(self, get, sleep):
		get.return_value = response(404)
		self.assertEqual(ema.download_export()[0], 404)
		self.assertEqual(get.call_count, 1)
