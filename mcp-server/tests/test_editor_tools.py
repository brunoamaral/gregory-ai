"""The editor address end to end: tool visibility per tier, the write tools, the
routing to Django's `/editor/` routes, and that `/mcp` is untouched.
MCP-AUTH-PLAN.md, "Editor tools" and "Access tiers"."""

from __future__ import annotations

import json
import logging

import httpx2
import pytest
from mcp.shared.exceptions import MCPError

import gregory_mcp.tools.editor as editor
from gregory_mcp.client import GregoryAPIError

from gregory_mcp.tool_access import HISTORY_TOOL, READ_TOOLS, WRITE_TOOLS
from tests.editor_helpers import (
	OTHER_HOST,
	SERVICE_KEY,
	SITE_ID,
	FakeDjango,
	anonymous_client,
	introspection,
	mcp_client,
	new_app,
	running,
)

STORED = {
	"article_id": 11,
	"takeaways": "New takeaways",
	"summary_plain_english": "",
	"last_change": {"by": "ana", "at": "2026-01-01T00:00:00Z", "via": "mcp"},
}


def _editor_routes():
	return {
		"/editor/articles/resolve/": lambda r: (
			httpx2.Response(200, json={"article_id": 11})
			if r.url.params.get("doi") == "10.1/ok"
			else httpx2.Response(
				409 if r.url.params.get("doi") == "10.1/dup" else 404,
				json={"article_ids": [11, 12]} if r.url.params.get("doi") == "10.1/dup" else {"detail": "no"},
			)
		),
		"/editor/articles/11/editorial/": httpx2.Response(200, json=STORED),
		"/editor/articles/11/relevance/1/": httpx2.Response(200, json={"article_id": 11, "subject_id": 1, "is_relevant": True}),
		"/editor/articles/11/trials/": httpx2.Response(201, json={"article_id": 11, "trial_id": 5, "source": "editor"}),
		"/editor/articles/11/trials/5/": httpx2.Response(200, json={"article_id": 11, "trial_id": 5, "removed": "deleted"}),
		"/editor/articles/11/history/": httpx2.Response(200, json={"results": []}),
		"/editor/categories/": lambda r: (
			httpx2.Response(
				409,
				json={"detail": "A category with the slug taken already exists.", "category_id": 102},
			)
			if json.loads(r.content).get("category_slug") == "taken"
			else httpx2.Response(201, json={"id": 140, **json.loads(r.content)})
		),
		"/editor/categories/140/": httpx2.Response(200, json={"id": 140, "category_terms": ["apotransferrin"]}),
		"/editor/categories/141/": httpx2.Response(
			403,
			json={"detail": "This category also covers subjects outside this site, so it can only be changed in the admin."},
		),
		"/editor/articles/11/categories/140/": lambda r: httpx2.Response(
			201 if r.method == "PUT" else 200,
			json={"article_id": 11, "category_id": 140, "source": "manual"},
		),
		"/editor/articles/11/categories/141/": httpx2.Response(
			409, json={"detail": "The pipeline assigned this article from the category's terms."}
		),
		"/editor/articles/404/editorial/": httpx2.Response(404, json={"detail": "nope"}),
		"/editor/articles/405/editorial/": httpx2.Response(403, json={"detail": "nope"}),
		"/editor/articles/429/editorial/": httpx2.Response(429, json={"detail": "slow"}, headers={"Retry-After": "30"}),
		"/editor/articles/400/editorial/": httpx2.Response(400, json={"takeaways": ["too long"]}),
		"/editor/articles/500/editorial/": httpx2.Response(500, text="boom"),
	}


@pytest.fixture
def django(mock_editor_gregory):
	fake = FakeDjango(
		tokens={
			"editor": introspection(),
			"readonly": introspection(scope="articles:read"),
			"public": introspection(tier="public", scope="articles:read"),
		},
		api_routes=_editor_routes(),
	)
	mock_editor_gregory.set_handler(fake.handler())
	return fake


async def _tool_names(token):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token=token) as client:
			return {t.name for t in (await client.list_tools()).tools}


async def test_editor_with_edit_scope_sees_every_tool(django):
	assert await _tool_names("editor") == READ_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS


async def test_tool_counts_per_tier(django):
	"""The merge keeps the editor list short: 10 for the public tier, 11 with
	history, 17 with the six write tools."""
	assert len(await _tool_names("public")) == 10
	assert len(await _tool_names("readonly")) == 11
	assert len(await _tool_names("editor")) == 17
	assert len(WRITE_TOOLS) == 6


async def test_the_old_tool_names_are_gone_with_no_aliases(django):
	names = await _tool_names("editor")
	for old in (
		"get_article_history",
		"update_article_editorial",
		"link_trial_to_article",
		"unlink_trial_from_article",
		"assign_article_category",
		"unassign_article_category",
	):
		assert old not in names


async def test_read_only_editor_sees_reads_and_history_but_no_writes(django):
	assert await _tool_names("readonly") == READ_TOOLS | {HISTORY_TOOL}


async def test_public_tier_sees_exactly_the_anonymous_tools(django):
	assert await _tool_names("public") == READ_TOOLS


async def test_anonymous_mcp_is_unchanged_and_has_no_editor_tools(django):
	app = new_app()
	async with running(app):
		async with anonymous_client(app) as client:
			names = {t.name for t in (await client.list_tools()).tools}

	assert names == READ_TOOLS


async def test_write_tools_are_annotated_and_never_read_only(django):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			tools = {t.name: t for t in (await client.list_tools()).tools}

	assert tools["get_history"].annotations.read_only_hint is True
	for name in WRITE_TOOLS:
		assert tools[name].annotations.read_only_hint is False
		assert tools[name].annotations.idempotent_hint is (name != "create_category")
		# Every write tool can remove or overwrite, so all are destructive; only
		# creating a category, which just adds, is not.
		assert tools[name].annotations.destructive_hint is (name != "create_category"), name
	assert tools["update_editorial"].annotations.destructive_hint is True
	assert tools["set_trial_link"].annotations.destructive_hint is True
	assert tools["set_category_assignment"].annotations.destructive_hint is True
	assert tools["set_article_relevance"].annotations.destructive_hint is True
	assert tools["update_category"].annotations.destructive_hint is True
	assert tools["create_category"].annotations.destructive_hint is False


# Every write tool, with a valid call for each direction of the two-way ones.
_WRITE_CALLS = [
	("update_editorial", {"article_id": 11, "takeaways": "x"}),
	("set_article_relevance", {"article_id": 11, "subject_id": 1, "is_relevant": True}),
	("set_trial_link", {"article_id": 11, "trial_id": 5, "linked": True}),
	("set_trial_link", {"article_id": 11, "trial_id": 5, "linked": False}),
	("create_category", {"category_name": "X", "subject_ids": [1], "category_terms": ["x"]}),
	("update_category", {"category_id": 140, "add_terms": ["x"]}),
	("set_category_assignment", {"article_id": 11, "category_id": 140, "assigned": True}),
	("set_category_assignment", {"article_id": 11, "category_id": 140, "assigned": False}),
]


def test_the_hidden_call_matrix_covers_every_write_tool():
	assert {tool for tool, _ in _WRITE_CALLS} == WRITE_TOOLS


@pytest.mark.parametrize("token", ["readonly", "public"])
@pytest.mark.parametrize("tool,args", _WRITE_CALLS, ids=[f"{t}-{i}" for i, (t, _) in enumerate(_WRITE_CALLS)])
async def test_calling_a_hidden_write_tool_is_refused_and_writes_nothing(django, mock_editor_gregory, token, tool, args):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token=token) as client:
			result = await client.call_tool(tool, args)

	assert result.is_error
	assert "Unknown tool" in result.content[0].text
	assert [r for r in mock_editor_gregory.requests if r.method != "GET" and r.url.path != "/o/introspect/"] == []


async def test_history_is_hidden_from_the_public_tier(django):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="public") as client:
			result = await client.call_tool("get_history", {"article_id": 11})

	assert result.is_error


# --- writes -------------------------------------------------------------------


async def _call(tool, args, token="editor"):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token=token) as client:
			return await client.call_tool(tool, args)


def _writes(mock):
	return [r for r in mock.requests if r.url.path.startswith("/editor/") and r.method != "GET"]


async def test_update_editorial_patches_as_the_signed_in_person(django, mock_editor_gregory):
	result = await _call("update_editorial", {"article_id": 11, "takeaways": "New takeaways"})

	assert not result.is_error
	assert json.loads(result.content[0].text)["last_change"]["via"] == "mcp"
	(request,) = _writes(mock_editor_gregory)
	assert request.method == "PATCH"
	assert request.url.path == "/editor/articles/11/editorial/"
	assert request.headers["authorization"] == f"Bearer {SERVICE_KEY}"
	assert request.headers["x-gregory-editor-user"] == "7"
	assert request.headers["x-gregory-editor-site"] == str(SITE_ID)
	assert request.content == b'{"takeaways":"New takeaways"}'


async def test_only_the_fields_given_are_sent_and_empty_string_clears(django, mock_editor_gregory):
	await _call("update_editorial", {"article_id": 11, "summary_plain_english": ""})

	(request,) = _writes(mock_editor_gregory)
	assert request.content == b'{"summary_plain_english":""}'


async def test_update_with_no_fields_is_a_tool_error_and_sends_nothing(django, mock_editor_gregory):
	result = await _call("update_editorial", {"article_id": 11})

	assert result.is_error
	assert "takeaways" in result.content[0].text
	assert _writes(mock_editor_gregory) == []


async def test_doi_is_resolved_on_the_editor_route(django, mock_editor_gregory):
	result = await _call("update_editorial", {"doi": "10.1/ok", "takeaways": "x"})

	assert not result.is_error
	resolves = [r for r in mock_editor_gregory.requests if r.url.path == "/editor/articles/resolve/"]
	assert len(resolves) == 1
	assert resolves[0].headers["x-gregory-editor-user"] == "7"
	assert _writes(mock_editor_gregory)[0].url.path == "/editor/articles/11/editorial/"


async def test_unknown_doi_is_a_readable_tool_error(django):
	result = await _call("update_editorial", {"doi": "10.1/none", "takeaways": "x"})

	assert result.is_error
	assert "10.1/none" in result.content[0].text
	assert "not found" in result.content[0].text.lower() or "No article" in result.content[0].text


async def test_ambiguous_doi_lists_the_candidates(django):
	result = await _call("update_editorial", {"doi": "10.1/dup", "takeaways": "x"})

	assert result.is_error
	assert "11, 12" in result.content[0].text
	assert "article_id" in result.content[0].text


@pytest.mark.parametrize("args", [{}, {"article_id": 11, "doi": "10.1/ok"}])
async def test_both_or_neither_article_identifier_is_invalid_params(django, args):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			with pytest.raises(MCPError) as caught:
				await client.call_tool("update_editorial", {**args, "takeaways": "x"})

	assert "exactly one" in str(caught.value).lower()


@pytest.mark.parametrize(
	("article_id", "expected"),
	[
		(404, "was not found on this site"),
		(405, "can no longer edit"),
		(429, "30 seconds"),
		(400, "too long"),
		(500, "status 500"),
	],
)
async def test_upstream_failures_become_messages_the_model_can_read(django, article_id, expected):
	result = await _call("update_editorial", {"article_id": article_id, "takeaways": "x"})

	assert result.is_error
	assert expected in result.content[0].text
	# The detail follows the SDK's prefix; the bare "Error executing tool <name>"
	# is what any non-ToolError exception would have left the model with.
	assert result.content[0].text.strip() != "Error executing tool update_editorial"


async def test_a_failed_write_is_not_retried(django, mock_editor_gregory):
	await _call("update_editorial", {"article_id": 500, "takeaways": "x"})

	assert len([r for r in mock_editor_gregory.requests if r.url.path == "/editor/articles/500/editorial/"]) == 1


async def test_set_relevance_puts_null_for_not_reviewed(django, mock_editor_gregory):
	result = await _call("set_article_relevance", {"article_id": 11, "subject_id": 1, "is_relevant": None})

	assert not result.is_error
	(request,) = _writes(mock_editor_gregory)
	assert request.method == "PUT"
	assert request.url.path == "/editor/articles/11/relevance/1/"
	assert request.content == b'{"is_relevant":null}'


async def test_link_and_unlink_trial(django, mock_editor_gregory):
	linked = await _call("set_trial_link", {"article_id": 11, "trial_id": 5, "linked": True})
	unlinked = await _call("set_trial_link", {"article_id": 11, "trial_id": 5, "linked": False})

	assert not linked.is_error and not unlinked.is_error
	post, delete = _writes(mock_editor_gregory)
	assert (post.method, post.url.path, post.content) == ("POST", "/editor/articles/11/trials/", b'{"trial_id":5}')
	assert (delete.method, delete.url.path) == ("DELETE", "/editor/articles/11/trials/5/")


async def test_create_category_posts_only_what_was_given(django, mock_editor_gregory):
	result = await _call(
		"create_category",
		{
			"category_name": "Apotransferrin",
			"subject_ids": [1],
			"category_terms": ["apotransferrin", "apo-transferrin"],
			"modality": "biologic_antibody",
		},
	)

	assert not result.is_error
	(request,) = _writes(mock_editor_gregory)
	assert (request.method, request.url.path) == ("POST", "/editor/categories/")
	assert json.loads(request.content) == {
		"category_name": "Apotransferrin",
		"subject_ids": [1],
		"category_terms": ["apotransferrin", "apo-transferrin"],
		"category_type": "automatic",
		"match_scope": "title_summary",
		"modality": "biologic_antibody",
	}


async def test_create_category_sends_exclude_terms_only_when_given(django, mock_editor_gregory):
	result = await _call(
		"create_category",
		{
			"category_name": "Apotransferrin",
			"subject_ids": [1],
			"category_terms": ["apotransferrin"],
			"category_exclude_terms": ["mouse"],
		},
	)

	assert not result.is_error
	(request,) = _writes(mock_editor_gregory)
	assert json.loads(request.content)["category_exclude_terms"] == ["mouse"]


async def test_a_taken_slug_names_the_existing_category(django):
	result = await _call(
		"create_category",
		{"category_name": "Taken", "subject_ids": [1], "category_terms": ["x"], "category_slug": "taken"},
	)

	assert result.is_error
	assert "already exists" in result.content[0].text
	assert "category 102" in result.content[0].text


async def test_update_category_patches_only_what_was_given(django, mock_editor_gregory):
	result = await _call("update_category", {"category_id": 140, "remove_terms": ["3'-deoxyadenosine"]})

	assert not result.is_error
	(request,) = _writes(mock_editor_gregory)
	assert (request.method, request.url.path) == ("PATCH", "/editor/categories/140/")
	assert json.loads(request.content) == {"remove_terms": ["3'-deoxyadenosine"]}


async def test_update_category_sends_exclude_term_edits(django, mock_editor_gregory):
	result = await _call(
		"update_category",
		{"category_id": 140, "add_exclude_terms": ["mouse"], "remove_exclude_terms": ["rat"]},
	)

	assert not result.is_error
	(request,) = _writes(mock_editor_gregory)
	assert json.loads(request.content) == {"add_exclude_terms": ["mouse"], "remove_exclude_terms": ["rat"]}


async def test_update_category_with_nothing_to_change_sends_nothing(django, mock_editor_gregory):
	result = await _call("update_category", {"category_id": 140})

	assert result.is_error
	assert "at least one field" in result.content[0].text
	assert _writes(mock_editor_gregory) == []


async def test_a_category_shared_with_another_site_is_a_readable_refusal(django):
	result = await _call("update_category", {"category_id": 141, "category_name": "x"})

	assert result.is_error
	assert "only be changed in the admin" in result.content[0].text


async def test_assign_and_unassign_article_category(django, mock_editor_gregory):
	assigned = await _call("set_category_assignment", {"article_id": 11, "category_id": 140, "assigned": True})
	unassigned = await _call("set_category_assignment", {"doi": "10.1/ok", "category_id": 140, "assigned": False})

	assert not assigned.is_error and not unassigned.is_error
	put, delete = _writes(mock_editor_gregory)
	assert (put.method, put.url.path) == ("PUT", "/editor/articles/11/categories/140/")
	assert (delete.method, delete.url.path) == ("DELETE", "/editor/articles/11/categories/140/")


async def test_unassigning_a_pipeline_match_explains_why_not(django):
	result = await _call("set_category_assignment", {"article_id": 11, "category_id": 141, "assigned": False})

	assert result.is_error
	assert "The edit was refused" in result.content[0].text
	assert "category's terms" in result.content[0].text


async def test_history_reads_with_a_clamped_limit(django, mock_editor_gregory):
	result = await _call("get_history", {"article_id": 11, "limit": 9999})

	assert not result.is_error
	(request,) = [r for r in mock_editor_gregory.requests if r.url.path == "/editor/articles/11/history/"]
	assert request.url.params["limit"] == "200"


async def test_no_write_tool_takes_a_site(django):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			tools = (await client.list_tools()).tools

	for tool in tools:
		if tool.name in WRITE_TOOLS or tool.name == HISTORY_TOOL:
			assert "site_id" not in tool.input_schema["properties"]
			assert "site" not in tool.input_schema["properties"]


# --- routing and isolation ----------------------------------------------------


async def test_reads_on_the_editor_address_go_to_the_editor_routes_as_the_person(django, mock_editor_gregory):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			await client.call_tool("list_subjects", {})

	reads = [r for r in mock_editor_gregory.requests if r.url.path.startswith("/editor/") and r.url.path != "/editor/tenants/"]
	assert reads
	assert all(r.headers["x-gregory-editor-user"] == "7" for r in reads)
	assert all(r.headers["x-gregory-editor-site"] == str(SITE_ID) for r in reads)


async def test_the_public_tier_reads_without_the_editor_headers(django, mock_editor_gregory):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="public") as client:
			await client.call_tool("list_subjects", {})

	after_auth = [r for r in mock_editor_gregory.requests if r.url.path not in ("/o/introspect/", "/editor/tenants/", "/tenants/")]
	assert all("x-gregory-editor-user" not in r.headers for r in after_auth)


async def test_anonymous_requests_never_carry_the_service_key(django, mock_editor_gregory):
	app = new_app()
	async with running(app):
		async with anonymous_client(app) as client:
			await client.call_tool("list_subjects", {})

	for request in mock_editor_gregory.requests:
		if request.url.path != "/editor/tenants/":
			assert SERVICE_KEY not in request.headers.get("authorization", "")


async def test_editor_and_anonymous_caches_do_not_mix(django, mock_editor_gregory):
	"""Same site, same path: an editor's answer must never be served to the
	anonymous endpoint (or the reverse) from the response cache."""
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			await client.call_tool("list_subjects", {})
		editor_requests = len(mock_editor_gregory.requests)
		async with anonymous_client(app) as client:
			await client.call_tool("list_subjects", {})

	anonymous_reads = [
		r for r in mock_editor_gregory.requests[editor_requests:] if r.url.path.startswith("/subjects")
	]
	assert anonymous_reads


async def test_editor_address_on_an_unconfigured_process_does_not_exist(mock_gregory):
	"""Without GREGORY_MCP_SERVICE_KEY / GREGORY_OAUTH_ISSUER the process serves
	`/mcp` only, so deploying this code changes nothing until it is configured."""
	import dataclasses

	from gregory_mcp.app import build_app
	from tests.conftest import EDITOR_SETTINGS
	from tests.editor_helpers import http_client

	app = build_app(dataclasses.replace(EDITOR_SETTINGS, service_key="", oauth_issuer=""))
	async with running(app):
		async with http_client(app) as http:
			editor = await http.post("/mcp/editor", json={})
			metadata = await http.get("/.well-known/oauth-protected-resource/mcp/editor")

	assert editor.status_code == 404
	assert metadata.status_code == 404


async def test_a_private_site_serves_its_editors_only(mock_editor_gregory):
	"""An api_public=False site has no anonymous `/mcp` (the gate says so) but
	its editor address works for a signed-in person."""
	from tests.editor_helpers import two_tenants

	fake = FakeDjango(
		editor_tenants=two_tenants(private_second=True),
		anon_tenants=two_tenants(private_second=True),
		tokens={"editor": introspection(site_id=4, aud=[f"https://{OTHER_HOST}/mcp/editor"])},
		api_routes=_editor_routes(),
	)
	mock_editor_gregory.set_handler(fake.handler())
	app = new_app()
	async with running(app):
		async with mcp_client(app, host=OTHER_HOST, token="editor") as client:
			names = {t.name for t in (await client.list_tools()).tools}

	assert WRITE_TOOLS <= names


@pytest.mark.parametrize(("token", "editorial"), [("editor", True), ("readonly", True), ("public", False)])
async def test_detail_reads_include_editorial_content_for_editors_only(django, mock_editor_gregory, token, editorial):
	"""An editor's get_article/get_trial carries the site's takeaways and
	summary (the text they may be about to edit); the public tier reads
	exactly what /mcp reads."""
	app = new_app()
	async with running(app):
		async with mcp_client(app, token=token) as client:
			await client.call_tool("get_article", {"article_id": 11})
			await client.call_tool("get_trial", {"trial_id": 5})

	reads = [
		r
		for r in mock_editor_gregory.requests
		if r.url.path.rstrip("/").endswith(("/articles/11", "/trials/5"))
	]
	assert len(reads) == 2
	for request in reads:
		assert (request.url.params.get("include") == "editorial") is editorial


# ===== The merged tools: set_trial_link, set_category_assignment, get_history, update_editorial =====
#
# The merge is a pure rename: every call to an old tool has an equivalent call to a
# new one that makes the same upstream request and returns the same result. These
# tests pin that equivalence, the two directions' differences, and what is logged.


# --- the direction flag is required, and means what it says ------------------------


@pytest.mark.parametrize(
	"tool,args",
	[
		("set_trial_link", {"article_id": 11, "trial_id": 5}),
		("set_category_assignment", {"article_id": 11, "category_id": 140}),
	],
)
async def test_the_direction_is_required(django, mock_editor_gregory, tool, args):
	"""A model must say which way it means: there is no default that could turn
	an intended removal into an addition, or the reverse."""
	result = await _call(tool, args)

	assert result.is_error
	assert "linked" in result.content[0].text or "assigned" in result.content[0].text
	assert _writes(mock_editor_gregory) == []


@pytest.mark.parametrize("tool,flag", [("set_trial_link", "linked"), ("set_category_assignment", "assigned")])
async def test_the_direction_must_be_a_boolean(django, mock_editor_gregory, tool, flag):
	args = {"article_id": 11, "trial_id": 5, "category_id": 140, flag: "maybe"}
	args = {k: v for k, v in args.items() if k != ("category_id" if tool == "set_trial_link" else "trial_id")}

	result = await _call(tool, args)

	assert result.is_error
	assert _writes(mock_editor_gregory) == []


async def test_the_schema_marks_the_direction_required_and_boolean(django):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			tools = {t.name: t for t in (await client.list_tools()).tools}

	for name, flag in (("set_trial_link", "linked"), ("set_category_assignment", "assigned")):
		schema = tools[name].input_schema
		assert flag in schema["required"]
		assert schema["properties"][flag]["type"] == "boolean"


# --- the same upstream request as before, per direction ------------------------------


@pytest.mark.parametrize(
	"args,method,path,body",
	[
		({"article_id": 11, "trial_id": 5, "linked": True}, "POST", "/editor/articles/11/trials/", b'{"trial_id":5}'),
		({"article_id": 11, "trial_id": 5, "linked": False}, "DELETE", "/editor/articles/11/trials/5/", b""),
		({"doi": "10.1/ok", "trial_id": 5, "linked": True}, "POST", "/editor/articles/11/trials/", b'{"trial_id":5}'),
	],
)
async def test_set_trial_link_makes_the_request_the_old_tools_did(django, mock_editor_gregory, args, method, path, body):
	result = await _call("set_trial_link", args)

	assert not result.is_error
	(request,) = _writes(mock_editor_gregory)
	assert (request.method, request.url.path, request.content) == (method, path, body)


@pytest.mark.parametrize(
	"args,method",
	[
		({"article_id": 11, "category_id": 140, "assigned": True}, "PUT"),
		({"article_id": 11, "category_id": 140, "assigned": False}, "DELETE"),
		({"doi": "10.1/ok", "category_id": 140, "assigned": True}, "PUT"),
	],
)
async def test_set_category_assignment_makes_the_request_the_old_tools_did(django, mock_editor_gregory, args, method):
	result = await _call("set_category_assignment", args)

	assert not result.is_error
	(request,) = _writes(mock_editor_gregory)
	assert (request.method, request.url.path) == (method, "/editor/articles/11/categories/140/")
	assert request.content == b""


async def test_the_result_is_what_django_stored(django):
	linked = await _call("set_trial_link", {"article_id": 11, "trial_id": 5, "linked": True})
	unlinked = await _call("set_trial_link", {"article_id": 11, "trial_id": 5, "linked": False})

	assert '"source": "editor"' in linked.content[0].text.replace('":"', '": "')
	assert '"removed"' in unlinked.content[0].text


# --- exactly one article identifier, for every merged tool ------------------------------


@pytest.mark.parametrize(
	"tool,extra",
	[
		("update_editorial", {"takeaways": "x"}),
		("get_history", {}),
		("set_trial_link", {"trial_id": 5, "linked": True}),
		("set_category_assignment", {"category_id": 140, "assigned": True}),
	],
)
@pytest.mark.parametrize("ids", [{}, {"article_id": 11, "doi": "10.1/ok"}])
async def test_every_merged_tool_wants_exactly_one_article_identifier(django, mock_editor_gregory, tool, extra, ids):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			with pytest.raises(MCPError) as caught:
				await client.call_tool(tool, {**ids, **extra})

	assert "exactly one of article_id and doi" in str(caught.value)
	assert _writes(mock_editor_gregory) == []


@pytest.mark.parametrize(
	"tool,extra",
	[
		("get_history", {}),
		("set_trial_link", {"trial_id": 5, "linked": False}),
		("set_category_assignment", {"category_id": 140, "assigned": False}),
	],
)
async def test_a_doi_resolves_through_the_editor_route_for_every_merged_tool(django, mock_editor_gregory, tool, extra):
	result = await _call(tool, {"doi": "10.1/ok", **extra})

	assert not result.is_error
	assert [r.url.path for r in mock_editor_gregory.requests if r.url.path == "/editor/articles/resolve/"]


# --- error wording per direction -------------------------------------------------------------


class _Failing:
	"""A client whose writes all fail with `status`."""

	def __init__(self, status):
		self.status = status

	async def _fail(self, *args, **kwargs):
		raise GregoryAPIError(self.status, '{"detail": "nope"}')

	post = put = delete = patch = _fail


@pytest.mark.parametrize(
	"args,expected",
	[
		({"trial_id": 5, "linked": True}, "Article 11 or trial 5 was not found on this site."),
		({"trial_id": 5, "linked": False}, "The link between article 11 and trial 5 was not found on this site."),
	],
)
async def test_set_trial_link_404_names_the_pair_the_way_each_old_tool_did(monkeypatch, args, expected):
	monkeypatch.setattr(editor, "get_client", lambda: _Failing(404))

	with pytest.raises(editor.ToolError) as caught:
		await editor.set_trial_link(article_id=11, **args)

	assert str(caught.value) == expected


@pytest.mark.parametrize(
	"assigned,expected",
	[
		(True, "Article 11 or category 140 was not found on this site."),
		(False, "Article 11 in category 140 was not found on this site."),
	],
)
async def test_set_category_assignment_404_names_what_each_old_tool_did(monkeypatch, assigned, expected):
	monkeypatch.setattr(editor, "get_client", lambda: _Failing(404))

	with pytest.raises(editor.ToolError) as caught:
		await editor.set_category_assignment(category_id=140, assigned=assigned, article_id=11)

	assert str(caught.value) == expected


# --- what is logged ---------------------------------------------------------------------------


def _edits(caplog):
	return [r for r in caplog.records if r.getMessage() == "mcp_edit"]


@pytest.mark.parametrize(
	"tool,args,action,fields",
	[
		("set_trial_link", {"article_id": 11, "trial_id": 5, "linked": True}, "link", ["trial_link"]),
		("set_trial_link", {"article_id": 11, "trial_id": 5, "linked": False}, "unlink", ["trial_link"]),
		("set_category_assignment", {"article_id": 11, "category_id": 140, "assigned": True}, "assign", ["category"]),
		("set_category_assignment", {"article_id": 11, "category_id": 140, "assigned": False}, "unassign", ["category"]),
	],
)
async def test_two_way_tools_log_which_way_they_went(django, caplog, tool, args, action, fields):
	"""One tool name now covers both directions, so the line says which."""
	caplog.set_level(logging.INFO)

	await _call(tool, args)

	(edit,) = _edits(caplog)
	assert edit.tool == tool
	assert edit.record_type == "article"
	assert edit.article_id == 11
	assert edit.action == action
	assert edit.fields == fields
	assert edit.outcome == "ok"


async def test_a_failed_two_way_edit_logs_the_error_and_the_direction(monkeypatch, caplog):
	caplog.set_level(logging.INFO)
	monkeypatch.setattr(editor, "get_client", lambda: _Failing(404))

	with pytest.raises(editor.ToolError):
		await editor.set_category_assignment(category_id=140, assigned=False, article_id=11)

	(edit,) = _edits(caplog)
	assert (edit.tool, edit.outcome, edit.action, edit.category_id) == (
		"set_category_assignment",
		"error",
		"unassign",
		140,
	)


async def test_one_way_tools_log_a_record_type_and_no_action(django, caplog):
	caplog.set_level(logging.INFO)

	await _call("update_editorial", {"article_id": 11, "takeaways": "x"})
	await _call("set_article_relevance", {"article_id": 11, "subject_id": 1, "is_relevant": True})

	edits = _edits(caplog)
	assert [e.tool for e in edits] == ["update_editorial", "set_article_relevance"]
	assert {e.record_type for e in edits} == {"article"}
	assert not any(hasattr(e, "action") for e in edits)


# --- documentation the model reads -----------------------------------------------------------------


@pytest.mark.parametrize(
	"fn,phrases",
	[
		(editor.set_trial_link, ["linked=true", "linked=false", "hidden"]),
		(editor.set_category_assignment, ["assigned=true", "assigned=false", "update_category"]),
	],
)
def test_docstrings_explain_both_directions(fn, phrases):
	for phrase in phrases:
		assert phrase in fn.__doc__, f"{fn.__name__} docstring should mention {phrase!r}"


def test_the_merged_tools_name_no_old_tool():
	for fn in (editor.set_trial_link, editor.set_category_assignment, editor.get_history, editor.update_editorial):
		for old in ("link_trial_to_article", "unlink_trial_from_article", "assign_article_category", "unassign_article_category"):
			assert old not in (fn.__doc__ or "")
