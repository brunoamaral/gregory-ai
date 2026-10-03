"""Authentication for the editor address, `https://<host>/mcp/editor`
(MCP-AUTH-PLAN.md, option A of "Deployment options", D14).

The SDK can protect an MCP endpoint with `auth=AuthSettings(...)`, but that
takes ONE `resource_server_url` for the whole process, and this process serves
every site's hostname, each with its own editor address and so its own token
audience (D4, D13). So the editor mount sits behind this small ASGI layer of
ours, which works per `Host`, and reuses the SDK's types so the protocol details
stay the SDK's: `TokenVerifier`, `AccessToken`, `ProtectedResourceMetadata`,
`build_resource_metadata_url`. If a later SDK release accepts a resource URL per
request, the editor mount switches to the built-in wiring without anything
changing for editors.

What happens to a request to `/mcp/editor`:

1. The `Host` is resolved to a tenant from `GET /editor/tenants/`. No tenant,
   no answer: the same "not available at this address" as `/mcp`.
2. No token, or one that is expired, revoked, malformed, or issued for a
   different address or site, is a 401 with a `WWW-Authenticate` header that
   points at this host's protected resource metadata. MCP clients start the
   sign-in flow only on a 401, which is why public data is NOT served here
   instead (it would hide an expired session).
3. A valid token sets the request's `EditorSession` (site_context.py) and the
   request goes on to the editor `MCPServer`.

The token is checked against Django through RFC 7662 introspection and is never
forwarded to the API: the MCP authorization spec forbids passing a client's
token downstream, and its audience is this endpoint, not the API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl, ValidationError
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from .client import GregoryAPIError, get_client
from .config import Settings
from .site import _normalize_host
from .site_context import (
	SCOPE_EDIT,
	SCOPE_READ,
	TIER_EDITOR,
	TIER_PUBLIC,
	EditorSession,
	_editor_context,
)
from .tenants import TENANT_NOT_FOUND_MESSAGE, directory_was_unavailable, resolve_tenant

logger = logging.getLogger("gregory_mcp.auth")

EDITOR_PATH = "/mcp/editor"
RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource" + EDITOR_PATH

# Django's introspection answers are cached this long, so a revocation takes
# effect within it (Security checklist: at most 60 seconds).
INTROSPECTION_CACHE_SECONDS = 60
_MAX_CACHED_TOKENS = 2048


class EditorAccessToken(AccessToken):
	"""An SDK `AccessToken` plus what Django told us about the person and site."""

	user_id: int
	site_id: int
	tier: str


class IntrospectionUnavailable(Exception):
	"""Django could not be asked. Not the token's fault: the caller gets a 503,
	not a 401 that would send the client round the sign-in flow again."""


class IntrospectionVerifier:
	"""An SDK `TokenVerifier` backed by Django's `/o/introspect/`.

	Results, including "inactive", are cached in process for
	`INTROSPECTION_CACHE_SECONDS`, keyed by a hash of the token, so a busy
	connector costs one introspection a minute and a stream of junk tokens
	can't hammer Django. The token itself is never stored or logged.
	"""

	def __init__(
		self,
		introspect: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
		clock: Callable[[], float] = time.monotonic,
		wall_clock: Callable[[], float] = time.time,
	):
		self._introspect = introspect
		self._clock = clock
		self._wall_clock = wall_clock
		self._cache: dict[str, tuple[float, EditorAccessToken | None]] = {}

	async def verify_token(self, token: str) -> EditorAccessToken | None:
		key = hashlib.sha256(token.encode("utf-8")).hexdigest()
		cached = self._cache.get(key)
		now = self._clock()
		if cached is not None and cached[0] > now:
			return cached[1]

		introspect = self._introspect or get_client().introspect
		try:
			payload = await introspect(token)
		except GregoryAPIError as exc:
			# 5xx or the network: unavailable. A 4xx (a wrong service key) is our
			# own misconfiguration; also not the token's fault.
			logger.warning("gregory_introspection_failed", extra={"status_code": exc.status_code})
			raise IntrospectionUnavailable() from exc

		verified = self._parse(key, payload)
		ttl = INTROSPECTION_CACHE_SECONDS
		if verified is not None and verified.expires_at is not None:
			ttl = max(0, min(ttl, verified.expires_at - self._wall_clock()))
		if len(self._cache) >= _MAX_CACHED_TOKENS:
			self._evict(now)
		self._cache[key] = (now + ttl, verified)
		return verified

	@staticmethod
	def _parse(key: str, payload: Any) -> EditorAccessToken | None:
		if not isinstance(payload, dict) or payload.get("active") is not True:
			return None
		user_id, site_id, tier = payload.get("user_id"), payload.get("site_id"), payload.get("tier")
		scope, exp, aud = payload.get("scope"), payload.get("exp"), payload.get("aud")
		valid = (
			isinstance(user_id, int)
			and not isinstance(user_id, bool)
			and isinstance(site_id, int)
			and not isinstance(site_id, bool)
			and tier in (TIER_EDITOR, TIER_PUBLIC)
			and isinstance(scope, str)
			and isinstance(exp, int)
			and isinstance(aud, list)
		)
		if not valid:
			logger.warning("gregory_introspection_malformed")
			return None
		# A token with several audiences is not bound to one address (D13).
		resource = aud[0] if len(aud) == 1 and isinstance(aud[0], str) else None
		client_id = payload.get("client_id")
		return EditorAccessToken(
			token=key,  # a digest: the raw token is not kept
			client_id=client_id if isinstance(client_id, str) else "",
			scopes=scope.split(),
			expires_at=exp,
			resource=resource,
			subject=str(user_id),
			user_id=user_id,
			site_id=site_id,
			tier=tier,
		)

	def _evict(self, now: float) -> None:
		expired = [k for k, (until, _) in self._cache.items() if until <= now]
		for k in expired:
			del self._cache[k]
		if len(self._cache) >= _MAX_CACHED_TOKENS:
			self._cache.clear()


def same_resource(token_resource: str | None, expected: str) -> bool:
	"""Whether a token's audience is this editor address.

	Compared as URLs (so case and default-port spelling do not matter), a
	trailing slash aside: the rule of the SDK's own
	`BearerAuthBackend._issued_for_this_resource`.
	"""
	try:
		return str(AnyHttpUrl(token_resource or "")).removesuffix("/") == str(AnyHttpUrl(expected)).removesuffix("/")
	except ValidationError:
		return False


def editor_resource_url(settings: Settings, host_header: str | None) -> str | None:
	"""`https://<host>/mcp/editor` for the inbound Host, or None if there is none.

	Keeps a non-default port (and IPv6 brackets): the port is part of the
	address a client signed in for, so dropping it would point discovery at
	the wrong place and fail the token's audience check. Tenant lookup still
	ignores the port (`site._normalize_host`). Production sits behind nginx's
	`Host $host`, which carries no port.
	"""
	host = _normalize_host(host_header)
	if host is None:
		return None
	try:
		port = urlparse(f"//{host_header}").port
	except ValueError:
		return None
	authority = f"[{host}]" if ":" in host else host
	default_port = 443 if settings.editor_scheme == "https" else 80
	if port is not None and port != default_port:
		authority = f"{authority}:{port}"
	return f"{settings.editor_scheme}://{authority}{EDITOR_PATH}"


def _json_response(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, list, bytes]:
	payload = json.dumps(body).encode()
	out = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
	for key, value in (headers or {}).items():
		out.append((key.lower().encode(), value.encode()))
	return status, out, payload


async def _send(send: Send, response: tuple[int, list, bytes]) -> None:
	status, headers, body = response
	await send({"type": "http.response.start", "status": status, "headers": headers})
	await send({"type": "http.response.body", "body": body})


class EditorAuthMiddleware:
	"""Wraps the editor `MCPServer`'s streamable-HTTP endpoint. See the module
	docstring for what it decides."""

	def __init__(self, app: ASGIApp, *, settings: Settings, verifier: IntrospectionVerifier):
		self.app = app
		self._settings = settings
		self._verifier = verifier

	async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
		if scope["type"] != "http":
			await self.app(scope, receive, send)
			return

		request = Request(scope)
		host_header = request.headers.get("host")
		tenant = await resolve_tenant(host_header, editor=True)
		resource = editor_resource_url(self._settings, host_header)
		if tenant is None and directory_was_unavailable():
			await _send(send, _directory_unavailable_response())
			return
		if tenant is None or resource is None:
			await _send(
				send,
				_json_response(404, {"error": "not_found", "error_description": TENANT_NOT_FOUND_MESSAGE}, _NO_STORE),
			)
			return

		metadata_url = str(build_resource_metadata_url(AnyHttpUrl(resource)))
		bearer = _bearer_token(request)
		if bearer is None:
			await _send(send, _challenge(metadata_url, error=None, description="Sign in to use this address."))
			return

		try:
			verified = await self._verifier.verify_token(bearer)
		except IntrospectionUnavailable:
			await _send(
				send,
				_json_response(
					503,
					{"error": "temporarily_unavailable", "error_description": "Sign-in could not be checked. Try again shortly."},
					{**_NO_STORE, "Retry-After": "5"},
				),
			)
			return

		# One generic answer for every way a token can fail, so a probe learns
		# nothing about which check it tripped.
		valid = (
			verified is not None
			and (verified.expires_at is None or verified.expires_at > time.time())
			and same_resource(verified.resource, resource)
			and verified.site_id == tenant.site_id
		)
		if not valid or verified is None:
			await _send(send, _challenge(metadata_url, error="invalid_token", description="The access token is not valid for this address."))
			return

		session = EditorSession(
			user_id=verified.user_id,
			site_id=verified.site_id,
			tier=verified.tier,
			scopes=frozenset(verified.scopes) & {SCOPE_READ, SCOPE_EDIT},
		)
		token = _editor_context.set(session)
		try:
			await self.app(scope, receive, _no_store(send))
		finally:
			_editor_context.reset(token)


_NO_STORE = {"Cache-Control": "no-store"}


def _directory_unavailable_response(extra_headers: dict[str, str] | None = None) -> tuple[int, list, bytes]:
	"""503 when the tenant directory couldn't be fetched and nothing is cached
	yet: a temporary outage, not an address that doesn't exist (404)."""
	return _json_response(
		503,
		{"error": "temporarily_unavailable", "error_description": "This address could not be checked. Try again shortly."},
		{**(extra_headers or {}), **_NO_STORE, "Retry-After": "5"},
	)


def _bearer_token(request: Request) -> str | None:
	header = request.headers.get("authorization", "")
	scheme, _, value = header.partition(" ")
	if scheme.lower() != "bearer" or not value.strip():
		return None
	return value.strip()


def _challenge(metadata_url: str, *, error: str | None, description: str) -> tuple[int, list, bytes]:
	"""401 with the RFC 9728 pointer a client needs to find the authorization
	server. `error` is left out when no token was presented (RFC 6750, 3.1)."""
	parts = []
	if error:
		parts.append(f'error="{error}"')
		parts.append(f'error_description="{description}"')
	parts.append(f'resource_metadata="{metadata_url}"')
	body = {"error": error or "unauthorized", "error_description": description}
	return _json_response(401, body, {**_NO_STORE, "WWW-Authenticate": "Bearer " + ", ".join(parts)})


def _no_store(send: Send) -> Send:
	"""`Cache-Control: no-store` on every response of the editor endpoint."""

	async def wrapped(message: dict[str, Any]) -> None:
		if message["type"] == "http.response.start":
			headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
			headers.append((b"cache-control", b"no-store"))
			message = {**message, "headers": headers}
		await send(message)

	return wrapped


class ProtectedResourceMetadataApp:
	"""`GET /.well-known/oauth-protected-resource/mcp/editor` (RFC 9728), for
	whichever tenant host asked: the resource is that host's editor address, and
	it names Django as the authorization server."""

	def __init__(self, *, settings: Settings):
		self._settings = settings

	async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
		request = Request(scope)
		cors = {
			"Access-Control-Allow-Origin": "*",
			"Access-Control-Allow-Methods": "GET, OPTIONS",
			"Access-Control-Allow-Headers": "*",
		}
		if request.method == "OPTIONS":
			await _send(send, (204, [(k.lower().encode(), v.encode()) for k, v in cors.items()], b""))
			return

		host_header = request.headers.get("host")
		tenant = await resolve_tenant(host_header, editor=True)
		resource = editor_resource_url(self._settings, host_header)
		if tenant is None and directory_was_unavailable():
			await _send(send, _directory_unavailable_response(cors))
			return
		if tenant is None or resource is None:
			await _send(send, _json_response(404, {"error": "not_found"}, {**cors, **_NO_STORE}))
			return

		metadata = ProtectedResourceMetadata(
			resource=AnyHttpUrl(resource),
			authorization_servers=[AnyHttpUrl(self._settings.oauth_issuer)],
			scopes_supported=[SCOPE_READ, SCOPE_EDIT],
			resource_name=tenant.title,
		)
		await _send(
			send,
			_json_response(200, metadata.model_dump(mode="json", exclude_none=True), {**cors, "Cache-Control": "public, max-age=300"}),
		)
