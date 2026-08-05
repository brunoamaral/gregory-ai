"""Docker HEALTHCHECK probe.

Any HTTP response — including the redirect or 4xx a bare GET on the
streamable-http endpoint gets — proves the process is up and answering.
Only a connection failure or timeout means it's actually down.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

try:
	urllib.request.urlopen("http://127.0.0.1:8001/mcp/", timeout=3)
except urllib.error.HTTPError:
	pass
except Exception:
	sys.exit(1)
