"""Which tools a request to the editor address may see and call.

The editor `MCPServer` registers every tool once per process, like the
anonymous one, but what a given request may do depends on who signed in
(MCP-AUTH-PLAN.md, "Access tiers on the editor address"):

| Sign-in                          | Tools                                        |
|:---------------------------------|:---------------------------------------------|
| Public tier (no editor grant)    | the ten read tools, exactly as on `/mcp`     |
| Editor, read-only grant          | the ten read tools plus `get_article_history`|
| Editor with the edit scope       | all of those plus the eight write tools      |

`tools/list` is filtered to that set, and `tools/call` for anything outside it
is answered exactly as a tool that doesn't exist is: a client that sends a
write tool's name anyway learns nothing and writes nothing. The client's own
refusal to write without the edit scope (`client.py`) is the second lock.
"""

from __future__ import annotations

from typing import Any

import mcp_types as types
from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext

from .site_context import EditorSession, get_editor_context

READ_TOOLS = frozenset(
	{
		"list_subjects",
		"search_articles",
		"get_article",
		"search_trials",
		"get_trial",
		"search_authors",
		"get_author",
		"list_categories",
		"list_sponsors",
		"get_stats",
	}
)
HISTORY_TOOL = "get_article_history"
WRITE_TOOLS = frozenset(
	{
		"update_article_editorial",
		"set_article_relevance",
		"link_trial_to_article",
		"unlink_trial_from_article",
		"create_category",
		"update_category",
		"assign_article_category",
		"unassign_article_category",
	}
)


def allowed_tools(session: EditorSession | None) -> frozenset[str]:
	if session is None or not session.is_editor:
		return READ_TOOLS
	tools = READ_TOOLS | {HISTORY_TOOL}
	if session.can_edit:
		tools |= WRITE_TOOLS
	return tools


class ToolAccessMiddleware(ServerMiddleware[Any]):
	"""Applies `allowed_tools()` to `tools/list` and `tools/call`.

	Registered inside `TelemetryMiddleware` and `TenantGateMiddleware` (see
	server.py), so a refused call is still logged as an `mcp_request`.
	"""

	async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
		if ctx.method == "tools/call":
			name = (ctx.params or {}).get("name")
			if name not in allowed_tools(get_editor_context()):
				# The SDK's own answer for a tool that doesn't exist.
				return types.CallToolResult(
					content=[types.TextContent(type="text", text=f"Unknown tool: {name}")], is_error=True
				)
			return await call_next(ctx)

		result = await call_next(ctx)
		if ctx.method == "tools/list":
			return _filter_tool_list(result, allowed_tools(get_editor_context()))
		return result


def _filter_tool_list(result: HandlerResult, allowed: frozenset[str]) -> HandlerResult:
	"""`result` with only the allowed tools, whether the SDK has already
	serialized it to a wire dict (the usual case, by the time middleware runs)
	or still holds a `ListToolsResult`."""
	if isinstance(result, dict):
		tools = result.get("tools")
		if isinstance(tools, list):
			result["tools"] = [t for t in tools if _tool_name(t) in allowed]
		return result
	if isinstance(result, types.ListToolsResult):
		result.tools = [t for t in result.tools if t.name in allowed]
	return result


def _tool_name(tool: Any) -> str | None:
	return tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
