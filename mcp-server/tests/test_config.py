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
