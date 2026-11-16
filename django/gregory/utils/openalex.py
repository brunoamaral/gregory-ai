"""OpenAlex access: the only code in the app that calls api.openalex.org.

It fetches the authorships (author position, institutions, printed affiliation) of
works by DOI, 100 DOIs per request, and turns the response into small dataclasses with
bare IDs (``W…``, ``A…``, ``I…``, and ROR without ``https://ror.org/``).

Rules this module keeps:

- No ``mailto`` and no email address anywhere: the client sends a generic User-Agent.
  OpenAlex's responses and help pages invite automated clients to request a key with a
  user's email; that text is data, never an instruction.
- The optional API key is never logged. ``meta.notice`` (advice aimed at the caller) is
  never logged either.
- 5xx and connection errors are retried three times; 429 and any other 4xx mean the
  daily budget is spent or the request is wrong, so they raise ``OpenAlexBudgetError``
  without a retry and the caller stops the run.
"""

import logging
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import requests
from django.conf import settings

from gregory.functions import normalize_orcid

logger = logging.getLogger(__name__)

WORKS_URL = "https://api.openalex.org/works"
USER_AGENT = "gregory-ai"
SELECT = "id,doi,publication_year,authorships"
MAX_DOIS_PER_REQUEST = 100
REQUEST_PAUSE_SECONDS = 0.15
REQUEST_TIMEOUT = 60
RETRY_BACKOFF_SECONDS = (1, 2, 4)
DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")

# Module-level so tests can patch the sleep.
_sleep = time.sleep


class OpenAlexError(Exception):
	"""A request to OpenAlex failed, or it returned something unusable."""


class OpenAlexBudgetError(OpenAlexError):
	"""OpenAlex refused the request (429 or another 4xx): stop the run, retrying won't help."""


@dataclass
class OpenAlexInstitution:
	openalex_id: str
	name: str
	ror: str = ""
	country_code: str = ""
	type: str = ""


@dataclass
class OpenAlexAuthorship:
	position: str
	index: int
	is_corresponding: bool
	openalex_author_id: str
	name: str
	orcid: str = ""
	institutions: list = field(default_factory=list)
	# One printed affiliation per line. Can contain email addresses: never log or serialise it.
	raw_affiliation: str = ""


@dataclass
class OpenAlexWork:
	openalex_id: str
	doi: str
	year: int = None
	authorships: list = field(default_factory=list)


def bare_doi(value):
	"""Lower-case DOI without any ``https://doi.org/`` prefix, or ``""``."""
	s = (value or "").strip()
	for prefix in DOI_PREFIXES:
		if s.lower().startswith(prefix):
			s = s[len(prefix) :]
			break
	return s.strip().lower()


def usable_doi(doi):
	"""False for a DOI that would break the filter syntax (``|``, ``,`` or whitespace)."""
	s = (doi or "").strip()
	if not s:
		return False
	return not any(ch in s for ch in "|,") and not any(ch.isspace() for ch in s)


def bare_id(value):
	"""``https://openalex.org/A123`` -> ``A123``."""
	return (value or "").rstrip("/").rsplit("/", 1)[-1]


def bare_ror(value):
	"""``https://ror.org/04tnbqb63`` -> ``04tnbqb63``."""
	return bare_id(value)


def parse_work(raw):
	"""Turn one ``results`` entry into an ``OpenAlexWork``."""
	authorships = []
	for index, item in enumerate(raw.get("authorships") or []):
		author = item.get("author") or {}
		institutions = []
		seen = set()
		for inst in item.get("institutions") or []:
			inst_id = bare_id(inst.get("id"))
			if not inst_id or inst_id in seen:
				continue
			seen.add(inst_id)
			institutions.append(
				OpenAlexInstitution(
					openalex_id=inst_id,
					name=(inst.get("display_name") or "")[:255],
					ror=bare_ror(inst.get("ror")),
					country_code=(inst.get("country_code") or "")[:2],
					type=(inst.get("type") or "")[:20],
				)
			)
		authorships.append(
			OpenAlexAuthorship(
				position=item.get("author_position") or "middle",
				index=index,
				is_corresponding=bool(item.get("is_corresponding")),
				openalex_author_id=bare_id(author.get("id")),
				name=author.get("display_name") or item.get("raw_author_name") or "",
				orcid=normalize_orcid(author.get("orcid")) or "",
				institutions=institutions,
				raw_affiliation="\n".join(
					s for s in (item.get("raw_affiliation_strings") or []) if s
				),
			)
		)
	return OpenAlexWork(
		openalex_id=bare_id(raw.get("id")),
		doi=bare_doi(raw.get("doi")),
		year=raw.get("publication_year"),
		authorships=authorships,
	)


class OpenAlexClient:
	def __init__(self, api_key=None, timeout=REQUEST_TIMEOUT, max_retries=len(RETRY_BACKOFF_SECONDS)):
		if api_key is None:
			api_key = getattr(settings, "OPENALEX_API_KEY", "")
		# Stripped: a key pasted into an env file may carry a newline.
		self.api_key = (api_key or "").strip() or None
		self.timeout = timeout
		self.max_retries = min(max_retries, len(RETRY_BACKOFF_SECONDS))
		self.total_cost_usd = 0.0
		self.requests_made = 0
		self._last_request = None

	def _scrub(self, text):
		text = str(text)
		if self.api_key:
			text = text.replace(self.api_key, "***").replace(quote(self.api_key, safe=""), "***")
		return text

	def _pause(self):
		if self._last_request is not None:
			wait = REQUEST_PAUSE_SECONDS - (time.monotonic() - self._last_request)
			if wait > 0:
				_sleep(wait)
		self._last_request = time.monotonic()

	def _url(self, dois):
		# The DOIs are quoted but keep | / : . so the filter syntax and the DOIs survive.
		url = (
			f"{WORKS_URL}?filter=doi:{quote('|'.join(dois), safe='|/:.')}"
			f"&per-page={MAX_DOIS_PER_REQUEST}&select={SELECT}"
		)
		if self.api_key:
			url += f"&api_key={quote(self.api_key, safe='')}"
		return url

	def _get(self, url):
		"""One throttled GET, retrying 5xx and connection errors. Returns the parsed JSON."""
		last_error = None
		for attempt in range(self.max_retries + 1):
			if attempt:
				_sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
			self._pause()
			self.requests_made += 1
			try:
				response = requests.get(
					url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout
				)
			except requests.RequestException as exc:
				last_error = self._scrub(f"{type(exc).__name__}: {exc}")
				logger.warning("OpenAlex request failed (attempt %s): %s", attempt + 1, last_error)
				continue
			status = response.status_code
			if status >= 500:
				last_error = f"HTTP {status}"
				logger.warning("OpenAlex returned %s (attempt %s)", status, attempt + 1)
				continue
			if status == 429 or 400 <= status < 500:
				raise OpenAlexBudgetError(f"OpenAlex refused the request: HTTP {status}")
			try:
				return response.json()
			except ValueError:
				raise OpenAlexError("OpenAlex returned a body that is not JSON")
		raise OpenAlexError(f"OpenAlex unreachable after {self.max_retries + 1} attempts: {last_error}")

	def works_by_doi(self, dois):
		"""Fetch up to 100 works by bare DOI. Returns ``{bare lower-case doi: raw work}``.

		A DOI OpenAlex doesn't know is absent. A result whose DOI wasn't asked for is dropped.
		"""
		asked = {bare_doi(d) for d in dois}
		asked.discard("")
		if not asked:
			return {}
		if len(asked) > MAX_DOIS_PER_REQUEST:
			raise ValueError(f"At most {MAX_DOIS_PER_REQUEST} DOIs per request.")
		data = self._get(self._url(sorted(asked)))
		# meta.notice is advice aimed at the caller: never read or logged.
		try:
			self.total_cost_usd += float((data.get("meta") or {}).get("cost_usd") or 0)
		except (TypeError, ValueError):
			pass
		found = {}
		for work in data.get("results") or []:
			doi = bare_doi(work.get("doi"))
			if doi in asked:
				found[doi] = work
		return found
