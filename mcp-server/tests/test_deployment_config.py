"""The deployment files that make the editor address reachable and safe
(MCP-AUTH-PLAN.md, phase 6). `nginx -t` is not available in CI, so these tests
read the example nginx config and the compose file and pin the properties that
matter: the right locations exist, the machine-only routes are hidden, and the
environment names match what the code reads. They skip when the files are not
there (the MCP image is built from `mcp-server/` alone)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "nginx-example-configuration" / "nginx.conf"
COMPOSE = ROOT / "docker-compose.yaml"

pytestmark = pytest.mark.skipif(not (NGINX.exists() and COMPOSE.exists()), reason="repository files not present")


def _server_block(config: str, server_name: str) -> str:
	"""The text of the `server { ... }` block naming `server_name` (by brace depth)."""
	for match in re.finditer(r"^\s*server\s*\{", config, re.MULTILINE):
		depth, i = 1, match.end()
		while depth and i < len(config):
			depth += {"{": 1, "}": -1}.get(config[i], 0)
			i += 1
		block = config[match.start() : i]
		if re.search(rf"server_name\s+{re.escape(server_name)}\s*;", block):
			return block
	raise AssertionError(f"no server block for {server_name}")


def _location(block: str, header: str) -> str:
	start = block.index(header)
	depth, i = 0, block.index("{", start)
	begin = i
	while True:
		depth += {"{": 1, "}": -1}.get(block[i], 0)
		i += 1
		if depth == 0:
			return block[begin:i]


@pytest.fixture(scope="module")
def nginx() -> str:
	return NGINX.read_text()


def test_editor_address_has_its_own_longer_location_and_zone(nginx):
	mcp_host = _server_block(nginx, "gregory-ai.DOMAIN.ORG")
	editor = _location(mcp_host, "location /mcp/editor")

	assert "zone=mcp_editor_per_client" in editor
	assert "zone=mcp_per_tool" not in editor
	assert "limit_req_status                   429" in editor
	assert "proxy_pass                         http://127.0.0.1:8001;" in editor
	# SSE: no buffering, long read timeout, as on the anonymous location.
	assert "proxy_buffering                    off" in editor
	assert "proxy_read_timeout                 300s" in editor
	# The zone is declared at http level.
	assert re.search(r"limit_req_zone\s+\$binary_remote_addr\s+zone=mcp_editor_per_client:", nginx)


def test_editor_location_is_not_captured_by_the_anonymous_one(nginx):
	"""`location /mcp` is a prefix match; the editor path must have its own,
	longer, location so it does not share the anonymous per-tool limits. nginx
	picks the longest matching prefix whatever the declaration order, so the
	order of the two blocks is not checked."""
	mcp_host = _server_block(nginx, "gregory-ai.DOMAIN.ORG")
	assert "location /mcp {" in mcp_host
	assert "location /mcp/editor {" in mcp_host


def test_protected_resource_metadata_is_proxied_and_the_catch_all_still_404s(nginx):
	mcp_host = _server_block(nginx, "gregory-ai.DOMAIN.ORG")
	well_known = _location(mcp_host, "location = /.well-known/oauth-protected-resource/mcp/editor")

	assert "proxy_pass                         http://127.0.0.1:8001;" in well_known
	assert "proxy_set_header Host              $host;" in well_known
	catch_all = _location(mcp_host, "location / {")
	assert "return 404" in catch_all
	# The dotfile rule must not swallow /.well-known (it has a lookahead for it).
	assert r"location ~ /\.(?!well-known)" in mcp_host


def test_no_authorization_header_is_logged_or_replaced(nginx):
	mcp_host = _server_block(nginx, "gregory-ai.DOMAIN.ORG")
	assert "http_authorization" not in nginx
	assert not re.search(r"proxy_set_header\s+Authorization", mcp_host)
	assert not re.search(r"proxy_set_header\s+Authorization", nginx)


def test_machine_only_routes_are_hidden_on_the_api_host(nginx):
	api_host = _server_block(nginx, "api.DOMAIN.ORG")

	assert "return 404" in _location(api_host, "location ^~ /editor/")
	assert "return 404" in _location(api_host, "location = /o/introspect/")
	proxy = _location(api_host, "location / {")
	assert 'proxy_set_header X-Gregory-Editor-User ""' in proxy
	assert 'proxy_set_header X-Gregory-Editor-Site ""' in proxy
	# The sign-in flow itself is not hidden.
	assert "/o/authorize" not in api_host.split("location /media/")[0]
	assert "location ^~ /o/" not in api_host


def test_nginx_braces_balance(nginx):
	uncommented = "\n".join(line.split("#", 1)[0] for line in nginx.splitlines())
	assert uncommented.count("{") == uncommented.count("}")


def test_compose_wires_the_editor_environment_to_both_services():
	compose = yaml.safe_load(COMPOSE.read_text())
	django = compose["services"]["gregory"]["environment"]
	mcp = compose["services"]["gregory-mcp"]["environment"]

	assert "GREGORY_MCP_SERVICE_KEY=${GREGORY_MCP_SERVICE_KEY:-}" in django
	assert "GREGORY_MCP_SERVICE_KEY=${GREGORY_MCP_SERVICE_KEY:-}" in mcp
	assert any(item.startswith("OAUTH_ISSUER=") for item in django)
	assert any(item.startswith("GREGORY_OAUTH_ISSUER=") for item in mcp)
	# Django reads this without an `or` fallback, so compose must default it.
	assert "MCP_EDITOR_HOST_PREFIX=${MCP_EDITOR_HOST_PREFIX:-gregory-ai}" in django
	# With no key set the MCP container serves /mcp only.
	assert not any(item.startswith("GREGORY_MCP_SERVICE_KEY=") and "changeme" in item for item in mcp)


def test_compose_environment_names_are_the_ones_the_code_reads():
	source = (ROOT / "mcp-server" / "gregory_mcp" / "config.py").read_text()
	assert '"GREGORY_MCP_SERVICE_KEY"' in source
	assert '"GREGORY_OAUTH_ISSUER"' in source
	settings = (ROOT / "django" / "admin" / "settings.py").read_text()
	assert "'GREGORY_MCP_SERVICE_KEY'" in settings or '"GREGORY_MCP_SERVICE_KEY"' in settings
	assert "'OAUTH_ISSUER'" in settings
	assert "'MCP_EDITOR_HOST_PREFIX'" in settings


def test_example_env_has_placeholders_only():
	env = (ROOT / "example.env").read_text()
	for name in ("GREGORY_MCP_SERVICE_KEY", "OAUTH_ISSUER"):
		assert re.search(rf"^{name}=''\s*$", env, re.MULTILINE), f"{name} must be an empty placeholder"
