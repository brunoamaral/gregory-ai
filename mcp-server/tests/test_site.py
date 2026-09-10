from __future__ import annotations

from dataclasses import replace

import httpx2
import pytest
from mcp.server.context import ServerRequestContext

from gregory_mcp.site import (
	SiteMiddleware,
	_extract_host_header,
	_match_domain,
	_normalize_host,
	init_site_resolution,
	resolve_site_id,
)
from gregory_mcp.site_context import get_current_site_id
from tests.conftest import TEST_SETTINGS


# --- host normalisation -----------------------------------------------------


def test_normalize_host_lowercases():
	assert _normalize_host("Gregory-AI.Brain-Regeneration.COM") == "gregory-ai.brain-regeneration.com"


def test_normalize_host_strips_port():
	assert _normalize_host("gregory-ai.brain-regeneration.com:8443") == "gregory-ai.brain-regeneration.com"


def test_normalize_host_strips_ipv6_brackets():
	assert _normalize_host("[::1]:8001") == "::1"


def test_normalize_host_none_or_empty_is_none():
	assert _normalize_host(None) is None
	assert _normalize_host("") is None


# --- domain matching (mirrors django/subscriptions/views.py:_find_site_by_domain) --


def test_match_domain_exact():
	domain_map = {"brain-regeneration.com": 3, "encefalites.pt": 7}
	assert _match_domain("brain-regeneration.com", domain_map) == 3


def test_match_domain_falls_back_one_subdomain_level():
	domain_map = {"brain-regeneration.com": 3}
	# The MCP server's own deployed host, per docs/07-mcp-server.md.
	assert _match_domain("gregory-ai.brain-regeneration.com", domain_map) == 3


def test_match_domain_two_levels_stripped_is_not_attempted():
	# _find_site_by_domain only ever strips ONE subdomain level.
	domain_map = {"brain-regeneration.com": 3}
	assert _match_domain("a.b.brain-regeneration.com", domain_map) is None


def test_match_domain_no_match_returns_none():
	domain_map = {"brain-regeneration.com": 3}
	assert _match_domain("unrelated.example.com", domain_map) is None


def test_match_domain_bare_domain_has_no_parent_to_strip():
	# len(parts) < 3 — a two-label host has no "one subdomain level" to strip.
	domain_map = {"other.com": 9}
	assert _match_domain("example.com", domain_map) is None


# --- resolve_site_id: env override wins outright ----------------------------


async def test_env_override_wins_without_touching_the_network(mock_gregory):
	calls = []
	mock_gregory.set_handler(lambda request: (calls.append(1), httpx2.Response(200, json=[]))[1])
	init_site_resolution(replace(TEST_SETTINGS, site_id_override=3))

	result = await resolve_site_id("gregory-ai.brain-regeneration.com")

	assert result == 3
	assert calls == []  # GET /sites/ never called — the override short-circuits


async def test_env_override_wins_even_with_no_host_header(mock_gregory):
	init_site_resolution(replace(TEST_SETTINGS, site_id_override=5))
	assert await resolve_site_id(None) == 5


# --- resolve_site_id: Host resolution via GET /sites/ -----------------------


def _sites_handler(rows):
	return lambda request: httpx2.Response(200, json=rows)


async def test_host_resolves_via_sites_directory(mock_gregory):
	mock_gregory.set_handler(
		_sites_handler([{"site_id": 3, "domain": "brain-regeneration.com", "name": "Brain Regeneration"}])
	)

	result = await resolve_site_id("gregory-ai.brain-regeneration.com")

	assert result == 3


async def test_sites_directory_is_cached_across_resolutions(mock_gregory):
	mock_gregory.set_handler(
		_sites_handler([{"site_id": 3, "domain": "brain-regeneration.com", "name": "Brain Regeneration"}])
	)

	await resolve_site_id("gregory-ai.brain-regeneration.com")
	await resolve_site_id("gregory-ai.brain-regeneration.com")
	await resolve_site_id("brain-regeneration.com")

	sites_requests = [r for r in mock_gregory.requests if r.url.path == "/sites/"]
	assert len(sites_requests) == 1


async def test_no_host_header_omits_without_calling_the_api(mock_gregory):
	calls = []
	mock_gregory.set_handler(lambda request: (calls.append(1), httpx2.Response(200, json=[]))[1])

	assert await resolve_site_id(None) is None
	assert calls == []


async def test_unresolvable_host_returns_none(mock_gregory):
	mock_gregory.set_handler(_sites_handler([{"site_id": 3, "domain": "brain-regeneration.com", "name": "BR"}]))

	assert await resolve_site_id("totally-unrelated-domain.example") is None


async def test_sites_endpoint_missing_degrades_to_none_rather_than_raising(mock_gregory):
	"""GET /sites/ is new (PR #859) — an older API build 404s. That must
	degrade to "no domain map", not break every tool call."""
	mock_gregory.set_handler(lambda request: httpx2.Response(404, text="not found"))

	assert await resolve_site_id("gregory-ai.brain-regeneration.com") is None


async def test_sites_endpoint_unexpected_shape_degrades_to_none(mock_gregory):
	"""GET /sites/ is documented as a plain JSON array, not the paginated
	{count, next, results} shape other list endpoints use — guard against a
	future shape change surfacing as a crash instead of a degraded result."""
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json={"results": []}))

	assert await resolve_site_id("gregory-ai.brain-regeneration.com") is None


async def test_sites_call_itself_carries_no_site_id(mock_gregory):
	"""GET /sites/ is the discovery entry point and is deliberately unscoped
	— it "cannot require the thing it exists to provide" (schema.yml). It
	must never carry a site_id of its own, even transitively through the
	same client.get() every other call goes through."""
	mock_gregory.set_handler(_sites_handler([]))

	await resolve_site_id("gregory-ai.brain-regeneration.com")

	sites_requests = [r for r in mock_gregory.requests if r.url.path == "/sites/"]
	assert len(sites_requests) == 1
	assert "site_id" not in sites_requests[0].url.params


# --- SiteMiddleware ----------------------------------------------------------


class _FakeRequest:
	def __init__(self, headers: dict[str, str]):
		self.headers = headers


def _make_ctx(*, request=None, request_id="req-1", method="tools/call"):
	return ServerRequestContext(
		session=None,
		lifespan_context={},
		protocol_version="2026-07-28",
		method=method,
		params={},
		request_id=request_id,
		request=request,
	)


async def test_extract_host_header_reads_the_request_headers():
	ctx = _make_ctx(request=_FakeRequest({"host": "gregory-ai.brain-regeneration.com"}))
	assert _extract_host_header(ctx) == "gregory-ai.brain-regeneration.com"


def test_extract_host_header_none_on_stdio_shaped_request():
	ctx = _make_ctx(request=None)
	assert _extract_host_header(ctx) is None


async def test_middleware_sets_site_id_for_the_duration_of_the_call(mock_gregory):
	mock_gregory.set_handler(_sites_handler([{"site_id": 3, "domain": "brain-regeneration.com", "name": "BR"}]))
	seen_during_call = {}

	async def call_next(ctx):
		seen_during_call["site_id"] = get_current_site_id()
		return {"ok": True}

	ctx = _make_ctx(request=_FakeRequest({"host": "gregory-ai.brain-regeneration.com"}))
	result = await SiteMiddleware()(ctx, call_next)

	assert result == {"ok": True}
	assert seen_during_call["site_id"] == 3
	assert get_current_site_id() is None  # reset once the request finishes


async def test_middleware_resets_even_when_call_next_raises(mock_gregory):
	mock_gregory.set_handler(_sites_handler([{"site_id": 3, "domain": "brain-regeneration.com", "name": "BR"}]))

	async def call_next(ctx):
		raise RuntimeError("boom")

	ctx = _make_ctx(request=_FakeRequest({"host": "gregory-ai.brain-regeneration.com"}))
	with pytest.raises(RuntimeError):
		await SiteMiddleware()(ctx, call_next)

	assert get_current_site_id() is None


async def test_middleware_skips_resolution_for_notifications(mock_gregory):
	calls = []
	mock_gregory.set_handler(lambda request: (calls.append(1), httpx2.Response(200, json=[]))[1])

	async def call_next(ctx):
		return None

	ctx = _make_ctx(request=_FakeRequest({"host": "gregory-ai.brain-regeneration.com"}), request_id=None)
	await SiteMiddleware()(ctx, call_next)

	assert calls == []  # no /sites/ fetch triggered for a notification


async def test_middleware_with_no_matching_site_calls_upstream_without_site_id(mock_gregory):
	def handler(request):
		if request.url.path == "/sites/":
			return httpx2.Response(
				200, json=[{"site_id": 3, "domain": "brain-regeneration.com", "name": "BR"}]
			)
		return httpx2.Response(200, json={"next": None, "results": []})

	mock_gregory.set_handler(handler)

	from gregory_mcp.tools.catalog import list_subjects

	async def call_next(ctx):
		return await list_subjects()

	# A host that resolves to no known site.
	ctx = _make_ctx(request=_FakeRequest({"host": "unrelated.example.com"}))
	await SiteMiddleware()(ctx, call_next)

	subjects_requests = [r for r in mock_gregory.requests if r.url.path == "/subjects/"]
	assert len(subjects_requests) == 1
	assert "site_id" not in subjects_requests[0].url.params


async def test_sites_endpoint_non_json_body_degrades_to_none(mock_gregory):
	"""A 2xx response with a non-JSON body (e.g. an HTML proxy/gateway error
	page returned with a 200/204) makes response.json() raise
	json.JSONDecodeError from inside GregoryClient.get() — a different
	exception than GregoryAPIError, which only covers HTTP error statuses
	and transport failures. _fetch_site_directory's docstring promises it
	never raises; this must degrade to "no domain map" like every other
	failure mode here, not propagate and fail the whole request."""
	mock_gregory.set_handler(lambda request: httpx2.Response(200, text="<html>Bad Gateway</html>"))

	assert await resolve_site_id("gregory-ai.brain-regeneration.com") is None
