"""Thin, GET-only HTTP client for the GregoryAI REST API.

The MCP server is a stateless proxy: it never writes, never holds a
database connection, and only ever issues `GET` requests against the
instance named by `GREGORY_API_URL`.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx2

from .config import Settings

logger = logging.getLogger("gregory_mcp.client")


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

	def __init__(self, settings: Settings):
		self._settings = settings
		self._client = httpx2.AsyncClient(
			base_url=settings.api_base,
			timeout=httpx2.Timeout(settings.request_timeout, connect=settings.connect_timeout),
			headers={"Accept": "application/json", "User-Agent": "gregory-mcp/0.1.0"},
		)

	async def aclose(self) -> None:
		await self._client.aclose()

	async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
		"""Issue a single GET, retrying transient network/5xx failures.

		Query params with a `None` value are dropped so tools can pass every
		optional filter unconditionally without hand-pruning the dict.
		"""
		clean_params = {k: v for k, v in (params or {}).items() if v is not None}
		attempts = self._settings.max_retries + 1
		last_exc: Exception | None = None

		for attempt in range(1, attempts + 1):
			try:
				response = await self._client.get(path, params=clean_params)
			except httpx2.TransportError as exc:
				last_exc = exc
				logger.warning("gregory_api_transport_error", extra={"path": path})
				if attempt == attempts:
					raise GregoryAPIError(0, f"network error calling {path}: {exc}") from exc
				continue

			if response.status_code >= 500 and attempt < attempts:
				logger.warning(
					"gregory_api_5xx_retry", extra={"path": path, "status_code": response.status_code}
				)
				continue

			if response.status_code >= 400:
				raise GregoryAPIError(response.status_code, response.text[:500])

			return response.json()

		# Unreachable in practice — the loop always returns or raises — but keeps
		# the type checker honest about last_exc being used.
		raise GregoryAPIError(0, f"exhausted retries calling {path}: {last_exc}")

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
