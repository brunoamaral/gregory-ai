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
	# Editor access (MCP-AUTH-PLAN.md). All three have to be set for the
	# authenticated /mcp/editor address to exist; without them the server runs
	# exactly as before, anonymous /mcp only.
	service_key: str = ""
	oauth_issuer: str = ""
	editor_scheme: str = "https"

	@property
	def api_base(self) -> str:
		return self.api_url.rstrip("/")

	@property
	def editor_enabled(self) -> bool:
		return bool(self.service_key and self.oauth_issuer)


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
		service_key=os.environ.get("GREGORY_MCP_SERVICE_KEY", "").strip(),
		oauth_issuer=os.environ.get("GREGORY_OAUTH_ISSUER", "").strip().rstrip("/"),
		editor_scheme=_load_editor_scheme(),
	)


def _load_editor_scheme() -> str:
	"""`MCP_EDITOR_SCHEME`: the scheme of editor addresses, `https` unless a
	local setup without TLS says `http`. Anything else fails at startup: the
	scheme is part of the audience a token is checked against, so a typo here
	would lock every editor out."""
	scheme = os.environ.get("MCP_EDITOR_SCHEME", "https").strip().lower() or "https"
	if scheme not in ("http", "https"):
		raise RuntimeError(f"MCP_EDITOR_SCHEME must be http or https, got {scheme!r}")
	return scheme


def _load_site_id_override() -> int | None:
	"""`GREGORY_SITE_ID`: pins every request to one tenant, bypassing
	Host-based resolution (gregory_mcp/tenants.py) entirely. For a
	single-tenant deployment that doesn't want to depend on the inbound Host
	header. Unlike before Phase 3, this does NOT skip the network: the
	server still fetches `GET /tenants/` to find that id's full record (name,
	title, description, subjects, prompts, documents) — it just doesn't need
	the Host header to pick which entry. Unset by default — Host resolution
	is the fallback in that case, not an error.

	Invalid (non-integer) values fail loudly at startup rather than silently
	falling back to Host resolution — a typo'd env var here should surface as
	a crash-on-boot, not as requests quietly landing on the wrong tenant's
	data.
	"""
	raw = os.environ.get("GREGORY_SITE_ID", "").strip()
	if not raw:
		return None
	try:
		return int(raw)
	except ValueError:
		raise RuntimeError(f"GREGORY_SITE_ID must be an integer Site ID, got {raw!r}") from None
