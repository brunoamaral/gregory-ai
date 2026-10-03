"""The in-flight request's resolved `site_id` and `Tenant`, threaded via
`ContextVar` rather than a parameter added to every tool signature — the
same pattern `telemetry._accumulator` uses, and for the same reason (see
telemetry.py's module docstring): `GregoryClient.get()` and
`CatalogCache._key()` both need `site_id`, and neither is called with a
`Context`/request object in hand.

Deliberately dependency-free. `client.py` (reads `site_id` on every upstream
call) and `cache.py` (mixes it into every cache key) both import this module
at module scope; `site.py` (whose `SiteMiddleware` resolves both values —
via `tenants.resolve_tenant()` — and itself depends on `client.py` and
`cache.py`) sets them. If these ContextVars lived in `site.py` instead,
`client.py` and `cache.py` would have to import `site.py` to read `site_id`,
and `site.py` already imports both of them — a real cycle at module-load
time, the same shape telemetry.py's own docstring calls out for
`query_shape`. `_current_tenant` holds `tenants.Tenant | None`, typed as
`Any` here for the same reason: importing `tenants.py`'s `Tenant` at module
scope would pull in `tenants.py`, which itself imports Host-matching helpers
from `site.py` — this module has to stay leaf-level for every other module
here to depend on it safely.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

_current_site_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
	"gregory_mcp_site_id", default=None
)

_current_tenant: contextvars.ContextVar[Any] = contextvars.ContextVar(
	"gregory_mcp_tenant", default=None
)


def get_current_site_id() -> int | None:
	"""The site_id resolved for the in-flight request.

	`None` means "nothing resolved" — env override unset, Host unresolved,
	the tenant directory unavailable, or simply outside a tracked request
	(most of this test suite calls client/cache code directly, as
	`telemetry.test_no_accumulator_active_is_a_silent_no_op` does for the
	telemetry accumulator). Callers must treat `None` as "omit the
	parameter" — never as site `0` or as a sentinel for "no site".
	"""
	return _current_site_id.get()


def get_current_tenant() -> Any:
	"""The `tenants.Tenant` resolved for the in-flight request, or `None`.

	`None` means the same set of things `get_current_site_id()` returning
	`None` does. `TenantGateMiddleware` (tenants.py) refuses every request
	except `ping` when this is `None`, so most handlers downstream of it can
	assume a real `Tenant` — but code that can run before or outside the
	gate (SiteMiddleware itself, tests) must still check.
	"""
	return _current_tenant.get()


# The editor tiers (MCP-AUTH-PLAN.md). "editor": a person with an active grant on
# the site, whose calls go to Django's /editor/ routes. "public": a signed-in
# person without one, who gets exactly the anonymous /mcp data and tools.
TIER_EDITOR = "editor"
TIER_PUBLIC = "public"
SCOPE_READ = "articles:read"
SCOPE_EDIT = "articles:edit"


@dataclass(frozen=True)
class EditorSession:
	"""The verified identity behind one request to the editor address.

	Built by `auth.py` from Django's token introspection, never from anything
	the client sent. `site_id` is the one site the token is bound to.
	"""

	user_id: int
	site_id: int
	tier: str
	scopes: frozenset[str]

	@property
	def is_editor(self) -> bool:
		return self.tier == TIER_EDITOR

	@property
	def can_edit(self) -> bool:
		return self.is_editor and SCOPE_EDIT in self.scopes


_editor_context: contextvars.ContextVar[EditorSession | None] = contextvars.ContextVar(
	"gregory_mcp_editor_context", default=None
)


def get_editor_context() -> EditorSession | None:
	"""The in-flight request's `EditorSession`, or `None` on the anonymous
	address (and outside a tracked request). `None` means anonymous, never
	"editor with unknown rights"."""
	return _editor_context.get()


def get_current_tier() -> str:
	"""`anon`, `public` or `editor`, for telemetry."""
	session = get_editor_context()
	return "anon" if session is None else session.tier


def get_cache_tier() -> str:
	"""`editor` or `anon`, for the catalog cache key.

	The public tier reads exactly what an anonymous caller reads, so it shares
	the anonymous entries; only the editor tier can see more.
	"""
	session = get_editor_context()
	return "editor" if session is not None and session.is_editor else "anon"
