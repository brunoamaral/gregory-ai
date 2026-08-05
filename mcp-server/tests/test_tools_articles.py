from __future__ import annotations

import httpx2

from gregory_mcp.tools.articles import get_article, search_articles


async def test_search_articles_compacts_results(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(
			200,
			json={
				"count": 1,
				"next": None,
				"results": [
					{
						"article_id": 42,
						"title": "Stem cells in MS",
						"published_date": "2026-01-01",
						"container_title": "Nature",
						"doi": "10.1/x",
						"link": "http://example.com/42",
						"summary": "x" * 1000,
						"ml_score": 0.87,
						"access": "open",
						"authors": [{"full_name": "Should not appear"}],
					}
				],
			},
		)
	)

	result = await search_articles(search="stem cells")

	assert result["count"] == 1
	article = result["articles"][0]
	assert article["article_id"] == 42
	assert article["journal"] == "Nature"
	assert len(article["summary"]) <= 401
	assert "authors" not in article  # compact projection drops the full record

	request = mock_gregory.requests[0]
	assert request.url.path == "/articles/"
	assert request.url.params["search"] == "stem cells"
	assert request.url.params["page"] == "1"
	assert request.url.params["page_size"] == "10"


async def test_search_articles_drops_none_filters(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"count": 0, "results": []}))

	await search_articles(search="x")

	params = mock_gregory.requests[0].url.params
	assert "subject_id" not in params
	assert "relevant" not in params
	assert "doi" not in params


async def test_search_articles_caps_page_size(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"count": 0, "results": []}))

	await search_articles(page_size=999)

	assert mock_gregory.requests[0].url.params["page_size"] == "25"


async def test_search_articles_clamps_non_positive_page_and_page_size(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"count": 0, "results": []}))

	await search_articles(page=-1, page_size=0)

	params = mock_gregory.requests[0].url.params
	assert params["page"] == "1"
	assert params["page_size"] == "1"


async def test_get_article_returns_full_record(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(200, json={"article_id": 42, "authors": [{"full_name": "A"}]})
	)

	result = await get_article(42)

	assert result["article_id"] == 42
	assert result["authors"] == [{"full_name": "A"}]
	assert mock_gregory.requests[0].url.path == "/articles/42/"
