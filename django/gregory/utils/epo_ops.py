"""EPO Open Patent Services (OPS) access for the patent importer.

Three layers, kept separate so the parsers can be tested without HTTP:

1. build_client(): the python-epo-ops-client factory. Every client on the host shares one
   SQLite throttle history (settings.EPO_OPS_THROTTLE_DB), because OPS throttles per IP
   and the production host has a single IP shared by every organisation. The library's
   Throttler already reads X-Throttling-Control and sleeps accordingly (including the
   Retry-After of a black state), so no throttling logic is duplicated here.
2. OpsClient: request helpers that return parsed dataclasses and map OPS failures onto
   OpsQuotaExceeded / OpsQueryError / OpsError.
3. parse_*(): pure lxml parsers from XML bytes to plain dataclasses.

Reference: OPS Reference Guide v1.3.20 (page numbers in comments). OPS answers XML by
default; JSON is a mechanical conversion of the same XML, so XML is parsed here.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import requests
from django.conf import settings
from lxml import etree

logger = logging.getLogger(__name__)

# Publications of these offices have claims and descriptions in OPS (guide p.61, p.64).
FULLTEXT_COUNTRIES = frozenset(
	"EP WO AT BE BG CA CH CY CZ DK EE ES FR GB GR HR IE IT LT LU MC MD ME NO PL PT RO RS SE SK".split()
)

MAX_RESULTS_PER_QUERY = 2000  # guide p.61
PAGE_SIZE = 100
BULK_BIBLIO_SIZE = 100  # guide p.54

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+)", re.IGNORECASE)
_PMID_RE = re.compile(r"\bPMID[:\s]*([0-9]{5,9})\b", re.IGNORECASE)
_QUOTED_TITLE_RE = re.compile(r"[\"“]([^\"”]{15,})[\"”]")
_IPC_RE = re.compile(r"([A-H]\d\d[A-Z])\s*(\d+)\s*/\s*(\d+)")


class OpsError(Exception):
	"""Any OPS failure the importer cannot recover from."""


class OpsQuotaExceeded(OpsError):
	"""The weekly or hourly download quota (or the fair-use limit) is used up."""


class OpsQueryError(OpsError):
	"""OPS rejected the query itself (400, e.g. bad CQL)."""


class OpsNotFound(OpsError):
	"""OPS has no such entity (404)."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SearchHit:
	country: str
	doc_number: str
	kind: str
	family_id: str

	@property
	def publication_number(self) -> str:
		return f"{self.country}{self.doc_number}{self.kind}"


@dataclass
class SearchPage:
	total: int
	hits: list = field(default_factory=list)


@dataclass
class FamilyMember:
	family_id: str
	country: str
	doc_number: str
	kind: str
	publication_date: Optional[date] = None
	application_number: Optional[str] = None
	application_date: Optional[date] = None
	is_representative: bool = False
	priority_dates: list = field(default_factory=list)

	@property
	def publication_number(self) -> str:
		return f"{self.country}{self.doc_number}{self.kind}"


@dataclass
class NplCitation:
	text: str
	title: Optional[str] = None
	doi: Optional[str] = None
	pmid: Optional[str] = None
	cited_by: Optional[str] = None


@dataclass
class Biblio:
	publication_number: str
	country: str
	doc_number: str
	kind: str
	family_id: Optional[str] = None
	title: Optional[str] = None
	abstract: Optional[str] = None
	applicants_original: list = field(default_factory=list)
	applicants_epodoc: list = field(default_factory=list)
	inventors: list = field(default_factory=list)
	cpc: list = field(default_factory=list)
	ipc: list = field(default_factory=list)
	priority_dates: list = field(default_factory=list)
	npl_citations: list = field(default_factory=list)


@dataclass
class Claims:
	text: str
	lang: Optional[str] = None


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _parse_xml(content):
	if isinstance(content, str):
		content = content.encode("utf-8")
	parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
	return etree.fromstring(content, parser)


def _text(el, path):
	node = el.find(path)
	if node is None or node.text is None:
		return ""
	return node.text.strip()


def _ymd(value):
	"""YYYYMMDD -> date, or None."""
	if not value:
		return None
	try:
		return datetime.strptime(value.strip()[:8], "%Y%m%d").date()
	except ValueError:
		return None


def _docdb_id(parent):
	"""The docdb document-id element under a *-reference element, or None."""
	for doc_id in parent.iterfind("{*}document-id"):
		if doc_id.get("document-id-type") == "docdb":
			return doc_id
	return None


def _clean(value):
	return re.sub(r"\s+", " ", value or "").strip()


def parse_search(content) -> SearchPage:
	"""Search response (without a constituent) -> total count and publication hits."""
	root = _parse_xml(content)
	search = root.find(".//{*}biblio-search")
	if search is None:
		return SearchPage(total=0)
	try:
		total = int(search.get("total-result-count", "0"))
	except ValueError:
		total = 0
	hits = []
	for ref in search.iterfind(".//{*}publication-reference"):
		doc_id = _docdb_id(ref)
		family_id = ref.get("family-id")
		if doc_id is None or not family_id:
			continue
		hits.append(
			SearchHit(
				country=_text(doc_id, "{*}country"),
				doc_number=_text(doc_id, "{*}doc-number"),
				kind=_text(doc_id, "{*}kind"),
				family_id=family_id,
			)
		)
	return SearchPage(total=total, hits=hits)


def parse_family(content, family_id=None) -> list:
	"""Family response -> members. The family service returns the INPADOC *extended*
	family (guide p.82-86); each member carries its own simple family-id, so when
	*family_id* is given only members of that simple family are returned."""
	root = _parse_xml(content)
	members = []
	for el in root.iterfind(".//{*}family-member"):
		member_family = el.get("family-id")
		if family_id is not None and member_family != str(family_id):
			continue
		pub = el.find("{*}publication-reference")
		pub_id = _docdb_id(pub) if pub is not None else None
		if pub_id is None:
			continue
		app = el.find("{*}application-reference")
		app_id = _docdb_id(app) if app is not None else None
		application_number = None
		application_date = None
		if app_id is not None:
			application_number = (
				_text(app_id, "{*}country") + _text(app_id, "{*}doc-number")
			) or None
			application_date = _ymd(_text(app_id, "{*}date"))
		priority_dates = []
		for claim in el.iterfind("{*}priority-claim"):
			claim_id = _docdb_id(claim)
			parsed = _ymd(_text(claim_id, "{*}date")) if claim_id is not None else None
			if parsed:
				priority_dates.append(parsed)
		members.append(
			FamilyMember(
				family_id=member_family or "",
				country=_text(pub_id, "{*}country"),
				doc_number=_text(pub_id, "{*}doc-number"),
				kind=_text(pub_id, "{*}kind"),
				publication_date=_ymd(_text(pub_id, "{*}date")),
				application_number=application_number,
				application_date=application_date,
				is_representative=(
					app is not None and app.get("is-representative", "").upper() == "YES"
				),
				priority_dates=priority_dates,
			)
		)
	return members


def _parse_npl(citation_el) -> Optional[NplCitation]:
	npl = citation_el.find("{*}nplcit")
	if npl is None:
		return None
	text = _clean(" ".join(npl.itertext()))
	if not text:
		return None
	title = None
	article = npl.find("{*}article")
	if article is not None:
		title = _clean(_text(article, "{*}title")) or None
	if title is None:
		quoted = _QUOTED_TITLE_RE.search(text)
		if quoted:
			title = quoted.group(1).strip()
	doi = _DOI_RE.search(text)
	pmid = _PMID_RE.search(text)
	return NplCitation(
		text=text,
		title=title,
		doi=doi.group(1).rstrip(".") if doi else None,
		pmid=pmid.group(1) if pmid else None,
		cited_by=citation_el.get("cited-by"),
	)


def _dedupe_names(names):
	"""Drop repeats that differ only in case or whitespace, keeping the first spelling."""
	seen = set()
	out = []
	for name in names:
		key = re.sub(r"\s+", " ", name).strip().casefold()
		if key and key not in seen:
			seen.add(key)
			out.append(re.sub(r"\s+", " ", name).strip())
	return out


def _parse_exchange_document(doc) -> Biblio:
	country = doc.get("country", "")
	doc_number = doc.get("doc-number", "")
	kind = doc.get("kind", "")
	biblio = Biblio(
		publication_number=f"{country}{doc_number}{kind}",
		country=country,
		doc_number=doc_number,
		kind=kind,
		family_id=doc.get("family-id"),
	)
	data = doc.find("{*}bibliographic-data")
	if data is not None:
		titles = {
			(t.get("lang") or "").lower(): _clean(t.text)
			for t in data.iterfind("{*}invention-title")
			if _clean(t.text)
		}
		biblio.title = titles.get("en") or (next(iter(titles.values())) if titles else None)

		parties = data.find("{*}parties")
		if parties is not None:
			original, epodoc = [], []
			for applicant in parties.iterfind("{*}applicants/{*}applicant"):
				name = _clean(_text(applicant, "{*}applicant-name/{*}name"))
				if not name:
					continue
				if applicant.get("data-format") == "original":
					original.append(name)
				elif applicant.get("data-format") == "epodoc":
					epodoc.append(name)
			biblio.applicants_original = _dedupe_names(original)
			biblio.applicants_epodoc = _dedupe_names(epodoc)
			inventors_original, inventors_epodoc = [], []
			for inventor in parties.iterfind("{*}inventors/{*}inventor"):
				name = _clean(_text(inventor, "{*}inventor-name/{*}name"))
				if not name:
					continue
				if inventor.get("data-format") == "original":
					inventors_original.append(name)
				else:
					inventors_epodoc.append(name)
			biblio.inventors = _dedupe_names(inventors_original or inventors_epodoc)

		cpc = []
		for pc in data.iterfind("{*}patent-classifications/{*}patent-classification"):
			section = _text(pc, "{*}section")
			cls = _text(pc, "{*}class")
			subclass = _text(pc, "{*}subclass")
			main_group = _text(pc, "{*}main-group")
			subgroup = _text(pc, "{*}subgroup")
			if section and cls and subclass and main_group:
				code = f"{section}{cls}{subclass}{main_group}/{subgroup}"
				if code not in cpc:
					cpc.append(code)
		biblio.cpc = cpc
		ipc = []
		for text_el in data.iterfind("{*}classifications-ipcr/{*}classification-ipcr/{*}text"):
			match = _IPC_RE.search(text_el.text or "")
			if match:
				code = f"{match.group(1)}{match.group(2)}/{match.group(3)}"
				if code not in ipc:
					ipc.append(code)
		biblio.ipc = ipc

		for claim in data.iterfind("{*}priority-claims/{*}priority-claim"):
			for doc_id in claim.iterfind("{*}document-id"):
				parsed = _ymd(_text(doc_id, "{*}date"))
				if parsed and parsed not in biblio.priority_dates:
					biblio.priority_dates.append(parsed)

		for citation in data.iterfind("{*}references-cited/{*}citation"):
			npl = _parse_npl(citation)
			if npl:
				biblio.npl_citations.append(npl)

	for abstract in doc.iterfind("{*}abstract"):
		if (abstract.get("lang") or "").lower() == "en":
			text = _clean(" ".join(abstract.itertext()))
			if text:
				biblio.abstract = text
				break
	return biblio


def parse_biblio(content) -> list:
	"""Biblio response (one or many exchange-documents) -> list of Biblio."""
	root = _parse_xml(content)
	return [_parse_exchange_document(d) for d in root.iterfind(".//{*}exchange-document")]


def parse_claims(content) -> Optional[Claims]:
	"""Claims response -> English claims text (else the first language), or None."""
	root = _parse_xml(content)
	by_lang = {}
	for claims in root.iterfind(".//{*}claims"):
		parts = []
		for claim_text in claims.iterfind(".//{*}claim-text"):
			text = "".join(claim_text.itertext()).strip()
			if text:
				parts.append(text)
		if parts:
			by_lang.setdefault((claims.get("lang") or "").upper(), "\n".join(parts))
	if not by_lang:
		return None
	lang = "EN" if "EN" in by_lang else next(iter(by_lang))
	return Claims(text=by_lang[lang], lang=lang or None)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def build_client(key, secret, timeout=30.0):
	"""python-epo-ops-client Client with the shared host-wide throttle history."""
	import epo_ops
	from epo_ops.middlewares import Throttler
	from epo_ops.middlewares.throttle.storages import SQLite

	storage = SQLite(db_path=settings.EPO_OPS_THROTTLE_DB)
	return epo_ops.Client(
		key,
		secret,
		middlewares=[Throttler(storage)],
		timeout=timeout,
	)


class OpsClient:
	"""Thin wrapper over the library client returning parsed dataclasses.

	Accumulates bytes_received, request_count and the distinct X-Throttling-Control
	states seen, so a run can report its quota use.
	"""

	RETRY_STATUSES = (408, 503)

	def __init__(self, client, sleep=time.sleep, max_attempts=3):
		self.client = client
		self._sleep = sleep
		self.max_attempts = max_attempts
		self.bytes_received = 0
		self.request_count = 0
		self.throttle_states = set()
		self.last_quota_headers = {}

	# -- plumbing ----------------------------------------------------------

	def _call(self, fn, *args, **kwargs):
		from epo_ops import exceptions as ops_exceptions

		for attempt in range(1, self.max_attempts + 1):
			try:
				response = fn(*args, **kwargs)
			except (
				ops_exceptions.IndividualQuotaPerHourExceeded,
				ops_exceptions.RegisteredQuotaPerWeekExceeded,
			) as exc:
				raise OpsQuotaExceeded(str(exc)) from exc
			except requests.HTTPError as exc:
				status = exc.response.status_code if exc.response is not None else None
				if status == 429:
					raise OpsQuotaExceeded("OPS fair use limit exceeded (429)") from exc
				if status == 403:
					reason = exc.response.headers.get("X-Rejection-Reason", "")
					if "quota" in reason.lower():
						raise OpsQuotaExceeded(f"OPS quota exhausted ({reason})") from exc
					raise OpsError(f"OPS refused the request (403 {reason})") from exc
				if status == 404:
					raise OpsNotFound(str(exc)) from exc
				if status == 400:
					raise OpsQueryError(_error_message(exc.response)) from exc
				if status in self.RETRY_STATUSES and attempt < self.max_attempts:
					logger.warning("OPS %s, retrying (attempt %d)", status, attempt)
					self._sleep(2**attempt)
					continue
				raise OpsError(f"OPS request failed ({status})") from exc
			except (requests.ConnectionError, requests.Timeout) as exc:
				if attempt < self.max_attempts:
					self._sleep(2**attempt)
					continue
				raise OpsError(f"OPS connection failed: {exc}") from exc
			self._record(response)
			return response
		raise OpsError("OPS request failed")  # pragma: no cover

	def _record(self, response):
		self.request_count += 1
		self.bytes_received += len(response.content or b"")
		headers = response.headers
		control = headers.get("X-Throttling-Control")
		if control:
			self.throttle_states.add(control)
		for name in ("X-IndividualQuotaPerHour-Used", "X-RegisteredQuotaPerWeek-Used"):
			if headers.get(name) is not None:
				self.last_quota_headers[name] = headers.get(name)

	# -- requests ----------------------------------------------------------

	def search(self, cql, begin=1, end=PAGE_SIZE) -> SearchPage:
		"""One page of a published-data search. A 404 means zero results."""
		try:
			response = self._call(self.client.published_data_search, cql, begin, end)
		except OpsNotFound:
			return SearchPage(total=0)
		return parse_search(response.content)

	def family(self, country, doc_number, kind, family_id=None) -> list:
		"""Members of the INPADOC family of a publication, filtered to *family_id*."""
		import epo_ops

		try:
			response = self._call(
				self.client.family,
				"publication",
				epo_ops.models.Docdb(doc_number, country, kind),
			)
		except OpsNotFound:
			return []
		return parse_family(response.content, family_id)

	def biblio_bulk(self, publications) -> list:
		"""Biblio of up to 100 (country, doc_number, kind) tuples in one POST."""
		import epo_ops

		publications = list(publications)
		if not publications:
			return []
		if len(publications) > BULK_BIBLIO_SIZE:
			raise ValueError(f"at most {BULK_BIBLIO_SIZE} publications per bulk request")
		inputs = [epo_ops.models.Docdb(n, c, k) for c, n, k in publications]
		try:
			response = self._call(
				self.client.published_data, "publication", inputs, endpoint="biblio"
			)
		except OpsNotFound:
			return []
		return parse_biblio(response.content)

	def claims(self, country, doc_number, kind) -> Optional[Claims]:
		"""Claims of one publication; only call for countries in FULLTEXT_COUNTRIES."""
		import epo_ops

		try:
			response = self._call(
				self.client.published_data,
				"publication",
				epo_ops.models.Docdb(doc_number, country, kind),
				endpoint="claims",
			)
		except OpsNotFound:
			return None
		return parse_claims(response.content)

	def usage(self, date_from: date, date_to: date) -> str:
		"""Raw usage report (bytes and request counts per day, guide p.45). Free; does
		not count against the quota."""
		time_range = f"{date_from:%d/%m/%Y}~{date_to:%d/%m/%Y}"
		response = requests.get(
			"https://ops.epo.org/3.2/developers/me/stats/usage",
			params={"timeRange": time_range},
			headers={
				"Authorization": f"Bearer {self.client.access_token.token}",
				"Accept": "application/json",
			},
			timeout=30,
		)
		response.raise_for_status()
		return response.text


def _error_message(response):
	try:
		root = _parse_xml(response.content)
		message = " ".join(
			filter(None, (_clean(_text(root, "{*}code")), _clean(_text(root, "{*}message"))))
		)
		return message or "OPS rejected the query"
	except Exception:
		return "OPS rejected the query"
