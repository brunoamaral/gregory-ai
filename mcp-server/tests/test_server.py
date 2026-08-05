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


def test_client_exposes_no_write_methods():
	"""The server issues GET only — assert the client has no write verbs at all."""
	for verb in ("post", "put", "patch", "delete"):
		assert not hasattr(GregoryClient, verb), f"GregoryClient must not expose .{verb}()"
