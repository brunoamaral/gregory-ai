from __future__ import annotations

from collections.abc import Callable

import httpx2
import pytest

from gregory_mcp.client import GregoryClient
from gregory_mcp.config import Settings
from gregory_mcp.server import build_server

TEST_SETTINGS = Settings(
	api_url="https://gregory.test",
	host="127.0.0.1",
	port=8001,
	request_timeout=5,
	connect_timeout=2,
	max_retries=0,
	log_level="ERROR",
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
	"""
	import gregory_mcp.client as client_module

	client = GregoryClient(TEST_SETTINGS)
	recorder = RecordingTransport(lambda request: httpx2.Response(200, json={}))
	client._client._transport = recorder.transport

	monkeypatch.setattr(client_module, "_client", client)

	class Handle:
		def set_handler(self, fn):
			recorder._handler = fn

		@property
		def requests(self):
			return recorder.requests

	yield Handle()


@pytest.fixture
def server():
	return build_server()
