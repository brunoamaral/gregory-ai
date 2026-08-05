"""Docker HEALTHCHECK probe.

This server negotiates POST JSON-RPC and SSE, not plain GET, so a GET is a
cheap liveness probe. Which rejection you get is deterministic, not router
variance — measured against a live container on 2026-08-05:

    GET  /mcp/  (trailing slash)          -> 307 redirect to /mcp
    GET  /mcp   Accept: application/json  -> 406 Not Acceptable
    GET  /mcp   Accept: */*               -> 200, then an open SSE stream
    HEAD /mcp                             -> 405 Method Not Allowed

So the probe requests /mcp directly with an explicit `Accept:
application/json` and expects 406. Both parts are deliberate: hitting /mcp
rather than /mcp/ avoids depending on redirect handling, and setting Accept
ourselves avoids depending on urllib's default header behaviour — if that
default ever became SSE-compatible, an implicit probe would match the 200
row and hang on the stream until the timeout instead of returning.

405 is accepted alongside 406 so the probe still passes if it is ever
switched to a HEAD request. Either proves the process is up, routed
correctly, and the streamable-http app is really mounted. Anything else —
404 (route not mounted, a real misconfiguration), 5xx (up but erroring), a
connection failure, or a timeout — means something is actually wrong.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

EXPECTED_CODES = {405, 406}

REQUEST = urllib.request.Request(
	"http://127.0.0.1:8001/mcp",
	headers={"Accept": "application/json"},
	method="GET",
)

try:
	urllib.request.urlopen(REQUEST, timeout=3)
	sys.exit(1)  # a 2xx/3xx isn't the expected shape either — investigate
except urllib.error.HTTPError as exc:
	if exc.code not in EXPECTED_CODES:
		sys.exit(1)
except Exception:
	sys.exit(1)
