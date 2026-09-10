"""The in-flight request's resolved `site_id`, threaded via `ContextVar`
rather than a parameter added to every tool signature — the same pattern
`telemetry._accumulator` uses, and for the same reason (see telemetry.py's
module docstring): `GregoryClient.get()` and `CatalogCache._key()` both need
this value, and neither is called with a `Context`/request object in hand.

Deliberately dependency-free. `client.py` (reads it on every upstream call)
and `cache.py` (mixes it into every cache key) both import this module at
module scope; `site.py` (which resolves the value — Host header, `GET
/sites/`, `GREGORY_SITE_ID` — and depends on both `client.py` and `cache.py`)
sets it. If this ContextVar lived in `site.py` instead, `client.py` and
`cache.py` would have to import `site.py` to read it, and `site.py` already
imports both of them — a real cycle at module-load time, the same shape
telemetry.py's own docstring calls out for `query_shape`.
"""

from __future__ import annotations

import contextvars

_current_site_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
	"gregory_mcp_site_id", default=None
)


def get_current_site_id() -> int | None:
	"""The site_id resolved for the in-flight request.

	`None` means "nothing resolved" — env override unset, Host unresolved,
	`GET /sites/` unavailable, or simply outside a tracked request (most of
	this test suite calls client/cache code directly, as
	`telemetry.test_no_accumulator_active_is_a_silent_no_op` does for the
	telemetry accumulator). Callers must treat `None` as "omit the
	parameter" — never as site `0` or as a sentinel for "no site".
	"""
	return _current_site_id.get()
