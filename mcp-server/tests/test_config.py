from __future__ import annotations

import pytest

from gregory_mcp.config import load_settings


def test_requires_api_url(monkeypatch):
	monkeypatch.delenv("GREGORY_API_URL", raising=False)
	with pytest.raises(RuntimeError, match="GREGORY_API_URL"):
		load_settings()


def test_negative_max_retries_clamps_to_zero(monkeypatch):
	monkeypatch.setenv("GREGORY_API_URL", "https://gregory.test")
	monkeypatch.setenv("GREGORY_MAX_RETRIES", "-3")

	settings = load_settings()

	assert settings.max_retries == 0


def test_positive_max_retries_passes_through(monkeypatch):
	monkeypatch.setenv("GREGORY_API_URL", "https://gregory.test")
	monkeypatch.setenv("GREGORY_MAX_RETRIES", "5")

	settings = load_settings()

	assert settings.max_retries == 5


def test_log_dir_defaults_to_none(monkeypatch):
	monkeypatch.setenv("GREGORY_API_URL", "https://gregory.test")
	monkeypatch.delenv("MCP_LOG_DIR", raising=False)

	settings = load_settings()

	assert settings.log_dir is None


def test_log_dir_passes_through(monkeypatch):
	monkeypatch.setenv("GREGORY_API_URL", "https://gregory.test")
	monkeypatch.setenv("MCP_LOG_DIR", "/var/log/gregory-mcp")

	settings = load_settings()

	assert settings.log_dir == "/var/log/gregory-mcp"


def test_site_id_override_defaults_to_none(monkeypatch):
	monkeypatch.setenv("GREGORY_API_URL", "https://gregory.test")
	monkeypatch.delenv("GREGORY_SITE_ID", raising=False)

	settings = load_settings()

	assert settings.site_id_override is None


def test_site_id_override_parses_int(monkeypatch):
	monkeypatch.setenv("GREGORY_API_URL", "https://gregory.test")
	monkeypatch.setenv("GREGORY_SITE_ID", "3")

	settings = load_settings()

	assert settings.site_id_override == 3


def test_site_id_override_rejects_non_integer(monkeypatch):
	monkeypatch.setenv("GREGORY_API_URL", "https://gregory.test")
	monkeypatch.setenv("GREGORY_SITE_ID", "brain-regeneration")

	with pytest.raises(RuntimeError, match="GREGORY_SITE_ID"):
		load_settings()
