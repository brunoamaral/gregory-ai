"""search_medicines and get_medicine (tools/medicines.py)."""

from __future__ import annotations

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gregory_mcp.tools.medicines import (
	COVERAGE_NOTE,
	MAX_PAGE_SIZE,
	get_medicine,
	search_medicines,
)

SOURCE = "European Medicines Agency"

ROW = {
	"id": 12,
	"ema_product_number": "EMEA/H/C/004043",
	"name": "Ocrevus",
	"inn": "ocrelizumab",
	"active_substance": "ocrelizumab",
	"substances": ["ocrelizumab"],
	"is_combination": False,
	"status": "Authorised",
	"opinion_status": "",
	"therapeutic_areas": ["Multiple Sclerosis"],
	"indication": "x" * 1000,
	"holder": "Roche Registration GmbH",
	"advanced_therapy": False,
	"orphan_medicine": False,
	"biosimilar": False,
	"generic": False,
	"opinion_date": "2017-11-16",
	"authorisation_date": "2018-01-08",
	"latest_ec_decision_date": "2026-06-01",
	"ema_last_updated": "2026-10-01",
	"url": "https://www.ema.europa.eu/en/medicines/human/EPAR/ocrevus",
	"tracked": True,
	"grouped_under": None,
	"categories": [{"id": 6, "category_name": "Ocrelizumab", "category_slug": "ocrelizumab"}],
	"subjects": [
		{"id": 1, "subject_name": "Multiple Sclerosis", "subject_slug": "ms", "matched_on": "Multiple Sclerosis"}
	],
	"source": SOURCE,
}


def _json(payload, status=200):
	return lambda request: httpx2.Response(status, json=payload)


# --- search_medicines ---------------------------------------------------------


async def test_search_medicines_returns_compact_rows_with_the_source(mock_gregory):
	mock_gregory.set_handler(_json({"count": 1, "next": None, "results": [ROW]}))

	result = await search_medicines(substance="ocrelizumab")

	assert mock_gregory.requests[0].url.path == "/medicines/"
	assert result["source"] == SOURCE
	assert result["note"] == COVERAGE_NOTE
	assert result["count"] == 1
	assert result["next_page"] is None
	assert result["medicines"] == [
		{
			"medicine_id": 12,
			"name": "Ocrevus",
			"substances": ["ocrelizumab"],
			"status": "Authorised",
			"opinion_status": "",
			"authorisation_date": "2018-01-08",
			"opinion_date": "2017-11-16",
			"latest_ec_decision_date": "2026-06-01",
			"holder": "Roche Registration GmbH",
			"indication": "x" * 400 + "…",
			"advanced_therapy": False,
			"orphan_medicine": False,
			"is_combination": False,
			"generic": False,
			"biosimilar": False,
			"tracked": True,
			"categories": [{"category_id": 6, "category_name": "Ocrelizumab", "category_slug": "ocrelizumab"}],
			"subjects": [
				{"subject_id": 1, "subject_name": "Multiple Sclerosis", "matched_on": "Multiple Sclerosis"}
			],
			"link": "https://www.ema.europa.eu/en/medicines/human/EPAR/ocrevus",
		}
	]


async def test_an_untracked_medicine_row_has_no_categories_and_its_subjects(mock_gregory):
	row = dict(ROW, tracked=False, categories=[])
	mock_gregory.set_handler(_json({"count": 1, "next": None, "results": [row]}))

	compact = (await search_medicines(subject_id=1, tracked=False))["medicines"][0]

	assert compact["tracked"] is False
	assert compact["categories"] == []
	assert [s["subject_id"] for s in compact["subjects"]] == [1]


async def test_the_ec_decision_date_is_never_called_the_approval_date(mock_gregory):
	mock_gregory.set_handler(_json({"count": 1, "next": None, "results": [ROW]}))

	row = (await search_medicines())["medicines"][0]

	assert row["latest_ec_decision_date"] == "2026-06-01"
	assert row["authorisation_date"] == "2018-01-08"
	assert not any("approved" in key for key in row)


async def test_search_medicines_passes_every_filter_through(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	await search_medicines(
		name="ocrevus",
		substance="ocrelizumab",
		category_id=6,
		category_slug="ocrelizumab",
		subject_id=1,
		status="Authorised",
		opinion_status="Positive",
		authorised_after="2018-01-01",
		authorised_before="2020-01-01",
		include_generics=True,
		tracked=False,
		ordering="-authorisation_date",
		page=2,
		page_size=5,
	)

	params = dict(mock_gregory.requests[0].url.params)
	assert params == {
		"name": "ocrevus",
		"substance": "ocrelizumab",
		"category_id": "6",
		"category_slug": "ocrelizumab",
		"subject_id": "1",
		"status": "Authorised",
		"opinion_status": "Positive",
		"authorised_after": "2018-01-01",
		"authorised_before": "2020-01-01",
		"include_generics": "true",
		"tracked": "false",
		"ordering": "-authorisation_date",
		"page": "2",
		"page_size": "5",
	}


async def test_unset_filters_are_not_sent(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	await search_medicines()

	assert set(mock_gregory.requests[0].url.params) == {"page", "page_size"}


async def test_page_size_is_capped_and_a_next_page_is_reported(mock_gregory):
	mock_gregory.set_handler(_json({"count": 90, "next": "https://x/medicines/?page=2", "results": [ROW]}))

	result = await search_medicines(page_size=500)

	assert mock_gregory.requests[0].url.params["page_size"] == str(MAX_PAGE_SIZE)
	assert result["next_page"] == 2


async def test_a_zero_hit_search_says_why_and_repeats_the_coverage_caveat(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	result = await search_medicines(substance="Ocrevus", status="Authorised")

	assert result["count"] == 0
	assert result["medicines"] == []
	assert result["note"] == COVERAGE_NOTE
	guidance = result["guidance"]
	assert guidance["applied_filters"] == ["status", "substance"]
	text = " ".join(guidance["suggestions"])
	assert "looks like a brand or product name: use `name`" in text
	assert "include_generics" in text
	assert "not approved" in text


@pytest.mark.parametrize("value", ["Ocrevus", "ocrevus 600", "Cenrifki", "Dimethyl fumarate"])
async def test_a_failed_substance_search_that_looks_like_a_brand_suggests_name(mock_gregory, value):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	result = await search_medicines(substance=value)

	text = " ".join(result["guidance"]["suggestions"])
	assert "use `name` instead" in text
	# Only filter names are echoed, never the value.
	assert value not in text


async def test_a_failed_substance_search_that_looks_like_an_inn_still_mentions_name(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	result = await search_medicines(substance="ocrelizumabb")

	text = " ".join(result["guidance"]["suggestions"])
	assert "looks like a brand" not in text
	assert "use `name`" in text


async def test_a_failed_name_search_suggests_substance(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	result = await search_medicines(name="ocrelizumab")

	assert result["guidance"]["applied_filters"] == ["name"]
	text = " ".join(result["guidance"]["suggestions"])
	assert "use `substance` instead" in text
	assert "not approved" in text


def test_the_docstring_tells_name_and_substance_apart():
	doc = search_medicines.__doc__
	assert "Use `name` for a brand or product name" in doc
	assert "Use `substance` for the" in doc
	assert "not Ocrevus" not in doc and "not \"Ocrevus\"" not in doc


async def test_the_guidance_does_not_blame_generics_when_they_are_included(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	result = await search_medicines(substance="x", include_generics=True)

	assert "already included" in " ".join(result["guidance"]["suggestions"])


def test_the_coverage_note_says_not_in_ema_is_not_unapproved():
	assert "European Medicines Agency" in COVERAGE_NOTE
	assert "individual EU countries" in COVERAGE_NOTE
	assert "not mean 'not approved'" in COVERAGE_NOTE


def test_the_tool_descriptions_name_the_source_and_the_caveat():
	for fn in (search_medicines, get_medicine):
		doc = fn.__doc__
		assert "European Medicines Agency" in doc
		assert "individual" in doc and "not approved" in doc
	assert "latest_ec_decision_date" in search_medicines.__doc__
	assert "latest_ec_decision_date" in get_medicine.__doc__


# --- get_medicine -------------------------------------------------------------


DETAIL = {
	**ROW,
	"events": [
		{
			"id": 90,
			"kind": "ec_decision",
			"kind_label": "New EC decision",
			"event_date": "2026-06-01",
			"basis": "observed",
			"from_value": "",
			"to_value": "",
		},
		{
			"id": 89,
			"kind": "authorised",
			"kind_label": "Authorised in the EU",
			"event_date": "2018-01-08",
			"basis": "reconstructed",
			"from_value": "",
			"to_value": "",
		},
	],
}


async def test_get_medicine_returns_the_record_and_its_events_worded_by_kind_label(mock_gregory):
	mock_gregory.set_handler(_json(DETAIL))

	result = await get_medicine(12)

	assert mock_gregory.requests[0].url.path == "/medicines/12/"
	assert result["medicine_id"] == 12
	assert "id" not in result
	assert result["name"] == "Ocrevus"
	assert result["source"] == SOURCE
	assert result["note"] == COVERAGE_NOTE
	assert result["url"] == ROW["url"]
	assert result["latest_ec_decision_date"] == "2026-06-01"
	assert result["categories"] == ROW["categories"]
	assert [e["kind_label"] for e in result["events"]] == ["New EC decision", "Authorised in the EU"]
	assert [e["event_date"] for e in result["events"]] == ["2026-06-01", "2018-01-08"]
	assert all("id" not in e for e in result["events"])
	assert result["events"][1]["basis"] == "reconstructed"


async def test_get_medicine_without_events_returns_an_empty_list(mock_gregory):
	mock_gregory.set_handler(_json({k: v for k, v in DETAIL.items() if k != "events"}))

	assert (await get_medicine(12))["events"] == []


async def test_get_medicine_strips_team_data(mock_gregory):
	payload = {**DETAIL, "categories": [{"id": 6, "category_name": "C", "category_slug": "c", "team_id": 9}]}
	mock_gregory.set_handler(_json(payload))

	result = await get_medicine(12)

	assert "team_id" not in str(result)


async def test_a_missing_or_invisible_medicine_is_not_found(mock_gregory):
	mock_gregory.set_handler(_json({"detail": "Not found."}, status=404))

	with pytest.raises(ToolError, match="Medicine 12 was not found"):
		await get_medicine(12)


async def test_other_errors_are_not_swallowed(mock_gregory):
	from gregory_mcp.client import GregoryAPIError

	mock_gregory.set_handler(_json({"detail": "boom"}, status=500))

	with pytest.raises(GregoryAPIError):
		await get_medicine(12)


def test_the_docstring_explains_tracked_and_the_two_ways_a_medicine_shows():
	doc = search_medicines.__doc__
	assert "tracked=false" in doc
	assert "approved it for one of" in doc
	assert "duplicate products" in doc


async def test_the_zero_hit_text_names_both_ways_a_medicine_shows(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	result = await search_medicines(name="x", tracked=True, include_generics=True)

	text = " ".join(result["guidance"]["suggestions"])
	assert "filed under its categories or approved for one of its conditions" in text
	assert "`tracked=false` keeps only" in text
	assert "duplicate products are already included" in text
	assert "tracked" in result["guidance"]["applied_filters"]


async def test_the_detail_keeps_subjects_and_grouped_products(mock_gregory):
	detail = dict(
		ROW,
		grouped_products=[{"id": 13, "name": "Axura", "url": "https://x/axura"}],
		events=[],
	)
	mock_gregory.set_handler(_json(detail))

	result = await get_medicine(12)

	assert result["subjects"] == ROW["subjects"]
	assert result["tracked"] is True
	assert result["grouped_products"] == [{"id": 13, "name": "Axura", "url": "https://x/axura"}]
