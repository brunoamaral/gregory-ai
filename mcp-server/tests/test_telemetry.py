from __future__ import annotations

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
	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry"]
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

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry"]
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

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry"]
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

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry"]
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

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry"]
	assert len(records) == 1
	fields = _record_fields(records[0])
	assert fields["outcome"] == "error"
	assert fields["error_kind"] == "protocol_error"


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

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry"]
	assert len(records) == 1
	fields = _record_fields(records[0])
	assert fields["outcome"] == "error"
	assert fields["error_kind"] == "tool_error"


async def test_notifications_are_not_logged(caplog):
	async def call_next(ctx):
		return None

	ctx = _make_ctx(method="notifications/cancelled", params={}, request_id=None)
	await TelemetryMiddleware()(ctx, call_next)

	assert [r for r in caplog.records if r.name == "gregory_mcp.telemetry"] == []


async def test_non_tool_method_gets_ok_outcome_without_a_tool_field(caplog):
	async def call_next(ctx):
		return {"tools": []}

	ctx = _make_ctx(method="tools/list", params=None)
	await TelemetryMiddleware()(ctx, call_next)

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry"]
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

	records = [r for r in caplog.records if r.name == "gregory_mcp.telemetry"]
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
