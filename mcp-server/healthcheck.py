"""Docker HEALTHCHECK probe.

A 4xx response — the redirect-then-406 a bare GET on the streamable-http
endpoint gets — proves the process is up and answering, so that alone
doesn't fail the check. A 5xx means the process is up but the app itself is
erroring (misconfiguration, an unhandled exception per request), and a
connection failure or timeout means it isn't up at all — both count as
unhealthy so Docker actually reports a broken server as broken.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

try:
	urllib.request.urlopen("http://127.0.0.1:8001/mcp/", timeout=3)
except urllib.error.HTTPError as exc:
	if exc.code >= 500:
		sys.exit(1)
except Exception:
	sys.exit(1)
