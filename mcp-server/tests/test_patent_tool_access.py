"""Patents are offered per tenant: `search_patents` and `get_patent` are listed
and callable only when the resolved tenant has `patents_enabled`, on both `/mcp`
and `/mcp/editor`, and nothing the tenant sees names patents otherwise."""

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
from gregory_mcp.tool_access import HISTORY_TOOL, PATENT_TOOLS, READ_TOOLS, WRITE_TOOLS, allowed_tools
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

WITH, WITHOUT = {"patents_enabled": True}, {"patents_enabled": False}


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


def test_patent_tools_are_named():
	assert PATENT_TOOLS == {"search_patents", "get_patent"}
	assert not PATENT_TOOLS & READ_TOOLS  # the ten stay the ten


@pytest.mark.parametrize("tenant", [None, _tenant(), _tenant(patents_enabled=False)])
def test_no_tenant_or_a_tenant_without_patents_gets_none(tenant):
	for session in (None, _editor(), _editor(can_edit=False), _editor(tier="public")):
		assert not allowed_tools(session, tenant) & PATENT_TOOLS


def test_a_tenant_with_patents_adds_exactly_the_two_tools():
	tenant = _tenant(patents_enabled=True)
	assert allowed_tools(None, tenant) == READ_TOOLS | PATENT_TOOLS
	assert allowed_tools(_editor(tier="public", can_edit=False), tenant) == READ_TOOLS | PATENT_TOOLS
	assert allowed_tools(_editor(can_edit=False), tenant) == READ_TOOLS | PATENT_TOOLS | {HISTORY_TOOL}
	assert allowed_tools(_editor(), tenant) == READ_TOOLS | PATENT_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS


def test_the_session_alone_still_works_as_before():
	assert allowed_tools(None) == READ_TOOLS
	assert allowed_tools(_editor(can_edit=False)) == READ_TOOLS | {HISTORY_TOOL}


# --- /mcp ---------------------------------------------------------------------


async def _anonymous_tools(mock_gregory, **tenant):
	mock_gregory.set_handler(
		route_by_path(
			{"/tenants/": lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, **tenant}))}
		)
	)
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))
	async with Client(build_server()) as client:
		return client, {t.name for t in (await client.list_tools()).tools}


async def test_anonymous_mcp_hides_the_patent_tools_without_patents(mock_gregory):
	_, names = await _anonymous_tools(mock_gregory, **WITHOUT)
	assert names == READ_TOOLS


async def test_no_listed_description_points_at_a_hidden_patent_tool(mock_gregory):
	"""A tenant without patents must not be sent to a tool it doesn't have: the
	parameters the existing tools gained appear everywhere, but their text must
	not name search_patents or get_patent."""
	mock_gregory.set_handler(
		route_by_path(
			{"/tenants/": lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, **WITHOUT}))}
		)
	)
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))
	async with Client(build_server()) as client:
		listed = (await client.list_tools()).tools
	assert listed
	for tool in listed:
		text = f"{tool.description or ''} {tool.input_schema}"
		for hidden in PATENT_TOOLS:
			assert hidden not in text, f"{tool.name} points at {hidden}"


async def test_anonymous_mcp_lists_the_patent_tools_with_patents(mock_gregory):
	_, names = await _anonymous_tools(mock_gregory, **WITH)
	assert names == READ_TOOLS | PATENT_TOOLS
	assert len(names) == 12


@pytest.mark.parametrize("tool,args", [("search_patents", {}), ("get_patent", {"patent_id": 1})])
async def test_calling_a_patent_tool_without_patents_is_answered_like_an_unknown_tool(mock_gregory, tool, args):
	mock_gregory.set_handler(
		route_by_path(
			{"/tenants/": lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, **WITHOUT}))}
		)
	)
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))

	async with Client(build_server()) as client:
		result = await client.call_tool(tool, args)

	assert result.is_error
	assert "Unknown tool" in result.content[0].text
	# Nothing was asked of Django on its behalf.
	assert [r for r in mock_gregory.requests if r.url.path.startswith("/patents")] == []


async def test_calling_a_patent_tool_with_patents_reaches_django(mock_gregory):
	mock_gregory.set_handler(
		route_by_path(
			{
				"/tenants/": lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, **WITH})),
				"/patents/": lambda request: httpx2.Response(
					200, json={"count": 1, "next": None, "results": [{"patent_id": 9, "title": "T"}]}
				),
			}
		)
	)
	init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=3))

	async with Client(build_server()) as client:
		result = await client.call_tool("search_patents", {"search": "x"})

	assert not result.is_error
	assert json.loads(result.content[0].text)["patents"][0]["patent_id"] == 9


async def test_two_tenants_one_process_see_different_tools(mock_gregory):
	"""The tool list is per tenant, not per process."""
	rows = tenants_payload(
		{"site_id": 3, "domain": "with.test", **WITH}, {"site_id": 4, "domain": "without.test", **WITHOUT}
	)
	mock_gregory.set_handler(route_by_path({"/tenants/": lambda request: httpx2.Response(200, json=rows)}))

	seen = {}
	for site_id in (3, 4):
		init_tenant_resolution(dataclasses.replace(TEST_SETTINGS, site_id_override=site_id))
		async with Client(build_server()) as client:
			seen[site_id] = {t.name for t in (await client.list_tools()).tools}

	assert seen[3] == READ_TOOLS | PATENT_TOOLS
	assert seen[4] == READ_TOOLS


# --- /mcp/editor --------------------------------------------------------------


def _editor_django(mock_editor_gregory, *, with_patents: bool, routes=None):
	flag = {"patents_enabled": with_patents}
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
async def test_editor_address_without_patents_is_unchanged(mock_editor_gregory, token, expected):
	_editor_django(mock_editor_gregory, with_patents=False)
	assert await _editor_tools(token) == expected


@pytest.mark.parametrize(
	"token,expected",
	[
		("public", READ_TOOLS | PATENT_TOOLS),
		("readonly", READ_TOOLS | PATENT_TOOLS | {HISTORY_TOOL}),
		("editor", READ_TOOLS | PATENT_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS),
	],
)
async def test_editor_address_with_patents_adds_the_two_read_tools_to_every_tier(mock_editor_gregory, token, expected):
	_editor_django(mock_editor_gregory, with_patents=True)
	assert await _editor_tools(token) == expected


async def test_editor_address_refuses_a_patent_tool_without_patents(mock_editor_gregory):
	_editor_django(mock_editor_gregory, with_patents=False)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			result = await client.call_tool("get_patent", {"patent_id": 1})

	assert result.is_error
	assert "Unknown tool" in result.content[0].text
	assert [r for r in mock_editor_gregory.requests if "patents" in r.url.path] == []


async def test_an_editors_patent_search_reads_from_the_editor_route(mock_editor_gregory):
	_editor_django(
		mock_editor_gregory,
		with_patents=True,
		routes={
			"/editor/patents/": httpx2.Response(
				200, json={"count": 1, "next": None, "results": [{"patent_id": 9, "title": "T"}]}
			)
		},
	)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			result = await client.call_tool("search_patents", {"search": "x"})

	assert not result.is_error
	request = next(r for r in mock_editor_gregory.requests if r.url.path == "/editor/patents/")
	assert request.headers["x-gregory-editor-site"] == str(SITE_ID)


async def test_the_public_tier_reads_patents_from_the_public_route(mock_editor_gregory):
	_editor_django(
		mock_editor_gregory,
		with_patents=True,
		routes={"/patents/": httpx2.Response(200, json={"count": 0, "next": None, "results": []})},
	)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="public") as client:
			await client.call_tool("search_patents", {})

	assert [r.url.path for r in mock_editor_gregory.requests if "patents" in r.url.path] == ["/patents/"]


async def test_anonymous_address_on_the_editor_server_sees_the_same_per_tenant_list(mock_editor_gregory):
	_editor_django(mock_editor_gregory, with_patents=True)
	app = new_app()
	async with running(app):
		async with anonymous_client(app) as client:
			names = {t.name for t in (await client.list_tools()).tools}
	assert names == READ_TOOLS | PATENT_TOOLS


# --- the tenant record --------------------------------------------------------


def _row(**extra):
	row = tenants_payload({"site_id": 3, "domain": "a.test"})[0]
	row.pop("patents_enabled", None)
	return {**row, **extra}


def test_patents_enabled_is_false_when_the_directory_does_not_send_it():
	"""An older Django must not break the server."""
	tenant = _parse_tenant(_row())
	assert tenant is not None
	assert tenant.patents_enabled is False


def test_patents_enabled_is_read_when_sent():
	assert _parse_tenant(_row(patents_enabled=True)).patents_enabled is True
	assert _parse_tenant(_row(patents_enabled=False)).patents_enabled is False


@pytest.mark.parametrize("value", ["true", 1, None, [], {}])
def test_a_malformed_flag_means_no_patents_and_keeps_the_tenant(value):
	tenant = _parse_tenant(_row(patents_enabled=value))
	assert tenant is not None
	assert tenant.patents_enabled is False


# --- what the tenant is told --------------------------------------------------


def test_instructions_name_patents_only_for_a_tenant_with_them():
	assert "patents" in instructions_for(_tenant(patents_enabled=True)).lower()
	assert "patent" not in instructions_for(_tenant(patents_enabled=False)).lower()
	assert "patent" not in instructions_for(_tenant()).lower()


def test_the_server_description_names_patents_only_with_them():
	assert "patents" in _server_info(_tenant(patents_enabled=True))["description"]
	assert "patent" not in _server_info(_tenant())["description"]


def test_an_authored_description_is_left_alone():
	text = instructions_for(_tenant(patents_enabled=True, mcp_description="Our own words."))
	assert text.split("\n\n")[0] == "Our own words."


@pytest.mark.parametrize("session", [None, _editor(), _editor(can_edit=False), _editor(tier="public")])
def test_no_platform_vocabulary_either_way(session):
	for enabled in (True, False):
		tenant = _tenant(patents_enabled=enabled)
		for text in (instructions_for(tenant, session), str(_server_info(tenant, session))):
			for banned in ("gregoryai", "instance", "tenant"):
				assert banned not in text.lower()


def test_the_wording_without_patents_is_exactly_what_it_was():
	text = instructions_for(_tenant(title="Brain Regeneration"))
	assert text.startswith(
		"This is Brain Regeneration's research database: articles, clinical trials and researchers, "
		"curated to the subjects below."
	)
	assert _server_info(_tenant(title="Brain Regeneration"))["description"] == (
		"Read-only access to Brain Regeneration's research database: articles, "
		"clinical trials, authors, subjects, categories and sponsors."
	)


# --- the editor instructions ------------------------------------------------------------------


def test_editor_instructions_name_patents_only_for_a_site_with_them():
	with_patents = instructions_for(_tenant(patents_enabled=True), _editor())
	without = instructions_for(_tenant(patents_enabled=False), _editor())

	assert "patent" in with_patents.lower()
	assert "patent" not in without.lower()
	assert "article and patent content" in with_patents
	assert "article content" in without


def test_the_wording_for_an_editor_of_a_site_without_patents_is_exactly_what_it_was():
	text = instructions_for(_tenant(title="Brain Regeneration"), _editor())
	assert (
		"can change its article content with: update_editorial (set an article's takeaways and plain-English "
		"summary for this site), set_article_relevance (mark an article relevant, not relevant or not reviewed "
		"for a subject), set_trial_link (connect an article and a clinical trial, or disconnect them), "
		"create_category and update_category (add a category or change its name, terms or subjects), "
		"set_category_assignment (put an article in a category by hand, or take it out)."
	) in text
	assert "get_history shows who changed an article's content, relevance and trial links, and when." in text


def test_editor_instructions_for_patents_name_every_write_tool_and_the_link_rule():
	text = instructions_for(_tenant(patents_enabled=True), _editor())

	for name in WRITE_TOOLS | {HISTORY_TOOL}:
		assert name in text, name
	# The model is told, up front, that a patent's link can be hidden or restored but not made.
	assert "hide a link to a trial or restore a hidden one" in text
	assert "only detection creates those" in text
	assert "confirm" in text.lower()


def test_read_only_editor_instructions_name_patents_only_with_them():
	assert "patent" in instructions_for(_tenant(patents_enabled=True), _editor(can_edit=False)).lower()
	assert "patent" not in instructions_for(_tenant(), _editor(can_edit=False)).lower()
	# A read-only editor is not pointed at write tools either way.
	for enabled in (True, False):
		text = instructions_for(_tenant(patents_enabled=enabled), _editor(can_edit=False))
		for name in WRITE_TOOLS:
			assert name not in text


def test_the_public_tier_never_hears_about_editing_patents():
	for enabled in (True, False):
		text = instructions_for(_tenant(patents_enabled=enabled), _editor(tier="public", can_edit=False))
		assert "set_trial_link" not in text
		assert "hand" not in text.lower()
