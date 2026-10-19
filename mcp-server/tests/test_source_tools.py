"""The source tools (`list_sources`, `preview_source`, `create_source`): offered
only to a sign-in with the sources scope, locked again in the client, and mapped
onto Django's `/editor/sources/` routes. MCP add-sources plan, PR 4."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx2
import pytest
import yaml

import gregory_mcp.tools.sources as sources
from gregory_mcp.client import GregoryAPIError, get_client
from gregory_mcp.identity import instructions_for
from gregory_mcp.site_context import SCOPE_SOURCES, EditorSession, _editor_context
from gregory_mcp.tenants import Tenant
from gregory_mcp.tool_access import HISTORY_TOOL, READ_TOOLS, SOURCE_TOOLS, WRITE_TOOLS, allowed_tools
from tests.conftest import EDITOR_SETTINGS
from tests.editor_helpers import FakeDjango, introspection, mcp_client, new_app, running

SOURCE = {
	"id": 31,
	"name": "PubMed | Multiple Sclerosis",
	"source_type": "pubmed",
	"source_for": "science paper",
	"method": "pubmed_api",
	"subject_id": 1,
	"subject_name": "Multiple Sclerosis",
	"team_id": 2,
	"team_name": "Research",
	"active": True,
	"settings": {"pubmed_query": "multiple sclerosis[MeSH Terms]", "excluded_publication_types": []},
	"last_successful_fetch_at": "2026-10-19T10:00:00Z",
}
PREVIEW = {
	"source_type": "pubmed",
	"subject_id": 1,
	"counts": [{"key": "last_30_days", "value": 412, "label": "papers created in the last 30 days"}],
	"samples": [{"title": "A paper", "date": "2026-10-01", "link": "https://pubmed.ncbi.nlm.nih.gov/1/"}],
	"first_run": "Only the last 2 days.",
	"pubmed": {"querytranslation": "multiple sclerosis[MeSH Terms]", "warnings": []},
	"refused": None,
	"duplicate_of": None,
}
CREATED = {
	"source": SOURCE,
	"counts": PREVIEW["counts"],
	"samples": PREVIEW["samples"],
	"first_run": PREVIEW["first_run"],
	"pubmed": PREVIEW["pubmed"],
	"first_fetch": "next pipeline run (every 12 hours), from today on",
}
EXISTING = {"source_id": 30, "name": "PubMed | MS", "subject_id": 1, "subject_name": "Multiple Sclerosis"}

PUBMED_ARGS = {"source_type": "pubmed", "subject_id": 1, "pubmed_query": "multiple sclerosis[MeSH Terms]"}


def _sources_route(request: httpx2.Request) -> httpx2.Response:
	"""A fake `/editor/sources/` whose answer is chosen by the request's `subject_id`
	(GET: the query; POST: the body), so each test picks its failure by subject."""
	if request.method == "GET":
		return httpx2.Response(200, json={"count": 1, "results": [SOURCE]})
	return _answer(json.loads(request.content), CREATED, 201)


def _preview_route(request: httpx2.Request) -> httpx2.Response:
	return _answer(json.loads(request.content), PREVIEW, 200)


def _answer(body: dict, ok: dict, status: int) -> httpx2.Response:
	return {
		400: httpx2.Response(
			400, json={"ctgov_search_condition": ["Fill in at least one ClinicalTrials.gov search field."]}
		),
		404: httpx2.Response(404, json={"detail": "Subject not found."}),
		403: httpx2.Response(403, json={"detail": "nope"}),
		409: httpx2.Response(
			409, json={"detail": "An active source of this team already does this.", "duplicate_of": EXISTING}
		),
		429: httpx2.Response(429, json={"detail": "slow"}, headers={"Retry-After": "120"}),
		503: httpx2.Response(503, json={"detail": "PubMed could not be reached. Try again later."}),
		500: httpx2.Response(500, text="boom"),
	}.get(body["subject_id"], httpx2.Response(status, json=ok))


@pytest.fixture
def django(mock_editor_gregory):
	fake = FakeDjango(
		tokens={
			"sources": introspection(scope="articles:read articles:edit sources:add"),
			"editor": introspection(),
			"readonly": introspection(scope="articles:read"),
			# A scope without the edit scope should never exist; if it did it gets nothing.
			"sources-no-edit": introspection(scope="articles:read sources:add"),
			"public": introspection(tier="public", scope="articles:read"),
		},
		api_routes={
			"/editor/sources/": _sources_route,
			"/editor/sources/preview/": _preview_route,
			"/editor/categories/140/": httpx2.Response(200, json={"id": 140}),
		},
	)
	mock_editor_gregory.set_handler(fake.handler())
	return fake


async def _tool_names(token):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token=token) as client:
			return {t.name for t in (await client.list_tools()).tools}


async def _call(tool, args, token="sources"):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token=token) as client:
			return await client.call_tool(tool, args)


def _body(result) -> dict:
	return json.loads(result.content[0].text)


def _source_requests(mock):
	return [r for r in mock.requests if r.url.path.startswith("/editor/sources/")]


def _session(*scopes: str, tier: str = "editor") -> EditorSession:
	return EditorSession(user_id=7, site_id=3, tier=tier, scopes=frozenset(scopes))


# --- who is offered the tools ------------------------------------------------------------------


def test_the_source_tools_are_named_and_apart_from_the_others():
	assert SOURCE_TOOLS == {"list_sources", "preview_source", "create_source"}
	assert not SOURCE_TOOLS & (READ_TOOLS | WRITE_TOOLS | {HISTORY_TOOL})


def test_the_session_needs_the_edit_and_the_sources_scope():
	assert _session("articles:read", "articles:edit", SCOPE_SOURCES).can_manage_sources
	assert not _session("articles:read", "articles:edit").can_manage_sources
	assert not _session("articles:read", SCOPE_SOURCES).can_manage_sources
	assert not _session("articles:read", "articles:edit", SCOPE_SOURCES, tier="public").can_manage_sources


def test_allowed_tools_adds_the_source_tools_only_with_the_scope():
	editor = _session("articles:read", "articles:edit")
	assert allowed_tools(editor) == READ_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS
	assert allowed_tools(_session("articles:read", "articles:edit", SCOPE_SOURCES)) == (
		READ_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS | SOURCE_TOOLS
	)
	for session in (None, _session("articles:read"), _session("articles:read", SCOPE_SOURCES)):
		assert not allowed_tools(session) & SOURCE_TOOLS


async def test_only_a_sign_in_with_the_scope_lists_the_source_tools(django):
	assert await _tool_names("sources") == READ_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS | SOURCE_TOOLS
	assert await _tool_names("editor") == READ_TOOLS | {HISTORY_TOOL} | WRITE_TOOLS
	for token in ("readonly", "sources-no-edit", "public"):
		assert not await _tool_names(token) & SOURCE_TOOLS, token


_SOURCE_CALLS = [
	("list_sources", {}),
	("preview_source", PUBMED_ARGS),
	("create_source", PUBMED_ARGS),
]


@pytest.mark.parametrize("token", ["editor", "readonly", "sources-no-edit", "public"])
@pytest.mark.parametrize("tool,args", _SOURCE_CALLS)
async def test_without_the_scope_the_source_tools_do_not_exist(django, mock_editor_gregory, token, tool, args):
	"""The same answer as for a tool that was never registered, and no request."""
	result = await _call(tool, args, token=token)
	missing = await _call("no_such_tool", {}, token=token)

	assert result.is_error
	assert result.content[0].text == missing.content[0].text.replace("no_such_tool", tool)
	assert "Unknown tool" in result.content[0].text
	assert _source_requests(mock_editor_gregory) == []


def test_the_hidden_call_matrix_covers_every_source_tool():
	assert {tool for tool, _ in _SOURCE_CALLS} == SOURCE_TOOLS


async def test_the_annotations(django):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="sources") as client:
			tools = {t.name: t for t in (await client.list_tools()).tools}

	listing = tools["list_sources"].annotations
	assert (listing.read_only_hint, listing.open_world_hint) == (True, False)
	preview = tools["preview_source"].annotations
	assert (preview.read_only_hint, preview.idempotent_hint, preview.open_world_hint) == (True, True, True)
	create = tools["create_source"].annotations
	assert (create.read_only_hint, create.destructive_hint, create.idempotent_hint) == (False, False, False)


# --- the client's own lock ----------------------------------------------------------------------


@pytest.fixture
def entered():
	"""Runs a block with `session` as the in-flight request's editor session."""
	from contextlib import contextmanager

	@contextmanager
	def enter(session):
		token = _editor_context.set(session)
		try:
			yield
		finally:
			_editor_context.reset(token)

	return enter


_NO_SCOPE = [
	_session("articles:read"),
	_session("articles:read", "articles:edit"),
	_session("articles:read", SCOPE_SOURCES),
]


@pytest.mark.parametrize("session", _NO_SCOPE)
async def test_the_client_refuses_a_source_request_without_the_scope(mock_editor_gregory, entered, session):
	fresh = get_client()
	with entered(session):
		for send in (
			lambda: fresh.get("/editor/sources/", {}),
			lambda: fresh.get("/sources/", {}),
			lambda: fresh.post("/editor/sources/", json={}),
			lambda: fresh.post("/editor/sources/preview/", json={}),
			lambda: fresh.post("/sources/preview/", json={}),
		):
			with pytest.raises(GregoryAPIError) as caught:
				await send()
			assert caught.value.status_code == 403

	assert _source_requests(mock_editor_gregory) == []


@pytest.mark.parametrize("session", [None, _session("articles:read", "articles:edit", SCOPE_SOURCES, tier="public")])
async def test_the_client_never_sends_a_source_request_anonymously(mock_editor_gregory, entered, session):
	with entered(session):
		with pytest.raises(GregoryAPIError) as caught:
			await get_client().get("/editor/sources/", {})

	assert caught.value.status_code == 403
	assert _source_requests(mock_editor_gregory) == []


async def test_the_refusal_for_a_sign_in_that_can_edit_says_it_cannot_add_sources(mock_editor_gregory, entered):
	with entered(_session("articles:read", "articles:edit")):
		with pytest.raises(GregoryAPIError, match="cannot add sources"):
			await get_client().post("/editor/sources/", json={})


async def test_a_sign_in_with_the_scope_is_let_through(mock_editor_gregory, entered):
	with entered(_session("articles:read", "articles:edit", SCOPE_SOURCES)):
		await get_client().post("/editor/sources/preview/", json={})

	(request,) = _source_requests(mock_editor_gregory)
	assert request.headers["x-gregory-editor-site"] == "3"


async def test_an_ordinary_edit_without_the_scope_is_not_affected(django, mock_editor_gregory):
	result = await _call("update_category", {"category_id": 140, "add_terms": ["x"]}, token="editor")

	assert not result.is_error
	(edit,) = [r for r in mock_editor_gregory.requests if r.url.path == "/editor/categories/140/"]
	assert edit.method == "PATCH"


async def _timeout_of(mock, path):
	(request,) = [r for r in mock.requests if r.url.path == path]
	return request.extensions["timeout"]["read"]


async def test_the_two_source_posts_get_their_own_longer_timeout(django, mock_editor_gregory):
	await _call("preview_source", PUBMED_ARGS)
	await _call("create_source", PUBMED_ARGS)
	await _call("list_sources", {})
	await _call("update_category", {"category_id": 140, "add_terms": ["x"]}, token="sources")

	assert await _timeout_of(mock_editor_gregory, "/editor/sources/preview/") == EDITOR_SETTINGS.source_request_timeout
	posts = [r for r in mock_editor_gregory.requests if r.url.path == "/editor/sources/" and r.method == "POST"]
	assert posts[0].extensions["timeout"]["read"] == EDITOR_SETTINGS.source_request_timeout
	assert EDITOR_SETTINGS.source_request_timeout > EDITOR_SETTINGS.request_timeout * 4
	(listing,) = [r for r in mock_editor_gregory.requests if r.url.path == "/editor/sources/" and r.method == "GET"]
	assert listing.extensions["timeout"]["read"] == EDITOR_SETTINGS.request_timeout
	(edit,) = [r for r in mock_editor_gregory.requests if r.url.path == "/editor/categories/140/"]
	assert edit.extensions["timeout"]["read"] == EDITOR_SETTINGS.request_timeout


def test_the_source_timeout_is_configurable(monkeypatch):
	from gregory_mcp.config import load_settings

	monkeypatch.setenv("GREGORY_API_URL", "https://gregory.test")
	assert load_settings().source_request_timeout == 75
	monkeypatch.setenv("GREGORY_SOURCE_REQUEST_TIMEOUT", "120")
	assert load_settings().source_request_timeout == 120


# --- list_sources -------------------------------------------------------------------------------


async def test_list_sources_defaults_to_active_ones_as_the_person(django, mock_editor_gregory):
	result = await _call("list_sources", {})

	assert not result.is_error
	assert _body(result)["results"][0]["id"] == 31
	(request,) = _source_requests(mock_editor_gregory)
	assert request.method == "GET"
	assert dict(request.url.params) == {"active": "true"}
	assert request.headers["authorization"] == "Bearer svc-key-for-tests"
	assert request.headers["x-gregory-editor-user"] == "7"


async def test_list_sources_passes_its_filters(django, mock_editor_gregory):
	await _call("list_sources", {"subject_id": 1, "source_type": "pubmed", "active": False})
	await _call("list_sources", {"active": None})

	first, second = _source_requests(mock_editor_gregory)
	assert dict(first.url.params) == {"subject_id": "1", "source_type": "pubmed", "active": "false"}
	assert dict(second.url.params) == {}


# --- preview_source and create_source -------------------------------------------------------------


async def test_preview_posts_the_search_and_returns_what_django_found(django, mock_editor_gregory):
	result = await _call("preview_source", PUBMED_ARGS)

	assert not result.is_error
	assert _body(result) == PREVIEW
	(request,) = _source_requests(mock_editor_gregory)
	assert (request.method, request.url.path) == ("POST", "/editor/sources/preview/")
	assert json.loads(request.content) == PUBMED_ARGS


async def test_a_refused_preview_is_an_answer_not_an_error(monkeypatch):
	refused = {**PREVIEW, "counts": [], "refused": "Fill in the link.", "duplicate_of": None}

	class Client:
		async def post(self, path, json=None):
			return refused

	monkeypatch.setattr(sources, "get_client", lambda: Client())
	assert await sources.preview_source("rss_papers", 1) == refused


async def test_create_posts_to_the_collection_with_name_and_description(django, mock_editor_gregory):
	result = await _call("create_source", {**PUBMED_ARGS, "name": "PubMed | MS", "description": "From Ana"})

	assert not result.is_error
	assert _body(result) == CREATED
	(request,) = _source_requests(mock_editor_gregory)
	assert (request.method, request.url.path) == ("POST", "/editor/sources/")
	assert json.loads(request.content) == {**PUBMED_ARGS, "name": "PubMed | MS", "description": "From Ana"}


async def test_the_preview_body_has_no_name_or_description_field(django, mock_editor_gregory):
	"""preview_source does not take them at all."""
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="sources") as client:
			tools = {t.name: t for t in (await client.list_tools()).tools}

	assert "name" not in tools["preview_source"].input_schema["properties"]
	assert "description" not in tools["preview_source"].input_schema["properties"]
	assert {"name", "description"} <= set(tools["create_source"].input_schema["properties"])


@pytest.mark.parametrize(
	"args,body",
	[
		(
			{"source_type": "rss_papers", "subject_id": 1, "link": "https://x.test/feed", "keyword_filter": "ms"},
			{"source_type": "rss_papers", "subject_id": 1, "link": "https://x.test/feed", "keyword_filter": "ms"},
		),
		(
			{"source_type": "rss_trials", "subject_id": 1, "link": "https://x.test/trials"},
			{"source_type": "rss_trials", "subject_id": 1, "link": "https://x.test/trials"},
		),
		(
			{"source_type": "pubmed", "subject_id": 1, "pubmed_query": "q", "excluded_publication_types": ["Review"]},
			{"source_type": "pubmed", "subject_id": 1, "pubmed_query": "q", "excluded_publication_types": ["Review"]},
		),
		(
			{
				"source_type": "ctgov",
				"subject_id": 1,
				"ctgov_condition": "ms",
				"ctgov_intervention": "drug",
				"ctgov_term": "cells",
			},
			{
				"source_type": "ctgov",
				"subject_id": 1,
				"ctgov_search_condition": "ms",
				"ctgov_search_intervention": "drug",
				"ctgov_search_term": "cells",
			},
		),
	],
	ids=["rss_papers", "rss_trials", "pubmed", "ctgov"],
)
@pytest.mark.parametrize("tool,path", [("preview_source", "/editor/sources/preview/"), ("create_source", "/editor/sources/")])
async def test_each_type_sends_only_what_was_given_under_djangos_field_names(django, mock_editor_gregory, tool, path, args, body):
	await _call(tool, args)

	(request,) = _source_requests(mock_editor_gregory)
	assert request.url.path == path
	assert json.loads(request.content) == body


async def test_no_source_tool_takes_a_site_team_or_ssl_setting(django):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="sources") as client:
			tools = {t.name: t for t in (await client.list_tools()).tools}

	forbidden = {"site_id", "team_id", "ignore_ssl", "active", "last_successful_fetch_at"}
	for name in ("preview_source", "create_source"):
		assert not set(tools[name].input_schema["properties"]) & forbidden, name
	assert not set(tools["list_sources"].input_schema["properties"]) & (forbidden - {"active"})


@pytest.mark.parametrize(
	"subject_id,expected",
	[
		(400, ["The source was not accepted", "ctgov_condition: Fill in at least one ClinicalTrials.gov search field.", "Nothing was added"]),
		(404, ["That subject was not found on this site", "list_subjects", "Nothing was added"]),
		(403, ["can no longer add sources", "Reconnect"]),
		(429, ["limit for adding sources", "about 120 seconds", "Nothing was added"]),
		(503, ["PubMed could not be reached. Try again later.", "Nothing was added"]),
		(500, ["could not be completed (status 500)", "Nothing was added"]),
	],
)
async def test_create_failures_become_messages_the_model_can_read(django, subject_id, expected):
	result = await _call("create_source", {**PUBMED_ARGS, "subject_id": subject_id})

	assert result.is_error
	for fragment in expected:
		assert fragment in result.content[0].text, fragment


@pytest.mark.parametrize(
	"subject_id,expected",
	[
		(400, "The source was not accepted"),
		(404, "That subject was not found on this site"),
		(429, "limit for previews"),
		(503, "PubMed could not be reached"),
	],
)
async def test_preview_failures_become_messages_and_say_nothing_was_saved(django, subject_id, expected):
	result = await _call("preview_source", {**PUBMED_ARGS, "subject_id": subject_id})

	assert result.is_error
	assert expected in result.content[0].text
	assert "Nothing was saved" in result.content[0].text


async def test_a_duplicate_is_reported_as_already_existing_not_as_an_error(django):
	result = await _call("create_source", {**PUBMED_ARGS, "subject_id": 409})

	assert not result.is_error
	body = _body(result)
	assert (body["created"], body["already_exists"]) == (False, True)
	assert body["duplicate_of"] == EXISTING
	assert 'source 30, "PubMed | MS" for Multiple Sclerosis' in body["message"]
	assert "already exists" in body["message"]


async def test_a_duplicate_without_a_readable_body_still_says_it_exists(monkeypatch):
	class Client:
		async def post(self, path, json=None):
			raise GregoryAPIError(409, "<html>conflict</html>")

	monkeypatch.setattr(sources, "get_client", lambda: Client())
	body = await sources.create_source("pubmed", 1, pubmed_query="q")

	assert body["already_exists"] is True
	assert "duplicate_of" not in body


async def test_a_timeout_on_create_says_the_source_may_have_been_added(monkeypatch):
	class Client:
		async def post(self, path, json=None):
			raise GregoryAPIError(0, "network error calling /editor/sources/: timed out")

	monkeypatch.setattr(sources, "get_client", lambda: Client())
	with pytest.raises(sources.ToolError) as caught:
		await sources.create_source("pubmed", 1, pubmed_query="q")

	message = str(caught.value)
	assert "may have been added" in message
	assert "list_sources" in message
	assert "already existing" in message


async def test_a_timeout_on_preview_is_just_a_failed_preview(monkeypatch):
	class Client:
		async def post(self, path, json=None):
			raise GregoryAPIError(0, "timed out")

	monkeypatch.setattr(sources, "get_client", lambda: Client())
	with pytest.raises(sources.ToolError, match="Nothing was saved"):
		await sources.preview_source("pubmed", 1, pubmed_query="q")


async def test_a_failed_create_is_not_retried(django, mock_editor_gregory):
	await _call("create_source", {**PUBMED_ARGS, "subject_id": 503})

	assert len(_source_requests(mock_editor_gregory)) == 1


# --- what is logged ----------------------------------------------------------------------------------


def _edits(caplog):
	return [r for r in caplog.records if r.getMessage() == "mcp_edit"]


async def test_each_call_is_logged_with_field_names_only(django, caplog):
	caplog.set_level(logging.INFO)

	await _call("preview_source", {**PUBMED_ARGS, "pubmed_query": "SECRET-SEARCH"})
	await _call("create_source", {**PUBMED_ARGS, "pubmed_query": "SECRET-SEARCH"})
	await _call("create_source", {**PUBMED_ARGS, "subject_id": 409})
	await _call("create_source", {**PUBMED_ARGS, "subject_id": 503})

	preview, created, exists, failed = _edits(caplog)
	assert [(e.tool, e.outcome) for e in (preview, created, exists, failed)] == [
		("preview_source", "ok"),
		("create_source", "ok"),
		("create_source", "exists"),
		("create_source", "error"),
	]
	assert {e.record_type for e in (preview, created, exists, failed)} == {"source"}
	assert created.source_id == 31
	assert created.fields == ["pubmed_query", "source_type", "subject_id"]
	assert "SECRET-SEARCH" not in str(created.__dict__)


async def test_the_source_id_reaches_the_written_log_line(django, caplog):
	from gregory_mcp.logging_config import JsonFormatter

	caplog.set_level(logging.INFO)
	await _call("create_source", PUBMED_ARGS)

	line = json.loads(JsonFormatter().format(_edits(caplog)[0]))
	assert (line["tool"], line["record_type"], line["source_id"]) == ("create_source", "source", 31)


# --- what the model reads ----------------------------------------------------------------------------


def test_create_source_says_it_is_live_feeds_every_site_and_must_be_previewed_first():
	text = " ".join((sources.create_source.__doc__ or "").split())

	assert "live immediately" in text
	assert "recorded under the signed-in person's name" in text
	assert "every site that covers the subject" in text
	assert "from today on, not older content" in text
	assert "preview_source first" in text
	assert "get their confirmation before calling" in text


def test_preview_source_tells_the_model_to_show_the_numbers_and_to_run_before_create():
	text = " ".join((sources.preview_source.__doc__ or "").split())

	assert "saves nothing" in text
	assert "Always run this before create_source" in text
	assert "counts" in text and "sample titles" in text


@pytest.mark.parametrize("fn", [sources.preview_source, sources.create_source])
def test_each_type_is_described_with_an_example(fn):
	text = fn.__doc__ or ""
	for fragment in ("rss_papers", "rss_trials", "pubmed", "ctgov", "[MeSH", "dihydroartemisinin", "cell reprogramming"):
		assert fragment in text, fragment


# --- the instructions ---------------------------------------------------------------------------------


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


@pytest.mark.parametrize("patents", [False, True])
def test_only_a_source_manager_is_told_about_the_source_tools(patents):
	tenant = _tenant(patents_enabled=patents)
	editor = _session("articles:read", "articles:edit")
	manager = _session("articles:read", "articles:edit", SCOPE_SOURCES)

	without = instructions_for(tenant, editor)
	with_scope = instructions_for(tenant, manager)

	for name in SOURCE_TOOLS:
		assert name not in without
		assert name in with_scope
	assert "source" not in without.lower()
	assert "always run preview_source first" in with_scope
	assert "get confirmation before calling create_source" in with_scope
	assert with_scope.count(". You can also add sources with") == 1


@pytest.mark.parametrize("session", [None, _session("articles:read"), _session("articles:read", tier="public")])
def test_every_other_sessions_instructions_name_no_source_tool(session):
	text = instructions_for(_tenant(), session)
	for name in SOURCE_TOOLS:
		assert name not in text


def test_the_editor_text_is_unchanged_apart_from_the_sentence():
	tenant = _tenant()
	editor = instructions_for(tenant, _session("articles:read", "articles:edit"))
	manager = instructions_for(tenant, _session("articles:read", "articles:edit", SCOPE_SOURCES))

	sentence_start = manager.index(" You can also add sources with")
	sentence_end = manager.index(" get_history shows")
	assert manager[:sentence_start] + manager[sentence_end:] == editor


def test_the_source_sentence_uses_no_platform_vocabulary():
	text = instructions_for(_tenant(), _session("articles:read", "articles:edit", SCOPE_SOURCES)).lower()
	for banned in ("gregoryai", "instance", "tenant"):
		assert banned not in text


# --- the schema ----------------------------------------------------------------------------------------


def test_the_routes_and_fields_exist_in_the_regenerated_schema():
	path = Path(__file__).resolve().parents[2] / "django" / "schema.yml"
	if not path.exists():
		pytest.skip(f"schema.yml not found at {path}")
	doc = yaml.safe_load(path.read_text())
	paths = doc["paths"]

	assert "get" in paths["/editor/sources/"] and "post" in paths["/editor/sources/"]
	assert "post" in paths["/editor/sources/preview/"]
	listing = {p["name"] for p in paths["/editor/sources/"]["get"]["parameters"]}
	assert {"subject_id", "source_type", "active"} <= listing

	body = doc["components"]["schemas"]["EditorSourceInput"]["properties"]
	sent = {
		"source_type", "subject_id", "link", "keyword_filter", "pubmed_query", "excluded_publication_types",
		"ctgov_search_condition", "ctgov_search_intervention", "ctgov_search_term", "name", "description",
	}
	assert set(body) == sent
	# Every field a tool can send is a field Django declares, under Django's name.
	built = sources._source_body("ctgov", 1, "l", "k", "p", ["x"], "c", "i", "t")
	assert set(built) <= set(body)
