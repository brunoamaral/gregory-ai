"""Builds the MCPServer: registers tools, resources, and prompts.

`build_server()` is the anonymous server behind `/mcp`: everything on it is
read-only. Every tool is annotated `read_only_hint=True, idempotent_hint=True,
open_world_hint=False` since none of them write, and every one only ever talks
to the one Gregory instance named by `GREGORY_API_URL`.

`build_server(editor=True)` is the server behind `/mcp/editor`
(MCP-AUTH-PLAN.md): the same ten read tools, plus `get_article_history` and four
write tools. What a given request may see and call depends on who signed in
(tool_access.py); the process registers every tool once, like the anonymous one.
"""

from __future__ import annotations

import mcp_types as types
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from . import prompts, resources
from .cache import CATALOG_CACHE_TTL_MS
from .identity import SERVER_VERSION, TenantIdentityMiddleware
from .site import SiteMiddleware
from .telemetry import TelemetryMiddleware
from .tenants import TenantGateMiddleware
from .tool_access import ToolAccessMiddleware
from .tools import articles, authors, catalog, stats, trials
from .tools import editor as editor_tools

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

# The write tools ask the client for confirmation (`destructive_hint`), which
# clients may or may not honour: the edit rate limit and Django's history are
# the safeguards that don't depend on the client. All are idempotent (the same
# call twice leaves the same state). Linking only adds, so it isn't destructive.
EDIT_DESTRUCTIVE = ToolAnnotations(
	read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
EDIT_ADDITIVE = ToolAnnotations(
	read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
# Creating a category: adds rather than overwrites, but a second identical call
# is refused (the slug is taken) rather than answered with the first result.
EDIT_CREATE = ToolAnnotations(
	read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)

# All five cache hints are "private" (decision 3, MCP-MULTI-TENANCY-PHASE-3-PLAN.md):
# every response now carries the resolved tenant's own serverInfo stamp
# (TenantIdentityMiddleware, identity.py), and most carry per-tenant content or
# instructions too, so no result is identical for every caller any more.
# "public" bought a shared cache/proxy the ability to serve one response to
# every caller regardless of which tenant's hostname they reached — that stops
# being true the moment identity itself is per-tenant. It costs nothing here:
# this server is always reached on one tenant's own hostname, never shared
# anonymously across tenants the way a single pre-Phase-3 deployment was.
#
# TTLs are unchanged from before Phase 3 — only the scope moved.
CATALOG_LIST_CACHE = CacheHint(ttl_ms=CATALOG_CACHE_TTL_MS, scope="private")
CATALOG_READ_CACHE = CacheHint(ttl_ms=CATALOG_CACHE_TTL_MS, scope="private")

# Tool/prompt schemas and server capabilities are static *between deploys* — unlike
# the reference data above they change only when this code changes, so they get their
# own TTL rather than inheriting one that exists to bound data staleness.
#
# Worth caching: tools/list alone is ~17 KB (~4.2k tokens) of schemas that otherwise
# gets refetched on every reconnect, and it lands in the model's system prompt, so
# refetching it risks invalidating the upstream prompt cache — the "keep upstream
# prompt caches stable across reconnects" the 2026-07-28 release notes call out.
#
# 30 minutes bounds how long a client can hold a stale catalog after a deploy.
# Staleness here is benign in both directions: a client missing a NEW parameter is
# merely degraded, and one sending a REMOVED parameter is rejected against the
# current schema. Neither is the silent-wrong-results failure mode that unknown
# *API* query params cause, since django-filter ignores those instead of erroring.
STATIC_CACHE = CacheHint(ttl_ms=30 * 60 * 1000, scope="private")

# Module-level so tests can assert on it: MCPServer keeps no public accessor for
# the hints it was constructed with, and a missing entry degrades silently to
# ttlMs=0 ("never cache"), which is indistinguishable from working.
CACHE_HINTS = {
	"resources/list": CATALOG_LIST_CACHE,
	"resources/read": CATALOG_READ_CACHE,
	"tools/list": STATIC_CACHE,
	"prompts/list": STATIC_CACHE,
	"server/discover": STATIC_CACHE,
}


# The editor address: a newly granted editor should see the edit tools soon
# after reconnecting, and what tools/list and server/discover say depends on the
# signed-in person's tier, so they get 5 minutes instead of STATIC_CACHE's 30.
# Still private: never shared between callers.
EDITOR_LIST_CACHE = CacheHint(ttl_ms=5 * 60 * 1000, scope="private")
EDITOR_CACHE_HINTS = {
	**CACHE_HINTS,
	"tools/list": EDITOR_LIST_CACHE,
	"server/discover": EDITOR_LIST_CACHE,
}


def _replace_handler(server: MCPServer, method: str, params_type: type, handler) -> None:
	"""Registers `handler` for `method`, replacing whatever the SDK's own
	high-level decorators would have registered.

	`server._lowlevel_server.add_request_handler` is private SDK API (`mcp`
	pinned to `==2.3.0` in pyproject.toml) — the one seam this server uses to
	serve prompts/resources per resolved tenant, since the
	`@server.prompt()`/`@server.resource()` decorators fix their
	registration at construction time, once for the whole process, while
	this server's active prompt/document set now varies per request. An SDK
	upgrade needs re-checking this attribute still exists;
	test_server.py::test_replace_handler_seam_still_exists fails loudly if
	it doesn't.
	"""
	lowlevel = getattr(server, "_lowlevel_server", None)
	if lowlevel is None or not callable(getattr(lowlevel, "add_request_handler", None)):
		raise RuntimeError(
			"MCPServer._lowlevel_server.add_request_handler is gone -- the mcp "
			"SDK's internals changed. _replace_handler (server.py) depends on "
			"this private attribute to serve per-tenant prompts/resources; "
			"re-check it against the new SDK version before upgrading further."
		)
	lowlevel.add_request_handler(method, params_type, handler)


def build_server(editor: bool = False) -> MCPServer:
	server = MCPServer(
		# Neutral defaults, naming no platform (decision F) — a client only
		# ever sees these if no tenant resolved, and TenantGateMiddleware
		# already refuses every such request except ping/notifications.
		# Every real response's identity comes from TenantIdentityMiddleware
		# instead (identity.py).
		name="gregory-ai",
		title="Research assistant",
		description=(
			"Access for editors to a research database of articles, clinical trials, authors, and sponsors."
			if editor
			else "Read-only access to a research database of articles, clinical trials, authors, and sponsors."
		),
		version=SERVER_VERSION,
		cache_hints=EDITOR_CACHE_HINTS if editor else CACHE_HINTS,
		# SiteMiddleware first (outermost): it resolves this request's tenant
		# — env override, else inbound Host via GET /tenants/ — before
		# anything else runs, so a cold-cache /tenants/ fetch's latency lands
		# outside TelemetryMiddleware's own per-tool-call accounting rather
		# than being smeared into whichever tool call happened to trigger it.
		# TenantGateMiddleware comes after Telemetry so a refusal is still
		# logged as an mcp_request (site_id: null, error_kind:
		# "protocol_error") rather than disappearing before telemetry sees it.
		# TenantIdentityMiddleware is innermost: the SDK serializes each
		# result, including its default identity stamp, *inside* the
		# middleware chain, so only the middleware closest to the handler
		# sees that stamp on the dict call_next returns (see identity.py).
		#
		# On the editor address SiteMiddleware reads the signed-in person's
		# EditorSession (auth.py set it) to pick the tenant directory, the gate also
		# checks the token's site against the host's, and ToolAccessMiddleware
		# hides and refuses the tools that person may not use.
		middleware=(
			[
				SiteMiddleware(editor=True),
				TelemetryMiddleware(),
				TenantGateMiddleware(editor=True),
				ToolAccessMiddleware(),
				TenantIdentityMiddleware(),
			]
			if editor
			else [SiteMiddleware(), TelemetryMiddleware(), TenantGateMiddleware(), TenantIdentityMiddleware()]
		),
	)

	server.add_tool(catalog.list_subjects, annotations=READ_ONLY)
	server.add_tool(articles.search_articles, annotations=READ_ONLY)
	server.add_tool(articles.get_article, annotations=READ_ONLY)
	server.add_tool(trials.search_trials, annotations=READ_ONLY)
	server.add_tool(trials.get_trial, annotations=READ_ONLY)
	server.add_tool(authors.search_authors, annotations=READ_ONLY)
	server.add_tool(authors.get_author, annotations=READ_ONLY)
	server.add_tool(catalog.list_categories, annotations=READ_ONLY)
	server.add_tool(catalog.list_sponsors, annotations=READ_ONLY)
	server.add_tool(stats.get_stats, annotations=READ_ONLY)

	if editor:
		server.add_tool(editor_tools.get_article_history, annotations=READ_ONLY)
		server.add_tool(editor_tools.update_article_editorial, annotations=EDIT_DESTRUCTIVE)
		server.add_tool(editor_tools.set_article_relevance, annotations=EDIT_DESTRUCTIVE)
		server.add_tool(editor_tools.link_trial_to_article, annotations=EDIT_ADDITIVE)
		server.add_tool(editor_tools.unlink_trial_from_article, annotations=EDIT_DESTRUCTIVE)
		server.add_tool(editor_tools.create_category, annotations=EDIT_CREATE)
		server.add_tool(editor_tools.update_category, annotations=EDIT_DESTRUCTIVE)
		server.add_tool(editor_tools.assign_article_category, annotations=EDIT_ADDITIVE)
		server.add_tool(editor_tools.unassign_article_category, annotations=EDIT_DESTRUCTIVE)

	_replace_handler(server, "prompts/list", types.PaginatedRequestParams, prompts.list_prompts)
	_replace_handler(server, "prompts/get", types.GetPromptRequestParams, prompts.get_prompt)
	_replace_handler(server, "resources/list", types.PaginatedRequestParams, resources.list_resources)
	_replace_handler(
		server, "resources/templates/list", types.PaginatedRequestParams, resources.list_resource_templates
	)
	_replace_handler(server, "resources/read", types.ReadResourceRequestParams, resources.read_resource)

	return server
