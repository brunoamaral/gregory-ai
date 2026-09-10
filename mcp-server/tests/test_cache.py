from __future__ import annotations

import asyncio

import httpx2
import pytest

from gregory_mcp.cache import CatalogCache, get_all_pages_cached, get_catalog_cache, reset_catalog_cache
from gregory_mcp.site_context import _current_site_id
from gregory_mcp.tools.articles import search_articles
from gregory_mcp.tools.catalog import list_categories, list_subjects


@pytest.fixture(autouse=True)
def _reset_cache():
	reset_catalog_cache()
	yield
	reset_catalog_cache()


class FakeClock:
	def __init__(self, start: float = 0.0):
		self.now = start

	def __call__(self) -> float:
		return self.now

	def advance(self, seconds: float) -> None:
		self.now += seconds


async def test_cache_hit_skips_the_fetch():
	cache = CatalogCache()
	calls = []

	async def fetch():
		calls.append(1)
		return ["row"]

	first = await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)
	second = await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)

	assert first == second == ["row"]
	assert len(calls) == 1


async def test_cache_expires_after_ttl():
	clock = FakeClock()
	cache = CatalogCache(ttl_ms=1000, clock=clock)
	calls = []

	async def fetch():
		calls.append(1)
		return ["row", len(calls)]

	await cache.get_or_fetch("/subjects/", None, fetch)
	clock.advance(0.5)
	await cache.get_or_fetch("/subjects/", None, fetch)
	assert len(calls) == 1  # still within TTL

	clock.advance(0.6)  # total 1.1s > 1s TTL
	await cache.get_or_fetch("/subjects/", None, fetch)
	assert len(calls) == 2  # expired, refetched


async def test_none_valued_params_and_omitted_params_share_a_key():
	cache = CatalogCache()
	calls = []

	async def fetch():
		calls.append(1)
		return ["row"]

	await cache.get_or_fetch("/subjects/", {"team_id": None, "search": None}, fetch)
	await cache.get_or_fetch("/subjects/", None, fetch)
	await cache.get_or_fetch("/subjects/", {}, fetch)

	assert len(calls) == 1


async def test_different_params_are_different_keys():
	cache = CatalogCache()
	calls = []

	async def fetch():
		calls.append(1)
		return ["row"]

	await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)
	await cache.get_or_fetch("/subjects/", {"team_id": 2}, fetch)

	assert len(calls) == 2


async def test_different_current_site_ids_are_different_keys():
	"""The highest-risk failure mode here: without this, site A's caller
	could be served site B's cached subjects/categories, because the two
	calls look identical from CatalogCache's own params — the site_id that
	distinguishes them is added transport-side by GregoryClient.get(), not
	passed into `params` by the caller."""
	cache = CatalogCache()
	calls = []

	async def fetch():
		calls.append(1)
		return [f"row-for-call-{len(calls)}"]

	token_a = _current_site_id.set(1)
	try:
		result_a = await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)
	finally:
		_current_site_id.reset(token_a)

	token_b = _current_site_id.set(2)
	try:
		result_b = await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)
	finally:
		_current_site_id.reset(token_b)

	assert len(calls) == 2
	assert result_a != result_b


async def test_same_current_site_id_still_shares_the_key():
	cache = CatalogCache()
	calls = []

	async def fetch():
		calls.append(1)
		return ["row"]

	token = _current_site_id.set(1)
	try:
		await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)
		await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)
	finally:
		_current_site_id.reset(token)

	assert len(calls) == 1


async def test_no_current_site_id_is_its_own_stable_key():
	"""Outside a tracked request (site_id unresolved, or most of this test
	suite calling cache code directly) every caller shares the same
	`None` bucket — unaffected by this change."""
	cache = CatalogCache()
	calls = []

	async def fetch():
		calls.append(1)
		return ["row"]

	await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)
	await cache.get_or_fetch("/subjects/", {"team_id": 1}, fetch)

	assert len(calls) == 1


async def test_single_flight_concurrent_cold_callers_fetch_once():
	cache = CatalogCache()
	calls = []
	release = asyncio.Event()

	async def slow_fetch():
		calls.append(1)
		await release.wait()
		return ["row"]

	async def caller():
		return await cache.get_or_fetch("/categories/", None, slow_fetch)

	tasks = [asyncio.create_task(caller()) for _ in range(10)]
	await asyncio.sleep(0.05)  # let every caller reach the fetch/lock
	release.set()
	results = await asyncio.gather(*tasks)

	assert all(r == ["row"] for r in results)
	assert len(calls) == 1


async def test_list_subjects_and_subjects_resource_share_the_cache(mock_gregory):
	calls = []

	def handler(request):
		calls.append(request.url.path)
		return httpx2.Response(200, json={"next": None, "results": [{"id": 1, "subject_name": "MS", "team_id": 1}]})

	mock_gregory.set_handler(handler)

	from gregory_mcp.resources import register_resources

	captured = {}

	class FakeServer:
		def resource(self, *args, **kwargs):
			def decorator(fn):
				captured[kwargs.get("name")] = fn
				return fn

			return decorator

	register_resources(FakeServer())

	await list_subjects()
	await captured["subjects_catalog"]()

	assert len(calls) == 1  # second call hit the cache, not the network


async def test_search_tools_are_never_cached(mock_gregory):
	calls = []

	def handler(request):
		calls.append(1)
		return httpx2.Response(200, json={"count": 0, "next": None, "results": []})

	mock_gregory.set_handler(handler)

	await search_articles(search="stem cells")
	await search_articles(search="stem cells")

	assert len(calls) == 2  # identical calls still both hit the network


async def test_list_categories_uses_the_shared_cache(mock_gregory):
	calls = []

	def handler(request):
		calls.append(1)
		return httpx2.Response(200, json={"next": None, "results": []})

	mock_gregory.set_handler(handler)

	await list_categories(team_id=1)
	await list_categories(team_id=1)

	assert len(calls) == 1


async def test_list_subjects_does_not_leak_across_sites(mock_gregory):
	"""End-to-end version of test_different_current_site_ids_are_different_keys,
	through the actual tool rather than CatalogCache directly — confirms
	list_subjects (and by the same code path, list_categories, and the
	gregory://subjects / gregory://categories resources) never serve one
	site's catalog to another's caller."""
	site_a_subject = {"id": 1, "subject_name": "MS (site A)", "team_id": 1}
	site_b_subject = {"id": 2, "subject_name": "MS (site B)", "team_id": 4}

	def handler(request):
		site_id = request.url.params.get("site_id")
		row = site_b_subject if site_id == "2" else site_a_subject
		return httpx2.Response(200, json={"next": None, "results": [row]})

	mock_gregory.set_handler(handler)

	token_a = _current_site_id.set(1)
	try:
		result_a = await list_subjects()
	finally:
		_current_site_id.reset(token_a)

	token_b = _current_site_id.set(2)
	try:
		result_b = await list_subjects()
	finally:
		_current_site_id.reset(token_b)

	assert result_a["subjects"] == [{"id": 1, "subject_name": "MS (site A)", "team_id": 1}]
	assert result_b["subjects"] == [{"id": 2, "subject_name": "MS (site B)", "team_id": 4}]


def test_get_catalog_cache_is_a_singleton():
	assert get_catalog_cache() is get_catalog_cache()


async def test_get_all_pages_cached_reuses_process_wide_cache(mock_gregory):
	calls = []

	def handler(request):
		calls.append(1)
		return httpx2.Response(200, json={"next": None, "results": []})

	mock_gregory.set_handler(handler)

	await get_all_pages_cached("/subjects/")
	await get_all_pages_cached("/subjects/")

	assert len(calls) == 1
