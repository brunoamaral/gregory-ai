"""Per-request site resolution: makes every upstream call carry `?site_id=`.

Why this exists: the site-scoped API visibility project's next phase will
make the Gregory API fail closed — an anonymous caller that names no site
gets a `400` (see `SITE-API-VISIBILITY-SPEC.md`, "Resolving a site without
being told one"). This server calls the API anonymously
(`gregory_mcp/client.py` sends only `Accept` and `User-Agent`), so every tool
call would break the moment that phase ships, unless it already sends
`?site_id=` by then. An unrecognised query parameter is ignored by
django-filter today, so sending it now — on every endpoint, not only the two
that currently declare it (`/articles/`, `/trials/`) — is a no-op until that
phase ships and a forward-compatible default once it does.

Resolution order for a request, cheapest/most-certain first:

1. `GREGORY_SITE_ID` (env, loaded once at startup into
   `Settings.site_id_override`) — wins outright, without ever touching the
   network. A single-tenant deployment can pin this and depend on neither
   the inbound Host header nor `GET /sites/` existing.
2. The inbound `Host` header the MCP server was reached on (nginx sets
   `proxy_set_header Host $host` — see docs/07-mcp-server.md), resolved to a
   site_id via the API's `GET /sites/` discovery endpoint (added alongside
   the visibility project, PR #859) — matched exactly the way
   `django/subscriptions/views.py`'s `_find_site_by_domain()` matches a
   domain: exact match, then one subdomain level stripped. Mirrored rather
   than imported — this is a separate deployable with no Django import path.
3. Neither resolves — omit the parameter. Exactly today's behaviour, so
   nothing regresses; once the API fail-closes this surfaces as its own
   `400`, which is the correct, visible failure. Never guess, never raise.

The resolved value is exposed to the rest of the request via
`site_context.get_current_site_id()` — see that module's docstring for why
it isn't defined here.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext

from .cache import CATALOG_CACHE_TTL_MS, CatalogCache
from .client import GregoryAPIError, get_client
from .config import Settings
from .site_context import _current_site_id

logger = logging.getLogger("gregory_mcp.site")

# GET /sites/ is unscoped, public, and slow-changing (it lists api_public
# sites) — cached the same way subjects/categories are, with the same TTL,
# rather than fetched per request. Its own CatalogCache instance, not the
# shared one from cache.py: that one is deliberately narrow to
# /subjects/+/categories/ (see its module docstring), and /sites/ isn't a
# catalog a tool exposes to a caller.
_site_directory_cache = CatalogCache(ttl_ms=CATALOG_CACHE_TTL_MS)

# Set once at startup by init_site_resolution(); None means "no override,
# resolve from Host". Not part of Settings' own consumers (client.py,
# logging_config.py) needing this — only this module does.
_site_id_override: int | None = None


def init_site_resolution(settings: Settings) -> None:
	"""Call once at startup, alongside client.init_client() — see __main__.py."""
	global _site_id_override
	_site_id_override = settings.site_id_override


def reset_site_resolution() -> None:
	"""Test-only: undo init_site_resolution() and drop the cached directory,
	so one test's GREGORY_SITE_ID/`GET /sites/` state can't leak into
	another's (mirrors cache.reset_catalog_cache())."""
	global _site_id_override
	_site_id_override = None
	_site_directory_cache.clear()


def _normalize_host(raw: str | None) -> str | None:
	"""Same normalisation `_find_site_by_domain` applies: strip a port or
	IPv6 brackets via urlparse, lowercase. Returns None for an empty/missing
	header rather than an empty string, so callers can `if host is None`."""
	if not raw:
		return None
	host = urlparse(f"//{raw}").hostname
	return host.lower() if host else None


def _match_domain(host: str, domain_map: dict[str, int]) -> int | None:
	"""Exact match, then one subdomain level stripped — mirrors
	`_find_site_by_domain` exactly: 'gregory-ai.brain-regeneration.com' has
	no exact entry in the directory, so this falls back to
	'brain-regeneration.com'."""
	if host in domain_map:
		return domain_map[host]
	parts = host.split(".")
	if len(parts) >= 3:
		parent = ".".join(parts[1:])
		if parent in domain_map:
			return domain_map[parent]
	return None


async def _fetch_site_directory() -> dict[str, int]:
	"""GET /sites/ -> {domain: site_id}.

	Never raises: this endpoint is new (PR #859), so an instance running
	slightly older Gregory code, or any other upstream failure, must degrade
	to "no domain map available" — which resolves to omitting site_id, the
	same safe fallback as an unresolvable Host — rather than breaking every
	tool call on this server.

	GregoryAPIError covers non-2xx statuses and transport failures, but a
	2xx response isn't guaranteed to be JSON — an intermediate proxy can
	return an HTML error page with a 200/204 (e.g. a misconfigured gateway
	swallowing the real status). GregoryClient.get() calls response.json()
	unguarded, so that shows up here as json.JSONDecodeError, not
	GregoryAPIError — caught alongside it for the same degrade-not-raise
	reason.
	"""
	try:
		data = await get_client().get("/sites/")
	except (GregoryAPIError, json.JSONDecodeError):
		logger.warning("gregory_sites_directory_unavailable", exc_info=True)
		return {}
	if not isinstance(data, list):
		# GET /sites/ returns a plain JSON array (see schema.yml), not the
		# {count, next, results} shape client.get_all_pages() expects —
		# an unexpected shape here means a contract change worth knowing
		# about, not a crash.
		logger.warning("gregory_sites_directory_unexpected_shape", extra={"type": type(data).__name__})
		return {}
	domain_map: dict[str, int] = {}
	for row in data:
		if not isinstance(row, dict):
			continue
		domain, site_id = row.get("domain"), row.get("site_id")
		if isinstance(domain, str) and isinstance(site_id, int):
			domain_map[domain.lower()] = site_id
	return domain_map


async def _get_site_directory() -> dict[str, int]:
	return await _site_directory_cache.get_or_fetch("/sites/", None, _fetch_site_directory)


async def resolve_site_id(host_header: str | None) -> int | None:
	"""The site_id for a request that carried `host_header` as its Host,
	or None if nothing resolves. See the module docstring for the order."""
	if _site_id_override is not None:
		return _site_id_override
	host = _normalize_host(host_header)
	if host is None:
		return None
	domain_map = await _get_site_directory()
	return _match_domain(host, domain_map)


def _extract_host_header(ctx: ServerRequestContext[Any, Any]) -> str | None:
	"""The inbound Host header for this request, or None on a transport that
	carries no headers (stdio) or a request object with none set.

	`ctx.request` is the transport's raw request object (a Starlette
	`Request` for streamable-http, `None` for stdio — see
	`mcp.shared.message.ServerMessageMetadata.request_context`); its
	`.headers` is a case-insensitive mapping, so `.get("host")` doesn't
	depend on how the client or an intermediate proxy cased it.
	"""
	headers = getattr(ctx.request, "headers", None)
	if headers is None:
		return None
	return headers.get("host")


class SiteMiddleware(ServerMiddleware[Any]):
	"""Resolves this request's site_id once, and makes it available for the
	rest of the request via `site_context.get_current_site_id()` — read by
	`GregoryClient.get()` (added to every upstream call) and
	`CatalogCache._key()` (mixed into the cache key, so the shared
	subjects/categories cache can never serve one site's catalog to
	another's caller).

	Registered outermost relative to TelemetryMiddleware (see server.py) so
	a cold-cache `GET /sites/` fetch's latency is never smeared into some
	unrelated tool call's own `upstream_ms`/`upstream_calls` telemetry.
	"""

	async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
		if ctx.request_id is None:
			return await call_next(ctx)  # notifications never call the upstream API

		site_id = await resolve_site_id(_extract_host_header(ctx))
		token = _current_site_id.set(site_id)
		try:
			return await call_next(ctx)
		finally:
			_current_site_id.reset(token)
