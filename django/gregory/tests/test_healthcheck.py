"""
The gregory container's Docker healthcheck: the /healthz/ view
(admin/health.py) and the probe that calls it (django/healthcheck.py).

Run with:
    docker exec gregory python -m pytest gregory/tests/test_healthcheck.py
"""

import io
import urllib.error
from unittest import mock

import pytest
from django.db import OperationalError
from django.test import Client

import healthcheck


@pytest.mark.django_db
class TestHealthzView:
	def setup_method(self):
		self.client = Client()

	def test_get_returns_ok(self):
		resp = self.client.get("/healthz/")
		assert resp.status_code == 200
		assert resp.content == b"ok\n"
		assert resp["Content-Type"] == "text/plain; charset=utf-8"

	def test_head_is_allowed(self):
		assert self.client.head("/healthz/").status_code == 200

	def test_post_is_rejected(self):
		assert self.client.post("/healthz/").status_code == 405

	def test_response_is_never_cached(self):
		resp = self.client.get("/healthz/")
		assert "no-store" in resp["Cache-Control"]

	def test_database_down_returns_503(self):
		# Replace only the view's handle on the connection: patching the shared
		# connection's cursor() would also break the test's own DB use.
		broken = mock.Mock()
		broken.cursor.side_effect = OperationalError("connection refused")
		with mock.patch("admin.health.connection", broken):
			resp = self.client.get("/healthz/")
		assert resp.status_code == 503
		# No error detail leaks into a publicly reachable response.
		assert resp.content == b"database unavailable\n"


class _Response(io.BytesIO):
	def __init__(self, status):
		super().__init__(b"")
		self.status = status


class TestProbe:
	def _run(self, **urlopen_kwargs):
		with mock.patch("healthcheck.urllib.request.urlopen", **urlopen_kwargs):
			return healthcheck.main()

	def test_200_is_healthy(self):
		assert self._run(return_value=_Response(200)) == 0

	def test_non_200_success_status_is_unhealthy(self):
		assert self._run(return_value=_Response(204)) == 1

	def test_503_is_unhealthy(self, capsys):
		err = urllib.error.HTTPError(
			healthcheck.URL, 503, "Service Unavailable", {}, None
		)
		assert self._run(side_effect=err) == 1
		assert "HTTP 503" in capsys.readouterr().out

	def test_connection_refused_is_unhealthy(self, capsys):
		err = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
		assert self._run(side_effect=err) == 1
		assert "Connection refused" in capsys.readouterr().out

	def test_timeout_is_unhealthy(self):
		assert self._run(side_effect=TimeoutError("timed out")) == 1

	def test_probe_against_live_server(self, live_server):
		# End to end: the real probe against the real view over HTTP.
		with mock.patch.object(healthcheck, "URL", f"{live_server.url}/healthz/"):
			assert healthcheck.main() == 0
