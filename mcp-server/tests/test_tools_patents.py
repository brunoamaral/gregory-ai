"""search_patents and get_patent (tools/patents.py), and the patent additions to
search_trials, get_trial, list_sponsors, list_categories and get_stats."""

from __future__ import annotations

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError

import gregory_mcp.tools.patents as patents_module
from gregory_mcp.client import GregoryAPIError
from gregory_mcp.site_context import EditorSession
from gregory_mcp.tools.catalog import list_categories, list_sponsors
from gregory_mcp.tools.patents import CLAIMS_CAP, get_patent, search_patents
from gregory_mcp.tools.stats import get_stats
from gregory_mcp.tools.trials import get_trial, search_trials


def _json(payload, status=200):
	return lambda request: httpx2.Response(status, json=payload)


# --- search_patents -----------------------------------------------------------


async def test_search_patents_compacts_results(mock_gregory):
	mock_gregory.set_handler(
		_json(
			{
				"count": 1,
				"next": None,
				"results": [
					{
						"patent_id": 7,
						"family_id": "1001",
						"title": "Anti-CD20 antibody formulation",
						"summary": "y" * 1000,
						"representative_publication": "EP4123456A1",
						"link": "https://worldwide.espacenet.com/publicationDetails/biblio?CC=EP&NR=4123456A1",
						"earliest_priority_date": "2018-03-01",
						"earliest_publication_date": "2019-09-01",
						"has_grant": True,
						"cpc_classes": ["A61K39/395"],
						"applicants": [
							{"raw_name": "ACME PHARMA LTD", "sponsor_name": "Acme Pharma", "sponsor_id": 4},
							{"raw_name": "Jane Researcher", "sponsor_name": None, "sponsor_id": None},
							{"raw_name": "ACME PHARMA LTD", "sponsor_name": "Acme Pharma"},
						],
						"team_categories": [{"id": 1}],
						"subjects": [{"id": 1}],
						"publications_count": 9,
						"trial_links_count": 2,
					}
				],
			}
		)
	)

	result = await search_patents(search="antibody")

	assert result["count"] == 1
	patent = result["patents"][0]
	assert patent == {
		"patent_id": 7,
		"title": "Anti-CD20 antibody formulation",
		"summary": "y" * 400 + "…",
		"representative_publication": "EP4123456A1",
		"link": "https://worldwide.espacenet.com/publicationDetails/biblio?CC=EP&NR=4123456A1",
		"earliest_priority_date": "2018-03-01",
		"earliest_publication_date": "2019-09-01",
		"has_grant": True,
		"applicants": ["Acme Pharma", "Jane Researcher"],
		"trial_links_count": 2,
	}
	assert mock_gregory.requests[0].url.path == "/patents/"


async def test_search_patents_passes_every_filter_through(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	await search_patents(
		search="ocrelizumab",
		subject_id=1,
		category_slug="ocrelizumab",
		category_id=5,
		category_modality="biologic_antibody",
		applicant="roche",
		applicant_sponsor_id=9,
		applicant_sponsor_slug="roche",
		priority_date_after="2018-01-01",
		priority_date_before="2022-12-31",
		publication_date_after="2019-01-01",
		publication_date_before="2023-12-31",
		has_grant=True,
		cpc="A61K39,A61P25/28",
		publication_number="EP4123456",
		has_trial_link=False,
		include_subject_links=True,
		ordering="-earliest_priority_date",
	)

	params = mock_gregory.requests[0].url.params
	assert params["search"] == "ocrelizumab"
	assert params["subject_id"] == "1"
	assert params["category_slug"] == "ocrelizumab"
	assert params["category_id"] == "5"
	assert params["category_modality"] == "biologic_antibody"
	assert params["applicant"] == "roche"
	assert params["applicant_sponsor_id"] == "9"
	assert params["applicant_sponsor_slug"] == "roche"
	assert params["priority_date_after"] == "2018-01-01"
	assert params["priority_date_before"] == "2022-12-31"
	assert params["publication_date_after"] == "2019-01-01"
	assert params["publication_date_before"] == "2023-12-31"
	assert params["has_grant"] == "true"
	assert params["cpc"] == "A61K39,A61P25/28"
	assert params["publication_number"] == "EP4123456"
	assert params["has_trial_link"] == "false"
	assert params["include_subject_links"] == "true"
	assert params["ordering"] == "-earliest_priority_date"


async def test_search_patents_omits_unset_filters(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	await search_patents()

	assert set(mock_gregory.requests[0].url.params) <= {"page", "page_size", "site_id"}


async def test_search_patents_clamps_page_size(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	await search_patents(page_size=500)

	assert mock_gregory.requests[0].url.params["page_size"] == "25"


async def test_search_patents_next_page_reflects_clamped_page(mock_gregory):
	mock_gregory.set_handler(
		_json({"count": 100, "next": "http://gregory:8000/patents/?page=4", "results": []})
	)

	result = await search_patents(page=3)

	assert result["next_page"] == 4
	assert "next" not in result


async def test_search_patents_zero_hits_adds_guidance(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	result = await search_patents(
		search="x", cpc="H99Z77", publication_number="ZZ1234567", priority_date_after="2024-01-01", has_grant=True
	)

	guidance = result["guidance"]
	assert guidance["applied_filters"] == [
		"cpc",
		"has_grant",
		"priority_date_after",
		"publication_number",
		"search",
	]
	# Names only, never the values the caller typed.
	assert "H99Z77" not in str(guidance)
	assert "ZZ1234567" not in str(guidance)
	assert guidance["fields_read"] == {
		"publication_number": ["publications"],
		"search": ["title", "summary"],
	}
	text = " ".join(guidance["suggestions"])
	for hint in ("priority/publication date", "has_grant", "cpc", "publication_number", "claims"):
		assert hint in text


async def test_search_patents_nonzero_hits_has_no_guidance_key(mock_gregory):
	mock_gregory.set_handler(_json({"count": 1, "next": None, "results": [{"patent_id": 1}]}))

	assert "guidance" not in await search_patents()


async def test_search_patents_records_intent_but_never_sends_it_upstream(mock_gregory, monkeypatch):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))
	calls = []

	async def fake_record(tool, text):
		calls.append((tool, text))

	monkeypatch.setattr(patents_module.intent_module, "record", fake_record)

	await search_patents(search="x", intent="patents on remyelination")

	assert calls == [("search_patents", "patents on remyelination")]
	assert "intent" not in mock_gregory.requests[0].url.params


# --- get_patent ---------------------------------------------------------------


async def test_get_patent_by_id_returns_the_full_record(mock_gregory):
	mock_gregory.set_handler(_json({"patent_id": 7, "publications": [{"publication_number": "EP1A1"}]}))

	result = await get_patent(patent_id=7)

	assert result["patent_id"] == 7
	(request,) = mock_gregory.requests
	assert request.url.path == "/patents/7/"
	assert "include" not in request.url.params


@pytest.mark.parametrize("kwargs", [{}, {"patent_id": 1, "publication_number": "EP1A1"}])
async def test_get_patent_needs_exactly_one_identifier(mock_gregory, kwargs):
	with pytest.raises(ToolError, match="exactly one of patent_id or publication_number"):
		await get_patent(**kwargs)

	assert mock_gregory.requests == []


async def test_get_patent_strips_team_data(mock_gregory):
	category = {"id": 5, "category_slug": "ocrelizumab"}
	mock_gregory.set_handler(
		_json(
			{
				"patent_id": 7,
				"teams": [{"id": 1}],
				"subjects": [{"id": 1, "subject_name": "MS", "team_id": 1}],
				"team_categories": [category],
			}
		)
	)

	result = await get_patent(patent_id=7)

	assert "teams" not in result
	assert result["subjects"] == [{"id": 1, "subject_name": "MS"}]
	assert result["team_categories"] == [category]


async def test_get_patent_by_publication_number_resolves_then_fetches(mock_gregory):
	def handler(request):
		if request.url.path == "/patents/":
			return httpx2.Response(200, json={"count": 1, "results": [{"patent_id": 42}]})
		return httpx2.Response(200, json={"patent_id": 42, "title": "T"})

	mock_gregory.set_handler(handler)

	result = await get_patent(publication_number="EP 4123456 A1")

	assert result["patent_id"] == 42
	lookup, detail = mock_gregory.requests
	assert lookup.url.path == "/patents/"
	assert lookup.url.params["publication_number"] == "EP 4123456 A1"
	assert detail.url.path == "/patents/42/"


async def test_get_patent_unknown_publication_number_is_not_found(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "results": []}))

	with pytest.raises(ToolError) as exc_info:
		await get_patent(publication_number="EP9999999A1")

	assert str(exc_info.value) == "Patent EP9999999A1 was not found."
	assert len(mock_gregory.requests) == 1


async def test_get_patent_ambiguous_publication_number_lists_the_ids(mock_gregory):
	mock_gregory.set_handler(
		_json({"count": 2, "results": [{"patent_id": 11}, {"patent_id": 12}]})
	)

	with pytest.raises(ToolError) as exc_info:
		await get_patent(publication_number="EP4123456")

	message = str(exc_info.value)
	assert "11, 12" in message
	assert "patent_id" in message
	assert len(mock_gregory.requests) == 1  # no detail call


async def test_get_patent_404_says_not_found_without_naming_sites(mock_gregory):
	mock_gregory.set_handler(_json({"detail": "Not found."}, status=404))

	with pytest.raises(ToolError) as exc_info:
		await get_patent(patent_id=555)

	message = str(exc_info.value)
	assert message == "Patent 555 was not found."
	for word in ("elsewhere", "scope", "site", "belongs", "instance", "enabled"):
		assert word not in message.lower()


async def test_get_patent_non_404_error_propagates(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(503, text="boom"))

	with pytest.raises(GregoryAPIError) as exc_info:
		await get_patent(patent_id=555)

	assert exc_info.value.status_code == 503


# --- claims -------------------------------------------------------------------


async def test_claims_are_not_requested_unless_asked_for(mock_gregory):
	# Even if upstream sent them, they are dropped.
	mock_gregory.set_handler(_json({"patent_id": 7, "claims": "1. A claim."}))

	result = await get_patent(patent_id=7)

	assert "claims" not in result
	assert "claims_length" not in result
	assert "include" not in mock_gregory.requests[0].url.params


async def test_include_claims_asks_django_for_them(mock_gregory):
	mock_gregory.set_handler(_json({"patent_id": 7, "claims": "1. A claim."}))

	await get_patent(patent_id=7, include_claims=True)

	assert mock_gregory.requests[0].url.params["include"] == "claims"


async def test_short_claims_are_returned_whole(mock_gregory):
	mock_gregory.set_handler(_json({"patent_id": 7, "claims": "1. A claim."}))

	result = await get_patent(patent_id=7, include_claims=True)

	assert result["claims"] == "1. A claim."
	assert result["claims_length"] == 11
	assert result["claims_truncated"] is False
	assert "claims_note" not in result


async def test_claims_at_the_cap_are_not_truncated(mock_gregory):
	mock_gregory.set_handler(_json({"patent_id": 7, "claims": "c" * CLAIMS_CAP}))

	result = await get_patent(patent_id=7, include_claims=True)

	assert len(result["claims"]) == CLAIMS_CAP
	assert result["claims_truncated"] is False


async def test_long_claims_are_capped_with_their_full_length(mock_gregory):
	mock_gregory.set_handler(_json({"patent_id": 7, "claims": "a" * 50_000}))

	result = await get_patent(patent_id=7, include_claims=True)

	assert CLAIMS_CAP == 20_000
	assert result["claims"] == "a" * CLAIMS_CAP
	assert result["claims_length"] == 50_000
	assert result["claims_truncated"] is True


@pytest.mark.parametrize("claims", [None, ""])
async def test_missing_claims_come_back_null_with_a_note(mock_gregory, claims):
	mock_gregory.set_handler(_json({"patent_id": 7, "claims": claims}))

	result = await get_patent(patent_id=7, include_claims=True)

	assert result["claims"] is None
	assert "full text" in result["claims_note"]
	assert "claims_length" not in result


async def test_missing_claims_key_is_also_a_note(mock_gregory):
	mock_gregory.set_handler(_json({"patent_id": 7}))

	result = await get_patent(patent_id=7, include_claims=True)

	assert result["claims"] is None
	assert "claims_note" in result


async def test_no_note_when_claims_were_not_asked_for(mock_gregory):
	mock_gregory.set_handler(_json({"patent_id": 7, "claims": None}))

	result = await get_patent(patent_id=7)

	assert "claims_note" not in result
	assert "claims" not in result


# --- detail include / links ---------------------------------------------------


async def test_include_subject_links_is_passed_through(mock_gregory):
	mock_gregory.set_handler(_json({"patent_id": 7}))

	await get_patent(patent_id=7, include_subject_links=True)

	assert mock_gregory.requests[0].url.params["include_subject_links"] == "true"


async def test_an_editor_detail_read_adds_editorial_to_include(mock_gregory, monkeypatch):
	mock_gregory.set_handler(_json({"patent_id": 7, "claims": "1."}))
	editor = EditorSession(user_id=7, site_id=1, tier="editor", scopes=frozenset({"articles:read"}))
	monkeypatch.setattr(patents_module, "get_editor_context", lambda: editor)

	await get_patent(patent_id=7, include_claims=True)
	await get_patent(patent_id=7)

	assert mock_gregory.requests[0].url.params["include"] == "claims,editorial"
	assert mock_gregory.requests[1].url.params["include"] == "editorial"


async def test_the_public_tier_gets_no_editorial(mock_gregory, monkeypatch):
	mock_gregory.set_handler(_json({"patent_id": 7}))
	public = EditorSession(user_id=7, site_id=1, tier="public", scopes=frozenset({"articles:read"}))
	monkeypatch.setattr(patents_module, "get_editor_context", lambda: public)

	await get_patent(patent_id=7)

	assert "include" not in mock_gregory.requests[0].url.params


# --- the patent additions to existing tools -----------------------------------


async def test_search_trials_passes_the_patent_link_filters(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	await search_trials(has_patent_link=True, include_subject_links=True)

	params = mock_gregory.requests[0].url.params
	assert params["has_patent_link"] == "true"
	assert params["include_subject_links"] == "true"


async def test_search_trials_leaves_them_off_by_default(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	await search_trials()

	params = mock_gregory.requests[0].url.params
	assert "has_patent_link" not in params
	assert "include_subject_links" not in params


async def test_get_trial_include_subject_links(mock_gregory):
	mock_gregory.set_handler(_json({"trial_id": 7, "patent_links": [{"patent_id": 1}]}))

	result = await get_trial(7, include_subject_links=True)

	assert result["patent_links"] == [{"patent_id": 1}]
	assert mock_gregory.requests[0].url.params["include_subject_links"] == "true"


async def test_get_trial_sends_no_params_by_default(mock_gregory):
	mock_gregory.set_handler(_json({"trial_id": 7}))

	await get_trial(7)

	assert "include_subject_links" not in mock_gregory.requests[0].url.params


async def test_list_sponsors_patent_applicants_and_counts(mock_gregory):
	mock_gregory.set_handler(
		_json(
			{
				"count": 1,
				"next": None,
				"results": [
					{"id": 1, "name": "Acme", "slug": "acme", "sponsor_type": "industry", "trials_count": 0, "patents_count": 3}
				],
			}
		)
	)

	result = await list_sponsors(include_patent_applicants=True)

	assert mock_gregory.requests[0].url.params["include_patent_applicants"] == "true"
	assert result["sponsors"][0]["patents_count"] == 3
	assert result["sponsors"][0]["trials_count"] == 0


async def test_list_sponsors_does_not_send_the_flag_by_default(mock_gregory):
	mock_gregory.set_handler(_json({"count": 0, "next": None, "results": []}))

	await list_sponsors()

	assert "include_patent_applicants" not in mock_gregory.requests[0].url.params


async def test_list_categories_carries_patents_count_total(mock_gregory):
	mock_gregory.set_handler(
		_json(
			{
				"next": None,
				"results": [
					{
						"id": 1,
						"category_name": "Ocrelizumab",
						"category_slug": "ocrelizumab",
						"modality": "biologic_antibody",
						"article_count_total": 10,
						"trials_count_total": 4,
						"patents_count_total": 6,
					}
				],
			}
		)
	)

	result = await list_categories()

	assert result["categories"][0]["patents_count_total"] == 6


async def test_get_stats_patents_scope(mock_gregory):
	mock_gregory.set_handler(_json({"total": 5, "with_grant": 1, "with_trial_link": 2, "by_priority_year": []}))

	result = await get_stats(scope="patents", subject_id=3, search="antibody")

	assert result["total"] == 5
	(request,) = mock_gregory.requests
	assert request.url.path == "/patents/stats/"
	assert request.url.params["subject_id"] == "3"
	assert request.url.params["search"] == "antibody"
