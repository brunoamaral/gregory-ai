"""search_medicines and get_medicine (tools/medicines.py)."""

from __future__ import annotations

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gregory_mcp.compact import EMA_ONLY_MEDICINE_FIELDS, FDA_ONLY_MEDICINE_FIELDS
from gregory_mcp.tools.medicines import (
	COVERAGE_NOTE,
	EMA_NOTE,
	FDA_NOTE,
	MAX_PAGE_SIZE,
	get_medicine,
	search_medicines,
)

SOURCE = "European Medicines Agency"
FDA_SOURCE = "U.S. Food and Drug Administration"

# What the API sends for an FDA row: the EMA-only fields are null, not absent.
FDA_ROW = {
	"id": 31,
	"regulator": "fda",
	"ema_product_number": None,
	"fda_application_number": "BLA761053",
	"fda_application_type": "BLA",
	"fda_center": "CDER",
	"name": "KESIMPTA / ARZERRA",
	"brand_names": ["KESIMPTA", "ARZERRA"],
	"inn": "OFATUMUMAB",
	"active_substance": "OFATUMUMAB",
	"substances": ["ofatumumab"],
	"is_combination": False,
	"status": "Approved",
	"opinion_status": None,
	"therapeutic_areas": None,
	"atc_code": None,
	"pharmacotherapeutic_group": "CD20-directed Cytolytic Antibody",
	"indication": "Relapsing forms of multiple sclerosis. " + "x" * 1000,
	"holder": "NOVARTIS",
	"advanced_therapy": None,
	"orphan_medicine": None,
	"conditional_approval": None,
	"exceptional_circumstances": None,
	"prime_priority_medicine": None,
	"accelerated_assessment": None,
	"additional_monitoring": None,
	"biosimilar": False,
	"generic": False,
	"interchangeable": False,
	"fda_priority_review": True,
	"fda_original_class": "TYPE 1 - NEW MOLECULAR ENTITY",
	"patient_safety": None,
	"rolling_review_start_date": None,
	"evaluation_start_date": None,
	"opinion_date": None,
	"authorisation_date": "2009-10-26",
	"latest_ec_decision_date": None,
	"refusal_date": None,
	"application_withdrawal_date": None,
	"suspension_date": None,
	"end_date": None,
	"ema_first_published": None,
	"ema_last_updated": None,
	"url": "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo=125326",
	"grouped_under": None,
	"tracked": True,
	"categories": [{"id": 8, "category_name": "Ofatumumab", "category_slug": "ofatumumab"}],
	"subjects": [],
	"source": FDA_SOURCE,
}

# An EMA row as the API sends it now: the FDA-only fields are null.
EMA_NULL_FDA_FIELDS = {
	"fda_application_number": None,
	"fda_application_type": None,
	"fda_center": None,
	"brand_names": None,
	"fda_priority_review": None,
	"fda_original_class": None,
	"interchangeable": None,
}

ROW = {
	"id": 12,
	"regulator": "ema",
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
	**EMA_NULL_FDA_FIELDS,
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
			"regulator": "ema",
			"source": SOURCE,
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
		regulator="fda",
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
		"regulator": "fda",
	}


async def test_unset_filters_are_not_sent(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	await search_medicines()

	# No `regulator` unless the caller chose one: the search covers both.
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


def test_the_coverage_note_says_not_in_a_regulators_data_is_not_unapproved():
	assert "European Medicines Agency" in COVERAGE_NOTE
	assert "individual EU countries" in COVERAGE_NOTE
	assert "not mean 'not approved'" in COVERAGE_NOTE
	assert "U.S. Food and Drug Administration" in COVERAGE_NOTE
	assert "ANDAs" in COVERAGE_NOTE and "under review" in COVERAGE_NOTE
	assert "'not in FDA's data' does not mean 'not approved'" in COVERAGE_NOTE
	assert "can have a record from each regulator" in COVERAGE_NOTE


def test_the_tool_descriptions_name_both_sources_and_the_caveat():
	search_doc, detail_doc = (" ".join(fn.__doc__.split()) for fn in (search_medicines, get_medicine))
	for doc in (search_doc, detail_doc):
		assert "European Medicines Agency" in doc
		assert "U.S. Food and Drug Administration" in doc
		assert "individual EU countries" in doc and "not approved" in doc
		assert "ANDAs" in doc and "under review" in doc
		assert "EU-authorised" not in doc
		assert "latest_ec_decision_date" in doc
	assert "twice, once per regulator" in search_doc
	assert "Pass `regulator` to search one regulator only" in search_doc
	assert "ordering=-authorisation_date" in search_doc
	assert "CHMP is EMA's" in detail_doc


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
	assert result["regulator"] == "ema"
	assert result["note"] == EMA_NOTE
	assert result["url"] == ROW["url"]
	assert result["latest_ec_decision_date"] == "2026-06-01"
	assert result["categories"] == ROW["categories"]
	assert [e["kind_label"] for e in result["events"]] == ["New EC decision", "Authorised in the EU"]
	assert [e["event_date"] for e in result["events"]] == ["2026-06-01", "2018-01-08"]
	assert all("id" not in e for e in result["events"])
	assert result["events"][1]["basis"] == "reconstructed"


FDA_DETAIL = {
	**FDA_ROW,
	"grouped_products": [],
	"events": [
		{
			"id": 95,
			"kind": "fda_approved",
			"kind_label": "Approved by the FDA",
			"event_date": "2009-10-26",
			"basis": "reconstructed",
			"from_value": "",
			"to_value": "",
		},
	],
}


async def test_get_medicine_describes_an_fda_record_in_its_own_words(mock_gregory):
	mock_gregory.set_handler(_json(FDA_DETAIL))

	result = await get_medicine(31)

	assert result["medicine_id"] == 31
	assert result["regulator"] == "fda"
	assert result["source"] == FDA_SOURCE
	assert result["fda_application_number"] == "BLA761053"
	assert result["fda_application_type"] == "BLA"
	assert result["fda_center"] == "CDER"
	assert result["brand_names"] == ["KESIMPTA", "ARZERRA"]
	assert result["fda_priority_review"] is True
	assert result["fda_original_class"] == "TYPE 1 - NEW MOLECULAR ENTITY"
	assert result["interchangeable"] is False
	assert result["authorisation_date"] == "2009-10-26"
	assert result["url"] == FDA_ROW["url"]
	assert result["events"][0]["kind_label"] == "Approved by the FDA"
	assert "id" not in result["events"][0]
	# The FDA's own caveat, not EMA's.
	assert result["note"] == FDA_NOTE
	assert "ANDAs" in result["note"]
	assert "EMA" not in result["note"]


async def test_an_fda_detail_has_none_of_the_null_ema_fields(mock_gregory):
	mock_gregory.set_handler(_json(FDA_DETAIL))

	result = await get_medicine(31)

	for field in EMA_ONLY_MEDICINE_FIELDS:
		assert field not in result
	# Nothing else on the record is null, so nothing prints as "None".
	assert {k for k, v in result.items() if v is None} == {"grouped_under"}


async def test_an_ema_detail_has_none_of_the_null_fda_fields_and_keeps_its_own_nulls(mock_gregory):
	detail = dict(DETAIL, end_date=None, refusal_date=None)
	mock_gregory.set_handler(_json(detail))

	result = await get_medicine(12)

	for field in FDA_ONLY_MEDICINE_FIELDS:
		assert field not in result
	assert result["regulator"] == "ema"
	assert result["note"] == EMA_NOTE
	# An EMA field that is empty on this EMA row is still its own field.
	assert result["end_date"] is None and result["refusal_date"] is None


async def test_a_record_without_a_regulator_is_read_as_ema_s(mock_gregory):
	legacy = {k: v for k, v in DETAIL.items() if k not in ("regulator", "source", *FDA_ONLY_MEDICINE_FIELDS)}
	mock_gregory.set_handler(_json(legacy))

	result = await get_medicine(12)

	assert result["regulator"] == "ema"
	assert result["source"] == SOURCE


async def test_a_grouped_fda_lead_keeps_its_grouped_products(mock_gregory):
	detail = dict(FDA_DETAIL, grouped_products=[{"id": 32, "name": "Tyruko", "url": "https://x/tyruko"}])
	mock_gregory.set_handler(_json(detail))

	assert (await get_medicine(31))["grouped_products"] == [{"id": 32, "name": "Tyruko", "url": "https://x/tyruko"}]


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
	doc = " ".join(search_medicines.__doc__.split())
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


# --- FDA records ------------------------------------------------------------------


async def test_an_fda_row_in_search_names_its_regulator_and_source(mock_gregory):
	mock_gregory.set_handler(_json({"count": 1, "next": None, "results": [FDA_ROW]}))

	result = await search_medicines(substance="ofatumumab", regulator="fda")

	assert mock_gregory.requests[0].url.params["regulator"] == "fda"
	assert result["source"] == FDA_SOURCE
	assert result["note"] == FDA_NOTE
	assert result["medicines"] == [
		{
			"medicine_id": 31,
			"regulator": "fda",
			"source": FDA_SOURCE,
			"name": "KESIMPTA / ARZERRA",
			"brand_names": ["KESIMPTA", "ARZERRA"],
			"fda_application_number": "BLA761053",
			"substances": ["ofatumumab"],
			"status": "Approved",
			"authorisation_date": "2009-10-26",
			"holder": "NOVARTIS",
			"indication": ("Relapsing forms of multiple sclerosis. " + "x" * 1000)[:400] + "…",
			"is_combination": False,
			"generic": False,
			"biosimilar": False,
			"tracked": True,
			"categories": [{"category_id": 8, "category_name": "Ofatumumab", "category_slug": "ofatumumab"}],
			"subjects": [],
			"link": FDA_ROW["url"],
		}
	]


async def test_an_fda_row_shows_no_ema_field_and_an_ema_row_no_fda_field(mock_gregory):
	mock_gregory.set_handler(_json({"count": 2, "next": None, "results": [ROW, FDA_ROW]}))

	ema, fda = (await search_medicines(substance="x"))["medicines"]

	for field in ("opinion_status", "opinion_date", "latest_ec_decision_date", "advanced_therapy", "orphan_medicine"):
		assert field in ema and field not in fda
	for field in ("brand_names", "fda_application_number"):
		assert field in fda and field not in ema
	# Nothing null that cannot apply to the row, so nothing for a model to print as "None".
	assert None not in fda.values()


async def test_a_mixed_search_names_both_sources_once_each(mock_gregory):
	mock_gregory.set_handler(_json({"count": 3, "next": None, "results": [ROW, ROW, FDA_ROW]}))

	result = await search_medicines(substance="x")

	assert result["source"] == f"{SOURCE} and {FDA_SOURCE}"
	assert result["note"] == COVERAGE_NOTE
	assert [m["source"] for m in result["medicines"]] == [SOURCE, SOURCE, FDA_SOURCE]


async def test_an_unfiltered_search_keeps_both_caveats_even_when_the_page_is_all_ema(mock_gregory):
	"""EMA rows sort first, so page 1 can be all EMA while page 2 has FDA ones."""
	mock_gregory.set_handler(_json({"count": 40, "next": "https://x/medicines/?page=2", "results": [ROW]}))

	result = await search_medicines()

	assert result["source"] == SOURCE
	assert result["note"] == COVERAGE_NOTE
	assert result["next_page"] == 2


async def test_a_regulator_filter_narrows_the_note_to_that_regulator(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	ema = await search_medicines(regulator="ema")
	fda = await search_medicines(regulator="fda")

	assert (ema["source"], ema["note"]) == (SOURCE, EMA_NOTE)
	assert (fda["source"], fda["note"]) == (FDA_SOURCE, FDA_NOTE)


async def test_a_zero_hit_search_with_no_filter_names_both_sources(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	result = await search_medicines(name="nothing")

	assert result["source"] == f"{SOURCE} and {FDA_SOURCE}"


async def test_the_zero_hit_guidance_handles_the_regulator_filter(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	result = await search_medicines(substance="ocrelizumab", regulator="fda", opinion_status="Positive")

	guidance = result["guidance"]
	assert guidance["applied_filters"] == ["opinion_status", "regulator", "substance"]
	text = " ".join(guidance["suggestions"])
	assert "drop `regulator` to search both" in text
	assert "leaves out every FDA record" in text
	assert "ANDAs" in text and "under review" in text
	assert "not conclude it is not approved" in text


def test_regulator_is_limited_to_the_two_regulators_on_the_tool():
	from typing import Literal, get_args, get_type_hints

	hint = get_type_hints(search_medicines)["regulator"]
	(literal, none) = get_args(hint)
	assert none is type(None)
	assert literal.__origin__ is Literal
	assert set(get_args(literal)) == {"ema", "fda"}
