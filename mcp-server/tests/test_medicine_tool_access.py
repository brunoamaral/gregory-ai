"""Medicines are offered per tenant: `search_medicines` and `get_medicine` are listed
and callable only when the resolved tenant has `medicines_enabled`, on both `/mcp`
and `/mcp/editor`; `set_medicine_link` additionally needs an editor with the edit
scope; and nothing a tenant without medicines sees names them."""

from __future__ import annotations

import dataclasses
import json

import httpx2
import pytest
from mcp.client import Client

from gregory_mcp.identity import _server_info, instructions_for
from gregory_mcp.server import build_server
from gregory_mcp.site_context import EditorSession
from gregory_mcp.tenants import Tenant, _parse_tenant, init_tenant_resolution
from gregory_mcp.tool_access import (
	HISTORY_TOOL,
	MEDICINE_EDIT_TOOL,
	MEDICINE_READ_TOOLS,
	MEDICINE_TOOLS,
	PATENT_TOOLS,
	READ_TOOLS,
	WRITE_TOOLS,
	allowed_tools,
)
from tests.conftest import TEST_SETTINGS, route_by_path, tenants_payload
from tests.editor_helpers import (
	SITE_ID,
	FakeDjango,
	anonymous_client,
	introspection,
	mcp_client,
	new_app,
	running,
	two_tenants,
)

WITH, WITHOUT = {"medicines_enabled": True}, {"medicines_enabled": False}


def _tenant(**overrides) -> Tenant:
	defaults = dict(
		site_id=1,
		domain="example.test",
		name="Example",
		title="Example",
		api_public=True,
		mcp_description="",
		subjects=((1, "Multiple Sclerosis"),),
		prompts=(),
		documents=(),
	)
	return Tenant(**{**defaults, **overrides})


def _editor(*, can_edit: bool = True, tier: str = "editor") -> EditorSession:
	scopes = {"articles:read"} | ({"articles:edit"} if can_edit else set())
	return EditorSession(user_id=7, site_id=1, tier=tier, scopes=frozenset(scopes))


# --- allowed_tools() ----------------------------------------------------------


def test_the_medicine_tools_are_named():
	assert MEDICINE_READ_TOOLS == {"search_medicines", "get_medicine"}
	assert MEDICINE_EDIT_TOOL == "set_medicine_link"
	assert MEDICINE_TOOLS == MEDICINE_READ_TOOLS | {"set_medicine_link"}
	# The ten and the six stay what they were.
	assert not MEDICINE_TOOLS & (READ_TOOLS | WRITE_TOOLS | PATENT_TOOLS | {HISTORY_TOOL})
	assert len(READ_TOOLS) == 10 and len(WRITE_TOOLS) == 6


@pytest.mark.parametrize("tenant", [None, _tenant(), _tenant(medicines_enabled=False)])
def test_no_tenant_or_a_tenant_without_medicines_gets_none(tenant):
	for session in (None, _editor(), _editor(can_edit=False), _editor(tier="public")):
		assert not allowed_tools(session, tenant) & MEDICINE_TOOLS


def test_a_tenant_with_medicines_adds_the_two_read_tools_to_every_tier():
	tenant = _tenant(medicines_enabled=True)
	assert allowed_tools(None, tenant) == READ_TOOLS | MEDICINE_READ_TOOLS
	assert allowed_tools(_editor(tier="public", can_edit=False), tenant) == READ_TOOLS | MEDICINE_READ_TOOLS
	assert allowed_tools(_editor(can_edit=False), tenant) == READ_TOOLS | MEDICINE_READ_TOOLS | {HISTORY_TOOL}


def test_the_link_tool_needs_the_edit_scope_as_well():
	tenant = _tenant(medicines_enabled=True)
	assert allowed_tools(_editor(), tenant) == READ_TOOLS | MEDICINE_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS
	for session in (None, _editor(can_edit=False), _editor(tier="public", can_edit=False)):
		assert MEDICINE_EDIT_TOOL not in allowed_tools(session, tenant)


def test_an_editor_of_a_tenant_without_medicines_gets_exactly_the_old_set():
	assert allowed_tools(_editor(), _tenant()) == READ_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS


def test_patents_and_medicines_are_independent():
	both = _tenant(patents_enabled=True, medicines_enabled=True)
	assert allowed_tools(None, both) == READ_TOOLS | PATENT_TOOLS | MEDICINE_READ_TOOLS
	assert allowed_tools(None, _tenant(patents_enabled=True)) == READ_TOOLS | PATENT_TOOLS
	assert allowed_tools(None, _tenant(medicines_enabled=True)) == READ_TOOLS | MEDICINE_READ_TOOLS


# --- /mcp ---------------------------------------------------------------------


def _anonymous_handler(**tenant):
	return route_by_path(
		{"/tenants/": lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, **tenant}))}
	)


async def _anonymous_tools(mock_gregory, **tenant):
	mock_gregory.set_handler(_anonymous_handler(**tenant))
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))
	async with Client(build_server()) as client:
		return {t.name for t in (await client.list_tools()).tools}


async def test_anonymous_mcp_hides_the_medicine_tools_without_medicines(mock_gregory):
	assert await _anonymous_tools(mock_gregory, **WITHOUT) == READ_TOOLS


async def test_anonymous_mcp_lists_the_two_read_tools_with_medicines(mock_gregory):
	names = await _anonymous_tools(mock_gregory, **WITH)
	assert names == READ_TOOLS | MEDICINE_READ_TOOLS
	assert len(names) == 12


async def test_no_listed_description_points_at_a_hidden_medicine_tool(mock_gregory):
	"""A tenant without medicines must not be sent to a tool it doesn't have, nor
	told about medicines by the tools it does."""
	mock_gregory.set_handler(_anonymous_handler(**WITHOUT))
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))
	async with Client(build_server()) as client:
		listed = (await client.list_tools()).tools
	assert listed
	for tool in listed:
		text = f"{tool.description or ''} {tool.input_schema}"
		assert "medicine" not in text.lower(), f"{tool.name} mentions medicines"


@pytest.mark.parametrize(
	"tool,args",
	[
		("search_medicines", {}),
		("get_medicine", {"medicine_id": 1}),
		("set_medicine_link", {"category_id": 1, "medicine_id": 1, "linked": True}),
	],
)
async def test_calling_a_medicine_tool_without_medicines_is_answered_like_an_unknown_tool(mock_gregory, tool, args):
	mock_gregory.set_handler(_anonymous_handler(**WITHOUT))
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))

	async with Client(build_server()) as client:
		result = await client.call_tool(tool, args)

	assert result.is_error
	assert "Unknown tool" in result.content[0].text
	# Nothing was asked of Django on its behalf.
	assert [r for r in mock_gregory.requests if "medicine" in r.url.path] == []


async def test_calling_a_medicine_tool_with_medicines_reaches_django(mock_gregory):
	mock_gregory.set_handler(
		route_by_path(
			{
				"/tenants/": lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, **WITH})),
				"/medicines/": lambda request: httpx2.Response(
					200, json={"count": 1, "next": None, "results": [{"id": 9, "name": "Ocrevus"}]}
				),
			}
		)
	)
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))

	async with Client(build_server()) as client:
		result = await client.call_tool("search_medicines", {"substance": "ocrelizumab"})

	assert not result.is_error
	body = json.loads(result.content[0].text)
	assert body["medicines"][0]["medicine_id"] == 9
	assert body["source"] == "European Medicines Agency"


async def test_anonymous_mcp_never_offers_the_link_tool_even_with_medicines(mock_gregory):
	"""The write tool is registered only on the editor server, and needs the edit scope."""
	names = await _anonymous_tools(mock_gregory, **WITH)
	assert MEDICINE_EDIT_TOOL not in names


async def test_two_tenants_one_process_see_different_tools(mock_gregory):
	rows = tenants_payload(
		{"site_id": 3, "domain": "with.test", **WITH}, {"site_id": 4, "domain": "without.test", **WITHOUT}
	)
	mock_gregory.set_handler(route_by_path({"/tenants/": lambda request: httpx2.Response(200, json=rows)}))

	seen = {}
	for site_id in (3, 4):
		init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=site_id))
		async with Client(build_server()) as client:
			seen[site_id] = {t.name for t in (await client.list_tools()).tools}

	assert seen[3] == READ_TOOLS | MEDICINE_READ_TOOLS
	assert seen[4] == READ_TOOLS


# --- /mcp/editor --------------------------------------------------------------


def _editor_django(mock_editor_gregory, *, with_medicines: bool, routes=None):
	flag = {"medicines_enabled": with_medicines}
	tenants = [{**t, **flag} for t in two_tenants()]
	fake = FakeDjango(
		editor_tenants=tenants,
		anon_tenants=tenants,
		tokens={
			"editor": introspection(),
			"readonly": introspection(scope="articles:read"),
			"public": introspection(tier="public", scope="articles:read"),
		},
		api_routes=routes or {},
	)
	mock_editor_gregory.set_handler(fake.handler())
	return fake


async def _editor_tools(token):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token=token) as client:
			return {t.name for t in (await client.list_tools()).tools}


@pytest.mark.parametrize(
	"token,expected",
	[
		("public", READ_TOOLS),
		("readonly", READ_TOOLS | {HISTORY_TOOL}),
		("editor", READ_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS),
	],
)
async def test_editor_address_without_medicines_is_unchanged(mock_editor_gregory, token, expected):
	_editor_django(mock_editor_gregory, with_medicines=False)
	assert await _editor_tools(token) == expected


@pytest.mark.parametrize(
	"token,expected",
	[
		("public", READ_TOOLS | MEDICINE_READ_TOOLS),
		("readonly", READ_TOOLS | MEDICINE_READ_TOOLS | {HISTORY_TOOL}),
		("editor", READ_TOOLS | MEDICINE_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS),
	],
)
async def test_editor_address_with_medicines_adds_the_read_tools_and_the_link_tool_for_editors(
	mock_editor_gregory, token, expected
):
	_editor_django(mock_editor_gregory, with_medicines=True)
	assert await _editor_tools(token) == expected


async def test_the_link_tool_is_annotated_like_the_other_two_way_tools(mock_editor_gregory):
	_editor_django(mock_editor_gregory, with_medicines=True)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			tools = {t.name: t for t in (await client.list_tools()).tools}

	for name in ("search_medicines", "get_medicine"):
		assert tools[name].annotations.read_only_hint is True
		assert tools[name].annotations.idempotent_hint is True
		assert tools[name].annotations.open_world_hint is False
	link = tools["set_medicine_link"].annotations
	assert (link.read_only_hint, link.destructive_hint, link.idempotent_hint, link.open_world_hint) == (
		False,
		True,
		True,
		False,
	)
	description = tools["set_medicine_link"].description.lower()
	assert "live immediately" in description
	assert "whole category" in description
	assert "confirmation" in description


@pytest.mark.parametrize("token", ["readonly", "public"])
async def test_a_read_only_sign_in_cannot_call_the_link_tool(mock_editor_gregory, token):
	_editor_django(mock_editor_gregory, with_medicines=True)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token=token) as client:
			result = await client.call_tool(
				"set_medicine_link", {"category_id": 1, "medicine_id": 1, "linked": False}
			)

	assert result.is_error
	assert "Unknown tool" in result.content[0].text
	assert [r for r in mock_editor_gregory.requests if "medicines" in r.url.path] == []


async def test_editor_address_refuses_a_medicine_tool_without_medicines(mock_editor_gregory):
	_editor_django(mock_editor_gregory, with_medicines=False)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			for tool, args in (
				("get_medicine", {"medicine_id": 1}),
				("set_medicine_link", {"category_id": 1, "medicine_id": 1, "linked": False}),
			):
				result = await client.call_tool(tool, args)
				assert result.is_error
				assert "Unknown tool" in result.content[0].text
	assert [r for r in mock_editor_gregory.requests if "medicines" in r.url.path] == []


async def test_an_editors_medicine_search_reads_from_the_editor_route(mock_editor_gregory):
	_editor_django(
		mock_editor_gregory,
		with_medicines=True,
		routes={
			"/editor/medicines/": httpx2.Response(
				200, json={"count": 1, "next": None, "results": [{"id": 9, "name": "Ocrevus"}]}
			)
		},
	)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			result = await client.call_tool("search_medicines", {"substance": "x"})

	assert not result.is_error
	request = next(r for r in mock_editor_gregory.requests if r.url.path == "/editor/medicines/")
	assert request.headers["x-gregory-editor-site"] == str(SITE_ID)


async def test_the_public_tier_reads_medicines_from_the_public_route(mock_editor_gregory):
	_editor_django(
		mock_editor_gregory,
		with_medicines=True,
		routes={"/medicines/": httpx2.Response(200, json={"count": 0, "next": None, "results": []})},
	)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="public") as client:
			await client.call_tool("search_medicines", {})

	assert [r.url.path for r in mock_editor_gregory.requests if "medicines" in r.url.path] == ["/medicines/"]


async def test_anonymous_address_on_the_editor_server_sees_the_same_per_tenant_list(mock_editor_gregory):
	_editor_django(mock_editor_gregory, with_medicines=True)
	app = new_app()
	async with running(app):
		async with anonymous_client(app) as client:
			names = {t.name for t in (await client.list_tools()).tools}
	assert names == READ_TOOLS | MEDICINE_READ_TOOLS


# --- the tenant record --------------------------------------------------------


def _row(**extra):
	row = tenants_payload({"site_id": 3, "domain": "a.test"})[0]
	row.pop("medicines_enabled", None)
	return {**row, **extra}


def test_medicines_enabled_is_false_when_the_directory_does_not_send_it():
	"""An older Django must not break the server."""
	tenant = _parse_tenant(_row())
	assert tenant is not None
	assert tenant.medicines_enabled is False


def test_medicines_enabled_is_read_when_sent():
	assert _parse_tenant(_row(medicines_enabled=True)).medicines_enabled is True
	assert _parse_tenant(_row(medicines_enabled=False)).medicines_enabled is False


@pytest.mark.parametrize("value", ["true", 1, None, [], {}])
def test_a_malformed_flag_means_no_medicines_and_keeps_the_tenant(value):
	tenant = _parse_tenant(_row(medicines_enabled=value))
	assert tenant is not None
	assert tenant.medicines_enabled is False


def test_the_flag_is_independent_of_patents():
	tenant = _parse_tenant(_row(medicines_enabled=True, patents_enabled=False))
	assert (tenant.medicines_enabled, tenant.patents_enabled) == (True, False)


# --- what the tenant is told --------------------------------------------------


def test_instructions_name_medicines_only_for_a_tenant_with_them():
	assert "medicines" in instructions_for(_tenant(medicines_enabled=True)).lower()
	assert "medicine" not in instructions_for(_tenant(medicines_enabled=False)).lower()
	assert "medicine" not in instructions_for(_tenant()).lower()
	assert "EMA" not in instructions_for(_tenant())


def test_the_server_description_names_medicines_only_with_them():
	assert "medicines" in _server_info(_tenant(medicines_enabled=True))["description"]
	assert "medicine" not in _server_info(_tenant())["description"]


def test_instructions_for_a_medicines_tenant_name_the_source_and_the_caveat():
	for session in (None, _editor(), _editor(can_edit=False), _editor(tier="public")):
		text = instructions_for(_tenant(medicines_enabled=True), session)
		assert "European Medicines Agency" in text
		assert "does not mean it is not approved" in text


def test_the_paragraph_count_is_unchanged_by_medicines():
	assert instructions_for(_tenant(medicines_enabled=True)).count("\n\n") == instructions_for(_tenant()).count("\n\n")


def test_an_authored_description_is_left_alone():
	text = instructions_for(_tenant(medicines_enabled=True, mcp_description="Our own words."))
	assert text.split("\n\n")[0] == "Our own words."


@pytest.mark.parametrize("session", [None, _editor(), _editor(can_edit=False), _editor(tier="public")])
def test_no_platform_vocabulary_either_way(session):
	for enabled in (True, False):
		tenant = _tenant(medicines_enabled=enabled)
		for text in (instructions_for(tenant, session), str(_server_info(tenant, session))):
			for banned in ("gregoryai", "instance", "tenant"):
				assert banned not in text.lower()


def test_the_wording_without_medicines_is_exactly_what_it_was():
	text = instructions_for(_tenant(title="Brain Regeneration"))
	assert text.startswith(
		"This is Brain Regeneration's research database: articles, clinical trials and researchers, "
		"curated to the subjects below."
	)
	assert _server_info(_tenant(title="Brain Regeneration"))["description"] == (
		"Read-only access to Brain Regeneration's research database: articles, "
		"clinical trials, authors, subjects, categories and sponsors."
	)


def test_the_records_list_names_each_kind_the_tenant_has():
	assert "articles, clinical trials, EU-authorised medicines and researchers" in instructions_for(
		_tenant(medicines_enabled=True)
	)
	assert "articles, clinical trials, patents, EU-authorised medicines and researchers" in instructions_for(
		_tenant(medicines_enabled=True, patents_enabled=True)
	)
	assert "articles, clinical trials, patents and researchers" in instructions_for(_tenant(patents_enabled=True))


# --- the editor instructions ------------------------------------------------------------------


def test_editor_instructions_list_the_link_tool_only_for_a_site_with_medicines():
	with_medicines = instructions_for(_tenant(medicines_enabled=True), _editor())
	without = instructions_for(_tenant(medicines_enabled=False), _editor())

	assert "set_medicine_link" in with_medicines
	assert "set_medicine_link" not in without
	assert "medicine" not in without.lower()


def test_a_read_only_editor_is_not_told_about_the_link_tool():
	text = instructions_for(_tenant(medicines_enabled=True), _editor(can_edit=False))
	assert "set_medicine_link" not in text


def test_the_wording_for_an_editor_of_a_site_without_medicines_is_exactly_what_it_was():
	text = instructions_for(_tenant(title="Brain Regeneration"), _editor())
	assert (
		"can change its article content with: update_editorial (set an article's takeaways and plain-English "
		"summary for this site), set_article_relevance (mark an article relevant, not relevant or not reviewed "
		"for a subject), set_trial_link (connect an article and a clinical trial, or disconnect them), "
		"create_category and update_category (add a category or change its name, terms or subjects), "
		"set_category_assignment (put an article in a category by hand, or take it out)."
	) in text


def test_the_link_tool_follows_the_other_edit_tools_in_the_same_sentence():
	text = instructions_for(_tenant(medicines_enabled=True), _editor())
	assert (
		"set_category_assignment (put an article in a category by hand, or take it out), "
		"set_medicine_link (put an EMA medicine in a category, or take it out; the change applies to the "
		"whole category)."
	) in text
	assert "Edits are live immediately" in text
	assert "get confirmation before writing" in text


def test_a_site_with_patents_and_medicines_lists_both():
	text = instructions_for(_tenant(patents_enabled=True, medicines_enabled=True), _editor())
	assert "article and patent content" in text
	assert "set_medicine_link" in text
