from __future__ import annotations

from gregory_mcp.client import GregoryClient

EXPECTED_TOOLS = {
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


async def test_server_registers_exactly_the_planned_tools(server):
	tools = await server.list_tools()
	assert {t.name for t in tools} == EXPECTED_TOOLS


async def test_every_tool_is_read_only(server):
	tools = await server.list_tools()
	for tool in tools:
		assert tool.annotations is not None, f"{tool.name} has no annotations"
		assert tool.annotations.read_only_hint is True, f"{tool.name} is not marked read-only"


async def test_server_registers_two_resources(server):
	# No sponsors resource — ~8,000 rows is not catalog-shaped; list_sponsors
	# (search + pagination) is the right tool for that data instead.
	resources = await server.list_resources()
	assert {r.uri for r in resources} == {
		"gregory://subjects",
		"gregory://categories",
	}


async def test_server_registers_three_prompts(server):
	prompts = await server.list_prompts()
	assert {p.name for p in prompts} == {
		"research_topic",
		"recent_trials_for_subject",
		"author_profile",
	}


def test_every_cacheable_method_carries_a_hint():
	"""A missing cache hint means ttlMs=0 — "never cache" — which is the SDK
	default and therefore fails silently. tools/list is ~17 KB of schemas that
	lands in the model's system prompt, so losing this hint quietly reintroduces
	a refetch on every reconnect. Assert the wiring rather than trusting it.

	Not only *list* methods: resources/read and server/discover are cacheable
	too, and are covered here for the same reason.

	Asserts the dict we pass in, not the server: MCPServer exposes no accessor
	for the hints it was constructed with. That the SDK honours them was verified
	on the wire (tools/list -> ttlMs 1800000, scope public).
	"""
	from gregory_mcp.server import CACHE_HINTS as hints

	for method in (
		"tools/list",
		"prompts/list",
		"resources/list",
		"resources/read",
		"server/discover",
	):
		assert method in hints, f"{method} has no cache hint — clients will refetch it every time"
		assert hints[method].ttl_ms > 0, f"{method} hint is ttl_ms=0, same as no hint at all"


def test_hints_for_site_invariant_methods_are_shareable():
	"""Every cacheable method except resources/read returns content that
	never depends on which site (Host/GREGORY_SITE_ID — see site.py) made
	the request: tool/prompt schemas are the same for every caller, and so
	is the *list* of available resource URIs (resources/list). A shared
	cache should be allowed to serve those."""
	from gregory_mcp.server import CACHE_HINTS as hints

	for method in ("tools/list", "prompts/list", "resources/list", "server/discover"):
		assert hints[method].scope == "public", (
			f"{method} is identical for every caller regardless of site, "
			"so a shared cache should be allowed to serve it"
		)


def test_resources_read_hint_is_private_so_a_proxy_cant_leak_across_sites():
	"""resources/read is the exception: SiteMiddleware makes the actual
	subjects/categories content it returns depend on the resolved site_id
	for that call (see CatalogCache._key() in cache.py, which mixes site_id
	into the server-side cache key for exactly this reason). CacheHint has
	no per-call scope — it's one static value for the whole method, chosen
	once here — so it must be "private" (the only other value the SDK's
	CacheHint accepts, per the 2026-07-28 SEP-2549 caching revision):
	advertising "public" would tell a client/proxy that any two callers'
	resources/read responses are interchangeable, letting a shared cache
	hand site A's catalog to site B's caller and undo the server-side
	per-site isolation entirely."""
	from gregory_mcp.server import CACHE_HINTS as hints

	assert hints["resources/read"].scope == "private"


def test_client_exposes_no_write_methods():
	"""The server issues GET only — assert the client has no write verbs at all."""
	for verb in ("post", "put", "patch", "delete"):
		assert not hasattr(GregoryClient, verb), f"GregoryClient must not expose .{verb}()"
