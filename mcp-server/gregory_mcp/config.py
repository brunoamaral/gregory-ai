"""Environment configuration for the Gregory MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
	api_url: str
	host: str
	port: int
	request_timeout: float
	connect_timeout: float
	max_retries: int
	log_level: str

	@property
	def api_base(self) -> str:
		return self.api_url.rstrip("/")


def load_settings() -> Settings:
	api_url = os.environ.get("GREGORY_API_URL", "").strip()
	if not api_url:
		raise RuntimeError(
			"GREGORY_API_URL is required — the base URL of the GregoryAI instance "
			"this server proxies (e.g. https://api.brain-regeneration.com)"
		)
	return Settings(
		api_url=api_url,
		host=os.environ.get("MCP_HOST", "0.0.0.0"),
		port=int(os.environ.get("MCP_PORT", "8001")),
		request_timeout=float(os.environ.get("GREGORY_REQUEST_TIMEOUT", "15")),
		connect_timeout=float(os.environ.get("GREGORY_CONNECT_TIMEOUT", "5")),
		# max(0, ...): a negative value here would make GregoryClient.get()'s
		# `attempts = max_retries + 1` reach 0, so its retry loop never runs at
		# all and every call fails immediately without ever hitting the network.
		max_retries=max(0, int(os.environ.get("GREGORY_MAX_RETRIES", "2"))),
		log_level=os.environ.get("MCP_LOG_LEVEL", "INFO").upper(),
	)
