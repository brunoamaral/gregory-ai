from __future__ import annotations

from collections.abc import Callable

import httpx2
import pytest

from gregory_mcp.cache import reset_catalog_cache
from gregory_mcp.client import GregoryClient
from gregory_mcp.config import Settings
from gregory_mcp.server import build_server
from gregory_mcp.tenants import reset_tenant_directory

TEST_SETTINGS = Settings(
	api_url="https://gregory.test",
	host="127.0.0.1",
	port=8001,
	request_timeout=5,
	connect_timeout=2,
	max_retries=0,
	log_level="ERROR",
	log_dir=None,
	site_id_override=None,
)


class RecordingTransport:
	"""Wraps an httpx2.MockTransport and records every request made through it."""

	def __init__(self, handler: Callable[[httpx2.Request], httpx2.Response]):
		self.requests: list[httpx2.Request] = []
		self._handler = handler
		self._transport = httpx2.MockTransport(self._record)

	def _record(self, request: httpx2.Request) -> httpx2.Response:
		self.requests.append(request)
		return self._handler(request)

	@property
	def transport(self) -> httpx2.MockTransport:
		return self._transport


@pytest.fixture
def mock_gregory(monkeypatch):
	"""Patches the module-level Gregory client with one backed by a mock transport.

	Yields a `set_handler(fn)` setter; `fn(request) -> httpx2.Response`.
	Also exposes `.requests` (via `recorder`) for asserting on query params.

	Resets the process-wide catalog cache (see gregory_mcp/cache.py) before
	and after each test — it's a module-level singleton, so without this a
	list_subjects()/list_categories() call in one test could be served a
	cached result left behind by a completely different test. Also resets
	gregory_mcp.tenants' module-level state (the GREGORY_SITE_ID override,
	the cached `/tenants/` directory, and the kept-stale-copy) for the same
	reason.

	The default handler returns `{}` for every path, which is not a tenant
	directory shape — a test that needs a tenant to resolve (most
	server-level ones do, since TenantGateMiddleware refuses everything
	else) must call `set_handler` with something built on `tenants_payload()`
	below, routing `/tenants/` to it.
	"""
	import gregory_mcp.client as client_module

	client = GregoryClient(TEST_SETTINGS)
	recorder = RecordingTransport(lambda request: httpx2.Response(200, json={}))
	client._client._transport = recorder.transport

	monkeypatch.setattr(client_module, "_client", client)
	reset_catalog_cache()
	reset_tenant_directory()

	class Handle:
		def set_handler(self, fn):
			recorder._handler = fn

		@property
		def requests(self):
			return recorder.requests

	yield Handle()
	reset_catalog_cache()
	reset_tenant_directory()


@pytest.fixture
def server():
	return build_server()


def tenants_payload(*tenants: dict) -> list[dict]:
	"""Build a `GET /tenants/` response body from partial tenant dicts.

	Fills in every field `tenants.py`'s parser requires, so a test only has
	to override what it cares about, e.g. `tenants_payload({"site_id": 3,
	"domain": "brain-regeneration.com"})`.
	"""
	defaults = {
		"site_id": 1,
		"domain": "example.test",
		"name": "Example",
		"title": "Example",
		"api_public": True,
		"mcp_description": "",
		"subjects": [],
		"prompts": [],
		"documents": [],
	}
	return [{**defaults, **t} for t in tenants]


def route_by_path(routes: dict[str, Callable[[httpx2.Request], httpx2.Response]]):
	"""A `mock_gregory` handler that dispatches on `request.url.path`, falling
	back to an empty-list 200 for anything not listed — the shape
	`get_all_pages`-backed tools/resources expect from an endpoint they
	don't care about in a given test.
	"""

	def handler(request: httpx2.Request) -> httpx2.Response:
		route = routes.get(request.url.path)
		if route is not None:
			return route(request)
		return httpx2.Response(200, json={"count": 0, "next": None, "results": []})

	return handler
