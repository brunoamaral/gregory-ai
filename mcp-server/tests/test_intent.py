from __future__ import annotations

import inspect
import logging

import pytest

import gregory_mcp.intent as intent
import gregory_mcp.query_shape as query_shape
from gregory_mcp.logging_config import INTENT_LOGGER_NAME, IntentJsonFormatter
from gregory_mcp.tools.articles import search_articles
from gregory_mcp.tools.authors import search_authors
from gregory_mcp.tools.trials import search_trials


def test_intent_is_offered_on_search_articles_and_search_trials_only():
	assert "intent" in inspect.signature(search_articles).parameters
	assert "intent" in inspect.signature(search_trials).parameters
	# An intent phrased around a named person would reintroduce exactly what
	# search_authors' own carve-out removes — see MCP-TELEMETRY-PLAN.md.
	assert "intent" not in inspect.signature(search_authors).parameters


class _CollectingHandler(logging.Handler):
	"""Attached directly to the `gregory_mcp.intent` logger rather than via
	caplog: that logger is configured with propagate=False in production
	(see logging_config.configure_logging), so a handler that only listens
	on the root logger — which is how caplog's own capture works — would
	never see these records regardless of propagate, since a handler
	attached straight to a logger fires independently of that setting.
	"""

	def __init__(self):
		super().__init__()
		self.records: list[logging.LogRecord] = []

	def emit(self, record):
		self.records.append(record)


@pytest.fixture
def intent_records():
	handler = _CollectingHandler()
	logger = logging.getLogger(INTENT_LOGGER_NAME)
	logger.addHandler(handler)
	logger.setLevel(logging.INFO)
	yield handler.records
	logger.removeHandler(handler)


def _patch_categories(monkeypatch, categories):
	async def fake_get_all_pages_cached(path, params=None):
		return categories

	monkeypatch.setattr(query_shape, "get_all_pages_cached", fake_get_all_pages_cached)


# --- scan_for_pii -----------------------------------------------------------


async def test_email_is_flagged():
	assert "email" in await intent.scan_for_pii("contact me at jane@example.com")


async def test_long_digit_run_is_flagged():
	assert "long_digit_run" in await intent.scan_for_pii("call 5551234567 please")


async def test_short_digit_run_is_not_flagged():
	flags = await intent.scan_for_pii("trial phase 2026")
	assert "long_digit_run" not in flags


@pytest.mark.parametrize(
	"phrase",
	[
		"treatment options for my son with encephalitis",
		"our daughter was recently diagnosed",
		"what helps my wife's condition",
		"I was diagnosed with a rare disease last year",
		"I'm being treated for a chronic illness",
	],
)
async def test_first_person_medical_is_flagged(phrase):
	assert "first_person_medical" in await intent.scan_for_pii(phrase)


async def test_third_person_medical_language_is_not_flagged():
	flags = await intent.scan_for_pii("treatment options for paediatric encephalitis")
	assert "first_person_medical" not in flags


@pytest.mark.parametrize("phrase", ["a 7-year-old with encephalitis", "outcomes in patients aged 7"])
async def test_age_specificity_is_flagged(phrase):
	assert "age_specificity" in await intent.scan_for_pii(phrase)


async def test_no_flags_for_a_benign_query(monkeypatch):
	_patch_categories(monkeypatch, [])
	flags = await intent.scan_for_pii("recent treatment options for multiple sclerosis")
	assert flags == []


async def test_specificity_co_occurrence_needs_both_a_specificity_token_and_a_category_match(monkeypatch):
	categories = [{"category_slug": "encephalitis", "category_name": "Encephalitis", "category_terms": []}]
	_patch_categories(monkeypatch, categories)

	# age + matching category term -> co-occurrence flag
	flags = await intent.scan_for_pii("a 7-year-old with encephalitis")
	assert "specificity_co_occurrence" in flags

	# age alone, no category match -> no co-occurrence flag (age_specificity still fires)
	flags2 = await intent.scan_for_pii("a 7-year-old patient")
	assert "specificity_co_occurrence" not in flags2
	assert "age_specificity" in flags2

	# category match alone, no age/geography token -> no co-occurrence flag
	flags3 = await intent.scan_for_pii("treatment for encephalitis")
	assert "specificity_co_occurrence" not in flags3


async def test_geography_token_with_category_match_triggers_co_occurrence(monkeypatch):
	categories = [{"category_slug": "encephalitis", "category_name": "Encephalitis", "category_terms": []}]
	_patch_categories(monkeypatch, categories)

	flags = await intent.scan_for_pii("encephalitis cases at the hospital")
	assert "specificity_co_occurrence" in flags


async def test_co_occurrence_check_degrades_gracefully_when_taxonomy_fetch_fails(monkeypatch):
	async def failing_fetch(path, params=None):
		raise RuntimeError("upstream unreachable")

	monkeypatch.setattr(query_shape, "get_all_pages_cached", failing_fetch)

	flags = await intent.scan_for_pii("a 7-year-old with encephalitis")
	assert "specificity_co_occurrence" not in flags
	assert "age_specificity" in flags  # the other flags still fire


# --- record -------------------------------------------------------------


async def test_record_emits_exactly_one_event_with_the_expected_fields(intent_records, monkeypatch):
	_patch_categories(monkeypatch, [])

	await intent.record("search_articles", "treatment options for multiple sclerosis")

	assert len(intent_records) == 1
	record = intent_records[0]
	assert record.tool == "search_articles"
	assert record.intent == "treatment options for multiple sclerosis"
	assert record.pii_flags == []


async def test_record_includes_flags_when_present(intent_records, monkeypatch):
	_patch_categories(monkeypatch, [])

	await intent.record("search_trials", "contact jane@example.com about this")

	assert intent_records[0].pii_flags == ["email"]


async def test_record_is_a_no_op_for_blank_text(intent_records):
	await intent.record("search_articles", "")
	await intent.record("search_articles", "   ")
	assert intent_records == []


async def test_record_never_raises_when_the_pii_scan_fails(intent_records, monkeypatch):
	async def failing_scan(text):
		raise RuntimeError("scan exploded")

	monkeypatch.setattr(intent, "scan_for_pii", failing_scan)

	await intent.record("search_articles", "some intent text")  # must not raise

	# A warning about the failed scan lands on the same logger too — filter
	# down to the actual mcp_intent event.
	events = [r for r in intent_records if r.getMessage() == "mcp_intent"]
	assert len(events) == 1
	assert events[0].pii_flags == []


async def test_record_never_includes_telemetry_fields():
	"""No shared correlation key with telemetry.py's mcp_request events —
	assert at the call site that record() only ever passes the intent
	stream's own three fields, never a request id or anything else.
	"""
	import logging as _logging

	captured = {}
	original_makeRecord = _logging.Logger.makeRecord

	def spy(self, *args, **kwargs):
		rec = original_makeRecord(self, *args, **kwargs)
		if self.name == "gregory_mcp.intent":
			captured.update(rec.__dict__)
		return rec

	_logging.Logger.makeRecord = spy
	try:
		await intent.record("search_articles", "benign query about a rare disease")
	finally:
		_logging.Logger.makeRecord = original_makeRecord

	standard_attrs = {
		"name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
		"exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
		"relativeCreated", "thread", "threadName", "processName", "process", "message", "taskName",
	}
	extra_fields = {k for k in captured if k not in standard_attrs}
	assert extra_fields == {"tool", "intent", "pii_flags"}


# --- IntentJsonFormatter -------------------------------------------------


def test_intent_formatter_only_emits_its_own_tiny_allowlist():
	formatter = IntentJsonFormatter()
	logger = logging.getLogger("test.intent.formatter")
	record = logger.makeRecord(
		logger.name,
		logging.INFO,
		__file__,
		0,
		"mcp_intent",
		(),
		None,
		extra={
			"tool": "search_articles",
			"intent": "some intent text",
			"pii_flags": ["email"],
			# Fields that belong to the *other* stream — must never leak through here.
			"duration_ms": 12.3,
			"upstream_ms": 5.0,
			"params_used": ["search"],
		},
	)
	import json

	payload = json.loads(formatter.format(record))
	assert payload["tool"] == "search_articles"
	assert payload["intent"] == "some intent text"
	assert payload["pii_flags"] == ["email"]
	assert "duration_ms" not in payload
	assert "upstream_ms" not in payload
	assert "params_used" not in payload
