"""Which tools a request may see and call.

Both `MCPServer`s register every tool once per process, but what a given
request may do depends on who signed in (MCP-AUTH-PLAN.md, "Access tiers on the
editor address") and on the site it is for:

| Sign-in                          | Tools                                        |
|:---------------------------------|:---------------------------------------------|
| Anonymous, or public tier        | the ten read tools, exactly as on `/mcp`     |
| Editor, read-only grant          | the ten read tools plus `get_history`        |
| Editor with the edit scope       | all of those plus the six write tools        |

On top of that, `search_patents` and `get_patent` are offered only when the
resolved tenant has patents (`Tenant.patents_enabled`, from `GET /tenants/`).
A tenant without them has the list above and nothing mentions patents there.
That applies on both addresses: `/mcp` runs this middleware too, for exactly
this reason.

`tools/list` is filtered to that set, and `tools/call` for anything outside it
is answered exactly as a tool that doesn't exist is: a client that sends a
write tool's (or a patent tool's) name anyway learns nothing and writes
nothing. The client's own refusal to write without the edit scope (`client.py`)
is the second lock.
"""

from __future__ import annotations

from typing import Any

import mcp_types as types
from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext

from .site_context import EditorSession, get_current_tenant, get_editor_context

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
#: Offered only on a tenant with `patents_enabled`.
PATENT_TOOLS = frozenset({"search_patents", "get_patent"})
HISTORY_TOOL = "get_history"
WRITE_TOOLS = frozenset(
	{
		"update_editorial",
		"set_article_relevance",
		"set_trial_link",
		"create_category",
		"update_category",
		"set_category_assignment",
	}
)


def allowed_tools(session: EditorSession | None, tenant: Any = None) -> frozenset[str]:
	"""The tools this caller may use on this tenant's site. A missing tenant has
	no patents: nothing is offered that the tenant has not been shown to have."""
	tools = READ_TOOLS
	if session is not None and session.is_editor:
		tools = tools | {HISTORY_TOOL}
		if session.can_edit:
			tools = tools | WRITE_TOOLS
	if tenant is not None and getattr(tenant, "patents_enabled", False):
		tools = tools | PATENT_TOOLS
	return tools


class ToolAccessMiddleware(ServerMiddleware[Any]):
	"""Applies `allowed_tools()` to `tools/list` and `tools/call`.

	Registered inside `TelemetryMiddleware` and `TenantGateMiddleware` (see
	server.py), so a refused call is still logged as an `mcp_request`.
	"""

	async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
		if ctx.method == "tools/call":
			name = (ctx.params or {}).get("name")
			if name not in allowed_tools(get_editor_context(), get_current_tenant()):
				# The SDK's own answer for a tool that doesn't exist.
				return types.CallToolResult(
					content=[types.TextContent(type="text", text=f"Unknown tool: {name}")], is_error=True
				)
			return await call_next(ctx)

		result = await call_next(ctx)
		if ctx.method == "tools/list":
			return _filter_tool_list(result, allowed_tools(get_editor_context(), get_current_tenant()))
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
