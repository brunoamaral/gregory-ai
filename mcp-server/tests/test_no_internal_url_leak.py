"""Regression test for the `next` passthrough leaking the internal
container hostname (e.g. http://gregory:8000/...) to MCP clients.

Every paginated tool used to return `"next": data.get("next")` — the raw
upstream URL, unusable by any external client and a minor information
disclosure on an unauthenticated endpoint. They return `next_page: int |
None` instead now. This walks each tool's full result recursively and
fails if any string value contains the API host at all, so the specific
bug (and any similar future one) can't reappear silently.
"""

from __future__ import annotations

import httpx2
import pytest

from gregory_mcp.tools.articles import search_articles
from gregory_mcp.tools.authors import search_authors
from gregory_mcp.tools.catalog import list_sponsors
from gregory_mcp.tools.trials import search_trials
from tests.conftest import TEST_SETTINGS

# What the real bug looked like in production, reproduced in the mock so the
# test would have caught it.
LEAKY_NEXT = f"{TEST_SETTINGS.api_url}/whatever/?page=2"


def _find_leaks(value, host: str, path: str = "$") -> list[str]:
	leaks = []
	if isinstance(value, str):
		if host in value:
			leaks.append(f"{path} = {value!r}")
	elif isinstance(value, dict):
		for key, item in value.items():
			leaks.extend(_find_leaks(item, host, f"{path}.{key}"))
	elif isinstance(value, (list, tuple)):
		for i, item in enumerate(value):
			leaks.extend(_find_leaks(item, host, f"{path}[{i}]"))
	return leaks


PAGINATED_TOOLS = [
	(search_articles, {}),
	(search_trials, {}),
	(search_authors, {}),
	(list_sponsors, {}),
]


@pytest.mark.parametrize("fn,kwargs", PAGINATED_TOOLS, ids=[fn.__name__ for fn, _ in PAGINATED_TOOLS])
async def test_tool_output_never_contains_the_api_host(fn, kwargs, mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(200, json={"count": 1, "next": LEAKY_NEXT, "results": []})
	)

	result = await fn(**kwargs)

	host = TEST_SETTINGS.api_url.removeprefix("https://").removeprefix("http://")
	leaks = _find_leaks(result, host)
	assert not leaks, f"tool output leaks the API host: {leaks}"
