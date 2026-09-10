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
	log_dir: str | None
	site_id_override: int | None

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
		log_dir=os.environ.get("MCP_LOG_DIR", "").strip() or None,
		site_id_override=_load_site_id_override(),
	)


def _load_site_id_override() -> int | None:
	"""`GREGORY_SITE_ID`: pins every upstream call to one site, bypassing
	Host-based resolution (gregory_mcp/site.py) entirely. For a single-tenant
	deployment that doesn't want to depend on the inbound Host header (or on
	the API's `GET /sites/` discovery endpoint existing/being reachable) at
	all. Unset by default — Host resolution is the fallback in that case, not
	an error.

	Invalid (non-integer) values fail loudly at startup rather than silently
	falling back to Host resolution — a typo'd env var here should surface as
	a crash-on-boot, not as requests quietly landing on the wrong site's data.
	"""
	raw = os.environ.get("GREGORY_SITE_ID", "").strip()
	if not raw:
		return None
	try:
		return int(raw)
	except ValueError:
		raise RuntimeError(f"GREGORY_SITE_ID must be an integer Site ID, got {raw!r}") from None
