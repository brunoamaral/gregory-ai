"""Site attribution through the assembled server, end to end.

The telemetry tests elsewhere set the site ContextVar by hand and call
TelemetryMiddleware directly. That proves telemetry reads the value, but not
that the production composition delivers it: build_server() could omit or
reorder SiteMiddleware, or reset the context before telemetry emits, and every
resolved request would log `site_id: null` while those tests still passed.

These drive a real `tools/call` through build_server() over the SDK's
in-memory transport, so the middleware chain is the one production runs. An
in-memory call carries no HTTP request, so there is no Host header to resolve;
the resolved case uses the GREGORY_SITE_ID override path instead, which still
goes through SiteMiddleware.

They also pin the other half of the decision taken on PR #872's review: the
same call's intent event must NOT carry site_id (see intent.py's module
docstring for why).

Since Phase 3 (TenantGateMiddleware, decision 1), a request with no resolved
tenant is refused before any tool call — including the handshake itself, so
the unresolved case below can no longer complete a normal tools/call and
asserts on the refusal instead.
"""

import dataclasses
import logging

import httpx2
import pytest
from mcp.client import Client

from gregory_mcp.logging_config import INTENT_LOGGER_NAME
from gregory_mcp.server import build_server
from gregory_mcp.tenants import init_tenant_resolution
from tests.conftest import TEST_SETTINGS, route_by_path, tenants_payload


class _Capture(logging.Handler):
	def __init__(self):
		super().__init__(level=logging.INFO)
		self.records: list[logging.LogRecord] = []

	def emit(self, record):
		self.records.append(record)


async def _run_with_capture(coro_factory):
	"""Run `coro_factory()` with telemetry/intent capture handlers attached,
	returning (telemetry_records, intent_records). Handlers are attached
	directly to both loggers rather than via caplog: the intent logger is
	propagate=False in production, so relying on root propagation would test
	a configuration production does not have."""
	telemetry_logger = logging.getLogger("gregory_mcp.telemetry")
	intent_logger = logging.getLogger(INTENT_LOGGER_NAME)
	telemetry_capture, intent_capture = _Capture(), _Capture()
	saved_levels = (telemetry_logger.level, intent_logger.level)
	telemetry_logger.addHandler(telemetry_capture)
	intent_logger.addHandler(intent_capture)
	telemetry_logger.setLevel(logging.INFO)
	intent_logger.setLevel(logging.INFO)
	try:
		await coro_factory()
	finally:
		telemetry_logger.removeHandler(telemetry_capture)
		intent_logger.removeHandler(intent_capture)
		telemetry_logger.setLevel(saved_levels[0])
		intent_logger.setLevel(saved_levels[1])
	return telemetry_capture.records, intent_capture.records


async def test_resolved_site_reaches_telemetry_through_the_real_middleware(mock_gregory):
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=42))
	mock_gregory.set_handler(
		route_by_path(
			{
				"/tenants/": lambda request: httpx2.Response(
					200, json=tenants_payload({"site_id": 42, "domain": "tenant42.test"})
				),
			}
		)
	)

	async def call():
		async with Client(build_server()) as client:
			await client.call_tool(
				"search_articles",
				{"search": "remyelination", "intent": "find recent remyelination trials"},
			)

	telemetry_records, intent_records = await _run_with_capture(call)

	requests = [r for r in telemetry_records if r.getMessage() == "mcp_request"]
	tool_calls = [r for r in requests if getattr(r, "method", None) == "tools/call"]
	intents = [r for r in intent_records if r.getMessage() == "mcp_intent"]
	assert len(tool_calls) == 1, [getattr(r, "method", None) for r in requests]
	assert len(intents) == 1, [r.getMessage() for r in intent_records]

	assert tool_calls[0].site_id == 42
	assert {getattr(r, "site_id", "missing") for r in requests} == {42}
	# The same resolution fed the upstream call, so telemetry and the API agree
	# on which tenant this request was for.
	articles_requests = [r for r in mock_gregory.requests if r.url.path == "/articles/"]
	assert articles_requests and all("site_id=42" in str(r.url) for r in articles_requests)
	assert not hasattr(intents[0], "site_id")


async def test_unresolved_tenant_refusal_is_logged_with_null_site_id(mock_gregory):
	# mock_gregory resets tenant resolution, so no override is set and an
	# in-memory call has no Host header: nothing resolves, and
	# TenantGateMiddleware refuses every request except ping before any
	# tool call could run (decision 1) -- including the handshake itself,
	# so the whole client session fails rather than just the tool call.
	# Captured inline (not via _run_with_capture) because pytest.raises
	# discards a with-block's return value once it raises.
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json=[]))
	telemetry_logger = logging.getLogger("gregory_mcp.telemetry")
	intent_logger = logging.getLogger(INTENT_LOGGER_NAME)
	telemetry_capture, intent_capture = _Capture(), _Capture()
	saved_levels = (telemetry_logger.level, intent_logger.level)
	telemetry_logger.addHandler(telemetry_capture)
	intent_logger.addHandler(intent_capture)
	telemetry_logger.setLevel(logging.INFO)
	intent_logger.setLevel(logging.INFO)
	try:
		with pytest.raises(BaseException):
			async with Client(build_server()) as client:
				await client.call_tool(
					"search_articles",
					{"search": "remyelination", "intent": "find recent remyelination trials"},
				)
	finally:
		telemetry_logger.removeHandler(telemetry_capture)
		intent_logger.removeHandler(intent_capture)
		telemetry_logger.setLevel(saved_levels[0])
		intent_logger.setLevel(saved_levels[1])

	requests = [r for r in telemetry_capture.records if r.getMessage() == "mcp_request"]
	assert requests, "expected the refused handshake to still be logged"
	for r in requests:
		assert r.site_id is None
		assert r.error_kind == "protocol_error"
	# The refusal happens before any tool ever runs, so no intent is logged.
	intents = [r for r in intent_capture.records if r.getMessage() == "mcp_intent"]
	assert intents == []
