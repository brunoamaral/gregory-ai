"""Thin, GET-only HTTP client for the GregoryAI REST API.

The MCP server is a stateless proxy: it never writes, never holds a
database connection, and only ever issues `GET` requests against the
instance named by `GREGORY_API_URL`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2

from .config import Settings
from .telemetry import record_truncation_error, record_upstream_call, record_upstream_error

logger = logging.getLogger("gregory_mcp.client")

# "Full jitter" exponential backoff (https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/):
# sleep = uniform(0, min(cap, base * 2**(attempt-1))). Base ~200ms, capped ~2s —
# with the default max_retries=2 (3 attempts) that adds well under a second to
# the worst case, comfortably inside the 15s default request timeout.
BASE_BACKOFF_SECONDS = 0.2
MAX_BACKOFF_SECONDS = 2.0

# 429 is retried like a 5xx (the Gregory API throttles bulk-export-style
# requests via BulkExportThrottleMixin; a short backoff-and-retry is more
# useful to a caller than an immediate error). Anything else >= 400 is a
# client error retrying won't fix.
_RETRYABLE_CLIENT_STATUS = {429}


class GregoryAPIError(Exception):
	"""Raised when the upstream Gregory API returns an error response."""

	def __init__(self, status_code: int, detail: str):
		self.status_code = status_code
		self.detail = detail
		super().__init__(f"Gregory API returned {status_code}: {detail}")


class GregoryPaginationTruncatedError(Exception):
	"""Raised when get_all_pages hits max_pages before `next` runs out.

	A silent truncation is worse than a loud failure here: every caller of
	get_all_pages (catalog tools, catalog resources) treats the result as
	the complete set — a model reading a truncated list has no way to tell
	an item is missing from a partial fetch versus genuinely not existing.
	"""

	def __init__(self, path: str, max_pages: int, fetched: int):
		self.path = path
		self.max_pages = max_pages
		self.fetched = fetched
		super().__init__(
			f"get_all_pages({path!r}) did not reach the end of pagination after "
			f"{max_pages} pages ({fetched} rows fetched) — the result set is larger "
			"than this call site expected. Narrow the filters, or raise max_pages "
			"deliberately if the growth is expected."
		)


class GregoryClient:
	"""Async GET client with timeouts and bounded retries.

	One instance is shared for the lifetime of the server process; it holds
	no per-request or per-caller state, matching the stateless-core model of
	the 2026-07-28 spec revision.
	"""

	def __init__(
		self,
		settings: Settings,
		sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
		jitter: Callable[[float, float], float] = random.uniform,
	):
		self._settings = settings
		self._client = httpx2.AsyncClient(
			base_url=settings.api_base,
			timeout=httpx2.Timeout(settings.request_timeout, connect=settings.connect_timeout),
			headers={"Accept": "application/json", "User-Agent": "gregory-mcp/0.1.0"},
		)
		# Injectable so tests can assert on backoff spacing without real
		# sleeping and without depending on random's actual distribution.
		self._sleep = sleep
		self._jitter = jitter

	async def aclose(self) -> None:
		await self._client.aclose()

	async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
		"""Issue a single GET, retrying transient network/5xx/429 failures
		with exponential backoff and jitter between attempts.

		Query params with a `None` value are dropped so tools can pass every
		optional filter unconditionally without hand-pruning the dict.
		"""
		clean_params = {k: v for k, v in (params or {}).items() if v is not None}
		attempts = self._settings.max_retries + 1
		last_exc: Exception | None = None

		for attempt in range(1, attempts + 1):
			call_start = time.monotonic()
			try:
				response = await self._client.get(path, params=clean_params)
			except httpx2.TransportError as exc:
				record_upstream_call((time.monotonic() - call_start) * 1000)
				last_exc = exc
				logger.warning("gregory_api_transport_error", extra={"path": path}, exc_info=True)
				if attempt == attempts:
					record_upstream_error(0)
					raise GregoryAPIError(0, f"network error calling {path}: {exc}") from exc
				await self._sleep(self._backoff_seconds(attempt, None))
				continue

			record_upstream_call((time.monotonic() - call_start) * 1000)
			is_retryable = response.status_code >= 500 or response.status_code in _RETRYABLE_CLIENT_STATUS
			if is_retryable and attempt < attempts:
				logger.warning(
					"gregory_api_retry", extra={"path": path, "status_code": response.status_code}
				)
				await self._sleep(self._backoff_seconds(attempt, response.headers.get("Retry-After")))
				continue

			if response.status_code >= 400:
				record_upstream_error(response.status_code)
				raise GregoryAPIError(response.status_code, response.text[:500])

			return response.json()

		# Unreachable in practice — the loop always returns or raises — but keeps
		# the type checker honest about last_exc being used.
		raise GregoryAPIError(0, f"exhausted retries calling {path}: {last_exc}")

	def _backoff_seconds(self, attempt: int, retry_after_header: str | None) -> float:
		"""Delay before the next attempt. Honors `Retry-After` (seconds form)
		when the upstream sends one; otherwise full-jitter exponential backoff.
		"""
		if retry_after_header is not None:
			try:
				return max(0.0, float(retry_after_header))
			except ValueError:
				pass  # not a numeric Retry-After (e.g. an HTTP-date) — fall through
		cap = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
		return self._jitter(0, cap)

	async def get_all_pages(
		self, path: str, params: dict[str, Any] | None = None, max_pages: int = 20
	) -> list[dict[str, Any]]:
		"""Follow `next` across pages and return the concatenated `results`.

		For the small, slow-changing catalogs (subjects/categories) this is
		the only reliable way to get a complete list: those endpoints use
		plain DRF pagination with a fixed page_size and no `page_size` query
		param to raise it (see FlexiblePagination vs. the DRF default in
		django/admin/settings.py) — passing a bigger page_size silently does
		nothing on those endpoints.

		Raises GregoryPaginationTruncatedError rather than silently returning
		a partial list if `next` hasn't run out by max_pages — this is meant
		for catalogs that are known to be small, so hitting the cap means
		either that assumption broke (the catalog grew) or this was called
		on the wrong endpoint.
		"""
		base_params = dict(params or {})
		results: list[dict[str, Any]] = []
		page = 1
		while page <= max_pages:
			data = await self.get(path, {**base_params, "page": page})
			results.extend(data.get("results", []))
			if not data.get("next"):
				return results
			page += 1
		record_truncation_error()
		raise GregoryPaginationTruncatedError(path, max_pages, len(results))


_client: GregoryClient | None = None


def init_client(settings: Settings) -> GregoryClient:
	"""Create the process-wide client. Call once, before serving requests."""
	global _client
	_client = GregoryClient(settings)
	return _client


def get_client() -> GregoryClient:
	if _client is None:
		raise RuntimeError("GregoryClient has not been initialized — call init_client() first")
	return _client


async def close_client() -> None:
	global _client
	if _client is not None:
		await _client.aclose()
		_client = None
