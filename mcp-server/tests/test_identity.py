"""Tests for gregory_mcp/identity.py: instructions_for() and
TenantIdentityMiddleware. See MCP-MULTI-TENANCY-PHASE-3-PLAN.md, task B6.

Two tenants are driven through one real build_server() instance (the
GREGORY_SITE_ID override switched between connections) rather than mocked
in isolation, so these prove the production composition delivers per-tenant
identity -- not just that instructions_for() computes the right string.
"""

from __future__ import annotations

import dataclasses

import httpx2
from mcp.client import Client

from gregory_mcp.identity import SERVER_VERSION, instructions_for
from gregory_mcp.server import build_server
from gregory_mcp.tenants import Tenant, init_tenant_resolution
from tests.conftest import TEST_SETTINGS, route_by_path, tenants_payload

_BANNED_WORDS = ("gregoryai", "instance", "tenant")


def _tenants_route(*tenants):
	return route_by_path(
		{"/tenants/": lambda request: httpx2.Response(200, json=tenants_payload(*tenants))}
	)


# --- instructions_for() ------------------------------------------------


def _make_tenant(**overrides) -> Tenant:
	defaults = dict(
		site_id=1,
		domain="example.test",
		name="Example",
		title="Example",
		api_public=True,
		mcp_description="",
		subjects=((1, "Multiple Sclerosis"), (2, "Alzheimer's Disease")),
		prompts=(),
		documents=(),
	)
	return Tenant(**{**defaults, **overrides})


def test_blank_mcp_description_generates_an_introduction():
	tenant = _make_tenant(title="Brain Regeneration", mcp_description="")
	text = instructions_for(tenant)
	assert text.startswith("This is Brain Regeneration's research database")


def test_mcp_description_replaces_the_first_paragraph():
	tenant = _make_tenant(title="Brain Regeneration", mcp_description="  A custom introduction.  ")
	text = instructions_for(tenant)
	paragraphs = text.split("\n\n")
	assert paragraphs[0] == "A custom introduction."


def test_subject_names_appear_in_order():
	tenant = _make_tenant(subjects=((1, "Multiple Sclerosis"), (2, "Alzheimer's Disease")))
	text = instructions_for(tenant)
	assert "Subjects covered: Multiple Sclerosis, Alzheimer's Disease." in text


def test_behaviour_paragraph_is_present():
	tenant = _make_tenant()
	text = instructions_for(tenant)
	assert "read-only" in text.lower()
	assert "not found" in text.lower()


def test_instructions_omit_the_subjects_paragraph_when_there_are_none():
	"""Only reachable when every subject row failed to parse — the API
	lists no tenant with an empty scope. Better a missing paragraph than
	"Subjects covered: ." in a model's system prompt."""
	text = instructions_for(_make_tenant(subjects=()))

	assert "Subjects covered" not in text
	assert text.count("\n\n") == 1  # intro, then the read-only paragraph


def test_instructions_contain_no_banned_words():
	tenant = _make_tenant(
		title="Brain Regeneration",
		mcp_description="",
		subjects=((1, "Multiple Sclerosis"),),
	)
	text = instructions_for(tenant).lower()
	for banned in _BANNED_WORDS:
		assert banned not in text


# --- TenantIdentityMiddleware, end to end -------------------------------


async def test_two_tenants_on_one_server_get_their_own_identity_modern(mock_gregory):
	mock_gregory.set_handler(
		_tenants_route(
			{"site_id": 3, "domain": "brain-regeneration.com", "title": "Brain Regeneration", "mcp_description": ""},
			{"site_id": 7, "domain": "encefalites.pt", "title": "Encefalites", "mcp_description": "Custom intro."},
		)
	)
	server = build_server()

	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))
	async with Client(server, mode="auto") as client_a:
		assert client_a.server_info.name == "brain-regeneration.com"
		assert client_a.server_info.title == "Brain Regeneration"
		assert client_a.instructions.startswith("This is Brain Regeneration's research database")

	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=7))
	async with Client(server, mode="auto") as client_b:
		assert client_b.server_info.name == "encefalites.pt"
		assert client_b.server_info.title == "Encefalites"
		assert client_b.instructions.startswith("Custom intro.")


async def test_two_tenants_on_one_server_get_their_own_identity_legacy(mock_gregory):
	mock_gregory.set_handler(
		_tenants_route(
			{"site_id": 3, "domain": "brain-regeneration.com", "title": "Brain Regeneration", "mcp_description": ""},
			{"site_id": 7, "domain": "encefalites.pt", "title": "Encefalites", "mcp_description": "Custom intro."},
		)
	)
	server = build_server()

	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))
	async with Client(server, mode="legacy") as client_a:
		assert client_a.server_info.name == "brain-regeneration.com"
		assert client_a.server_info.title == "Brain Regeneration"
		assert client_a.instructions.startswith("This is Brain Regeneration's research database")

	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=7))
	async with Client(server, mode="legacy") as client_b:
		assert client_b.server_info.name == "encefalites.pt"
		assert client_b.server_info.title == "Encefalites"
		assert client_b.instructions.startswith("Custom intro.")


async def test_tools_list_carries_the_current_tenants_meta_stamp(mock_gregory):
	import mcp_types as types

	mock_gregory.set_handler(
		_tenants_route({"site_id": 3, "domain": "brain-regeneration.com", "title": "Brain Regeneration"})
	)
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))

	async with Client(build_server(), mode="auto") as client:
		result = await client.list_tools()

	assert result.meta is not None
	stamp = result.meta[types.SERVER_INFO_META_KEY]
	assert stamp["name"] == "brain-regeneration.com"
	assert stamp["title"] == "Brain Regeneration"
	assert stamp["version"] == SERVER_VERSION


async def test_middleware_leaves_the_result_untouched_with_no_resolved_tenant():
	"""Defence in depth: TenantGateMiddleware already refuses everything but
	ping/notifications when no tenant resolved, but this middleware must
	still do nothing on its own if it ever ran with none set -- e.g. a
	future ping result that happened to carry a stamped _meta."""
	from mcp.server.context import ServerRequestContext

	from gregory_mcp.identity import TenantIdentityMiddleware
	from gregory_mcp.site_context import _current_tenant

	assert _current_tenant.get() is None  # nothing resolved in this test
	stamped = {"_meta": {"io.modelcontextprotocol/serverInfo": {"name": "gregory-ai"}}}

	async def call_next(ctx):
		return stamped

	ctx = ServerRequestContext(
		session=None,
		lifespan_context={},
		protocol_version="2026-07-28",
		method="ping",
		params={},
		request_id="req-1",
		request=None,
	)
	result = await TenantIdentityMiddleware()(ctx, call_next)

	assert result is stamped
	assert result["_meta"]["io.modelcontextprotocol/serverInfo"] == {"name": "gregory-ai"}


async def test_neutral_defaults_name_no_platform():
	"""build_server()'s own construction-time identity (never seen by a
	client on a real tenant hostname, since TenantGateMiddleware refuses
	everything else) must still name no platform, per decision F."""
	server = build_server()
	assert server.name == "gregory-ai"
	assert "gregoryai" not in server.title.lower().replace(" ", "")
	stamp = server._lowlevel_server.server_info_stamp
	assert "gregoryai" not in stamp["description"].lower().replace(" ", "")
