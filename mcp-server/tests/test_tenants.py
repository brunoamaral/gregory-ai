"""Tests for gregory_mcp/tenants.py: the tenant directory fetch/cache,
resolve_tenant(), and TenantGateMiddleware. See
MCP-MULTI-TENANCY-PHASE-3-PLAN.md, task A4.

test_site.py keeps the pure Host-matching unit tests (_normalize_host,
_match_domain) and SiteMiddleware's own tests; this file covers everything
that used to be site.py's GET /sites/-based resolve_site_id() tests, now
against GET /tenants/ and resolve_tenant().
"""

from __future__ import annotations

from dataclasses import replace

import httpx2
import pytest
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError

from gregory_mcp.tenants import (
	TENANT_NOT_FOUND_MESSAGE,
	TenantGateMiddleware,
	_get_tenants_directory,
	_parse_tenants,
	directory_was_unavailable,
	init_tenant_resolution,
	resolve_tenant,
)
from tests.conftest import TEST_SETTINGS, tenants_payload

# --- parsing ------------------------------------------------------------


def test_parses_a_well_formed_row():
	rows = tenants_payload({"site_id": 3, "domain": "brain-regeneration.com", "name": "BR", "title": "Brain Regeneration"})
	tenants = _parse_tenants(rows)
	assert len(tenants) == 1
	assert tenants[0].site_id == 3
	assert tenants[0].domain == "brain-regeneration.com"


def test_a_malformed_row_is_skipped_and_the_rest_kept(caplog):
	rows = tenants_payload({"site_id": 3, "domain": "brain-regeneration.com"})
	rows.append({"site_id": "not-an-int", "domain": "bad.test"})  # malformed: site_id must be int
	tenants = _parse_tenants(rows)
	assert [t.domain for t in tenants] == ["brain-regeneration.com"]


def test_empty_list_parses_to_empty():
	assert _parse_tenants([]) == []


def test_malformed_prompt_argument_drops_the_whole_prompt_row_not_the_tenant():
	rows = tenants_payload(
		{
			"site_id": 3,
			"domain": "brain-regeneration.com",
			"prompts": [
				{
					"name": "p",
					"title": "P",
					"description": "",
					"template": "hi",
					"arguments": [{"name": "x", "description": "", "required": "not-a-bool"}],
				}
			],
		}
	)
	tenants = _parse_tenants(rows)
	assert len(tenants) == 0  # the whole tenant row is malformed, not silently missing one prompt


# --- the directory: caching, failure, and stale-copy behaviour ----------


async def test_directory_hit_within_the_ttl(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, "domain": "br.test"}))
	)

	first = await _get_tenants_directory()
	second = await _get_tenants_directory()

	assert first is not None and second is not None
	assert [t.site_id for t in first] == [3]
	tenants_requests = [r for r in mock_gregory.requests if r.url.path == "/tenants/"]
	assert len(tenants_requests) == 1


async def test_a_failure_is_never_cached(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(500, text="boom"))

	result = await _get_tenants_directory()

	assert result is None
	# A second call retries rather than serving a cached failure.
	await _get_tenants_directory()
	tenants_requests = [r for r in mock_gregory.requests if r.url.path == "/tenants/"]
	assert len(tenants_requests) == 2


async def test_a_failure_after_a_success_returns_the_stale_copy(mock_gregory, monkeypatch):
	calls = {"n": 0}

	def handler(request):
		calls["n"] += 1
		if calls["n"] == 1:
			return httpx2.Response(200, json=tenants_payload({"site_id": 3, "domain": "br.test"}))
		return httpx2.Response(500, text="boom")

	mock_gregory.set_handler(handler)

	first = await _get_tenants_directory()
	assert first is not None and [t.site_id for t in first] == [3]

	# Force the TTL cache to consider its entry expired, without waiting.
	import gregory_mcp.tenants as tenants_module

	monkeypatch.setattr(tenants_module._tenants_cache, "_clock", lambda: float("inf"))

	second = await _get_tenants_directory()
	assert second is not None
	assert [t.site_id for t in second] == [3]  # the stale copy, not a fresh empty one


async def test_a_failure_with_nothing_cached_returns_unavailable(mock_gregory):
	mock_gregory.set_handler(lambda request: httpx2.Response(500, text="boom"))

	assert await _get_tenants_directory() is None


async def test_tenants_call_itself_carries_no_site_id(mock_gregory):
	"""GET /tenants/ must be fetched before this request's own site_id is
	set — the same requirement /sites/ had, and for the same reason: it
	can't require the thing it exists to provide."""
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json=[]))

	await resolve_tenant("gregory-ai.brain-regeneration.com")

	tenants_requests = [r for r in mock_gregory.requests if r.url.path == "/tenants/"]
	assert len(tenants_requests) == 1
	assert "site_id" not in tenants_requests[0].url.params


# --- resolution -----------------------------------------------------------


async def test_override_picks_by_id(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(
			200,
			json=tenants_payload(
				{"site_id": 3, "domain": "brain-regeneration.com"},
				{"site_id": 7, "domain": "encefalites.pt"},
			),
		)
	)
	init_tenant_resolution(replace(TEST_SETTINGS, site_id_override=7))

	tenant = await resolve_tenant("totally-unrelated-host.example")

	assert tenant is not None
	assert tenant.site_id == 7


async def test_override_with_no_matching_directory_entry_resolves_to_none(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, "domain": "br.test"}))
	)
	init_tenant_resolution(replace(TEST_SETTINGS, site_id_override=999))

	assert await resolve_tenant(None) is None


async def test_exact_host_match(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, "domain": "brain-regeneration.com"}))
	)

	tenant = await resolve_tenant("brain-regeneration.com")

	assert tenant is not None and tenant.site_id == 3


async def test_one_stripped_subdomain_level_resolves(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, "domain": "brain-regeneration.com"}))
	)

	tenant = await resolve_tenant("gregory-ai.brain-regeneration.com")

	assert tenant is not None and tenant.site_id == 3


async def test_two_stripped_levels_does_not_resolve(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, "domain": "brain-regeneration.com"}))
	)

	assert await resolve_tenant("a.b.brain-regeneration.com") is None


async def test_unknown_host_returns_none(mock_gregory):
	mock_gregory.set_handler(
		lambda request: httpx2.Response(200, json=tenants_payload({"site_id": 3, "domain": "brain-regeneration.com"}))
	)

	assert await resolve_tenant("unrelated.example.com") is None


async def test_a_site_not_in_tenants_returns_none(mock_gregory):
	"""A public site that never ticked mcp_enabled (or has an empty scope)
	is absent from GET /tenants/ entirely -- see the Phase 2 plan. Its
	domain simply isn't in the directory."""
	mock_gregory.set_handler(lambda request: httpx2.Response(200, json=[]))

	assert await resolve_tenant("some-public-but-not-mcp-site.test") is None


# --- the gate ---------------------------------------------------------------


def _make_ctx(*, request_id="req-1", method="tools/call"):
	return ServerRequestContext(
		session=None,
		lifespan_context={},
		protocol_version="2026-07-28",
		method=method,
		params={},
		request_id=request_id,
		request=None,
	)


@pytest.mark.parametrize("method", ["tools/list", "tools/call", "prompts/list", "server/discover"])
async def test_gate_refuses_every_method_with_no_tenant(method):
	async def call_next(ctx):
		raise AssertionError("call_next must not run when there is no tenant")

	ctx = _make_ctx(method=method)
	with pytest.raises(MCPError) as exc_info:
		await TenantGateMiddleware()(ctx, call_next)

	assert exc_info.value.message == TENANT_NOT_FOUND_MESSAGE
	for banned in ("tenant", "instance", "gregory"):
		assert banned not in exc_info.value.message.lower()


async def test_gate_lets_ping_through_with_no_tenant():
	called = []

	async def call_next(ctx):
		called.append(1)
		return {"ok": True}

	ctx = _make_ctx(method="ping")
	result = await TenantGateMiddleware()(ctx, call_next)

	assert result == {"ok": True}
	assert called == [1]


async def test_gate_lets_notifications_through_with_no_tenant():
	called = []

	async def call_next(ctx):
		called.append(1)
		return None

	ctx = _make_ctx(method="notifications/cancelled", request_id=None)
	await TenantGateMiddleware()(ctx, call_next)

	assert called == [1]


async def test_gate_lets_requests_through_when_a_tenant_is_set(mock_gregory):
	from gregory_mcp.site_context import _current_tenant
	from gregory_mcp.tenants import Tenant

	tenant = Tenant(
		site_id=3, domain="br.test", name="BR", title="BR", api_public=True,
		mcp_description="", subjects=(), prompts=(), documents=(),
	)
	token = _current_tenant.set(tenant)
	try:
		called = []

		async def call_next(ctx):
			called.append(1)
			return {"ok": True}

		ctx = _make_ctx()
		result = await TenantGateMiddleware()(ctx, call_next)

		assert result == {"ok": True}
		assert called == [1]
	finally:
		_current_tenant.reset(token)


async def test_gate_reason_reflects_directory_unavailable_vs_no_match(mock_gregory):
	"""directory_was_unavailable() is set by resolve_tenant() -- exercised
	here through it directly, since the gate only reads the flag rather than
	computing it."""
	mock_gregory.set_handler(lambda request: httpx2.Response(500, text="boom"))
	await resolve_tenant("anything.test")
	assert directory_was_unavailable() is True

	mock_gregory.set_handler(lambda request: httpx2.Response(200, json=[]))
	await resolve_tenant("anything.test")
	assert directory_was_unavailable() is False
