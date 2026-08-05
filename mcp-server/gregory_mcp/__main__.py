"""Entrypoint: `python -m gregory_mcp`.

Runs the streamable-HTTP transport per the 2026-07-28 spec revision — the
stateless core here holds no session state, so `stateless_http=True` is safe
and lets nginx round-robin across replicas with no sticky sessions.
"""

from __future__ import annotations

import logging

from .client import init_client
from .config import load_settings
from .logging_config import configure_logging
from .server import build_server

logger = logging.getLogger("gregory_mcp")


def main() -> None:
	settings = load_settings()
	configure_logging(settings.log_level)
	init_client(settings)

	logger.info("gregory_mcp_starting", extra={"path": settings.api_base})
	server = build_server()
	server.run(
		"streamable-http",
		host=settings.host,
		port=settings.port,
		stateless_http=True,
	)


if __name__ == "__main__":
	main()
