from __future__ import annotations

from dataclasses import replace

import httpx2
import pytest

from gregory_mcp.client import GregoryAPIError, GregoryClient
from tests.conftest import TEST_SETTINGS


async def test_retries_on_5xx_then_succeeds():
	settings = replace(TEST_SETTINGS, max_retries=2)
	client = GregoryClient(settings)
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
	client = GregoryClient(settings)
	client._client._transport = httpx2.MockTransport(lambda r: httpx2.Response(503))

	with pytest.raises(GregoryAPIError):
		await client.get("/articles/")


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
