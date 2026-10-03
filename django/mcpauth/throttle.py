"""
mcpauth/throttle.py

Small counters on Django's cache, for the two places that take anonymous
input on the authorization server: the login page and client registration.
Approximate by design (the cache is shared, not transactional); the aim is to
make guessing passwords or filling the client table slow, not to be a ledger.
"""

import hashlib
import time

from django.core.cache import cache


def client_address(request) -> str:
	"""The address to count against, as nginx saw it.

	Not ``getIPAddress()``: that takes the first ``X-Forwarded-For`` entry, and
	nginx's ``$proxy_add_x_forwarded_for`` appends to whatever the client sent,
	so the first entry is the client's to choose and a fresh value per request
	would never hit a limit. nginx sets ``X-Real-IP`` to ``$remote_addr`` on
	every proxied location, replacing any client-sent value; without it (a
	request that didn't come through nginx) the socket address is all there is.
	"""
	return request.META.get("HTTP_X_REAL_IP", "").strip() or request.META.get("REMOTE_ADDR") or "unknown"


def _key(scope: str, subject: str) -> str:
	digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:32]
	return f"mcpauth:{scope}:{digest}"


def count(scope: str, subject: str) -> int:
	entry = cache.get(_key(scope, subject))
	if not entry or entry[1] <= time.time():
		return 0
	return entry[0]


def hit(scope: str, subject: str, window_seconds: int) -> int:
	"""Count one event for ``subject``; returns the count inside the window.

	The window's end is stored with the count and every write passes the
	time left as its timeout. ``cache.incr()`` can't be used: the base
	implementation, which the production ``DatabaseCache`` inherits, rewrites
	the value with the cache's default timeout (300 seconds) and so cut every
	window down to five minutes.
	"""
	key = _key(scope, subject)
	now = time.time()
	entry = cache.get(key)
	if not entry or entry[1] <= now:
		cache.set(key, (1, now + window_seconds), window_seconds)
		return 1
	hits, window_end = entry[0] + 1, entry[1]
	cache.set(key, (hits, window_end), max(1, int(window_end - now)))
	return hits


def reset(scope: str, subject: str) -> None:
	cache.delete(_key(scope, subject))
