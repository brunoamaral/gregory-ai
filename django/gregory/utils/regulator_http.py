"""
What the regulator importers (EMA, FDA) share: the User-Agent they send, the download
timeout and retry count, a whitespace-collapsing clip for text columns, and a GET that
retries on a server error or a connection problem. Pure functions with no database access.
"""

import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "gregory-ai (+https://gregory-ai.com)"
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_RETRIES = 2

_WHITESPACE = re.compile(r"\s+")


def clip(value, length):
	"""``value`` with whitespace collapsed and trimmed, cut to ``length`` characters."""
	return _WHITESPACE.sub(" ", value or "").strip()[:length]


def get_with_retries(url, headers, label):
	"""GET ``url``, retrying twice on a 5xx or a connection problem.

	Any other status comes back for the caller to judge. ``label`` names the download in log
	lines and in the error raised when the retries run out (``requests.RequestException``)."""
	last_error = None
	for attempt in range(DOWNLOAD_RETRIES + 1):
		if attempt:
			time.sleep(2 * attempt)
		try:
			response = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT)
		except (requests.ConnectionError, requests.Timeout) as exc:
			last_error = exc
			logger.warning("%s download failed (attempt %d): %s", label, attempt + 1, exc)
			continue
		if response.status_code >= 500:
			last_error = RuntimeError(f"HTTP {response.status_code}")
			logger.warning("%s returned HTTP %d (attempt %d)", label, response.status_code, attempt + 1)
			continue
		return response
	raise requests.RequestException(f"{label} unavailable after retries: {last_error}")
