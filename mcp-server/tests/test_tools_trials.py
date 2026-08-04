from __future__ import annotations

import httpx2

from gregory_mcp.tools.trials import get_trial, search_trials


async def test_search_trials_compacts_results(mock_gregory):
    mock_gregory.set_handler(
        lambda request: httpx2.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [
                    {
                        "trial_id": 7,
                        "title": "A phase 2 trial",
                        "published_date": "2026-02-01",
                        "recruitment_status_normalized": "recruiting",
                        "phase_normalized": ["phase_2"],
                        "study_type_normalized": "interventional",
                        "sponsor": {"name": "Acme Pharma", "slug": "acme-pharma"},
                        "primary_sponsor": "Acme Pharma Inc.",
                        "countries_normalized": ["DE", "FR"],
                        "link": "http://example.com/7",
                        "summary": "y" * 1000,
                        "identifiers": {"nct": "NCT00000007"},
                        "trial_sites": [{"name": "Should not appear"}],
                    }
                ],
            },
        )
    )

    result = await search_trials(recruitment_status_normalized="recruiting")

    trial = result["trials"][0]
    assert trial["trial_id"] == 7
    assert trial["sponsor"] == "Acme Pharma"
    assert trial["countries"] == ["DE", "FR"]
    assert "trial_sites" not in trial

    params = mock_gregory.requests[0].url.params
    assert params["recruitment_status_normalized"] == "recruiting"


async def test_search_trials_falls_back_to_primary_sponsor(mock_gregory):
    mock_gregory.set_handler(
        lambda request: httpx2.Response(
            200,
            json={
                "count": 1,
                "results": [{"trial_id": 8, "primary_sponsor": "Raw Sponsor String", "sponsor": None}],
            },
        )
    )

    result = await search_trials()

    assert result["trials"][0]["sponsor"] == "Raw Sponsor String"


async def test_get_trial_returns_full_record(mock_gregory):
    mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"trial_id": 7, "trial_sites": []}))

    result = await get_trial(7)

    assert result["trial_id"] == 7
    assert mock_gregory.requests[0].url.path == "/trials/7/"
