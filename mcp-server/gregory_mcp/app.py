"""The ASGI app behind the process: `/mcp` always, `/mcp/editor` when editor
access is configured (MCP-AUTH-PLAN.md, D14).

Two `MCPServer`s share one Starlette app and one port. Each builds its own
streamable-HTTP app; this lifts their routes into a single router, wraps the
editor endpoint in our per-`Host` auth (auth.py), and runs both session
managers' lifespans together. The anonymous endpoint is mounted exactly as the
SDK built it, so `/mcp` behaves as it always has.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from starlette.applications import Starlette
from starlette.routing import Route

from .auth import EDITOR_PATH, RESOURCE_METADATA_PATH, EditorAuthMiddleware, IntrospectionVerifier, ProtectedResourceMetadataApp
from .config import Settings
from .server import build_server


def build_app(settings: Settings, verifier: IntrospectionVerifier | None = None) -> Starlette:
	anonymous = build_server().streamable_http_app(
		streamable_http_path="/mcp", stateless_http=True, host=settings.host
	)
	inner_apps = [anonymous]
	routes = list(anonymous.routes)

	if settings.editor_enabled:
		editor_app = build_server(editor=True).streamable_http_app(
			streamable_http_path=EDITOR_PATH, stateless_http=True, host=settings.host
		)
		inner_apps.append(editor_app)
		# The SDK's route for the endpoint, with our auth in front of it. The
		# well-known document lives outside the SDK's app: it is per host.
		(editor_route,) = editor_app.routes
		routes.append(
			Route(
				EDITOR_PATH,
				endpoint=EditorAuthMiddleware(
					editor_route.endpoint, settings=settings, verifier=verifier or IntrospectionVerifier()
				),
			)
		)
		routes.append(
			Route(
				RESOURCE_METADATA_PATH,
				endpoint=ProtectedResourceMetadataApp(settings=settings),
				methods=["GET", "OPTIONS"],
			)
		)

	@contextlib.asynccontextmanager
	async def lifespan(_app: Starlette) -> AsyncIterator[None]:
		async with contextlib.AsyncExitStack() as stack:
			for inner in inner_apps:
				await stack.enter_async_context(inner.router.lifespan_context(inner))
			yield

	return Starlette(routes=routes, lifespan=lifespan)
