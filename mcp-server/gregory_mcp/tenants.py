"""Resolves each request to its MCP tenant, from `GET /tenants/`.

Why this exists: Phase 2 of MCP multi-tenancy (django/sitesettings +
`GET /tenants/`) gives every site that wants a research assistant its own
`mcp_enabled` flag, description, prompts and documents. This module is the
Phase 3 half: matching an inbound request to one of those tenants, so the
rest of the server can serve it as its own product rather than as one
anonymous corpus among several.

Replaces `gregory_mcp/site.py`'s old `GET /sites/`-based resolution
entirely (decision 2, MCP-MULTI-TENANCY-PHASE-3-PLAN.md): a hostname that
isn't a tenant is refused (`TenantGateMiddleware`, decision 1) before any
API call, so there is no reason left to also resolve through the unscoped
`/sites/` discovery endpoint. One directory, one fetch, one cache.

Resolution order for a request, cheapest/most-certain first:

1. `GREGORY_SITE_ID` (env, loaded once at startup into
   `Settings.site_id_override`) — picks the directory entry with that
   `site_id`. Unlike the old `/sites/`-based override, this still needs the
   directory fetched: the override used to mean "skip the network
   entirely", but this server now needs the *tenant's* full record (name,
   title, description, subjects, prompts, documents), not just permission
   to omit `?site_id=`.
2. The inbound `Host` header (nginx sets `proxy_set_header Host $host` —
   see docs/07-mcp-server.md), matched against tenant domains with
   `site._normalize_host()`/`site._match_domain()` — the same rule
   `django/gregory/site_resolution.py`'s `find_site_by_domain()` uses:
   exact match, then one subdomain level stripped.
3. Neither resolves — `None`. `TenantGateMiddleware` turns that into a
   refusal for every request except `ping`.

The resolved `Tenant` is exposed via `site_context.get_current_tenant()`,
set by `site.SiteMiddleware` (which calls `resolve_tenant()` below via a
lazy import, to avoid a module-load cycle: this module imports the Host
utilities *from* site.py, so site.py cannot also import this module at load
time).
"""

from __future__ import annotations

import contextvars
import json
import logging
from dataclasses import dataclass
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext
from mcp.shared.exceptions import MCPError

from .cache import CATALOG_CACHE_TTL_MS, CatalogCache
from .client import GregoryAPIError, get_client
from .config import Settings
from .site import _extract_host_header, _match_domain, _normalize_host
from .site_context import get_current_tenant

logger = logging.getLogger("gregory_mcp.tenants")

# JSON-RPC's range for implementation-defined server errors (-32000 to
# -32099). Not one of mcp_types' named codes (INVALID_PARAMS, etc.) because
# this isn't a malformed request — it's a request for a hostname this
# server has nothing to serve.
TENANT_NOT_FOUND_CODE = -32000
TENANT_NOT_FOUND_MESSAGE = "This research assistant is not available at this address."


@dataclass(frozen=True)
class TenantPromptArgument:
	name: str
	description: str
	required: bool


@dataclass(frozen=True)
class TenantPrompt:
	name: str
	title: str
	description: str
	template: str
	arguments: tuple[TenantPromptArgument, ...]


@dataclass(frozen=True)
class TenantDocument:
	slug: str
	title: str
	description: str
	mime_type: str
	body: str


@dataclass(frozen=True)
class Tenant:
	site_id: int
	domain: str
	name: str
	title: str
	api_public: bool
	mcp_description: str
	subjects: tuple[tuple[int, str], ...]
	prompts: tuple[TenantPrompt, ...]
	documents: tuple[TenantDocument, ...]


def _parse_prompt_argument(row: Any) -> TenantPromptArgument | None:
	if not isinstance(row, dict):
		return None
	name, description, required = row.get("name"), row.get("description"), row.get("required")
	if not isinstance(name, str) or not isinstance(description, str) or not isinstance(required, bool):
		return None
	return TenantPromptArgument(name=name, description=description, required=required)


def _parse_prompt(row: Any) -> TenantPrompt | None:
	if not isinstance(row, dict):
		return None
	name, title, description, template = row.get("name"), row.get("title"), row.get("description"), row.get("template")
	if not all(isinstance(v, str) for v in (name, title, description, template)):
		return None
	raw_arguments = row.get("arguments")
	if not isinstance(raw_arguments, list):
		return None
	arguments = tuple(_parse_prompt_argument(a) for a in raw_arguments)
	if any(a is None for a in arguments):
		return None
	return TenantPrompt(name=name, title=title, description=description, template=template, arguments=arguments)


def _parse_document(row: Any) -> TenantDocument | None:
	if not isinstance(row, dict):
		return None
	slug, title, description = row.get("slug"), row.get("title"), row.get("description")
	mime_type, body = row.get("mime_type"), row.get("body")
	if not all(isinstance(v, str) for v in (slug, title, description, mime_type, body)):
		return None
	return TenantDocument(slug=slug, title=title, description=description, mime_type=mime_type, body=body)


def _parse_subject(row: Any) -> tuple[int, str] | None:
	if not isinstance(row, dict):
		return None
	subject_id, subject_name = row.get("id"), row.get("subject_name")
	if not isinstance(subject_id, int) or isinstance(subject_id, bool) or not isinstance(subject_name, str):
		return None
	return (subject_id, subject_name)


def _parse_child_rows(parse, rows: list, kind: str, domain: str) -> tuple:
	"""Parse a tenant's subjects/prompts/documents, dropping any row the
	parser rejects rather than the whole tenant with it.

	A tenant is only dropped over the fields its server cannot work without
	— site_id, domain, identity (see `_parse_tenant`). One malformed prompt
	must not take a live tenant's whole endpoint dark, which is what
	returning None here used to do: `TenantGateMiddleware` refuses every
	request for a hostname with no tenant, so a single bad row would have
	been an outage rather than a missing prompt. Each dropped row is logged,
	so it is visible rather than silently absent.
	"""
	parsed = []
	for row in rows:
		item = parse(row)
		if item is None:
			logger.warning("gregory_tenant_child_row_malformed", extra={"kind": kind, "domain": domain})
			continue
		parsed.append(item)
	return tuple(parsed)


def _parse_tenant(row: Any) -> Tenant | None:
	"""One row of `GET /tenants/`, or None if malformed.

	Never raises — a single bad row must not take down every other tenant's
	server, so this is defensive on every field rather than trusting the
	API's own schema.

	Only the fields a tenant's server cannot work without are fatal here:
	`site_id`, `domain`, its identity, and the three collections being lists
	at all. A malformed row *inside* one of those collections costs that row
	only (`_parse_child_rows`), because dropping the tenant would refuse
	every request for its hostname — an outage in place of a missing prompt.
	"""
	if not isinstance(row, dict):
		return None
	site_id = row.get("site_id")
	domain, name, title = row.get("domain"), row.get("name"), row.get("title")
	api_public, mcp_description = row.get("api_public"), row.get("mcp_description")
	if not isinstance(site_id, int) or isinstance(site_id, bool):
		return None
	if not all(isinstance(v, str) for v in (domain, name, title, mcp_description)):
		return None
	if not isinstance(api_public, bool):
		return None

	raw_subjects = row.get("subjects")
	raw_prompts = row.get("prompts")
	raw_documents = row.get("documents")
	if not isinstance(raw_subjects, list) or not isinstance(raw_prompts, list) or not isinstance(raw_documents, list):
		return None

	subjects = _parse_child_rows(_parse_subject, raw_subjects, "subject", domain)
	prompts = _parse_child_rows(_parse_prompt, raw_prompts, "prompt", domain)
	documents = _parse_child_rows(_parse_document, raw_documents, "document", domain)

	return Tenant(
		site_id=site_id,
		domain=domain,
		name=name,
		title=title,
		api_public=api_public,
		mcp_description=mcp_description,
		subjects=subjects,
		prompts=prompts,
		documents=documents,
	)


def _parse_tenants(data: list) -> list[Tenant]:
	tenants: list[Tenant] = []
	for row in data:
		tenant = _parse_tenant(row)
		if tenant is None:
			# The domain, not the row: an API row is free-form and would be
			# dropped by JsonFormatter's allowlist anyway (logging_config.py).
			domain = row.get("domain") if isinstance(row, dict) else None
			logger.warning(
				"gregory_tenant_row_malformed",
				extra={"domain": domain if isinstance(domain, str) else None},
			)
			continue
		tenants.append(tenant)
	return tenants


class TenantsDirectoryError(Exception):
	"""Raised when `GET /tenants/` cannot be parsed into tenants — a non-list
	body. Caught alongside GregoryAPIError by `_get_tenants_directory()`;
	never cached (see that function)."""


_tenants_cache = CatalogCache(ttl_ms=CATALOG_CACHE_TTL_MS)

# The last successfully parsed directory, kept across TTL expiries and
# fetch failures so a brief API outage degrades to "stale but working"
# rather than "every request refused". None means "never fetched
# successfully" -- see _get_tenants_directory()'s docstring.
_last_good_tenants: list[Tenant] | None = None

# Set once at startup by init_tenant_resolution() -- None means "no
# override, resolve from Host". Mirrors the old site.py override, but now
# picks a tenant by id from the fetched directory rather than skipping the
# network (see the module docstring).
_site_id_override: int | None = None

# Per-request: whether resolve_tenant()'s last call found the directory
# fetch unavailable (as opposed to available but with no matching tenant).
# TenantGateMiddleware reads this only to choose its log message -- the
# refusal itself is decided by get_current_tenant() being None regardless
# of which case this is.
_directory_unavailable: contextvars.ContextVar[bool] = contextvars.ContextVar(
	"gregory_mcp_tenant_directory_unavailable", default=False
)


def init_tenant_resolution(settings: Settings) -> None:
	"""Call once at startup, alongside client.init_client() — see __main__.py."""
	global _site_id_override
	_site_id_override = settings.site_id_override


def reset_tenant_directory() -> None:
	"""Test-only: undo init_tenant_resolution() and drop every cached/kept
	directory state, so one test's GREGORY_SITE_ID/`GET /tenants/` state
	can't leak into another's (mirrors cache.reset_catalog_cache())."""
	global _site_id_override, _last_good_tenants
	_site_id_override = None
	_last_good_tenants = None
	_tenants_cache.clear()
	_directory_unavailable.set(False)


def directory_was_unavailable() -> bool:
	return _directory_unavailable.get()


async def _fetch_tenants_raw() -> list[Tenant]:
	"""GET /tenants/ -> parsed Tenant list.

	Raises on any failure (GregoryAPIError, a non-JSON 2xx body, or a body
	that isn't a JSON list) rather than degrading to an empty list — unlike
	the old `_fetch_site_directory()`, whose degrade-to-`{}` behaviour is
	exactly the caching-a-failure bug this phase removes (see the parent
	plan's "Things that will go wrong"). `_get_tenants_directory()` is what
	turns a raised exception into a stale-copy-or-unavailable outcome.
	"""
	try:
		data = await get_client().get("/tenants/")
	except json.JSONDecodeError as exc:
		raise TenantsDirectoryError("non-JSON body from /tenants/") from exc
	if not isinstance(data, list):
		raise TenantsDirectoryError(f"unexpected /tenants/ shape: {type(data).__name__}")
	return _parse_tenants(data)


async def _get_tenants_directory() -> list[Tenant] | None:
	"""The current tenant directory, or None if unavailable.

	A cache hit or a fresh successful fetch both update `_last_good_tenants`
	and return the CatalogCache's TTL-bounded value directly. A failure
	(propagated, uncached, from `_fetch_tenants_raw`) falls back to the last
	good copy if one exists (`gregory_tenants_directory_stale`), or to None
	if this process has never fetched successfully
	(`gregory_tenants_directory_unavailable`).
	"""
	global _last_good_tenants
	try:
		tenants = await _tenants_cache.get_or_fetch("/tenants/", None, _fetch_tenants_raw)
	except (GregoryAPIError, TenantsDirectoryError):
		if _last_good_tenants is not None:
			logger.warning("gregory_tenants_directory_stale", exc_info=True)
			return _last_good_tenants
		logger.warning("gregory_tenants_directory_unavailable", exc_info=True)
		return None
	_last_good_tenants = tenants
	return tenants


async def resolve_tenant(host_header: str | None) -> Tenant | None:
	"""The Tenant for a request that carried `host_header` as its Host, or
	None if nothing resolves or the directory is unavailable. See the
	module docstring for the resolution order.

	Sets the per-request `directory_was_unavailable()` flag as a side
	effect, for TenantGateMiddleware's log message only — it never changes
	whether this function returns a tenant.
	"""
	tenants = await _get_tenants_directory()
	if tenants is None:
		_directory_unavailable.set(True)
		return None
	_directory_unavailable.set(False)

	if _site_id_override is not None:
		return next((t for t in tenants if t.site_id == _site_id_override), None)

	host = _normalize_host(host_header)
	if host is None:
		return None
	# _normalize_host() lowercases the inbound Host, so the map's keys must
	# be lowercased too -- DNS hostnames are case-insensitive, and nothing
	# guarantees CustomSetting.domain is stored lowercase (the old
	# _fetch_site_directory() lowercased for the same reason).
	domain_map = {t.domain.lower(): t.site_id for t in tenants}
	site_id = _match_domain(host, domain_map)
	if site_id is None:
		return None
	return next((t for t in tenants if t.site_id == site_id), None)


class TenantGateMiddleware(ServerMiddleware[Any]):
	"""Refuses every request except `ping` when no tenant resolved
	(decision 1, MCP-MULTI-TENANCY-PHASE-3-PLAN.md): a hostname pointed at
	this container that isn't a tenant — wrong domain, or a site that never
	ticked `mcp_enabled` — gets a clear refusal before any API call, rather
	than a generic, unscoped identity.

	Registered after TelemetryMiddleware (see server.py) so a refusal is
	still logged as an `mcp_request` with `site_id: null` and
	`error_kind: "protocol_error"` — TelemetryMiddleware's own MCPError
	handler is what turns raising here into that log line.
	"""

	async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
		if ctx.request_id is None or ctx.method == "ping":
			return await call_next(ctx)

		if get_current_tenant() is not None:
			return await call_next(ctx)

		reason = "directory unavailable" if directory_was_unavailable() else "no tenant matched"
		host = _extract_host_header(ctx)
		logger.warning("gregory_tenant_gate_refused", extra={"host": host, "reason": reason, "method": ctx.method})
		raise MCPError(TENANT_NOT_FOUND_CODE, TENANT_NOT_FOUND_MESSAGE)
