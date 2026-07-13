"""
Best-effort DOI extraction from an article URL.

Used as a fallback when a feed doesn't carry the DOI in a dedicated field
(dc:identifier, prism:doi, guid, ...): many publisher links embed the DOI
directly in the path (Springer, PNAS "doi/abs/", generic doi.org redirects),
so before giving up we try to pull `10.NNNN/...` straight out of the URL.

See ARTICLES-MISSING-DOI-PLAN (root cause 1/2) for the ingestion bug this
closes: articles from feeds without a dedicated processor fell through to
``DefaultFeedProcessor.extract_doi`` which always returned ``None``, even
though the DOI was sitting in the link the whole time.
"""

import logging
import re
from urllib.parse import urlparse

import requests

# A DOI prefix is "10." followed by a 4-9 digit registrant code, then a slash
# and a suffix. The suffix is greedy up to whitespace/quote/angle-bracket/
# `?`/`#` so we don't swallow a trailing query string or HTML fragment.
_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s?#\"'<>]+")

# e.g. https://pubmed.ncbi.nlm.nih.gov/38812345/
_PMID_LINK_PATTERN = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)")

_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# Trailing characters that are almost always URL/markup noise rather than
# part of the DOI suffix itself.
_TRAILING_PUNCTUATION = ".,;:!)]}"

_DOI_RESOLVER_HOSTS = {"doi.org", "dx.doi.org"}


def _strip_trailing_punctuation(doi: str) -> str:
	"""Strip trailing punctuation and slashes that aren't part of the DOI."""
	return doi.rstrip(_TRAILING_PUNCTUATION).rstrip("/")


def extract_doi_from_url(url: str) -> str | None:
	"""Best-effort extraction of a bare DOI from an article URL.

	Handles:
	  - DOI resolver links (``https://doi.org/10.1007/...``,
	    ``https://dx.doi.org/10.1007/...``): the DOI is the URL path.
	  - DOIs embedded anywhere else in the path/query (Springer article pages,
	    PNAS ``doi/abs/10.1073/...?af=R``, SAGE ``doi/abs/10.1177/...``, etc.):
	    matched with a ``10.\\d{4,9}/...`` regex, with any trailing query
	    string/fragment and punctuation stripped off.

	Returns a clean, lowercased, bare DOI (no scheme, no trailing slash), or
	``None`` when nothing DOI-shaped is found. Non-DOI repository URLs
	(hdl.handle.net, arxiv.org, hal.science, escholarship.org, ...) naturally
	return ``None`` since they never contain a ``10.xxxx/...`` segment.
	"""
	if not url:
		return None

	parsed = urlparse(url)
	hostname = (parsed.hostname or "").lower()
	if hostname.startswith("www."):
		hostname = hostname[4:]

	if hostname in _DOI_RESOLVER_HOSTS:
		path = parsed.path.lstrip("/")
		if path:
			doi = _strip_trailing_punctuation(path)
			if doi.lower().startswith("10.") and "/" in doi:
				return doi.lower()
		# Fall through to the generic regex in case the resolver URL is
		# malformed (e.g. the DOI ended up in the query string instead).

	match = _DOI_PATTERN.search(url)
	if not match:
		return None

	doi = _strip_trailing_punctuation(match.group(0))
	if not doi or "/" not in doi:
		return None
	return doi.lower()


def extract_pmid_from_url(url: str | None) -> str | None:
	"""Pull the numeric PMID out of a pubmed.ncbi.nlm.nih.gov article link."""
	if not url:
		return None
	match = _PMID_LINK_PATTERN.search(url)
	return match.group(1) if match else None


def resolve_doi_from_pmid(pmid: str) -> str | None:
	"""Resolve a DOI for a PubMed ID via NCBI E-utilities' ``esummary``.

	Defensive by design: any network failure, non-2xx response, or
	unexpected payload shape returns ``None`` rather than raising, so a single
	flaky lookup never aborts a feed run or a backfill batch.
	"""
	if not pmid:
		return None
	try:
		response = requests.get(
			_ESUMMARY_URL,
			params={"db": "pubmed", "id": pmid, "retmode": "json"},
			timeout=10,
		)
		response.raise_for_status()
		data = response.json()
		article_ids = data.get("result", {}).get(pmid, {}).get("articleids", [])
		for article_id in article_ids:
			if article_id.get("idtype") == "doi":
				value = article_id.get("value")
				return value.strip() if value else None
	except Exception as e:
		logging.debug(f"Failed to resolve DOI via PMID {pmid}: {e}")
	return None


def resolve_doi_from_pubmed_url(url: str | None) -> str | None:
	"""Resolve a DOI from a PubMed article URL by extracting the PMID and
	querying NCBI E-utilities. Returns ``None`` if the URL has no PMID or the
	lookup fails."""
	pmid = extract_pmid_from_url(url)
	if not pmid:
		return None
	return resolve_doi_from_pmid(pmid)
