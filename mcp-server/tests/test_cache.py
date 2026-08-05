from __future__ import annotations

import asyncio

import httpx2
import pytest

from gregory_mcp.cache import CatalogCache, get_all_pages_cached, get_catalog_cache, reset_catalog_cache
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
