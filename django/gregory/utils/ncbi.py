"""NCBI E-utilities access: the only code in the app that calls eutils.ncbi.nlm.nih.gov.

Responsibilities:

1. Identify every request (``tool``, ``email`` and, when set, ``api_key``).
2. Throttle across processes. NCBI limits per IP (3 requests a second keyless, 10 with
   a key) and every organisation on the production host shares one IP, so the clock is
   an flock'd timestamp file per bucket rather than per-process state.
3. Retry 429, 5xx and NCBI's ``API rate limit exceeded`` body.
4. Turn ESearch failures into typed errors. PubMed reports errors as HTTP 200 with an
   ``ERROR`` or ``errorlist`` in the body, and caps a result set at 9,999 IDs; a wrong
   or capped result must fail loudly instead of coming back short.

Reference: NCBI's E-utilities documentation, https://www.ncbi.nlm.nih.gov/books/NBK25499/
"""

import fcntl
import hashlib
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
DEFAULT_TOOL = "gregory-ai"

# PubMed ESearch returns at most 9,999 IDs (retmax=9999 and retmax=10000 both return
# 9,999), so a count at or above this means the result set was capped.
RESULT_CAP = 9999

# Above this many IDs, or for a term this long, requests go as POST.
POST_ID_THRESHOLD = 200
POST_TERM_LENGTH = 300

KEYLESS_INTERVAL = 1 / 3
KEYED_INTERVAL = 1 / 10
REQUEST_TIMEOUT = 30
RETRY_BACKOFF_SECONDS = (1, 2, 4)
# 501 and 505 are not transient, so they are not retried.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class NcbiError(Exception):
	"""A request to NCBI failed, or NCBI returned something unusable."""


class NcbiQueryError(NcbiError):
	"""NCBI rejected the search term, or could not find a field or phrase in it."""


class NcbiResultCapError(NcbiError):
	"""The result set reached PubMed's 9,999 ID cap, so the ID list is incomplete."""


@dataclass
class ESearchResult:
	count: int
	ids: list = field(default_factory=list)
	querytranslation: str = ""
	webenv: Optional[str] = None
	querykey: Optional[str] = None
	warnings: list = field(default_factory=list)


# Module-level so tests can patch the clock. Wall-clock time, not time.monotonic():
# the timestamp lives in a file that outlives the process, and the monotonic clock
# restarts when the host reboots, which would leave a "future" stamp behind.
_clock = time.time
_sleep = time.sleep


def _throttle(bucket, interval):
	"""Block until ``interval`` seconds have passed since the last request in ``bucket``.

	The last-request time lives in an flock'd file so every process on the host shares
	one clock. The file name never contains the API key itself.
	"""
	path = os.path.join(tempfile.gettempdir(), f"{bucket}.throttle")
	# 'r+' (not 'a+'): append mode forces writes to EOF regardless of seek(), which
	# would break the truncate-and-rewrite below.
	if not os.path.exists(path):
		open(path, "a").close()
	with open(path, "r+") as f:
		fcntl.flock(f, fcntl.LOCK_EX)
		try:
			raw = f.read().strip()
			try:
				last = float(raw) if raw else None
			except ValueError:
				last = None
			now = _clock()
			# A stamp in the future (clock stepped back) is stale, never a reason to
			# wait: the sleep happens while holding the lock, so it would stall everyone.
			if last is not None and last <= now:
				wait = interval - (now - last)
				if wait > 0:
					_sleep(wait)
					now = _clock()
			f.seek(0)
			f.truncate()
			f.write(str(now))
			f.flush()
		finally:
			fcntl.flock(f, fcntl.LOCK_UN)


def _is_retryable_status(response):
	return response.status_code in RETRYABLE_STATUSES


def _is_rate_limit_body(data):
	return isinstance(data, dict) and str(data.get("error", "")).lower().startswith(
		"api rate limit"
	)


class NcbiClient:
	def __init__(
		self,
		api_key=None,
		email=None,
		tool=None,
		timeout=REQUEST_TIMEOUT,
		max_retries=len(RETRY_BACKOFF_SECONDS),
	):
		# Stripped: a key pasted into the admin textarea may carry a newline.
		self.api_key = (api_key or "").strip() or None
		self.email = (email or "").strip() or None
		self.tool = (tool or "").strip() or DEFAULT_TOOL
		self.timeout = timeout
		self.max_retries = min(max_retries, len(RETRY_BACKOFF_SECONDS))

	@classmethod
	def for_organization(cls, organization):
		"""Build a client from an organisation's NCBI credentials.

		Works with no credentials at all (keyless). The email falls back to the site's
		``admin_email``, the same address ``get_authors`` gives CrossRef.
		"""
		from subscriptions.management.commands.utils.get_credentials import (
			get_ncbi_credentials,
		)

		api_key, email, tool = get_ncbi_credentials(organization)
		return cls(api_key=api_key, email=email or _site_admin_email(), tool=tool)

	@classmethod
	def for_site(cls, **kwargs):
		"""A keyless client that still identifies itself with the site's admin email,
		for callers with no organisation (the admin's search check, a team-less source)."""
		return cls(email=_site_admin_email(), **kwargs)

	# -- plumbing ---------------------------------------------------------

	def _scrub(self, text):
		if self.api_key:
			return str(text).replace(self.api_key, "***")
		return str(text)

	def _throttle(self):
		if self.api_key:
			digest = hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()[:12]
			_throttle(f"ncbi-{digest}", KEYED_INTERVAL)
		else:
			_throttle("ncbi-keyless", KEYLESS_INTERVAL)

	def _params(self, params):
		merged = {k: v for k, v in params.items() if v is not None}
		merged["tool"] = self.tool
		if self.email:
			merged["email"] = self.email
		if self.api_key:
			merged["api_key"] = self.api_key
		return merged

	def _request(self, endpoint, params, *, post=False, expect_json=True):
		"""Send one throttled request, retrying transient failures.

		Retries 429, 5xx, NCBI's rate-limit body and a non-JSON 200 body.
		"""
		url = EUTILS_BASE + endpoint
		payload = self._params(params)
		# A keyed request always goes as POST, so the key stays out of the query
		# string that urllib3 logs at DEBUG.
		post = post or bool(self.api_key)
		attempts = self.max_retries + 1
		problem = ""
		for attempt in range(attempts):
			self._throttle()
			failure = None
			try:
				if post:
					response = requests.post(url, data=payload, timeout=self.timeout)
				else:
					response = requests.get(url, params=payload, timeout=self.timeout)
				if _is_retryable_status(response):
					problem = f"HTTP {response.status_code}"
				else:
					response.raise_for_status()
					if not expect_json:
						return response.content
					data = response.json()
					if _is_rate_limit_body(data):
						problem = "rate limit exceeded"
					else:
						return data
			except requests.exceptions.JSONDecodeError:
				problem = "response was not JSON"
			except requests.RequestException as exc:
				# The message of a failure can carry the full URL, key included.
				failure = f"{endpoint} request failed: {self._scrub(exc)}"
			if failure:
				raise NcbiError(failure) from None
			if attempt < attempts - 1:
				delay = RETRY_BACKOFF_SECONDS[attempt]
				logger.warning(
					"NCBI %s: %s; retrying in %ss", endpoint, problem, delay
				)
				_sleep(delay)
		raise NcbiError(f"{endpoint}: still failing after retries ({problem})")

	# -- E-utilities ------------------------------------------------------

	def esearch(self, term, db="pubmed", **params):
		"""Run an ESearch and return an :class:`ESearchResult`.

		Raises :class:`NcbiQueryError` when NCBI reports an error or fields or phrases it
		could not find, and :class:`NcbiResultCapError` when the count reaches PubMed's
		cap and the caller asked for IDs (anything but ``rettype="count"``).
		"""
		request = {"db": db, "term": term, "retmode": "json", **params}
		data = self._request(
			"esearch.fcgi", request, post=len(term) > POST_TERM_LENGTH
		)
		result = data.get("esearchresult") if isinstance(data, dict) else None
		if not isinstance(result, dict):
			message = data.get("error") if isinstance(data, dict) else None
			raise NcbiError(f"esearch: {message or 'unexpected response shape'}")
		if result.get("ERROR"):
			raise NcbiQueryError(str(result["ERROR"]))
		errorlist = result.get("errorlist") or {}
		not_found = [
			f"{kind}: {', '.join(map(str, items))}"
			for kind, items in errorlist.items()
			if items
		]
		if not_found:
			raise NcbiQueryError("; ".join(not_found))
		try:
			count = int(result.get("count", 0))
		except (TypeError, ValueError):
			raise NcbiError("esearch: count is not a number") from None
		# Only a count search is exempt: a retmax=0 search with usehistory still
		# leaves a capped set behind for the EFetch that follows.
		wants_ids = params.get("rettype") != "count"
		if wants_ids and count >= RESULT_CAP:
			raise NcbiResultCapError(
				f"{count} results reach PubMed's {RESULT_CAP} ID cap"
			)
		warnings = result.get("warninglist") or {}
		return ESearchResult(
			count=count,
			ids=list(result.get("idlist") or []),
			querytranslation=result.get("querytranslation", ""),
			webenv=result.get("webenv"),
			querykey=result.get("querykey"),
			warnings=[
				f"{kind}: {', '.join(map(str, items))}"
				for kind, items in warnings.items()
				if items
			],
		)

	def efetch_pubmed(self, pmids):
		"""Fetch PubMed records as raw XML bytes. PubMed EFetch has no JSON output."""
		ids = [str(p) for p in pmids]
		return self._request(
			"efetch.fcgi",
			{"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
			post=len(ids) > POST_ID_THRESHOLD,
			expect_json=False,
		)

	def esummary(self, db, ids):
		"""Return ESummary's parsed JSON."""
		ids = [str(i) for i in ids]
		return self._request(
			"esummary.fcgi",
			{"db": db, "id": ",".join(ids), "retmode": "json"},
			post=len(ids) > POST_ID_THRESHOLD,
		)


def _site_admin_email():
	"""The site's admin email, resolved the way ``get_authors`` does. None if unset."""
	try:
		from sitesettings.models import CustomSetting

		setting = CustomSetting.objects.get(
			site__domain=os.environ.get("DOMAIN_NAME")
		)
		return setting.admin_email or None
	except Exception:
		return None
