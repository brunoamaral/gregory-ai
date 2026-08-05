"""Docker HEALTHCHECK probe.

This server negotiates POST JSON-RPC and SSE, not plain GET, so a GET is a
cheap liveness probe. Which rejection you get is deterministic, not router
variance — measured against a live container on 2026-08-05:

    GET  /mcp/  (trailing slash)          -> 307 redirect to /mcp
    GET  /mcp   Accept: application/json  -> 406 Not Acceptable
    GET  /mcp   Accept: */*               -> 200, then an open SSE stream
    HEAD /mcp                             -> 405 Method Not Allowed

urlopen() follows the 307 and sends no SSE-compatible Accept header, so it
lands on 406. 405 is accepted too so the probe still passes if this is ever
switched to a HEAD request. Both prove the process is up, routed correctly,
and the streamable-http app is really mounted.

The 200 row is why this probe must never use a permissive Accept: it would
hang on the stream until the timeout rather than returning. Anything else —
404 (route not mounted, a real misconfiguration), 5xx (up but erroring), a
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
