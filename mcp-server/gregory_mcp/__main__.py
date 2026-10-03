"""Entrypoint: `python -m gregory_mcp`.

Runs the streamable-HTTP transport per the 2026-07-28 spec revision — the
stateless core here holds no session state, so `stateless_http=True` is safe
and lets nginx round-robin across replicas with no sticky sessions.

With `GREGORY_MCP_SERVICE_KEY` and `GREGORY_OAUTH_ISSUER` set, the same process
also serves the authenticated `/mcp/editor` (app.py).
"""

from __future__ import annotations

import logging

from .client import init_client
from .config import load_settings
from .logging_config import configure_logging
from .server import build_server
from .tenants import init_tenant_resolution

logger = logging.getLogger("gregory_mcp")


def main() -> None:
	settings = load_settings()
	configure_logging(settings.log_level, settings.log_dir)
	init_client(settings)
	init_tenant_resolution(settings)

	logger.info("gregory_mcp_starting", extra={"path": settings.api_base})
	if not settings.editor_enabled:
		# Anonymous /mcp only, run by the SDK exactly as before editor access.
		server = build_server()
		server.run(
			"streamable-http",
			host=settings.host,
			port=settings.port,
			stateless_http=True,
		)
		return

	# /mcp and /mcp/editor in one app (app.py). Needs GREGORY_MCP_SERVICE_KEY and
	# GREGORY_OAUTH_ISSUER; without both the editor address does not exist.
	import uvicorn

	from .app import build_app

	logger.info("gregory_mcp_editor_enabled")
	uvicorn.run(
		build_app(settings),
		host=settings.host,
		port=settings.port,
		log_level=settings.log_level.lower(),
	)


if __name__ == "__main__":
	main()
