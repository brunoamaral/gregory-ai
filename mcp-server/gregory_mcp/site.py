"""Host-matching helpers, and the middleware that resolves each request's
tenant before anything else runs.

The actual directory fetch, caching, and resolution order now live in
`tenants.py` (`resolve_tenant()`) — see that module's docstring for why:
Phase 3 of MCP multi-tenancy replaced `GET /sites/`-based resolution with
`GET /tenants/`, since a hostname that isn't a tenant is refused
(`tenants.TenantGateMiddleware`, decision 1) before any API call, so there is
no reason left to also resolve through the unscoped `/sites/` discovery
endpoint.

This module keeps only:

- `_normalize_host()` / `_match_domain()` — the Host-matching rules,
  mirrored from `django/gregory/site_resolution.py`'s `find_site_by_domain()`
  rather than imported, since this is a separate deployable with no Django
  import path. `tenants.py` imports them from here.
- `_extract_host_header()` — reading the inbound Host off a request context.
- `SiteMiddleware` — sets `site_context`'s per-request `site_id` and
  `Tenant` for the rest of the request, via `client.py`'s `?site_id=`
  injection and `cache.py`'s cache key. Calls `tenants.resolve_tenant()`
  through a lazy (in-function) import: this module is what `tenants.py`
  imports the Host helpers *from*, so importing `tenants.py` back at module
  scope here would be a load-time cycle.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext

from .site_context import _current_site_id, _current_tenant


def _normalize_host(raw: str | None) -> str | None:
	"""Same normalisation Django's `find_site_by_domain()` applies: strip a
	port or IPv6 brackets via urlparse, lowercase. Returns None for an
	empty/missing header rather than an empty string, so callers can
	`if host is None`."""
	if not raw:
		return None
	host = urlparse(f"//{raw}").hostname
	return host.lower() if host else None


def _match_domain(host: str, domain_map: dict[str, int]) -> int | None:
	"""Exact match, then one subdomain level stripped — the same rule as
	Django's `find_site_by_domain()`: 'gregory-ai.brain-regeneration.com' has
	no exact entry in the directory, so this falls back to
	'brain-regeneration.com'. Unlike Django's, it only ever matches against
	tenant domains, since that is all `GET /tenants/` lists."""
	if host in domain_map:
		return domain_map[host]
	parts = host.split(".")
	if len(parts) >= 3:
		parent = ".".join(parts[1:])
		if parent in domain_map:
			return domain_map[parent]
	return None


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
	"""Resolves this request's tenant once, and makes its `site_id` and
	`Tenant` available for the rest of the request via `site_context` — read
	by `GregoryClient.get()` (site_id added to every upstream call),
	`CatalogCache._key()` (site_id mixed into the cache key), and
	`tenants.TenantGateMiddleware`/`TenantIdentityMiddleware` (the `Tenant`
	itself).

	Registered outermost relative to TelemetryMiddleware and
	TenantGateMiddleware (see server.py) so a cold-cache `GET /tenants/`
	fetch's latency is never smeared into some unrelated tool call's own
	`upstream_ms`/`upstream_calls` telemetry.
	"""

	async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
		if ctx.request_id is None:
			return await call_next(ctx)  # notifications never call the upstream API

		from .tenants import resolve_tenant  # lazy: see module docstring

		tenant = await resolve_tenant(_extract_host_header(ctx))
		site_token = _current_site_id.set(tenant.site_id if tenant is not None else None)
		tenant_token = _current_tenant.set(tenant)
		try:
			return await call_next(ctx)
		finally:
			_current_site_id.reset(site_token)
			_current_tenant.reset(tenant_token)
