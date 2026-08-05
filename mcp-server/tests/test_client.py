from __future__ import annotations

from dataclasses import replace

import httpx2
import pytest

from gregory_mcp.client import (
	BASE_BACKOFF_SECONDS,
	MAX_BACKOFF_SECONDS,
	GregoryAPIError,
	GregoryClient,
	GregoryPaginationTruncatedError,
)
from tests.conftest import TEST_SETTINGS


async def _no_delay(seconds: float) -> None:
	"""A sleep stand-in that doesn't — keeps retry tests from taking real
	wall-clock time when they don't care about the spacing itself."""


class RecordingSleep:
	"""A sleep stand-in that records each delay instead of waiting."""

	def __init__(self):
		self.delays: list[float] = []

	async def __call__(self, seconds: float) -> None:
		self.delays.append(seconds)


def _max_jitter(low: float, high: float) -> float:
	"""A jitter stand-in that always returns the upper bound, so backoff
	values are predictable in tests instead of randomly distributed."""
	return high


async def test_retries_on_5xx_then_succeeds():
	settings = replace(TEST_SETTINGS, max_retries=2)
	client = GregoryClient(settings, sleep=_no_delay)
	attempts = []

	def handler(request):
		attempts.append(1)
		if len(attempts) < 2:
			return httpx2.Response(500, json={"detail": "boom"})
		return httpx2.Response(200, json={"ok": True})

	client._client._transport = httpx2.MockTransport(handler)

	result = await client.get("/articles/")

	assert result == {"ok": True}
	assert len(attempts) == 2


async def test_gives_up_after_max_retries():
	settings = replace(TEST_SETTINGS, max_retries=1)
	client = GregoryClient(settings, sleep=_no_delay)
	client._client._transport = httpx2.MockTransport(lambda r: httpx2.Response(503))

	with pytest.raises(GregoryAPIError):
		await client.get("/articles/")


async def test_429_is_retried_not_raised_immediately():
	settings = replace(TEST_SETTINGS, max_retries=2)
	client = GregoryClient(settings, sleep=_no_delay)
	attempts = []

	def handler(request):
		attempts.append(1)
		if len(attempts) < 2:
			return httpx2.Response(429, text="rate limited")
		return httpx2.Response(200, json={"ok": True})

	client._client._transport = httpx2.MockTransport(handler)

	result = await client.get("/articles/")

	assert result == {"ok": True}
	assert len(attempts) == 2


async def test_backoff_doubles_and_is_capped():
	settings = replace(TEST_SETTINGS, max_retries=5)
	sleep = RecordingSleep()
	client = GregoryClient(settings, sleep=sleep, jitter=_max_jitter)
	client._client._transport = httpx2.MockTransport(lambda r: httpx2.Response(500))

	with pytest.raises(GregoryAPIError):
		await client.get("/articles/")

	expected = [min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * 2**i) for i in range(5)]
	assert sleep.delays == expected
	assert sleep.delays[-1] == MAX_BACKOFF_SECONDS  # confirms the cap actually bites


async def test_backoff_jitter_stays_within_bounds():
	settings = replace(TEST_SETTINGS, max_retries=3)
	sleep = RecordingSleep()
	client = GregoryClient(settings, sleep=sleep)  # real random.uniform jitter
	client._client._transport = httpx2.MockTransport(lambda r: httpx2.Response(500))

	with pytest.raises(GregoryAPIError):
		await client.get("/articles/")

	for i, delay in enumerate(sleep.delays):
		cap = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * 2**i)
		assert 0 <= delay <= cap


async def test_retry_after_header_overrides_computed_backoff():
	settings = replace(TEST_SETTINGS, max_retries=1)
	sleep = RecordingSleep()
	client = GregoryClient(settings, sleep=sleep, jitter=_max_jitter)
	attempts = []

	def handler(request):
		attempts.append(1)
		if len(attempts) < 2:
			return httpx2.Response(429, headers={"Retry-After": "7"})
		return httpx2.Response(200, json={"ok": True})

	client._client._transport = httpx2.MockTransport(handler)

	await client.get("/articles/")

	assert sleep.delays == [7.0]  # not the jitter-computed 0.2s


async def test_invalid_retry_after_falls_back_to_computed_backoff():
	settings = replace(TEST_SETTINGS, max_retries=1)
	sleep = RecordingSleep()
	client = GregoryClient(settings, sleep=sleep, jitter=_max_jitter)
	client._client._transport = httpx2.MockTransport(
		lambda r: httpx2.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
	)

	with pytest.raises(GregoryAPIError):
		await client.get("/articles/")

	assert sleep.delays == [BASE_BACKOFF_SECONDS]


async def test_transport_error_retry_also_backs_off():
	settings = replace(TEST_SETTINGS, max_retries=1)
	sleep = RecordingSleep()
	client = GregoryClient(settings, sleep=sleep, jitter=_max_jitter)
	attempts = []

	def handler(request):
		attempts.append(1)
		if len(attempts) < 2:
			raise httpx2.ConnectError("boom", request=request)
		return httpx2.Response(200, json={"ok": True})

	client._client._transport = httpx2.MockTransport(handler)

	await client.get("/articles/")

	assert sleep.delays == [BASE_BACKOFF_SECONDS]


async def test_4xx_raises_immediately_without_retry():
	settings = replace(TEST_SETTINGS, max_retries=3)
	client = GregoryClient(settings)
	attempts = []

	def handler(request):
		attempts.append(1)
		return httpx2.Response(404, text="not found")

	client._client._transport = httpx2.MockTransport(handler)

	with pytest.raises(GregoryAPIError) as exc_info:
		await client.get("/articles/999/")

	assert exc_info.value.status_code == 404
	assert len(attempts) == 1


async def test_none_params_are_dropped():
	client = GregoryClient(TEST_SETTINGS)
	seen = {}

	def handler(request):
		seen.update(request.url.params)
		return httpx2.Response(200, json={})

	client._client._transport = httpx2.MockTransport(handler)

	await client.get("/articles/", {"team_id": 1, "subject_id": None, "search": None})

	assert seen == {"team_id": "1"}


async def test_get_all_pages_returns_everything_when_it_fits():
	client = GregoryClient(TEST_SETTINGS)

	def handler(request):
		page = request.url.params.get("page", "1")
		if page == "1":
			return httpx2.Response(200, json={"next": "https://x/?page=2", "results": [{"id": 1}]})
		return httpx2.Response(200, json={"next": None, "results": [{"id": 2}]})

	client._client._transport = httpx2.MockTransport(handler)

	results = await client.get_all_pages("/subjects/", max_pages=5)

	assert [r["id"] for r in results] == [1, 2]


async def test_get_all_pages_raises_rather_than_silently_truncating():
	client = GregoryClient(TEST_SETTINGS)

	def handler(request):
		# `next` never runs out — an unbounded/larger-than-expected catalog.
		return httpx2.Response(200, json={"next": "https://x/?page=999", "results": [{"id": 1}]})

	client._client._transport = httpx2.MockTransport(handler)

	with pytest.raises(GregoryPaginationTruncatedError) as exc_info:
		await client.get_all_pages("/sponsors/", max_pages=3)

	assert exc_info.value.path == "/sponsors/"
	assert exc_info.value.max_pages == 3
	assert exc_info.value.fetched == 3
