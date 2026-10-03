"""The editor address's authentication (auth.py): 401s with a pointer to the
protected resource metadata, per-host audience, the introspection cache.
MCP-AUTH-PLAN.md, "Two endpoints per tenant" and "Access tiers"."""

from __future__ import annotations

import json
import logging
import time

import httpx2
import pytest

from gregory_mcp.auth import (
	INTROSPECTION_CACHE_SECONDS,
	IntrospectionUnavailable,
	IntrospectionVerifier,
	same_resource,
)
from gregory_mcp.app import build_app
from gregory_mcp.client import GregoryAPIError
from gregory_mcp.server import build_server
from tests.conftest import EDITOR_SETTINGS
from tests.editor_helpers import (
	HOST,
	OTHER_HOST,
	OTHER_SITE_ID,
	SERVICE_KEY,
	FakeDjango,
	http_client,
	introspection,
	new_app,
	resource_for,
	running,
)

METADATA_URL = f"https://{HOST}/.well-known/oauth-protected-resource/mcp/editor"
INITIALIZE = {
	"jsonrpc": "2.0",
	"id": 1,
	"method": "tools/list",
	"params": {},
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def _post(app, *, host=HOST, token=None):
	async with http_client(app, host=host, token=token) as http:
		return await http.post("/mcp/editor", json=INITIALIZE, headers=MCP_HEADERS)


@pytest.fixture
def django(mock_editor_gregory):
	fake = FakeDjango(
		tokens={
			"good": introspection(),
			"public": introspection(tier="public", scope="articles:read"),
			"other-site": introspection(site_id=OTHER_SITE_ID, aud=[resource_for(OTHER_HOST)]),
			"wrong-audience": introspection(aud=[resource_for(OTHER_HOST)]),
			"two-audiences": introspection(aud=[resource_for(), resource_for(OTHER_HOST)]),
			"wrong-site-right-audience": introspection(site_id=OTHER_SITE_ID),
			"expired": introspection(exp=int(time.time()) - 10),
		}
	)
	mock_editor_gregory.set_handler(fake.handler())
	return fake


async def test_no_token_is_401_with_a_pointer_to_the_resource_metadata(django):
	app = new_app()
	async with running(app):
		response = await _post(app)

	assert response.status_code == 401
	challenge = response.headers["www-authenticate"]
	assert challenge.startswith("Bearer ")
	assert f'resource_metadata="{METADATA_URL}"' in challenge
	# No token was presented, so there is no error code to report (RFC 6750 3.1).
	assert "error=" not in challenge
	assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
	"token",
	["unknown", "expired", "other-site", "wrong-audience", "two-audiences", "wrong-site-right-audience"],
)
async def test_bad_tokens_are_401_never_public_data(django, token):
	app = new_app()
	async with running(app):
		response = await _post(app, token=token)

	assert response.status_code == 401
	assert 'error="invalid_token"' in response.headers["www-authenticate"]
	assert f'resource_metadata="{METADATA_URL}"' in response.headers["www-authenticate"]
	assert "result" not in response.text


async def test_a_good_editor_token_is_let_through(django):
	app = new_app()
	async with running(app):
		response = await _post(app, token="good")

	assert response.status_code == 200
	assert response.headers["cache-control"] == "no-store"
	assert "list_subjects" in response.text


async def test_token_for_site_a_is_refused_on_site_bs_host(django):
	"""D4: the audience is the editor address, so another site's host rejects it."""
	app = new_app()
	async with running(app):
		on_own_host = await _post(app, token="good")
		on_other_host = await _post(app, host=OTHER_HOST, token="good")

	assert on_own_host.status_code == 200
	assert on_other_host.status_code == 401


async def test_site_mismatch_fails_even_when_the_audience_matches(django):
	"""The verifier also compares the token's site with the host's, so a bug in
	the audience check alone can't open another site."""
	app = new_app()
	async with running(app):
		response = await _post(app, token="wrong-site-right-audience")

	assert response.status_code == 401


async def test_a_public_tier_token_is_accepted_on_the_editor_address(django):
	app = new_app()
	async with running(app):
		response = await _post(app, token="public")

	assert response.status_code == 200


async def test_unknown_host_is_404_not_a_challenge(django):
	app = new_app()
	async with running(app):
		response = await _post(app, host="gregory-ai.nowhere.test", token="good")

	assert response.status_code == 404
	assert "not available at this address" in response.text


async def test_introspection_down_is_503_not_a_sign_in_loop(mock_editor_gregory):
	fake = FakeDjango()

	def handler(request):
		if request.url.path == "/o/introspect/":
			return httpx2.Response(502, text="bad gateway")
		return fake.handler()(request)

	mock_editor_gregory.set_handler(handler)
	app = new_app()
	async with running(app):
		response = await _post(app, token="good")

	assert response.status_code == 503
	assert response.headers["retry-after"]


async def test_the_users_token_is_never_forwarded_to_django(django, mock_editor_gregory):
	"""The MCP authorization spec forbids passing the client's token on: it is
	only ever the body of an introspection call, and no other request carries it."""
	app = new_app()
	async with running(app):
		await _post(app, token="good")

	for request in mock_editor_gregory.requests:
		assert request.headers.get("authorization") != "Bearer good"
		assert "good" not in str(request.url)
	introspections = [r for r in mock_editor_gregory.requests if r.url.path == "/o/introspect/"]
	assert len(introspections) == 1
	assert introspections[0].method == "POST"
	assert introspections[0].headers["authorization"] == f"Bearer {SERVICE_KEY}"


# --- protected resource metadata (RFC 9728) ---------------------------------


async def test_protected_resource_metadata_names_this_hosts_editor_address(django):
	app = new_app()
	async with running(app):
		async with http_client(app) as http:
			response = await http.get("/.well-known/oauth-protected-resource/mcp/editor")

	assert response.status_code == 200
	body = response.json()
	assert body["resource"] == resource_for()
	assert body["authorization_servers"] == ["https://api.test/"] or body["authorization_servers"] == ["https://api.test"]
	assert body["scopes_supported"] == ["articles:read", "articles:edit"]
	assert body["bearer_methods_supported"] == ["header"]
	assert body["resource_name"] == "Brain Regeneration"
	assert response.headers["access-control-allow-origin"] == "*"


async def test_each_host_gets_its_own_resource(django):
	app = new_app()
	async with running(app):
		async with http_client(app, host=OTHER_HOST) as http:
			response = await http.get("/.well-known/oauth-protected-resource/mcp/editor")

	assert response.json()["resource"] == resource_for(OTHER_HOST)
	assert response.json()["resource_name"] == "Other Site"


async def test_metadata_for_an_unknown_host_is_404(django):
	app = new_app()
	async with running(app):
		async with http_client(app, host="gregory-ai.nowhere.test") as http:
			response = await http.get("/.well-known/oauth-protected-resource/mcp/editor")

	assert response.status_code == 404


async def test_metadata_answers_a_cors_preflight(django):
	app = new_app()
	async with running(app):
		async with http_client(app) as http:
			response = await http.options("/.well-known/oauth-protected-resource/mcp/editor")

	assert response.status_code == 204
	assert "GET" in response.headers["access-control-allow-methods"]


async def test_a_private_site_has_metadata_and_an_editor_address(mock_editor_gregory):
	mock_editor_gregory.set_handler(FakeDjango(editor_tenants=__import__("tests.editor_helpers", fromlist=["two_tenants"]).two_tenants(private_second=True)).handler())
	app = new_app()
	async with running(app):
		async with http_client(app, host=OTHER_HOST) as http:
			response = await http.get("/.well-known/oauth-protected-resource/mcp/editor")

	assert response.status_code == 200


# --- the verifier ------------------------------------------------------------


class Clock:
	def __init__(self, now=1000.0):
		self.now = now

	def __call__(self):
		return self.now


def _verifier(payloads, clock=None, wall=None):
	calls = []

	async def introspect(token):
		calls.append(token)
		return payloads(token) if callable(payloads) else payloads

	clock = clock or Clock()
	return IntrospectionVerifier(introspect=introspect, clock=clock, wall_clock=wall or (lambda: 0)), calls, clock


async def test_results_are_cached_for_sixty_seconds():
	verifier, calls, clock = _verifier(introspection(exp=10**10))

	first = await verifier.verify_token("tok")
	clock.now += INTROSPECTION_CACHE_SECONDS - 1
	second = await verifier.verify_token("tok")
	clock.now += 2
	third = await verifier.verify_token("tok")

	assert first == second
	assert third is not None
	assert len(calls) == 2


async def test_inactive_results_are_cached_too():
	verifier, calls, clock = _verifier({"active": False})

	assert await verifier.verify_token("tok") is None
	assert await verifier.verify_token("tok") is None
	assert len(calls) == 1


async def test_cache_never_outlives_the_token():
	payload = introspection(exp=1030)
	verifier, calls, clock = _verifier(payload, wall=lambda: 1000)

	await verifier.verify_token("tok")
	clock.now += 31
	await verifier.verify_token("tok")

	assert len(calls) == 2


async def test_the_raw_token_is_not_kept():
	verifier, _, _ = _verifier(introspection(exp=10**10))
	verified = await verifier.verify_token("super-secret-token")
	assert "super-secret-token" not in verified.model_dump_json()
	assert all("super-secret-token" not in key for key in verifier._cache)


async def test_a_verified_token_carries_the_person_site_and_tier():
	verifier, _, _ = _verifier(introspection(user_id=9, site_id=3, tier="editor", scope="articles:read articles:edit"))
	verified = await verifier.verify_token("tok")
	assert (verified.user_id, verified.site_id, verified.tier) == (9, 3, "editor")
	assert verified.scopes == ["articles:read", "articles:edit"]
	assert verified.resource == resource_for()


@pytest.mark.parametrize(
	"payload",
	[
		None,
		[],
		{"active": "yes"},
		{"active": True},
		{**introspection(), "user_id": "7"},
		{**introspection(), "site_id": None},
		{**introspection(), "tier": "admin"},
		{**introspection(), "aud": "https://x"},
		{**introspection(), "exp": "soon"},
	],
)
async def test_malformed_or_inactive_introspection_is_no_token(payload):
	verifier, _, _ = _verifier(payload)
	assert await verifier.verify_token("tok") is None


async def test_several_audiences_means_not_bound_to_one_address():
	verifier, _, _ = _verifier(introspection(aud=[resource_for(), resource_for(OTHER_HOST)]))
	verified = await verifier.verify_token("tok")
	assert verified.resource is None


async def test_upstream_failure_is_unavailable_not_inactive():
	async def introspect(token):
		raise GregoryAPIError(502, "bad gateway")

	verifier = IntrospectionVerifier(introspect=introspect)
	with pytest.raises(IntrospectionUnavailable):
		await verifier.verify_token("tok")


def test_resource_comparison_ignores_case_and_a_trailing_slash():
	assert same_resource("https://Gregory-AI.br.test/mcp/editor/", "https://gregory-ai.br.test/mcp/editor")
	assert same_resource("https://gregory-ai.br.test:443/mcp/editor", "https://gregory-ai.br.test/mcp/editor")
	assert not same_resource("https://gregory-ai.br.test/mcp", "https://gregory-ai.br.test/mcp/editor")
	assert not same_resource("http://gregory-ai.br.test/mcp/editor", "https://gregory-ai.br.test/mcp/editor")
	assert not same_resource(None, "https://gregory-ai.br.test/mcp/editor")
	assert not same_resource("not a url", "https://gregory-ai.br.test/mcp/editor")


# --- logs --------------------------------------------------------------------


async def test_no_token_or_authorization_header_reaches_the_logs(django, caplog):
	"""Same style as test_no_internal_url_leak.py: drive requests, then assert
	nothing a log handler saw contains the secret."""
	caplog.set_level(logging.DEBUG)
	app = new_app()
	async with running(app):
		await _post(app, token="good")
		await _post(app, token="expired")
		await _post(app)

	haystack = "\n".join(
		json.dumps({k: str(v) for k, v in record.__dict__.items() if k not in ("args",)}, default=str)
		+ record.getMessage()
		for record in caplog.records
	)
	assert "Bearer good" not in haystack
	assert SERVICE_KEY not in haystack
	assert '"good"' not in haystack


def test_build_app_drops_no_sdk_middleware():
	"""`build_app()` lifts the SDK apps' routes into one Starlette app and leaves
	the SDK apps themselves behind, so any middleware the SDK attached to them
	would silently stop applying to /mcp and /mcp/editor. On mcp 2.3.0 there is
	none without `auth=`, and the request body limit lives inside the route's
	own endpoint. If an SDK upgrade changes that, this fails instead of the
	server quietly losing it."""
	for editor, path in ((False, "/mcp"), (True, "/mcp/editor")):
		sdk_app = build_server(editor=editor).streamable_http_app(
			streamable_http_path=path, stateless_http=True, host=EDITOR_SETTINGS.host
		)
		assert sdk_app.user_middleware == [], f"the SDK now attaches middleware to {path}"
		assert [route.path for route in sdk_app.routes] == [path], f"the SDK now adds routes beside {path}"

	app = build_app(EDITOR_SETTINGS)
	assert app.user_middleware == []


@pytest.mark.parametrize(
	("scheme", "host_header", "expected"),
	[
		("https", "gregory-ai.br.test", "https://gregory-ai.br.test/mcp/editor"),
		("https", "gregory-ai.br.test:443", "https://gregory-ai.br.test/mcp/editor"),
		("http", "gregory-ai.br.test:8001", "http://gregory-ai.br.test:8001/mcp/editor"),
		("http", "GREGORY-AI.br.test:80", "http://gregory-ai.br.test/mcp/editor"),
		("http", "[::1]:8001", "http://[::1]:8001/mcp/editor"),
	],
)
def test_the_resource_url_keeps_a_non_default_port(scheme, host_header, expected):
	"""The port is part of the address a client signed in for: dropping it
	points discovery at the wrong place and fails the audience check."""
	import dataclasses

	from gregory_mcp.auth import editor_resource_url

	settings = dataclasses.replace(EDITOR_SETTINGS, editor_scheme=scheme)
	assert editor_resource_url(settings, host_header) == expected


@pytest.mark.parametrize("path", ["/mcp/editor", "/.well-known/oauth-protected-resource/mcp/editor"])
async def test_directory_down_on_a_cold_start_is_503_not_404(mock_editor_gregory, path):
	"""With no directory cached yet, a failed /editor/tenants/ is an outage,
	not an address that doesn't exist."""
	fake = FakeDjango()

	def handler(request):
		if request.url.path == "/editor/tenants/":
			return httpx2.Response(502, text="bad gateway")
		return fake.handler()(request)

	mock_editor_gregory.set_handler(handler)
	app = new_app()
	async with running(app):
		async with http_client(app, token="good") as http:
			if path == "/mcp/editor":
				response = await http.post(path, json=INITIALIZE, headers=MCP_HEADERS)
			else:
				response = await http.get(path)

	assert response.status_code == 503
	assert response.headers["retry-after"]
	assert response.headers["cache-control"] == "no-store"
	if path != "/mcp/editor":
		assert response.headers["access-control-allow-origin"] == "*"

