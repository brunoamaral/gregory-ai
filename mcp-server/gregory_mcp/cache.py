"""Server-side cache for the catalog endpoints (/subjects/, /categories/).

Deliberately narrow: only those two endpoints go through this. Search
results are high-cardinality — every distinct filter combination is a new
key — and must never be served stale, so search tools never touch this
cache; only client_get_all_pages callers backing the small, slow-changing
catalogs do.

Single-flight: on a cold cache, N concurrent callers for the same key await
one upstream fetch rather than each starting their own. /categories/ costs
about a second per request and takes 12 requests to read in full (measured
against a live instance — see HANDOVER-MCP-FOLLOWUP-PLAN.md), so that ~6s
window is wide enough for concurrent callers to actually collide in
practice.

Per-replica, in-process — no Redis. The stateless-core model means each
replica keeps its own cache and does one cold fetch after a deploy; that's
an acceptable cost for this data, and avoids reintroducing the shared state
the 2026-07-28 spec revision deliberately removed.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .client import get_client

# The single source of truth for how long the catalog is considered fresh.
# CacheHint (server.py, the client-facing MCP `ttlMs` hint) and this
# server-side cache's actual expiry both derive from this constant — two
# numbers that have to agree will eventually disagree if hardcoded twice.
CATALOG_CACHE_TTL_MS = 10 * 60 * 1000


class _Entry:
	__slots__ = ("value", "expires_at")

	def __init__(self, value: Any, expires_at: float):
		self.value = value
		self.expires_at = expires_at


class CatalogCache:
	"""A small TTL cache with single-flight, keyed on (path, sorted params).

	`clock` is injectable so tests can control expiry without real sleeping.
	"""

	def __init__(self, ttl_ms: int = CATALOG_CACHE_TTL_MS, clock: Callable[[], float] = time.monotonic):
		self._ttl_seconds = ttl_ms / 1000
		self._clock = clock
		self._entries: dict[str, _Entry] = {}
		self._locks: dict[str, asyncio.Lock] = {}

	def _key(self, path: str, params: dict[str, Any] | None) -> str:
		# None-valued params are dropped the same way client.get() drops them
		# before sending the request — so an explicit `{"team_id": None}` and
		# an omitted `team_id` land on the same cache entry. Without this,
		# list_subjects()'s no-filter call and the subjects_catalog resource's
		# no-params call would miss each other despite requesting the same data.
		clean = {k: v for k, v in (params or {}).items() if v is not None}
		items = tuple(sorted(clean.items()))
		return f"{path}?{items!r}"

	def _lock_for(self, key: str) -> asyncio.Lock:
		lock = self._locks.get(key)
		if lock is None:
			lock = asyncio.Lock()
			self._locks[key] = lock
		return lock

	async def get_or_fetch(
		self,
		path: str,
		params: dict[str, Any] | None,
		fetch: Callable[[], Awaitable[Any]],
	) -> Any:
		"""Return the cached value for (path, params), fetching on a miss.

		Concurrent callers that miss on the same key all await the same
		fetch — the lock is acquired before the fresh-entry re-check, so a
		caller that was waiting on it sees the value the winner just stored
		rather than triggering a second fetch.
		"""
		key = self._key(path, params)
		entry = self._entries.get(key)
		if entry is not None and entry.expires_at > self._clock():
			return entry.value

		async with self._lock_for(key):
			entry = self._entries.get(key)
			if entry is not None and entry.expires_at > self._clock():
				return entry.value

			value = await fetch()
			self._entries[key] = _Entry(value, self._clock() + self._ttl_seconds)
			return value

	def clear(self) -> None:
		self._entries.clear()
		self._locks.clear()


_catalog_cache: CatalogCache | None = None


def get_catalog_cache() -> CatalogCache:
	global _catalog_cache
	if _catalog_cache is None:
		_catalog_cache = CatalogCache()
	return _catalog_cache


def reset_catalog_cache() -> None:
	"""Replace the process-wide cache with a fresh one. Test-only."""
	global _catalog_cache
	_catalog_cache = None


async def get_all_pages_cached(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
	"""Cached `client.get_all_pages` for the two catalog endpoints.

	Cache the concatenated, parsed `results` rows — not a tool- or
	resource-specific projection of them — so list_subjects/list_categories
	and the matching gregory://subjects / gregory://categories resources
	share one cache entry per (path, params) instead of each fetching their
	own copy of the same data.
	"""
	async def fetch() -> list[dict[str, Any]]:
		return await get_client().get_all_pages(path, params)

	return await get_catalog_cache().get_or_fetch(path, params, fetch)
