"""Shared scaffolding for the editor-address tests: a fake Django that answers
the tenant directories, introspection and `/editor/` routes, the ASGI app built
by `app.build_app()`, and an SDK client wired to it in process."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from gregory_mcp.app import build_app
from gregory_mcp.auth import IntrospectionVerifier
from tests.conftest import EDITOR_SETTINGS, route_by_path, tenants_payload

HOST = "gregory-ai.br.test"
OTHER_HOST = "gregory-ai.other.test"
SITE_ID = 3
OTHER_SITE_ID = 4
SERVICE_KEY = EDITOR_SETTINGS.service_key
EDITOR_URL = f"https://{HOST}/mcp/editor"


def resource_for(host: str = HOST) -> str:
	return f"https://{host}/mcp/editor"


def introspection(
	*,
	user_id: int = 7,
	site_id: int = SITE_ID,
	tier: str = "editor",
	scope: str = "articles:read articles:edit",
	aud: list[str] | None = None,
	exp: int | None = None,
	client_id: str = "client-1",
) -> dict:
	return {
		"active": True,
		"scope": scope,
		"exp": exp if exp is not None else int(time.time()) + 3600,
		"client_id": client_id,
		"username": "ana",
		"token_type": "Bearer",
		"aud": aud if aud is not None else [resource_for()],
		"user_id": user_id,
		"site_id": site_id,
		"tier": tier,
	}


def two_tenants(*, private_second: bool = False) -> list[dict]:
	return tenants_payload(
		{
			"site_id": SITE_ID,
			"domain": "br.test",
			"name": "BR",
			"title": "Brain Regeneration",
			"subjects": [{"id": 1, "subject_name": "Multiple Sclerosis"}],
		},
		{
			"site_id": OTHER_SITE_ID,
			"domain": "other.test",
			"name": "Other",
			"title": "Other Site",
			"api_public": not private_second,
			"subjects": [{"id": 2, "subject_name": "Alzheimer's Disease"}],
		},
	)


class FakeDjango:
	"""Routes for the Django the MCP server talks to. `tokens` maps a raw
	bearer token to its introspection payload; anything else is inactive."""

	def __init__(self, *, editor_tenants=None, anon_tenants=None, tokens=None, api_routes=None):
		self.editor_tenants = editor_tenants if editor_tenants is not None else two_tenants()
		self.anon_tenants = anon_tenants if anon_tenants is not None else two_tenants()
		self.tokens = tokens or {}
		# A route is a ready `Response` or a function of the request.
		self.api_routes = {
			path: (route if callable(route) else (lambda _request, route=route: route))
			for path, route in (api_routes or {}).items()
		}
		self.introspect_calls: list[str] = []

	def handler(self) -> Callable[[httpx2.Request], httpx2.Response]:
		fallback = route_by_path(self.api_routes)

		def handle(request: httpx2.Request) -> httpx2.Response:
			path = request.url.path
			authorized = request.headers.get("authorization") == f"Bearer {SERVICE_KEY}"
			if path == "/tenants/":
				return httpx2.Response(200, json=self.anon_tenants)
			if path == "/editor/tenants/":
				if not authorized:
					return httpx2.Response(401, json={"detail": "no"})
				return httpx2.Response(200, json=self.editor_tenants)
			if path == "/o/introspect/":
				if not authorized:
					return httpx2.Response(401, json={"error": "invalid_client"})
				token = parse_qs(request.content.decode())["token"][0]
				self.introspect_calls.append(token)
				return httpx2.Response(200, json=self.tokens.get(token, {"active": False}))
			return fallback(request)

		return handle


def new_app(verifier: IntrospectionVerifier | None = None):
	return build_app(EDITOR_SETTINGS, verifier=verifier)


@asynccontextmanager
async def running(app) -> AsyncIterator[None]:
	async with app.router.lifespan_context(app):
		yield


def http_client(app, *, host: str = HOST, token: str | None = None) -> httpx2.AsyncClient:
	headers = {"Authorization": f"Bearer {token}"} if token else {}
	return httpx2.AsyncClient(
		transport=httpx2.ASGITransport(app=app), base_url=f"https://{host}", headers=headers
	)


@asynccontextmanager
async def mcp_client(app, *, host: str = HOST, token: str | None = None) -> AsyncIterator[Client]:
	"""An SDK client talking to `/mcp/editor` on `host`, in process."""
	async with http_client(app, host=host, token=token) as http:
		async with Client(streamable_http_client(f"https://{host}/mcp/editor", http_client=http)) as client:
			yield client


@asynccontextmanager
async def anonymous_client(app, *, host: str = HOST) -> AsyncIterator[Client]:
	async with http_client(app, host=host) as http:
		async with Client(streamable_http_client(f"https://{host}/mcp", http_client=http)) as client:
			yield client
