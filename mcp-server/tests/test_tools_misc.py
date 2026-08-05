from __future__ import annotations

import httpx2
import pytest

from gregory_mcp.tools.authors import get_author, search_authors
from gregory_mcp.tools.catalog import list_categories, list_sponsors, list_subjects
from gregory_mcp.tools.stats import get_stats


async def test_search_authors(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(
			200,
			json={
				"count": 1,
				"results": [
					{
						"author_id": 5,
						"full_name": "Jane Doe",
						"ORCID": "0000-0000-0000-0001",
						"country": "PT",
						"articles_count": 10,
						"relevant_articles_count": 3,
						"articles_list": [{"title": "should not appear"}],
					}
				],
			},
		)
	)

	result = await search_authors(search="Jane")

	author = result["authors"][0]
	assert author["orcid"] == "0000-0000-0000-0001"
	assert "articles_list" not in author


async def test_get_author_with_coauthors(mock_gregory):
	calls = []

	def handler(request):
		calls.append(request.url.path)
		if request.url.path.endswith("/coauthors/"):
			return httpx2.Response(200, json={"results": [{"author_id": 6}]})
		return httpx2.Response(200, json={"author_id": 5, "full_name": "Jane Doe"})

	mock_gregory.set_handler(handler)

	result = await get_author(5, include_coauthors=True)

	assert result["coauthors"] == [{"author_id": 6}]
	assert calls == ["/authors/5/", "/authors/5/coauthors/"]


async def test_get_author_without_coauthors_makes_one_call(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"author_id": 5}))

	await get_author(5)

	assert len(mock_gregory.requests) == 1


async def test_list_subjects_follows_pagination(mock_gregory):
	def handler(request):
		page = request.url.params.get("page", "1")
		if page == "1":
			return httpx2.Response(
				200,
				json={"next": "https://gregory.test/subjects/?page=2", "results": [{"id": 1, "subject_name": "A"}]},
			)
		return httpx2.Response(200, json={"next": None, "results": [{"id": 2, "subject_name": "B"}]})

	mock_gregory.set_handler(handler)

	result = await list_subjects()

	assert result["count"] == 2
	assert [s["id"] for s in result["subjects"]] == [1, 2]
	assert len(mock_gregory.requests) == 2


async def test_list_categories_excludes_expensive_ordering(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"next": None, "results": []}))

	await list_categories(team_id=1)

	params = mock_gregory.requests[0].url.params
	assert "ordering" not in params


async def test_list_sponsors_is_paginated_not_exhaustive(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"count": 500, "next": "x", "results": []}))

	result = await list_sponsors()

	assert len(mock_gregory.requests) == 1
	assert mock_gregory.requests[0].url.params["page_size"] == "25"
	assert result["count"] == 500


async def test_get_stats_global_maps_team_and_subject(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"articles": 1}))

	await get_stats(scope="global", team_id=1, subject_id=2)

	request = mock_gregory.requests[0]
	assert request.url.path == "/stats/"
	assert request.url.params["team"] == "1"
	assert request.url.params["subject"] == "2"


async def test_get_stats_articles_scope(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"total": 1}))

	await get_stats(scope="articles", team_id=1, relevant=True)

	request = mock_gregory.requests[0]
	assert request.url.path == "/articles/stats/"
	assert request.url.params["team_id"] == "1"
	assert request.url.params["relevant"] == "true"


async def test_get_stats_trials_scope(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"total": 1}))

	await get_stats(scope="trials", subject_id=3)

	request = mock_gregory.requests[0]
	assert request.url.path == "/trials/stats/"
	assert request.url.params["subject_id"] == "3"


async def test_get_stats_rejects_unknown_scope(mock_gregory):
	with pytest.raises(ValueError, match="unknown scope"):
		await get_stats(scope="bogus")

	assert mock_gregory.requests == []
