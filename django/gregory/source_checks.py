"""The checks a source's settings must pass before it is saved.

Shared by the admin form (SourceAdminForm) and the editor API (api/editor_views.py),
so a source added from either place meets the same rules and gets the same messages.

The functions take a source type key (gregory/source_types.py) and a dict of the
settings, and return plain data: no form, no request. `check_settings` is local and
free; `check_pubmed_search` makes one call to PubMed.

The editor API adds what a person adding a source from a chat needs and the admin does
not: `check_rss_address` (the server fetches an address someone typed in, so it only
goes to public hosts), the previews that say what a source would bring in
(`rss_preview`, `pubmed_preview`, `ctgov_preview`) and `find_duplicate`.
"""

import ipaddress
import logging
import socket
import time
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import Optional
from urllib.parse import urljoin, urlsplit

import feedparser
import requests
from django.utils import timezone

from . import source_types
from .classes import ClinicalTrialsGovAPI
from .models import Sources
from .utils import ctgov_search
from .utils.ncbi import ESearchResult, NcbiClient, NcbiError, NcbiQueryError
from .utils.pubmed_search import build_pubmed_term, unbalanced_query

logger = logging.getLogger(__name__)


def _filled(value):
	return bool(value.strip()) if isinstance(value, str) else bool(value)


def check_settings(type_key, values):
	"""Check the settings a source of this type needs, without any network call.

	`values` maps setting names (`link`, `ctgov_search_condition`, `pubmed_query`, …) to
	what the caller gave; a missing name counts as empty. Returns `{field: [messages]}`,
	empty when the settings are fine. An unknown type key returns no errors: the caller's
	own type validation already covers it.
	"""
	errors = {}

	def add(field, message):
		errors.setdefault(field, []).append(message)

	source_type = source_types.BY_KEY.get(type_key)
	if source_type is None:
		return errors

	if "link" in source_type.required and not _filled(values.get("link")):
		add("link", "Enter the feed's URL.")
	if source_type.key == "ctgov" and not any(
		_filled(values.get(f)) for f in source_type.fields
	):
		add(
			"ctgov_search_condition",
			"Fill in at least one of condition, intervention or other terms. With all "
			"three empty, ClinicalTrials.gov would return every trial in its registry.",
		)
	if source_type.key == "ctis":
		_check_ctis_criteria(values.get("ctis_search_criteria"), add)
	if source_type.key == "epo_ops":
		query = (values.get("ops_cql_query") or "").strip()
		if not query:
			add(
				"ops_cql_query",
				'Enter the EPO search, e.g. ta="multiple sclerosis" and cpc=/low A61P25/00.',
			)
		elif source_types.CQL_DATE_CLAUSE.search(query):
			add(
				"ops_cql_query",
				"Remove the publication-date clause (pd …). Gregory adds the date "
				"window itself on every run.",
			)
	if source_type.key == "pubmed":
		_check_pubmed_query(values.get("pubmed_query"), add)
	return errors


def _check_pubmed_query(query, add):
	query = (query or "").strip()
	if not query:
		add(
			"pubmed_query",
			'Enter the PubMed search, e.g. "multiple sclerosis"[MeSH Terms] AND remyelination.',
		)
		return
	if source_types.has_date_clause(query):
		add(
			"pubmed_query",
			"Remove the date clause ([dp], [crdt], last N days …). Gregory adds the "
			"creation-date window itself on every run.",
		)
		return
	problem = unbalanced_query(query)
	if problem:
		add("pubmed_query", f"Check the brackets and quotes. {problem}")


def _check_ctis_criteria(criteria, add):
	if not criteria:
		add(
			"ctis_search_criteria",
			'Enter the search criteria, e.g. {"medicalCondition": "Multiple Sclerosis"}. '
			"Without them this source is never fetched.",
		)
	elif not isinstance(criteria, dict):
		add(
			"ctis_search_criteria",
			'Enter a JSON object such as {"medicalCondition": "Multiple Sclerosis"}, '
			"not a list or a single value.",
		)
	else:
		unknown = sorted(set(criteria) - set(source_types.CTIS_SEARCH_KEYS))
		if unknown:
			add(
				"ctis_search_criteria",
				f"Unknown key(s): {', '.join(unknown)}. CTIS accepts "
				f"{', '.join(source_types.CTIS_SEARCH_KEYS)} (case-sensitive).",
			)


@dataclass
class PubmedSearchCheck:
	"""What asking PubMed about a search found. At most one of the three is set."""

	# The 30-day term that was searched.
	term: str = ""
	result: Optional[ESearchResult] = None
	# PubMed rejected the search; the admin shows it on the search field.
	rejection: Optional[str] = None
	# PubMed could not be reached, so the search was not checked.
	unreachable: bool = False


def check_pubmed_search(query, excluded_names, client=None):
	"""Ask PubMed what the importer's first run would find, so a typo or a search that
	is far too broad shows up now, not after a month of runs. Short timeout and one
	retry by default: callers run this inside a web request."""
	end = timezone.now().date()
	term = build_pubmed_term(query.strip(), excluded_names, end - timedelta(days=30), end)
	client = client or NcbiClient.for_site(timeout=10, max_retries=1)
	try:
		result = client.esearch(term, rettype="count")
	except NcbiQueryError as exc:
		return PubmedSearchCheck(term=term, rejection=f"PubMed rejected this search: {exc}")
	except NcbiError:
		return PubmedSearchCheck(term=term, unreachable=True)
	return PubmedSearchCheck(term=term, result=result)


# --- The editor API: addresses, previews and duplicates -----------------------

SAMPLE_SIZE = 5
FEED_TIMEOUT_SECONDS = 10
FEED_MAX_BYTES = 5 * 1024 * 1024
FEED_MAX_REDIRECTS = 3
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
FEED_ACCEPT = (
	"application/rss+xml, application/atom+xml, application/rdf+xml, "
	"application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"
)
FEED_USER_AGENT = "GregoryAI source check (+https://gregory-ai.com)"


class SourceRefused(Exception):
	"""A check the source fails. `field` names the setting to blame, or None."""

	def __init__(self, field, message):
		super().__init__(message)
		self.field = field
		self.message = message


class ServiceUnreachable(Exception):
	"""PubMed or ClinicalTrials.gov could not be reached, so a check could not run."""

	def __init__(self, service):
		super().__init__(f"{service} could not be reached.")
		self.service = service


@dataclass
class FeedCheck:
	"""A feed that was fetched and parsed. `entries` are feedparser's, `sample` the
	first few as {title, date, link}."""

	entries: list
	sample: list
	final_url: str


def _refuse_link(message):
	raise SourceRefused("link", message)


def _check_public_host(url):
	"""Refuse an address that is not http(s) or whose host resolves to anything but
	public addresses. Every address the name resolves to must be public."""
	try:
		parts = urlsplit(url)
		port = parts.port
	except ValueError:
		_refuse_link("This is not a valid web address.")
	if parts.scheme not in ("http", "https"):
		_refuse_link("The feed address must start with http:// or https://.")
	host = parts.hostname
	if not host:
		_refuse_link("This is not a valid web address.")
	if parts.username or parts.password:
		_refuse_link("The feed address cannot contain a user name or password.")
	try:
		infos = socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
	except (socket.gaierror, UnicodeError):
		_refuse_link(f"The host {host} could not be found.")
	if not infos:
		_refuse_link(f"The host {host} could not be found.")
	for info in infos:
		address = ipaddress.ip_address(info[4][0].split("%")[0])
		if address.version == 6 and address.ipv4_mapped:
			address = address.ipv4_mapped
		if not address.is_global:
			_refuse_link(
				f"The host {host} points to a private or internal address. Only public "
				"feeds can be added here."
			)


def _read_body(response):
	"""The response body, refusing one over FEED_MAX_BYTES or slower than the timeout."""
	declared = response.headers.get("Content-Length", "")
	if declared.isdigit() and int(declared) > FEED_MAX_BYTES:
		_refuse_link("The feed is larger than 5 MB.")
	deadline = time.monotonic() + FEED_TIMEOUT_SECONDS
	body = bytearray()
	for chunk in response.iter_content(65536):
		body.extend(chunk)
		if len(body) > FEED_MAX_BYTES:
			_refuse_link("The feed is larger than 5 MB.")
		if time.monotonic() > deadline:
			_refuse_link("The feed took too long to download.")
	return bytes(body)


def _fetch_feed_bytes(url):
	"""GET `url`, following up to FEED_MAX_REDIRECTS redirects by hand so each hop is
	checked like the first address. Returns `(final_url, body)`."""
	current = url
	for hop in range(FEED_MAX_REDIRECTS + 1):
		_check_public_host(current)
		try:
			response = requests.get(
				current,
				allow_redirects=False,
				stream=True,
				timeout=FEED_TIMEOUT_SECONDS,
				headers={"User-Agent": FEED_USER_AGENT, "Accept": FEED_ACCEPT},
			)
		except requests.exceptions.SSLError:
			_refuse_link(
				"The feed's security certificate is not valid. A feed like that has to be "
				"added in the admin."
			)
		except requests.exceptions.Timeout:
			_refuse_link("The feed did not answer within 10 seconds.")
		except requests.exceptions.RequestException:
			_refuse_link("The feed could not be reached.")
		try:
			if response.status_code in REDIRECT_STATUSES:
				location = response.headers.get("Location")
				if not location:
					_refuse_link("The address redirects without saying where.")
				if hop == FEED_MAX_REDIRECTS:
					_refuse_link(f"The address redirects more than {FEED_MAX_REDIRECTS} times.")
				current = urljoin(current, location)
				continue
			if response.status_code != 200:
				_refuse_link(f"The address answered with HTTP {response.status_code}, not a feed.")
			try:
				return current, _read_body(response)
			except requests.exceptions.RequestException:
				_refuse_link("The feed could not be downloaded.")
		finally:
			response.close()


def _entry_date(entry):
	parsed = entry.get("published_parsed") or entry.get("updated_parsed")
	if not parsed:
		return None
	try:
		return f"{parsed.tm_year:04d}-{parsed.tm_mon:02d}-{parsed.tm_mday:02d}"
	except AttributeError:
		return None


def _entry_sample(entry):
	return {
		"title": " ".join((entry.get("title") or "").split()),
		"date": _entry_date(entry),
		"link": entry.get("link") or None,
	}


def check_rss_address(url):
	"""Fetch a feed address and parse it. Returns a FeedCheck, or raises SourceRefused.

	Only public hosts are fetched: the name must resolve to public addresses only, and
	every redirect is checked the same way. The fetch has a 10-second timeout, a 5 MB
	limit and follows at most 3 redirects. feedparser gets the bytes, never the address,
	so it cannot fetch anything itself. A body that is not a feed, or has no entries, is
	refused.
	"""
	url = (url or "").strip()
	final_url, body = _fetch_feed_bytes(url)
	entries = list(feedparser.parse(body).get("entries") or [])
	if not entries:
		_refuse_link("This address did not return a feed with any entries.")
	return FeedCheck(
		entries=entries,
		sample=[_entry_sample(e) for e in entries[:SAMPLE_SIZE]],
		final_url=final_url,
	)


@dataclass
class Preview:
	"""What a source would bring in, for the person deciding whether to add it.

	`counts` are `{key, value, label}`: the label says what is counted.
	"""

	counts: list = field(default_factory=list)
	samples: list = field(default_factory=list)
	# What the first pipeline run brings in.
	first_run: str = ""
	# PubMed's reading of the search, and its warnings.
	querytranslation: Optional[str] = None
	warnings: list = field(default_factory=list)
	# Where an RSS feed ended up after redirects.
	final_url: Optional[str] = None


def _count(key, value, label):
	return {"key": key, "value": value, "label": label}


def rss_preview(source_for, link, keyword_filter):
	"""Fetch the feed and count what a first run would keep. `source_for` is the
	source's table ("science paper" or "trials"); only papers have a keyword filter.

	The keyword count looks at titles and summaries only. The importer also checks a
	paper's CrossRef abstract when a sparse feed has no summary, so the real number can
	be a little higher.
	"""
	feed = check_rss_address(link)
	counts = [_count("entries_in_feed", len(feed.entries), "Entries in the feed now")]
	samples = feed.sample
	keyword_filter = (keyword_filter or "").strip()
	if source_for == "science paper" and keyword_filter:
		from gregory.management.commands.feedreader_articles import Command as RssCommand

		# The importer picks its processor from the stored address, which is the one given,
		# not where the feed redirects to.
		processor = RssCommand().get_feed_processor(link)
		stand_in = SimpleNamespace(keyword_filter=keyword_filter)
		passing = []
		for entry in feed.entries:
			try:
				if processor.should_include_article(entry, stand_in):
					passing.append(entry)
			except Exception:
				# The importer logs an entry it cannot read and moves on; it brings in nothing.
				logger.debug("Keyword filter could not read a feed entry", exc_info=True)
				continue
		counts.append(
			_count(
				"entries_passing_keyword_filter",
				len(passing),
				"Of those, entries that pass the keyword filter (title and summary)",
			)
		)
		samples = [_entry_sample(e) for e in passing[:SAMPLE_SIZE]]
	return Preview(
		counts=counts,
		samples=samples,
		first_run="The first run brings in the feed's current entries that pass the filter.",
		final_url=feed.final_url,
	)


def pubmed_preview(query, excluded_names):
	"""What a PubMed search finds over the last 30 days, with up to 5 sample papers.

	Raises SourceRefused when PubMed rejects the search and ServiceUnreachable when it
	cannot be reached. There is no volume limit: a big count is reported, not refused.
	The samples are best effort and left out when PubMed will not list them.
	"""
	client = NcbiClient.for_site(timeout=10, max_retries=1)
	check = check_pubmed_search(query, excluded_names, client=client)
	if check.rejection:
		raise SourceRefused("pubmed_query", check.rejection)
	if check.unreachable:
		raise ServiceUnreachable("PubMed")
	samples = []
	try:
		ids = client.esearch(check.term, retmax=SAMPLE_SIZE, sort="pub_date").ids
		summaries = client.esummary("pubmed", ids).get("result", {}) if ids else {}
		for pmid in ids:
			record = summaries.get(str(pmid)) or {}
			samples.append(
				{
					"title": " ".join((record.get("title") or "").split()),
					"date": (record.get("sortpubdate") or "")[:10].replace("/", "-") or None,
					"link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
				}
			)
	except (NcbiError, AttributeError, TypeError):
		samples = []
	return Preview(
		counts=[_count("papers_last_30_days", check.result.count, "Papers created in the last 30 days")],
		samples=samples,
		first_run=(
			"The first run brings in only the last 2 days of new papers; the count above "
			"shows the monthly volume to expect."
		),
		querytranslation=check.result.querytranslation,
		warnings=list(check.result.warnings),
	)


def ctgov_preview(values):
	"""What a ClinicalTrials.gov search matches, with up to 5 of the most recently
	updated trials. `values` holds the three search fields.

	Raises SourceRefused when ClinicalTrials.gov rejects the search and ServiceUnreachable
	when it cannot be reached. There is no volume limit.
	"""
	api = ClinicalTrialsGovAPI()
	window_start = timezone.now() - timedelta(days=30)
	try:
		everything = api.search(
			**{
				**ctgov_search.build_search_params(values),
				"page_size": SAMPLE_SIZE,
				"fields": ["NCTId", "BriefTitle", "LastUpdatePostDate"],
			}
		)
		recent = api.search(
			**{
				**ctgov_search.build_search_params(values, window_start),
				"page_size": 1,
				"fields": ["NCTId"],
			}
		)
	except requests.exceptions.HTTPError as exc:
		if exc.response is not None and exc.response.status_code == 400:
			raise SourceRefused(
				"ctgov_search_condition", "ClinicalTrials.gov rejected this search."
			)
		raise ServiceUnreachable("ClinicalTrials.gov")
	except (requests.exceptions.RequestException, ValueError):
		raise ServiceUnreachable("ClinicalTrials.gov")

	samples = []
	for study in everything.get("studies") or []:
		protocol = study.get("protocolSection") or {}
		ident = protocol.get("identificationModule") or {}
		nct = ident.get("nctId")
		samples.append(
			{
				"title": " ".join((ident.get("briefTitle") or "").split()),
				"date": ((protocol.get("statusModule") or {}).get("lastUpdatePostDateStruct") or {}).get("date"),
				"link": f"https://clinicaltrials.gov/study/{nct}" if nct else None,
			}
		)
	studies = everything.get("studies") or []
	return Preview(
		counts=[
			_count("trials_matching", everything.get("totalCount", len(studies)), "Trials matching the search"),
			_count(
				"trials_updated_last_30_days",
				recent.get("totalCount", len(recent.get("studies") or [])),
				"Of those, trials updated in the last 30 days",
			),
		],
		samples=samples,
		first_run=(
			"The first run brings in only the trials updated in the last 2 days. The "
			"samples are the most recently updated matches."
		),
	)


def _norm_link(value):
	return (value or "").strip().lower().rstrip("/")


def _norm_text(value):
	return " ".join((value or "").split()).lower()


def find_duplicate(team, type_key, values, extra_links=()):
	"""The active source of this team and type with the same address or search, or None.

	Sources of other teams are never looked at. RSS compares the address without case or
	a trailing slash (`extra_links` are more addresses for the same feed, e.g. where it
	redirects to); PubMed compares the search without case or extra whitespace;
	ClinicalTrials.gov compares its three fields the same way.
	"""
	source_type = source_types.BY_KEY.get(type_key)
	if source_type is None or team is None:
		return None
	candidates = Sources.objects.filter(
		team=team, active=True, source_for=source_type.source_for, method=source_type.method
	).select_related("subject")
	if type_key in ("rss_papers", "rss_trials"):
		wanted = {_norm_link(v) for v in (values.get("link"), *extra_links)} - {""}
		return next((s for s in candidates if _norm_link(s.link) in wanted), None)
	if type_key == "pubmed":
		wanted = _norm_text(values.get("pubmed_query"))
		return next((s for s in candidates if _norm_text(s.pubmed_query) == wanted), None)
	if type_key == "ctgov":
		wanted = tuple(_norm_text(values.get(f)) for f, _ in ctgov_search.SEARCH_FIELDS)
		return next(
			(
				s
				for s in candidates
				if tuple(_norm_text(getattr(s, f)) for f, _ in ctgov_search.SEARCH_FIELDS) == wanted
			),
			None,
		)
	return None
