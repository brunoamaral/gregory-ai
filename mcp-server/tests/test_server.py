from __future__ import annotations

import dataclasses

import httpx2
import pytest
from mcp.client import Client

from gregory_mcp.client import GregoryClient
from gregory_mcp.server import build_server
from gregory_mcp.tenants import init_tenant_resolution
from tests.conftest import TEST_SETTINGS, route_by_path, tenants_payload

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


async def test_server_lists_the_built_in_resource_addresses(mock_gregory):
	"""resources/list and resources/templates/list are registered as raw
	handlers now (resources.py), not the @server.resource() decorator, so
	server.list_resources() -- which reads the SDK's own decorator-populated
	registry, not the JSON-RPC method -- can no longer see them. Drive a
	real request through Client instead. No sponsors resource: ~8,000 rows
	is not catalog-shaped; list_sponsors (search + pagination) is the right
	tool for that data instead."""
	mock_gregory.set_handler(
		route_by_path(
			{"/tenants/": lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, "domain": "br.test"}))}
		)
	)
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))

	async with Client(build_server()) as client:
		resources = await client.list_resources()
		templates = await client.list_resource_templates()

	assert {r.uri for r in resources.resources} == {
		"gregory-ai://subjects",
		"gregory-ai://categories",
		"gregory-ai://about",
	}
	assert {t.uri_template for t in templates.resource_templates} == {"gregory-ai://doc/{slug}"}


async def test_server_lists_the_resolved_tenants_prompts(mock_gregory):
	"""Same reasoning as above: prompts/list is a raw handler reading
	get_current_tenant().prompts now, not a fixed, decorator-registered set
	-- see tests/test_prompts.py for the full prompt-serving suite."""
	mock_gregory.set_handler(
		route_by_path(
			{
				"/tenants/": lambda request: httpx2.Response(
					200,
					json=tenants_payload(
						{
							"site_id": 3,
							"domain": "br.test",
							"prompts": [
								{
									"name": "research_topic",
									"title": "Research a topic",
									"description": "Survey recent work.",
									"template": "Research $topic.",
									"arguments": [{"name": "topic", "description": "", "required": True}],
								}
							],
						}
					),
				)
			}
		)
	)
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))

	async with Client(build_server()) as client:
		result = await client.list_prompts()

	assert {p.name for p in result.prompts} == {"research_topic"}


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


def test_every_cache_hint_is_private():
	"""Decision 3 (MCP-MULTI-TENANCY-PHASE-3-PLAN.md): all five hints are
	"private", not just resources/read as before Phase 3. Every response now
	carries the resolved tenant's own serverInfo stamp
	(TenantIdentityMiddleware, identity.py), and most carry per-tenant
	content or instructions too, so no result is identical for every caller
	any more — a shared cache/proxy could otherwise hand one tenant's
	identity or content to another's caller. This server is always reached
	on one tenant's own hostname, never shared anonymously across tenants,
	so nothing is lost by never advertising "public"."""
	from gregory_mcp.server import CACHE_HINTS as hints

	for method in ("tools/list", "prompts/list", "resources/list", "resources/read", "server/discover"):
		assert hints[method].scope == "private", f"{method} must be private now that identity is per-tenant"


def test_replace_handler_seam_still_exists():
	"""_replace_handler (server.py, task C1) depends on the private SDK
	attribute MCPServer._lowlevel_server.add_request_handler to serve
	prompts/resources per resolved tenant. `mcp` is pinned to `==2.0.0`; an
	upgrade that removes or renames this attribute must fail loudly here,
	not as prompts/resources silently reverting to whatever the SDK's own
	decorator-based defaults would be."""
	from gregory_mcp.server import _replace_handler

	server = build_server()
	assert hasattr(server, "_lowlevel_server")
	assert hasattr(server._lowlevel_server, "add_request_handler")

	calls = []
	_replace_handler(server, "ping", type(None), lambda ctx, params: calls.append(1))
	assert server._lowlevel_server._request_handlers["ping"] is not None


def test_replace_handler_raises_loudly_if_lowlevel_server_disappears():
	from gregory_mcp.server import _replace_handler

	class NoLowlevelServer:
		pass

	with pytest.raises(RuntimeError, match="add_request_handler"):
		_replace_handler(NoLowlevelServer(), "ping", type(None), lambda ctx, params: None)


def test_replace_handler_raises_loudly_if_add_request_handler_disappears():
	"""_lowlevel_server surviving an SDK upgrade doesn't guarantee
	add_request_handler does too -- this must fail with the same actionable
	RuntimeError, not a bare AttributeError, if just that method is renamed
	or removed."""
	from gregory_mcp.server import _replace_handler

	class LowlevelServerWithoutTheMethod:
		pass

	class ServerMissingOnlyTheMethod:
		_lowlevel_server = LowlevelServerWithoutTheMethod()

	with pytest.raises(RuntimeError, match="add_request_handler"):
		_replace_handler(ServerMissingOnlyTheMethod(), "ping", type(None), lambda ctx, params: None)


def test_client_exposes_no_write_methods():
	"""The server issues GET only — assert the client has no write verbs at all."""
	for verb in ("post", "put", "patch", "delete"):
		assert not hasattr(GregoryClient, verb), f"GregoryClient must not expose .{verb}()"
