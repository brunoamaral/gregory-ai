"""search_labs, get_lab, and the lab on the author tools."""

from __future__ import annotations

import inspect

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gregory_mcp.compact import compact_author, compact_lab
from gregory_mcp.site_context import EditorSession
from gregory_mcp.tenants import Tenant
from gregory_mcp.tool_access import HISTORY_TOOL, READ_TOOLS, allowed_tools
from gregory_mcp.tools.authors import get_author, search_authors
from gregory_mcp.tools.labs import get_lab, search_labs


def _link(id_, name, articles=10, relevant=4, recent=2):
	return {
		"id": id_,
		"name": name,
		"slug": name.lower(),
		"articles": articles,
		"relevant_articles": relevant,
		"recent_articles": recent,
		"latest_article_year": 2026,
	}


def _lab(**overrides):
	lab = {
		"id": 412,
		"name": "Silva lab",
		"curated": True,
		"head": {"author_id": 380002, "full_name": "Ana Silva", "orcid": "0000-0002-1825-0097"},
		"institution": {"name": "Universidade de Lisboa", "country_code": "PT", "openalex_id": "I1", "ror": "01c27hj86"},
		"member_count": 7,
		"members": [{"author_id": 380115, "full_name": "Rui Costa", "orcid": "0000-0001-0000-0001"}],
		"subjects": [_link(1, "MS", 31, 6, 9), _link(2, "ALS")],
		"categories": [_link(c, f"Cat{c}") for c in (10, 11, 12, 13, 14)],
		"topics_last_changed": "2026-11-19T08:25:11Z",
	}
	lab.update(overrides)
	return lab


def _respond(mock_gregory, payload):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json=payload))


# --- search_labs: arguments ---------------------------------------------------


async def test_search_labs_maps_every_argument_to_the_api(mock_gregory):
	_respond(mock_gregory, {"count": 0, "results": []})

	await search_labs(
		subject_id=1,
		category_id=14,
		search="silva",
		country="PT",
		author_id=380002,
		min_recent_articles=2,
		ordering="-relevant_articles",
		page=3,
	)

	request = mock_gregory.requests[0]
	assert request.url.path == "/labs/"
	params = request.url.params
	assert params["subject_id"] == "1"
	assert params["category_id"] == "14"
	assert params["search"] == "silva"
	assert params["country"] == "PT"
	assert params["author_id"] == "380002"
	assert params["min_recent_articles"] == "2"
	assert params["ordering"] == "-relevant_articles"
	assert params["page"] == "3"


async def test_search_labs_drops_none_params_and_clamps_the_page(mock_gregory):
	_respond(mock_gregory, {"count": 0, "results": []})

	await search_labs(search="silva", page=0)

	params = mock_gregory.requests[0].url.params
	assert params["search"] == "silva"
	assert params["page"] == "1"
	for name in ("subject_id", "category_id", "country", "author_id", "min_recent_articles", "ordering"):
		assert name not in params


async def test_search_labs_envelope(mock_gregory):
	_respond(
		mock_gregory,
		{"count": 25, "next": "http://gregory:8000/labs/?page=2", "results": [_lab()]},
	)

	result = await search_labs(search="silva")

	assert result["count"] == 25
	assert result["next_page"] == 2
	assert "next" not in result
	assert [lab["id"] for lab in result["labs"]] == [412]


@pytest.mark.parametrize("ordering", ["articles", "-articles", "relevant_articles", "-recent_articles"])
async def test_a_count_ordering_without_a_topic_is_refused_before_any_call(mock_gregory, ordering):
	with pytest.raises(ToolError) as exc_info:
		await search_labs(ordering=ordering)

	assert "subject_id or category_id" in str(exc_info.value)
	assert mock_gregory.requests == []


async def test_min_recent_articles_without_a_topic_is_refused_before_any_call(mock_gregory):
	with pytest.raises(ToolError) as exc_info:
		await search_labs(min_recent_articles=2)

	message = str(exc_info.value)
	assert "min_recent_articles" in message and "subject_id or category_id" in message
	assert mock_gregory.requests == []


@pytest.mark.parametrize("ordering", ["name", "-name", "member_count", "-member_count"])
async def test_name_and_member_orderings_need_no_topic(mock_gregory, ordering):
	_respond(mock_gregory, {"count": 0, "results": []})

	await search_labs(ordering=ordering)

	assert mock_gregory.requests[0].url.params["ordering"] == ordering


@pytest.mark.parametrize("topic", [{"subject_id": 1}, {"category_id": 14}])
async def test_count_orderings_pass_with_either_topic(mock_gregory, topic):
	_respond(mock_gregory, {"count": 0, "results": []})

	await search_labs(ordering="-recent_articles", min_recent_articles=1, **topic)

	assert len(mock_gregory.requests) == 1


def test_search_labs_takes_no_intent():
	assert "intent" not in inspect.signature(search_labs).parameters


# --- compact_lab --------------------------------------------------------------


def test_compact_lab_shape_leaves_out_members_orcids_and_the_rest():
	row = compact_lab(_lab())

	assert row == {
		"id": 412,
		"name": "Silva lab",
		"curated": True,
		"head": {"author_id": 380002, "full_name": "Ana Silva"},
		"institution": {"name": "Universidade de Lisboa", "country_code": "PT"},
		"member_count": 7,
		"top_categories": [
			{"id": c, "name": f"Cat{c}", "articles": 10, "relevant_articles": 4, "recent_articles": 2}
			for c in (10, 11, 12)
		],
	}
	flat = repr(row)
	for leaked in (
		"members",
		"orcid",
		"0000-0002-1825-0097",
		"0000-0001-0000-0001",
		"Rui Costa",
		"openalex_id",
		"I1",
		"ror",
		"topics_last_changed",
		"latest_article_year",
		"slug",
		"subjects",
	):
		assert leaked not in flat, leaked
	assert "subject_link" not in row


def test_top_categories_holds_at_most_three():
	assert len(compact_lab(_lab())["top_categories"]) == 3
	assert len(compact_lab(_lab(), category_id=14)["top_categories"]) == 3


def test_a_chosen_category_ranked_fifth_moves_first_and_the_rest_keep_order():
	row = compact_lab(_lab(), category_id=14)

	assert [c["id"] for c in row["top_categories"]] == [14, 10, 11]


def test_a_chosen_category_already_first_stays_first():
	assert [c["id"] for c in compact_lab(_lab(), category_id=10)["top_categories"]] == [10, 11, 12]


def test_a_chosen_category_the_lab_does_not_have_leaves_the_order_alone():
	assert [c["id"] for c in compact_lab(_lab(), category_id=999)["top_categories"]] == [10, 11, 12]


def test_subject_link_appears_only_with_the_subject_the_lab_has():
	row = compact_lab(_lab(), subject_id=1)
	assert row["subject_link"] == {
		"id": 1,
		"name": "MS",
		"articles": 31,
		"relevant_articles": 6,
		"recent_articles": 9,
	}
	assert "subject_link" not in compact_lab(_lab(), subject_id=99)
	assert "subject_link" not in compact_lab(_lab())
	# A category filter alone never adds it.
	assert "subject_link" not in compact_lab(_lab(), category_id=14)


def test_a_null_head_or_institution_and_missing_lists_are_handled():
	row = compact_lab({"id": 1, "name": "Lab 1", "curated": False, "head": None, "institution": None, "member_count": 0})

	assert row["head"] is None
	assert row["institution"] is None
	assert row["top_categories"] == []


async def test_search_labs_applies_the_chosen_topics_to_each_result(mock_gregory):
	_respond(mock_gregory, {"count": 1, "results": [_lab()]})

	result = await search_labs(category_id=14, subject_id=1)

	lab = result["labs"][0]
	assert lab["top_categories"][0]["id"] == 14
	assert lab["subject_link"]["id"] == 1


# --- get_lab ------------------------------------------------------------------


async def test_get_lab_returns_the_record_unchanged(mock_gregory):
	record = _lab()
	_respond(mock_gregory, record)

	result = await get_lab(412)

	assert result == record
	assert mock_gregory.requests[0].url.path == "/labs/412/"


async def test_get_lab_404_says_not_found(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(404, json={"detail": "Not found."}))

	with pytest.raises(ToolError) as exc_info:
		await get_lab(123)

	assert str(exc_info.value) == "Lab 123 was not found."


async def test_get_lab_other_errors_propagate(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(400, json={"detail": "bad"}))

	with pytest.raises(ToolError) as exc_info:
		await get_lab(1)

	assert "not found" not in str(exc_info.value)


# --- the lab on the author tools ---------------------------------------------


async def test_search_authors_always_sends_include_lab(mock_gregory):
	_respond(mock_gregory, {"count": 0, "results": []})

	await search_authors(search="Jane")

	assert mock_gregory.requests[0].url.params["include"] == "lab"
	assert "include" not in inspect.signature(search_authors).parameters


async def test_get_author_always_sends_include_lab_and_returns_the_record_as_given(mock_gregory):
	record = {
		"author_id": 5,
		"full_name": "Jane Doe",
		"lab": {"id": 412, "name": "Silva lab", "role": "member", "head": {"author_id": 1, "full_name": "Ana Silva"}},
	}
	_respond(mock_gregory, record)

	result = await get_author(5)

	assert mock_gregory.requests[0].url.params["include"] == "lab"
	assert result == record


def test_compact_author_carries_the_lab_as_id_name_and_role_only():
	author = {
		"author_id": 5,
		"full_name": "Jane Doe",
		"lab": {"id": 412, "name": "Silva lab", "role": "head", "head": {"author_id": 5, "full_name": "Jane Doe"}},
	}

	assert compact_author(author)["lab"] == {"id": 412, "name": "Silva lab", "role": "head"}


@pytest.mark.parametrize("author", [{"author_id": 5, "lab": None}, {"author_id": 5}])
def test_compact_author_lab_is_none_when_null_or_absent(author):
	assert compact_author(author)["lab"] is None


async def test_search_authors_results_carry_the_lab(mock_gregory):
	_respond(
		mock_gregory,
		{
			"count": 2,
			"results": [
				{"author_id": 5, "lab": {"id": 412, "name": "Silva lab", "role": "member", "head": {"author_id": 1}}},
				{"author_id": 6, "lab": None},
			],
		},
	)

	result = await search_authors()

	assert result["authors"][0]["lab"] == {"id": 412, "name": "Silva lab", "role": "member"}
	assert result["authors"][1]["lab"] is None


# --- access -------------------------------------------------------------------


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


def test_the_lab_tools_are_read_tools_and_there_are_twelve():
	assert {"search_labs", "get_lab"} <= READ_TOOLS
	assert len(READ_TOOLS) == 12


@pytest.mark.parametrize(
	"tenant",
	[None, _tenant(), _tenant(patents_enabled=False, medicines_enabled=False), _tenant(patents_enabled=True)],
)
def test_every_caller_on_every_tenant_is_offered_both_lab_tools(tenant):
	sessions = (
		None,
		_editor(tier="public", can_edit=False),
		_editor(can_edit=False),
		_editor(),
	)
	for session in sessions:
		tools = allowed_tools(session, tenant)
		assert {"search_labs", "get_lab"} <= tools
	assert HISTORY_TOOL not in allowed_tools(None, tenant)
