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
"""

import dataclasses
import logging

import httpx2
from mcp.client import Client

from gregory_mcp.logging_config import INTENT_LOGGER_NAME
from gregory_mcp.server import build_server
from gregory_mcp.site import init_site_resolution
from tests.conftest import TEST_SETTINGS


class _Capture(logging.Handler):
	def __init__(self):
		super().__init__(level=logging.INFO)
		self.records: list[logging.LogRecord] = []

	def emit(self, record):
		self.records.append(record)


async def _call_search_articles_and_capture(mock_gregory):
	"""Run one real tools/call and return (mcp_request, mcp_intent) records.

	Handlers are attached directly to both loggers rather than via caplog:
	the intent logger is propagate=False in production, so relying on root
	propagation would test a configuration production does not have.
	"""
	mock_gregory.set_handler(
		lambda request: httpx2.Response(200, json={"count": 0, "next": None, "results": []})
	)
	telemetry_logger = logging.getLogger("gregory_mcp.telemetry")
	intent_logger = logging.getLogger(INTENT_LOGGER_NAME)
	telemetry_capture, intent_capture = _Capture(), _Capture()
	saved_levels = (telemetry_logger.level, intent_logger.level)
	telemetry_logger.addHandler(telemetry_capture)
	intent_logger.addHandler(intent_capture)
	telemetry_logger.setLevel(logging.INFO)
	intent_logger.setLevel(logging.INFO)
	try:
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

	# The client makes its own protocol requests before tools/call, and
	# telemetry logs every request, so pick out the tool call — and keep the
	# rest, so the caller can check every request was attributed, not just it.
	requests = [r for r in telemetry_capture.records if r.getMessage() == "mcp_request"]
	tool_calls = [r for r in requests if getattr(r, "method", None) == "tools/call"]
	intents = [r for r in intent_capture.records if r.getMessage() == "mcp_intent"]
	assert len(tool_calls) == 1, [getattr(r, "method", None) for r in requests]
	assert len(intents) == 1, [r.getMessage() for r in intent_capture.records]
	return tool_calls[0], requests, intents[0]


async def test_resolved_site_reaches_telemetry_through_the_real_middleware(mock_gregory):
	init_site_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=42))

	request_record, all_requests, intent_record = await _call_search_articles_and_capture(mock_gregory)

	assert request_record.site_id == 42
	assert {getattr(r, "site_id", "missing") for r in all_requests} == {42}
	# The same resolution fed the upstream call, so telemetry and the API agree
	# on which tenant this request was for.
	assert all("site_id=42" in str(r.url) for r in mock_gregory.requests)
	assert not hasattr(intent_record, "site_id")


async def test_unresolved_site_is_recorded_as_null_through_the_real_middleware(mock_gregory):
	# mock_gregory resets site resolution, so no override is set and an
	# in-memory call has no Host header: nothing resolves.
	request_record, all_requests, intent_record = await _call_search_articles_and_capture(mock_gregory)

	assert hasattr(request_record, "site_id")
	assert request_record.site_id is None
	assert {getattr(r, "site_id", "missing") for r in all_requests} == {None}
	assert not hasattr(intent_record, "site_id")
