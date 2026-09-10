"""Builds the GregoryAI MCPServer: registers tools, resources, and prompts.

Everything here is read-only. Every tool is annotated
`read_only_hint=True, idempotent_hint=True, open_world_hint=False` since none
of them write, and every one only ever talks to the one Gregory instance
named by `GREGORY_API_URL`.
"""

from __future__ import annotations

from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from .cache import CATALOG_CACHE_TTL_MS
from .prompts import register_prompts
from .resources import register_resources
from .site import SiteMiddleware
from .telemetry import TelemetryMiddleware
from .tools import articles, authors, catalog, stats, trials

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

# Reference-data resources change slowly; let clients cache them for as long as
# the server itself does (CATALOG_CACHE_TTL_MS, see cache.py — one constant,
# so the client-facing hint and the server's actual cache can't drift apart).
#
# resources/list only enumerates the two URIs below (gregory://subjects,
# gregory://categories) — that list is identical for every caller regardless
# of site, so it's safe to share across callers ("public", the only other
# value CacheHint accepts per the 2026-07-28 SEP-2549 caching revision —
# there's no "scoped to one site" option to ask for here).
CATALOG_LIST_CACHE = CacheHint(ttl_ms=CATALOG_CACHE_TTL_MS, scope="public")

# resources/read is different: SiteMiddleware (site.py) makes the actual
# subjects/categories content this returns depend on the resolved site_id
# for whichever request read it — CatalogCache._key() (cache.py) already
# mixes site_id into the server-side cache key so this process never serves
# one site's catalog to a caller resolved to another. But CacheHint is one
# static value per method, chosen once here at server construction, not per
# call — it can't switch between "public" and "private" depending on
# whether *this particular* call resolved a site_id. Advertising it as
# "public" would tell a client/proxy every response is shareable, undoing
# that isolation the moment one exists (site A's catalog handed to site B's
# caller from a shared cache). "private" is the safe choice in both cases a
# single call can land in — it costs a no-site-resolved caller a caching
# optimization it could technically have shared, but a resolved-site caller
# can never leak into another's cache.
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
STATIC_CACHE = CacheHint(ttl_ms=30 * 60 * 1000, scope="public")

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


def build_server() -> MCPServer:
	server = MCPServer(
		name="gregory",
		title="GregoryAI",
		description=(
			"Read-only access to the GregoryAI research database: articles, clinical "
			"trials, authors, subjects, categories, and sponsors."
		),
		version="0.1.0",
		cache_hints=CACHE_HINTS,
		# SiteMiddleware first (outermost): it resolves this request's site_id
		# — env override, else inbound Host via GET /sites/ — before anything
		# else runs, so a cold-cache /sites/ fetch's latency lands outside
		# TelemetryMiddleware's own per-tool-call accounting rather than being
		# smeared into whichever tool call happened to trigger it.
		middleware=[SiteMiddleware(), TelemetryMiddleware()],
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

	register_resources(server)
	register_prompts(server)

	return server
