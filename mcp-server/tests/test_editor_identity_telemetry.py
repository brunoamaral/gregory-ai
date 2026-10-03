"""What the model is told on the editor address (per tier) and what the
operator sees in the logs (tier, user_id, edit lines without values)."""

from __future__ import annotations

import logging

import pytest

from tests.editor_helpers import FakeDjango, introspection, mcp_client, new_app, running
from tests.test_editor_tools import _editor_routes


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


async def _instructions(token):
	app = new_app()
	async with running(app):
		async with mcp_client(app, token=token) as client:
			return client.instructions


async def test_instructions_differ_by_tier_and_stay_free_of_internal_names(django):
	editor = await _instructions("editor")
	readonly = await _instructions("readonly")
	public = await _instructions("public")

	assert editor != public
	assert readonly != editor
	assert "confirm" in editor.lower()
	for text in (editor, readonly, public):
		for banned in ("GregoryAI", "instance", "tenant"):
			assert banned not in text
	# A public-tier sign-in is told it is read-only, like /mcp.
	assert "read-only" in public.lower()


async def test_edits_are_logged_with_field_names_never_values(django, caplog):
	caplog.set_level(logging.INFO)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			await client.call_tool("update_article_editorial", {"article_id": 11, "takeaways": "SECRET-TEXT"})

	edits = [r for r in caplog.records if r.getMessage() == "mcp_edit"]
	assert len(edits) == 1
	assert edits[0].fields == ["takeaways"]
	assert edits[0].outcome == "ok"
	assert "SECRET-TEXT" not in "".join(str(v) for v in edits[0].__dict__.values())


async def test_requests_log_the_tier_and_the_user(django, caplog):
	caplog.set_level(logging.INFO)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			await client.call_tool("list_subjects", {})
		async with mcp_client(app, token="public") as client:
			await client.call_tool("list_subjects", {})

	requests = [r for r in caplog.records if r.getMessage() == "mcp_request" and getattr(r, "method", "") == "tools/call"]
	assert [r.tier for r in requests] == ["editor", "public"]
	assert requests[0].user_id == 7
	assert not hasattr(requests[1], "user_id")


async def test_failed_edits_are_logged_as_errors_without_the_text(django, caplog):
	caplog.set_level(logging.INFO)
	app = new_app()
	async with running(app):
		async with mcp_client(app, token="editor") as client:
			await client.call_tool("update_article_editorial", {"article_id": 500, "takeaways": "SECRET-TEXT"})

	(edit,) = [r for r in caplog.records if r.getMessage() == "mcp_edit"]
	assert edit.outcome == "error"
	assert "SECRET-TEXT" not in caplog.text
