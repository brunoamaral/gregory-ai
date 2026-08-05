"""Docker HEALTHCHECK probe.

A bare GET on the streamable-http endpoint (this server negotiates POST
JSON-RPC and SSE, not plain GET) redirects once and lands on 406 Not
Acceptable — verified against a live instance. A 405 Method Not Allowed is
also accepted: which of the two a router/server returns for "wrong verb on
a mounted route" can legitimately vary. Either proves the process is up,
routed correctly, and the streamable-http app is actually mounted at
/mcp/, so together they're the only things that count as healthy.
Anything else — a 404 (route not mounted — a real misconfiguration, not
just "wrong verb/Accept header"), a 5xx (the app is up but erroring), a
connection failure, or a timeout — means something is actually wrong.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

EXPECTED_CODES = {405, 406}

try:
	urllib.request.urlopen("http://127.0.0.1:8001/mcp/", timeout=3)
	sys.exit(1)  # a 2xx/3xx isn't the expected shape either — investigate
except urllib.error.HTTPError as exc:
	if exc.code not in EXPECTED_CODES:
		sys.exit(1)
except Exception:
	sys.exit(1)
