from __future__ import annotations

import asyncio
import logging

import httpx2
import pytest
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import CLIENT_INFO_META_KEY, INTERNAL_ERROR, CallToolResult, TextContent

from gregory_mcp import telemetry
from gregory_mcp.cache import CatalogCache
from gregory_mcp.client import GregoryAPIError, GregoryClient
from gregory_mcp.telemetry import TelemetryMiddleware, _UpstreamAccumulator
from tests.conftest import TEST_SETTINGS

# Distinctive marker strings for the fields that must never reach a log
# line, however the field arrived (as an argument value, inside an error
# message, anywhere). If one of these ever appears in an emitted record it
# is a privacy regression, not a formatting nit.
_SENSITIVE_MARKERS = {
	"search": "encephalitis AND rituximab paediatric",
	"full_name": "Jane Q. Researcher",
	"given_name": "Jane",
	"family_name": "Researcher",
	"orcid": "0000-0002-1111-2222",
	"doi": "10.1234/secret.paper",
}


def _make_ctx(*, method="tools/call", params=None, meta=None, request_id="req-1", protocol_version="2026-07-28"):
	return ServerRequestContext(
		session=None,
		lifespan_context={},
		protocol_version=protocol_version,
		method=method,
		params=params,
		request_id=request_id,
		meta=meta,
	)


def _record_fields(record: logging.LogRecord) -> dict:
	"""Every attribute TelemetryMiddleware could plausibly have set via `extra`."""
	skip = {
		"name",
		"msg",
		"args",
		"levelname",
		"levelno",
		"pathname",
		"filename",
		"module",
		"exc_info",
		"exc_text",
		"stack_info",
		"lineno",
		"funcName",
		"created",
		"msecs",
		"relativeCreated",
		"thread",
		"threadName",
		"processName",
		"process",
		"message",
		"taskName",
	}
	return {k: v for k, v in record.__dict__.items() if k not in skip}


@pytest.fixture(autouse=True)
def _telemetry_caplog(caplog):
	caplog.set_level(logging.INFO, logger="gregory_mcp.telemetry")
	yield caplog


async def test_emits_one_record_for_a_successful_tool_call(caplog):
	async def call_next(ctx):
		return CallToolResult(
			content=[TextContent(type="text", text="ok")],
			structured_content={"count": 5, "next_page": 2, "articles": [1, 2, 3]},
		)

	ctx = _make_ctx(params={"name": "search_articles", "arguments": {"subject_id": 3, "page": 1}})
	result = await TelemetryMiddleware()(ctx, call_next)

	assert result.structured_content["articles"] == [1, 2, 3]
	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	assert len(records) == 1

	fields = _record_fields(records[0])
	assert fields["method"] == "tools/call"
	assert fields["tool"] == "search_articles"
	assert fields["outcome"] == "ok"
	assert fields["result_count"] == 3
	assert fields["total_count"] == 5
	assert fields["has_next"] is True
	assert fields["params_used"] == ["page", "subject_id"]
	assert fields["subject_id"] == 3
	assert fields["page"] == 1
	assert "duration_ms" in fields


def test_sanitized_logged_value_rejects_wrong_types_and_bad_formats():
	# int-typed fields: only real, non-bool ints pass.
	assert telemetry._sanitized_logged_value("subject_id", 3) == 3
	assert telemetry._sanitized_logged_value("subject_id", "3") is None
	assert telemetry._sanitized_logged_value("page", True) is None  # bool is an int subclass

	# category_slug: Django's SlugField alphabet only, length-capped.
	assert telemetry._sanitized_logged_value("category_slug", "encephalitis-2026") == "encephalitis-2026"
	assert telemetry._sanitized_logged_value("category_slug", "not a slug!") is None
	assert telemetry._sanitized_logged_value("category_slug", "x" * 101) is None
	assert telemetry._sanitized_logged_value("category_slug", 123) is None

	# category_modality: must be one of the declared literal values.
	assert telemetry._sanitized_logged_value("category_modality", "small_molecule") == "small_molecule"
	assert telemetry._sanitized_logged_value("category_modality", "made-up-value") is None


async def test_pre_validation_free_text_in_a_logged_arg_is_never_logged_by_value(caplog):
	"""ServerMiddleware runs before schema validation, so a malformed or
	malicious client can send a string where the schema expects an int, or
	free text where it expects a slug — this must never reach the log by
	value just because the field name is on _LOGGED_ARG_NAMES.
	"""

	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "articles": []})

	ctx = _make_ctx(
		params={
			"name": "search_articles",
			"arguments": {
				"subject_id": "contact me at jane@example.com",  # schema expects int
				"category_slug": "not a real slug; DROP TABLE articles",
				"category_modality": "totally-made-up-modality",
				"page": "1 OR 1=1",
			},
		}
	)
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	fields = _record_fields(records[0])
	# Names still show up in params_used (that's names-only, always safe)...
	assert fields["params_used"] == ["category_modality", "category_slug", "page", "subject_id"]
	# ...but none of the malformed values were logged by value.
	assert "subject_id" not in fields
	assert "category_slug" not in fields
	assert "category_modality" not in fields
	assert "page" not in fields
	assert "jane@example.com" not in repr(fields)
	assert "DROP TABLE" not in repr(fields)


async def test_a_well_formed_category_slug_is_still_logged_by_value(caplog):
	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "articles": []})

	ctx = _make_ctx(
		params={"name": "search_articles", "arguments": {"category_slug": "encephalitis-2026", "category_modality": "small_molecule"}}
	)
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	fields = _record_fields(records[0])
	assert fields["category_slug"] == "encephalitis-2026"
	assert fields["category_modality"] == "small_molecule"


async def test_result_shape_falls_back_to_the_text_content_block(caplog):
	"""None of our tools declare an output schema (they return plain dict),
	so structured_content is unset on the real wire — confirmed by driving
	search_articles through the actual server stack. The payload MCPServer
	actually sends is JSON inside content[0].text; that's what telemetry
	must read result_count/total_count/has_next from in production.
	"""
	import json as _json

	async def call_next(ctx):
		payload = {"count": 42, "next_page": None, "articles": [{"id": 1}, {"id": 2}]}
		return CallToolResult(content=[TextContent(type="text", text=_json.dumps(payload))])

	ctx = _make_ctx(params={"name": "search_articles", "arguments": {"subject_id": 3}})
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	assert len(records) == 1
	fields = _record_fields(records[0])
	assert fields["result_count"] == 2
	assert fields["total_count"] == 42
	assert fields["has_next"] is False


async def test_params_used_lists_names_never_values(caplog):
	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "articles": []})

	ctx = _make_ctx(
		params={
			"name": "search_articles",
			# team_id is explicitly None (an omitted-optional-arg shape some
			# clients send) and must be excluded from params_used just like a
			# truly absent key would be.
			"arguments": {"search": "stem cells", "team_id": None, "subject_id": 7},
		}
	)
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	assert len(records) == 1
	fields = _record_fields(records[0])
	assert fields["params_used"] == ["search", "subject_id"]
	assert "stem cells" not in repr(fields)


async def test_sensitive_fields_never_appear_in_any_emitted_field(caplog):
	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "authors": []})

	arguments = {**_SENSITIVE_MARKERS, "team_id": 9}
	ctx = _make_ctx(params={"name": "search_authors", "arguments": arguments})
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	assert len(records) == 1
	fields = _record_fields(records[0])

	# params_used may name the fields, but must never carry their values.
	assert fields["params_used"] == sorted(["team_id", *_SENSITIVE_MARKERS.keys()])

	serialized = repr(fields)
	for marker_value in _SENSITIVE_MARKERS.values():
		assert marker_value not in serialized


async def test_raising_tool_call_still_emits_an_event(caplog):
	async def call_next(ctx):
		raise MCPError(code=INTERNAL_ERROR, message="handler exploded")

	ctx = _make_ctx(params={"name": "search_articles", "arguments": {}})

	with pytest.raises(MCPError):
		await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	assert len(records) == 1
	fields = _record_fields(records[0])
	assert fields["outcome"] == "error"
	assert fields["error_kind"] == "protocol_error"
	# Numeric like every other status_code this middleware emits (upstream_error,
	# tool_error), not a str — keeps downstream aggregation (ranges, histograms)
	# from needing a special case for the protocol_error path.
	assert fields["status_code"] == INTERNAL_ERROR
	assert isinstance(fields["status_code"], int)


async def test_tool_error_result_is_recorded_as_error_outcome(caplog):
	"""The SDK's own tool-call handler catches exceptions raised by the tool
	function (e.g. search_authors' `ValueError: subject_id requires team_id`)
	and converts them into a CallToolResult(is_error=True) before our
	middleware ever sees them — so no exception reaches or leaves
	TelemetryMiddleware here; only the is_error result does.
	"""

	async def call_next(ctx):
		return CallToolResult(content=[TextContent(type="text", text="boom")], is_error=True)

	ctx = _make_ctx(params={"name": "search_authors", "arguments": {"subject_id": 1, "team_id": None}})
	result = await TelemetryMiddleware()(ctx, call_next)
	assert result.is_error

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	assert len(records) == 1
	fields = _record_fields(records[0])
	assert fields["outcome"] == "error"
	assert fields["error_kind"] == "tool_error"


_QUERY_SHAPE_CATEGORIES = [
	{"category_slug": "encephalitis", "category_name": "Encephalitis", "category_terms": ["encephalitis"]},
]


def _patch_categories(monkeypatch, categories=_QUERY_SHAPE_CATEGORIES):
	import gregory_mcp.query_shape as query_shape

	async def fake_get_all_pages_cached(path, params=None):
		return categories

	monkeypatch.setattr(query_shape, "get_all_pages_cached", fake_get_all_pages_cached)


async def test_search_articles_gets_query_shape_fields(caplog, monkeypatch):
	_patch_categories(monkeypatch)

	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "articles": []})

	ctx = _make_ctx(params={"name": "search_articles", "arguments": {"search": "encephalitis outcomes"}})
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	assert len(records) == 1
	fields = _record_fields(records[0])
	assert fields["term_count"] == 2
	assert fields["matched_category_slugs"] == ["encephalitis"]
	assert fields["unmatched_term_count"] == 1
	assert fields["has_boolean_ops"] is False
	assert fields["has_quoted_phrase"] is False
	assert "length_bucket" in fields
	assert "encephalitis outcomes" not in repr(fields)


async def test_search_trials_falls_back_to_title_when_search_is_absent(caplog, monkeypatch):
	_patch_categories(monkeypatch)

	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "trials": []})

	ctx = _make_ctx(params={"name": "search_trials", "arguments": {"title": "encephalitis"}})
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	fields = _record_fields(records[0])
	assert fields["matched_category_slugs"] == ["encephalitis"]


async def test_list_categories_uses_its_own_search_argument(caplog, monkeypatch):
	_patch_categories(monkeypatch)

	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "categories": []})

	ctx = _make_ctx(params={"name": "list_categories", "arguments": {"search": "encephalitis"}})
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	fields = _record_fields(records[0])
	assert fields["matched_category_slugs"] == ["encephalitis"]


async def test_tools_outside_the_query_shape_allowlist_get_no_shape_fields(caplog, monkeypatch):
	_patch_categories(monkeypatch)

	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"id": 1})

	ctx = _make_ctx(params={"name": "get_article", "arguments": {"article_id": 1}})
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	fields = _record_fields(records[0])
	assert "term_count" not in fields
	assert "matched_category_slugs" not in fields


async def test_query_shape_guardrail_hit_means_no_shape_fields_at_all(caplog, monkeypatch):
	_patch_categories(monkeypatch)

	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "articles": []})

	ctx = _make_ctx(params={"name": "search_articles", "arguments": {"search": "contact jane@example.com"}})
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	fields = _record_fields(records[0])
	assert "term_count" not in fields
	assert "jane@example.com" not in repr(fields)


async def test_query_shape_fetch_failure_does_not_break_the_request(caplog, monkeypatch):
	"""The Gregory API being unreachable for the taxonomy fetch must not
	fail the tool call telemetry is describing — it's a best-effort
	annotation, not a requirement."""
	import gregory_mcp.query_shape as query_shape

	async def failing_fetch(path, params=None):
		raise RuntimeError("upstream unreachable")

	monkeypatch.setattr(query_shape, "get_all_pages_cached", failing_fetch)

	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "articles": []})

	ctx = _make_ctx(params={"name": "search_articles", "arguments": {"search": "encephalitis"}})
	result = await TelemetryMiddleware()(ctx, call_next)
	assert not result.is_error

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	assert len(records) == 1
	fields = _record_fields(records[0])
	assert fields["outcome"] == "ok"
	assert "term_count" not in fields


async def test_notifications_are_not_logged(caplog):
	async def call_next(ctx):
		return None

	ctx = _make_ctx(method="notifications/cancelled", params={}, request_id=None)
	await TelemetryMiddleware()(ctx, call_next)

	assert [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"] == []


async def test_non_tool_method_gets_ok_outcome_without_a_tool_field(caplog):
	async def call_next(ctx):
		return {"tools": []}

	ctx = _make_ctx(method="tools/list", params=None)
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	assert len(records) == 1
	fields = _record_fields(records[0])
	assert fields["method"] == "tools/list"
	assert fields["outcome"] == "ok"
	assert "tool" not in fields


async def test_client_name_version_and_protocol_version_captured(caplog):
	async def call_next(ctx):
		return CallToolResult(content=[], structured_content={"count": 0, "articles": []})

	ctx = _make_ctx(
		params={"name": "search_articles", "arguments": {}},
		meta={CLIENT_INFO_META_KEY: {"name": "some-client", "version": "1.2.3"}},
		protocol_version="2026-07-28",
	)
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry" and r.getMessage() == "mcp_request"]
	fields = _record_fields(records[0])
	assert fields["client_name"] == "some-client"
	assert fields["client_version"] == "1.2.3"
	assert fields["protocol_version"] == "2026-07-28"


async def test_upstream_accounting_survives_retries():
	accumulator = _UpstreamAccumulator()
	token = telemetry._accumulator.set(accumulator)
	try:
		settings = TEST_SETTINGS
		from dataclasses import replace

		settings = replace(settings, max_retries=2)

		async def no_delay(seconds):
			return None

		client = GregoryClient(settings, sleep=no_delay)
		attempts = []

		def handler(request):
			attempts.append(1)
			if len(attempts) < 2:
				return httpx2.Response(500, json={"detail": "boom"})
			return httpx2.Response(200, json={"ok": True})

		client._client._transport = httpx2.MockTransport(handler)
		result = await client.get("/articles/")

		assert result == {"ok": True}
		assert accumulator.upstream_calls == 2
		assert accumulator.upstream_ms >= 0
		assert accumulator.error_kind is None
	finally:
		telemetry._accumulator.reset(token)


async def test_upstream_error_status_recorded_before_exception_is_raised():
	accumulator = _UpstreamAccumulator()
	token = telemetry._accumulator.set(accumulator)
	try:
		client = GregoryClient(TEST_SETTINGS)
		client._client._transport = httpx2.MockTransport(lambda r: httpx2.Response(404, text="nope"))

		with pytest.raises(GregoryAPIError):
			await client.get("/articles/999/")

		assert accumulator.error_kind == "upstream_error"
		assert accumulator.error_status == 404
	finally:
		telemetry._accumulator.reset(token)


async def test_cache_status_hit_then_miss_recorded_on_accumulator():
	cache = CatalogCache()

	async def fetch():
		return ["row"]

	accumulator = _UpstreamAccumulator()
	token = telemetry._accumulator.set(accumulator)
	try:
		await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)
		assert accumulator.cache_status == "miss"

		accumulator.cache_status = None
		await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)
		assert accumulator.cache_status == "hit"
	finally:
		telemetry._accumulator.reset(token)


async def test_cache_status_single_flight_wait_recorded_on_accumulator():
	"""A concurrent caller that waits on the lock and finds another caller's
	fetch already landed should record 'single-flight-wait' — matching
	MCP-TELEMETRY-PLAN.md's documented taxonomy of hit / miss /
	single-flight-wait exactly (not the older, undocumented 'wait').
	"""
	cache = CatalogCache()
	release = asyncio.Event()

	async def slow_fetch():
		await release.wait()
		return ["row"]

	async def call_with_accumulator():
		accumulator = _UpstreamAccumulator()
		token = telemetry._accumulator.set(accumulator)
		try:
			await cache.get_or_fetch("/categories/", None, slow_fetch)
			return accumulator.cache_status
		finally:
			telemetry._accumulator.reset(token)

	winner_task = asyncio.create_task(call_with_accumulator())
	await asyncio.sleep(0.01)  # let the winner reach the fetch/lock first
	waiter_task = asyncio.create_task(call_with_accumulator())
	await asyncio.sleep(0.01)  # let the waiter block on the lock
	release.set()

	winner_status, waiter_status = await asyncio.gather(winner_task, waiter_task)
	assert winner_status == "miss"
	assert waiter_status == "single-flight-wait"


async def test_no_accumulator_active_is_a_silent_no_op():
	"""Outside a tracked request (e.g. every other test file in this suite,
	which calls client/cache code directly) recording must not raise."""
	client = GregoryClient(TEST_SETTINGS)
	client._client._transport = httpx2.MockTransport(lambda r: httpx2.Response(200, json={"ok": True}))
	assert await client.get("/articles/") == {"ok": True}

	cache = CatalogCache()
	assert await cache.get_or_fetch("/subjects/", None, lambda: _identity(["row"])) == ["row"]


async def _identity(value):
	return value
