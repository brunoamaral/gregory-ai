from __future__ import annotations

import httpx2
import pytest
from mcp.server.context import ServerRequestContext

from gregory_mcp.site import (
	SiteMiddleware,
	_extract_host_header,
	_match_domain,
	_normalize_host,
)
from gregory_mcp.site_context import get_current_site_id, get_current_tenant
from tests.conftest import tenants_payload

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


# --- domain matching (mirrors django/gregory/site_resolution.py:find_site_by_domain) --


def test_match_domain_exact():
	domain_map = {"brain-regeneration.com": 3, "encefalites.pt": 7}
	assert _match_domain("brain-regeneration.com", domain_map) == 3


def test_match_domain_falls_back_one_subdomain_level():
	domain_map = {"brain-regeneration.com": 3}
	# The MCP server's own deployed host, per docs/07-mcp-server.md.
	assert _match_domain("gregory-ai.brain-regeneration.com", domain_map) == 3


def test_match_domain_two_levels_stripped_is_not_attempted():
	# find_site_by_domain only ever strips ONE subdomain level.
	domain_map = {"brain-regeneration.com": 3}
	assert _match_domain("a.b.brain-regeneration.com", domain_map) is None


def test_match_domain_no_match_returns_none():
	domain_map = {"brain-regeneration.com": 3}
	assert _match_domain("unrelated.example.com", domain_map) is None


def test_match_domain_bare_domain_has_no_parent_to_strip():
	# len(parts) < 3 — a two-label host has no "one subdomain level" to strip.
	domain_map = {"other.com": 9}
	assert _match_domain("example.com", domain_map) is None


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


def _tenants_handler(rows):
	return lambda request: httpx2.Response(200, json=rows)


async def test_middleware_sets_site_id_and_tenant_for_the_duration_of_the_call(mock_gregory):
	mock_gregory.set_handler(
		_tenants_handler(tenants_payload({"site_id": 3, "domain": "brain-regeneration.com"}))
	)
	seen_during_call = {}

	async def call_next(ctx):
		seen_during_call["site_id"] = get_current_site_id()
		seen_during_call["tenant"] = get_current_tenant()
		return {"ok": True}

	ctx = _make_ctx(request=_FakeRequest({"host": "gregory-ai.brain-regeneration.com"}))
	result = await SiteMiddleware()(ctx, call_next)

	assert result == {"ok": True}
	assert seen_during_call["site_id"] == 3
	assert seen_during_call["tenant"].domain == "brain-regeneration.com"
	assert get_current_site_id() is None  # reset once the request finishes
	assert get_current_tenant() is None


async def test_middleware_resets_even_when_call_next_raises(mock_gregory):
	mock_gregory.set_handler(
		_tenants_handler(tenants_payload({"site_id": 3, "domain": "brain-regeneration.com"}))
	)

	async def call_next(ctx):
		raise RuntimeError("boom")

	ctx = _make_ctx(request=_FakeRequest({"host": "gregory-ai.brain-regeneration.com"}))
	with pytest.raises(RuntimeError):
		await SiteMiddleware()(ctx, call_next)

	assert get_current_site_id() is None
	assert get_current_tenant() is None


async def test_middleware_skips_resolution_for_notifications(mock_gregory):
	calls = []
	mock_gregory.set_handler(lambda request: (calls.append(1), httpx2.Response(200, json=[]))[1])

	async def call_next(ctx):
		return None

	ctx = _make_ctx(request=_FakeRequest({"host": "gregory-ai.brain-regeneration.com"}), request_id=None)
	await SiteMiddleware()(ctx, call_next)

	assert calls == []  # no /tenants/ fetch triggered for a notification


async def test_middleware_with_no_matching_tenant_sets_no_site_id_or_tenant(mock_gregory):
	mock_gregory.set_handler(
		_tenants_handler(tenants_payload({"site_id": 3, "domain": "brain-regeneration.com"}))
	)
	seen_during_call = {}

	async def call_next(ctx):
		seen_during_call["site_id"] = get_current_site_id()
		seen_during_call["tenant"] = get_current_tenant()
		return {"ok": True}

	# A host that resolves to no known tenant.
	ctx = _make_ctx(request=_FakeRequest({"host": "unrelated.example.com"}))
	await SiteMiddleware()(ctx, call_next)

	assert seen_during_call["site_id"] is None
	assert seen_during_call["tenant"] is None
