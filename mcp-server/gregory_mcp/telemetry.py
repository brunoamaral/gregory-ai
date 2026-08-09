"""Per-request telemetry: one structured JSON log line per inbound MCP request.

Phase 1 of MCP-TELEMETRY-PLAN.md — timing, outcome, and selectivity only.
No query content (`search`, `full_name`, `given_name`, `family_name`,
`orcid`, `doi`) is logged at this phase, or ever for the author fields —
see the plan's "not logged at any phase" section. `params_used` records
which argument *names* a call supplied, never their values.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from mcp_types import CLIENT_INFO_META_KEY, CallToolResult
from pydantic import ValidationError

from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext
from mcp.shared.exceptions import MCPError

logger = logging.getLogger("gregory_mcp.telemetry")

# Argument names safe to log by value: public, low-cardinality taxonomy IDs
# and pagination controls. Never add `search`, `title`, `summary`,
# `full_name`, `given_name`, `family_name`, `orcid`, `doi`, `condition`,
# `intervention`, or any other free-text/identifying field here — see
# MCP-TELEMETRY-PLAN.md's "not logged at any phase".
_LOGGED_ARG_NAMES = ("page", "page_size", "subject_id", "team_id", "category_slug", "category_modality")


@dataclass
class _UpstreamAccumulator:
	"""Per-request scratch space.

	`GregoryClient.get()` and `CatalogCache.get_or_fetch()` write into the
	active request's accumulator as they run; `TelemetryMiddleware` reads it
	back after `call_next()` returns. A `ContextVar` rather than a parameter
	threaded through every tool signature — see MCP-TELEMETRY-PLAN.md Phase 1.
	"""

	upstream_ms: float = 0.0
	upstream_calls: int = 0
	error_kind: str | None = None
	error_status: int | None = None
	cache_status: str | None = None


_accumulator: contextvars.ContextVar["_UpstreamAccumulator | None"] = contextvars.ContextVar(
	"gregory_mcp_upstream_accumulator", default=None
)


def record_upstream_call(duration_ms: float) -> None:
	"""Called by GregoryClient.get() after each HTTP attempt (including
	retries, so upstream_calls > 1 signals a request that hit retry logic)."""
	acc = _accumulator.get()
	if acc is not None:
		acc.upstream_ms += duration_ms
		acc.upstream_calls += 1


def record_upstream_error(status_code: int) -> None:
	"""Called by GregoryClient.get() immediately before it raises
	GregoryAPIError — the exception itself is swallowed by the SDK's tool-call
	handler before it would otherwise reach TelemetryMiddleware, so the status
	code has to be captured on the way up rather than read off the exception."""
	acc = _accumulator.get()
	if acc is not None:
		acc.error_kind = "upstream_error"
		acc.error_status = status_code


def record_truncation_error() -> None:
	"""Called by GregoryClient.get_all_pages() before it raises
	GregoryPaginationTruncatedError, for the same reason as record_upstream_error."""
	acc = _accumulator.get()
	if acc is not None:
		acc.error_kind = "truncation_error"


def record_cache_status(status: str) -> None:
	"""Called by CatalogCache.get_or_fetch() with 'hit', 'miss', or 'wait'."""
	acc = _accumulator.get()
	if acc is not None:
		acc.cache_status = status


def _extract_call_tool_result(result: HandlerResult) -> tuple[Any, bool]:
	"""(payload, is_error) for a tools/call result.

	TelemetryMiddleware is registered innermost (see build_server()), so
	call_next() here returns the tool-call handler's own result directly —
	normally a CallToolResult. The dict branch mirrors the wire alias shape
	defensively, the same way OpenTelemetryMiddleware's own match does.

	None of our tools declare a structured-output schema (they return plain
	`dict`), so `structured_content` is unset on the wire; the same dict the
	tool returned is what `content[0].text` holds instead (MCPServer
	JSON-serializes it there — see func_metadata._convert_to_content). Fall
	back to parsing that: it's the identical payload already sent to the
	client, just read from the other place it landed.
	"""
	if isinstance(result, CallToolResult):
		payload = result.structured_content
		if payload is None:
			payload = _parse_first_text_block(result.content)
		return payload, result.is_error
	if isinstance(result, dict):
		payload = result.get("structuredContent")
		if payload is None:
			payload = _parse_first_text_block(result.get("content"))
		return payload, bool(result.get("isError", False))
	return None, False


def _parse_first_text_block(content: Any) -> Any:
	if not content:
		return None
	first = content[0]
	text = getattr(first, "text", None) if not isinstance(first, dict) else first.get("text")
	if not isinstance(text, str):
		return None
	try:
		return json.loads(text)
	except ValueError:
		return None


def _result_shape(payload: Any) -> tuple[int | None, int | None, bool | None]:
	"""(result_count, total_count, has_next) from a tool's returned dict.

	Generic over payload shape rather than special-cased per tool name: every
	list/search tool's return dict carries `count` (see articles.py,
	trials.py, authors.py, catalog.py) and, where paginated, `next_page`. The
	item list itself is under a tool-specific key (`articles`, `trials`,
	`subjects`, ...) — found here as "the first list-valued entry" rather
	than naming each one.
	"""
	if not isinstance(payload, dict) or "count" not in payload:
		return None, None, None
	total_count = payload["count"]
	has_next = payload.get("next_page") is not None if "next_page" in payload else None
	result_count = None
	for value in payload.values():
		if isinstance(value, list):
			result_count = len(value)
			break
	return result_count, total_count, has_next


class TelemetryMiddleware(ServerMiddleware[Any]):
	"""Emits one JSON log line per inbound request. No query content, ever.

	Registered last in `MCPServer(middleware=[...])`, which the SDK places
	after its own OpenTelemetry and RequestStateBoundary middleware — so this
	runs innermost, and `call_next(ctx)` is params validation plus the actual
	handler dispatch.
	"""

	async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
		if ctx.request_id is None:
			# Notifications (notifications/cancelled, notifications/initialized, ...)
			# aren't billable/latency-worthy requests.
			return await call_next(ctx)

		accumulator = _UpstreamAccumulator()
		token = _accumulator.set(accumulator)
		start = time.monotonic()
		event: dict[str, Any] = {"method": ctx.method, "protocol_version": ctx.protocol_version}

		params = ctx.params or {}
		tool_name = params.get("name") if ctx.method == "tools/call" else None
		if isinstance(tool_name, str):
			event["tool"] = tool_name
			arguments = params.get("arguments")
			if isinstance(arguments, dict):
				event["params_used"] = sorted(k for k, v in arguments.items() if v is not None)
				for key in _LOGGED_ARG_NAMES:
					value = arguments.get(key)
					if value is not None:
						event[key] = value

		client_info = ctx.meta.get(CLIENT_INFO_META_KEY) if ctx.meta else None
		if isinstance(client_info, dict):
			name = client_info.get("name")
			version = client_info.get("version")
			if isinstance(name, str):
				event["client_name"] = name
			if isinstance(version, str):
				event["client_version"] = version

		try:
			result = await call_next(ctx)
		except MCPError as exc:
			event["outcome"] = "error"
			event["error_kind"] = "protocol_error"
			event["status_code"] = str(exc.error.code)
			_emit(event, start, accumulator)
			_accumulator.reset(token)
			raise
		except ValidationError:
			event["outcome"] = "error"
			event["error_kind"] = "validation_error"
			_emit(event, start, accumulator)
			_accumulator.reset(token)
			raise
		except Exception:
			event["outcome"] = "error"
			event["error_kind"] = accumulator.error_kind or "internal_error"
			_emit(event, start, accumulator)
			_accumulator.reset(token)
			raise

		if ctx.method == "tools/call":
			payload, is_error = _extract_call_tool_result(result)
			if is_error:
				event["outcome"] = "error"
				event["error_kind"] = accumulator.error_kind or "tool_error"
				if accumulator.error_status is not None:
					event["status_code"] = accumulator.error_status
			else:
				event["outcome"] = "ok"
				result_count, total_count, has_next = _result_shape(payload)
				if result_count is not None:
					event["result_count"] = result_count
				if total_count is not None:
					event["total_count"] = total_count
				if has_next is not None:
					event["has_next"] = has_next
		else:
			event["outcome"] = "ok"

		_emit(event, start, accumulator)
		_accumulator.reset(token)
		return result


def _emit(event: dict[str, Any], start: float, accumulator: _UpstreamAccumulator) -> None:
	event["duration_ms"] = round((time.monotonic() - start) * 1000, 1)
	if accumulator.upstream_calls:
		event["upstream_ms"] = round(accumulator.upstream_ms, 1)
		event["upstream_calls"] = accumulator.upstream_calls
	if accumulator.cache_status is not None:
		event["cache"] = accumulator.cache_status
	logger.info("mcp_request", extra=event)
