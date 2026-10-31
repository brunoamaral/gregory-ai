"""Tests for gregory.utils.dailymed. DailyMed is never called: the labels and lists are the
real responses in fixtures/fda/dailymed/ (fetched on 2026-10-30; each label is trimmed to
its root, setId, versionNumber and indications section), and the download is mocked at
the HTTP helper."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from gregory.utils import dailymed
from gregory.utils.dailymed import (
	DailyMedError,
	DailyMedLabel,
	label_page_url,
	parse_indications,
	parse_label_list,
	parse_published,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fda" / "dailymed"
OCREVUS_SETID = "9da42362-3bb5-4b83-b4bb-b59fd4e55f0d"
GET = "gregory.utils.dailymed.get_with_retries"


def load(name):
	return json.loads((FIXTURES / name).read_text())


def response(status=200, payload=None, content=b""):
	mock = MagicMock()
	mock.status_code = status
	mock.json.return_value = payload
	mock.content = content
	return mock


class ParsePublishedTests(SimpleTestCase):
	def test_a_dailymed_date_becomes_an_iso_date(self):
		self.assertEqual(parse_published("Jan 08, 2025"), "2025-01-08")
		self.assertEqual(parse_published(" May 20, 2026 "), "2026-05-20")

	def test_an_unreadable_date_is_kept_as_sent(self):
		with self.assertLogs("gregory.utils.dailymed", "WARNING"):
			self.assertEqual(parse_published("sometime"), "sometime")

	def test_a_blank_date_is_blank(self):
		self.assertEqual(parse_published(None), "")
		self.assertEqual(parse_published(""), "")


class ParseLabelListTests(SimpleTestCase):
	def test_a_real_list(self):
		labels = parse_label_list(load("list-ocrevus.json"))
		self.assertEqual(
			labels,
			[
				DailyMedLabel(
					setid=OCREVUS_SETID,
					version="24",
					title="OCREVUS (OCRELIZUMAB) INJECTION [GENENTECH, INC.]",
					published="2026-05-20",
				)
			],
		)

	def test_two_labels_keep_dailymeds_order(self):
		labels = parse_label_list(load("list-viagra.json"))
		self.assertEqual([label.setid[:8] for label in labels], ["0b0be196", "98a9897b"])

	def test_an_empty_list(self):
		self.assertEqual(parse_label_list(load("list-empty.json")), [])

	def test_an_entry_without_a_setid_is_dropped(self):
		payload = {"data": [{"title": "x"}, "junk", {"setid": "a", "title": " T  t ", "spl_version": 3}]}
		labels = parse_label_list(payload)
		self.assertEqual(len(labels), 1)
		self.assertEqual((labels[0].setid, labels[0].version, labels[0].title), ("a", "3", "T t"))

	def test_a_response_of_the_wrong_shape_raises(self):
		for payload in ({}, {"data": None}, [], None):
			with self.subTest(payload=payload), self.assertRaises(DailyMedError):
				parse_label_list(payload)


class ParseIndicationsTests(SimpleTestCase):
	def test_a_real_label(self):
		text = parse_indications((FIXTURES / f"{OCREVUS_SETID}.xml").read_bytes())
		self.assertTrue(text.startswith("1 INDICATIONS AND USAGE OCREVUS is indicated for the treatment of:"))
		self.assertIn("Relapsing forms of multiple sclerosis (MS)", text)
		# Whitespace is collapsed.
		self.assertNotIn("  ", text)
		self.assertNotIn("\n", text)

	def test_text_is_accepted_as_well_as_bytes(self):
		raw = (FIXTURES / f"{OCREVUS_SETID}.xml").read_bytes()
		self.assertEqual(parse_indications(raw.decode("utf-8").split("?>", 1)[1]), parse_indications(raw))

	def test_a_label_without_the_section_gives_an_empty_string(self):
		xml = '<document xmlns="urn:hl7-org:v3"><section><code code="34084-4"/><text>Adverse</text></section></document>'
		self.assertEqual(parse_indications(xml), "")

	def test_only_the_indications_code_counts(self):
		xml = (
			'<document xmlns="urn:hl7-org:v3">'
			'<section><code code="34084-4"/><text>Adverse reactions</text></section>'
			'<section><code code="34067-9"/><text>For <content>X</content> only.</text></section>'
			"</document>"
		)
		self.assertEqual(parse_indications(xml), "For X only.")

	def test_malformed_xml_raises(self):
		with self.assertRaises(DailyMedError):
			parse_indications("<document><unclosed>")

	def test_an_entity_expansion_bomb_is_refused(self):
		bomb = (
			'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
			'<document xmlns="urn:hl7-org:v3">&b;</document>'
		)
		with self.assertRaises(DailyMedError):
			parse_indications(bomb)


class DownloadTests(SimpleTestCase):
	def setUp(self):
		sleep = patch("gregory.utils.dailymed.time.sleep")
		sleep.start()
		self.addCleanup(sleep.stop)

	def test_list_labels_sends_the_application_and_the_user_agent(self):
		with patch(GET, return_value=response(payload=load("list-ocrevus.json"))) as get:
			labels = dailymed.list_labels("BLA761053")
		self.assertEqual([label.setid for label in labels], [OCREVUS_SETID])
		url, headers, _ = get.call_args[0]
		self.assertIn("application_number=BLA761053", url)
		self.assertIn("pagesize=100", url)
		self.assertEqual(headers["User-Agent"], "gregory-ai (+https://gregory-ai.com)")

	def test_list_labels_reads_every_page(self):
		first = {"data": [{"setid": "a"}], "metadata": {"total_pages": 2, "current_page": 1}}
		second = {"data": [{"setid": "b"}], "metadata": {"total_pages": 2, "current_page": 2}}
		with patch(GET, side_effect=[response(payload=first), response(payload=second)]) as get:
			labels = dailymed.list_labels("NDA000001")
		self.assertEqual([label.setid for label in labels], ["a", "b"])
		self.assertEqual(get.call_count, 2)
		self.assertIn("page=2", get.call_args[0][0])

	def test_an_application_with_no_label_gives_an_empty_list(self):
		with patch(GET, return_value=response(payload=load("list-empty.json"))):
			self.assertEqual(dailymed.list_labels("NDA999999"), [])

	def test_a_404_is_an_error_not_an_empty_list(self):
		# An error page must never read as "no labels".
		with patch(GET, return_value=response(404)), self.assertRaises(DailyMedError):
			dailymed.list_labels("NDA999999")

	def test_an_error_status_raises(self):
		with patch(GET, return_value=response(429)), self.assertRaises(DailyMedError):
			dailymed.list_labels("NDA000001")

	def test_a_body_that_is_not_json_raises(self):
		bad = response()
		bad.json.side_effect = ValueError("no json")
		with patch(GET, return_value=bad), self.assertRaises(DailyMedError):
			dailymed.list_labels("NDA000001")

	def test_a_connection_failure_raises_dailymed_error(self):
		with patch(GET, side_effect=requests.RequestException("down")), self.assertRaises(DailyMedError):
			dailymed.list_labels("NDA000001")
		with patch(GET, side_effect=requests.RequestException("down")), self.assertRaises(DailyMedError):
			dailymed.fetch_indications("abc")

	def test_fetch_indications_downloads_and_parses_the_label(self):
		body = (FIXTURES / f"{OCREVUS_SETID}.xml").read_bytes()
		with patch(GET, return_value=response(content=body)) as get:
			text = dailymed.fetch_indications(OCREVUS_SETID)
		self.assertIn("multiple sclerosis", text)
		self.assertEqual(get.call_args[0][0], f"{dailymed.BASE_URL}/services/v2/spls/{OCREVUS_SETID}.xml")

	def test_fetch_indications_raises_on_an_error_status(self):
		with patch(GET, return_value=response(500)), self.assertRaises(DailyMedError):
			dailymed.fetch_indications("abc")

	def test_every_request_pauses_first(self):
		with patch(GET, return_value=response(payload=load("list-empty.json"))):
			with patch("gregory.utils.dailymed.time.sleep") as sleep:
				dailymed.list_labels("NDA999999")
		sleep.assert_called_once_with(dailymed.REQUEST_PAUSE)


class LabelPageUrlTests(SimpleTestCase):
	def test_the_page_of_a_label(self):
		self.assertEqual(
			label_page_url("abc"), "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=abc"
		)
