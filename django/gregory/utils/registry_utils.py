"""
Shared utilities for trial identity / de-duplication logic.

See docs/trials-identity-dedup.md for the full design rationale.
"""

from urllib.parse import urlparse


def _norm(v) -> str:
	"""Normalise a registry-identifier value for comparison (strip + upper)."""
	return str(v).strip().upper()


def identifiers_conflict(existing_ids: dict | None, incoming_ids: dict | None) -> bool:
	"""Return True if the two identifier dicts disagree on the *same* registry key.

	A conflict means the records belong to different trials and must NOT be merged.
	Disjoint keys (e.g. one has ``nct``, the other only ``euctr``) are NOT a conflict
	— they indicate a legitimately cross-registered study.

	Truth table (where 'nct' is used as the example key):

	+-----------------------+----------------------+-----------+
	| existing_ids          | incoming_ids         | conflict? |
	+=======================+======================+===========+
	| {'nct': 'NCT001'}     | {'nct': 'NCT002'}    | True      |
	| {'nct': 'NCT001'}     | {'nct': 'NCT001'}    | False     |
	| {'nct': 'NCT001'}     | {'euctr': 'EUCTR…'}  | False     |
	| {'nct': 'NCT001'}     | {}                   | False     |
	| None                  | {'nct': 'NCT001'}    | False     |
	| {'nct': ' nct001 '}   | {'nct': 'NCT001'}    | False     |  (normalised)
	+-----------------------+----------------------+-----------+
	"""
	existing_ids = existing_ids or {}
	for key, b in (incoming_ids or {}).items():
		a = existing_ids.get(key)
		if a and b and _norm(a) != _norm(b):
			return True
	return False


# --------------------------------------------------------------------------- #
# Multi-source link handling
#
# A cross-registered trial has one legitimate URL per registry, but the Trials
# model exposes a single ``link`` column. Importers used to overwrite it with
# their own URL, so the value flip-flopped depending on which source ran last
# (see docs/trials-multi-source-merge.md). Instead, every registry URL is kept
# in the ``links`` JSON map and ``link`` is recomputed deterministically.
# --------------------------------------------------------------------------- #

# Known registry domains → stable registry slug used as the key in Trials.links.
# Unknown domains fall back to their hostname, so two different registries can
# never collide on a key.
REGISTRY_DOMAINS = {
	"clinicaltrials.gov": "ctgov",
	"euclinicaltrials.eu": "ctis",
	"clinicaltrialsregister.eu": "euctr",
	"trialsearch.who.int": "ictrp",
	"isrctn.com": "isrctn",
	"drks.de": "drks",
	"anzctr.org.au": "anzctr",
}

# WHO ICTRP is an aggregator, not a registry of record — it ranks below any
# home-registry URL when picking the canonical link.
_AGGREGATOR_KEYS = {"ictrp"}


def registry_from_url(url: str | None) -> str | None:
	"""Return the registry slug for *url* (e.g. 'ctgov'), or its hostname when
	the domain is not in REGISTRY_DOMAINS. Returns None for empty/invalid URLs."""
	if not url:
		return None
	hostname = (urlparse(url).hostname or "").lower()
	if not hostname:
		return None
	if hostname.startswith("www."):
		hostname = hostname[4:]
	for domain, key in REGISTRY_DOMAINS.items():
		if hostname == domain or hostname.endswith("." + domain):
			return key
	return hostname


def merge_links(existing_links: dict | None, url: str | None) -> dict:
	"""Return *existing_links* with *url* filed under its registry slug or hostname key.

	Mirrors the conservative ``merge_identifiers`` semantics: an entry is only set
	when absent or empty, never replaced. Different sources write to different keys,
	so no source can clobber another source's URL. Used for both Trials and Articles.
	(WHO ICTRP exports the home registry's web address — e.g. a clinicaltrials.gov
	URL for an ``nct`` trial — which lands under the same key as the registry's own
	importer; keeping the first non-empty value avoids churn between equivalent URL
	formats.)
	"""
	merged = dict(existing_links or {})
	key = registry_from_url(url)
	if key and not merged.get(key):
		merged[key] = url
	return merged


def canonical_link(links: dict | None, current_link: str | None) -> str | None:
	"""Pick the canonical URL for a trial: first registry URL wins, chronologically.

	Registries are deliberately NOT ranked against each other — the registry the
	trial team registered with first is their primary choice, so the link stored
	first stays canonical and importers that arrive later must not replace it.

	The one exception is aggregators (WHO ICTRP): a search portal is not a
	registry a team registers with, so an aggregator URL is upgraded once to a
	registry-of-record URL when one becomes available. If several registry URLs
	are already present at that point their arrival order is unknown (JSONB does
	not preserve key order), so keys are tried alphabetically for stability.
	"""
	links = links or {}
	if current_link and registry_from_url(current_link) not in _AGGREGATOR_KEYS:
		return current_link
	for key in sorted(links):
		if key not in _AGGREGATOR_KEYS and links[key]:
			return links[key]
	if current_link:
		return current_link
	return next((links[key] for key in sorted(links) if links[key]), None)


def merge_identifiers(
	existing_identifiers: dict | None, new_identifiers: dict | None
) -> dict:
	"""Merge two trial identifier dicts, preserving existing non-empty values."""
	merged = existing_identifiers.copy() if existing_identifiers else {}
	for key, value in (new_identifiers or {}).items():
		if value and (key not in merged or merged[key] is None):
			merged[key] = value
	return merged


def safe_change_reason(reason: str) -> str:
	"""Truncate a django-simple-history change reason to the 100-character DB limit."""
	return reason[:100] if len(reason) > 100 else reason
