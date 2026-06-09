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
	'clinicaltrials.gov': 'ctgov',
	'euclinicaltrials.eu': 'ctis',
	'clinicaltrialsregister.eu': 'euctr',
	'trialsearch.who.int': 'ictrp',
	'isrctn.com': 'isrctn',
	'drks.de': 'drks',
	'anzctr.org.au': 'anzctr',
}

# WHO ICTRP is an aggregator, not a registry of record — it ranks below any
# home-registry URL when picking the canonical link.
_AGGREGATOR_KEYS = {'ictrp'}


def registry_from_url(url: str | None) -> str | None:
	"""Return the registry slug for *url* (e.g. 'ctgov'), or its hostname when
	the domain is not in REGISTRY_DOMAINS. Returns None for empty/invalid URLs."""
	if not url:
		return None
	hostname = (urlparse(url).hostname or '').lower()
	if not hostname:
		return None
	if hostname.startswith('www.'):
		hostname = hostname[4:]
	for domain, key in REGISTRY_DOMAINS.items():
		if hostname == domain or hostname.endswith('.' + domain):
			return key
	return hostname


def merge_trial_links(existing_links: dict | None, url: str | None) -> dict:
	"""Return *existing_links* with *url* filed under its registry key.

	Mirrors the conservative ``merge_identifiers`` semantics: a registry's entry
	is only set when absent or empty, never replaced. Different registries write
	to different keys, so no source can clobber another source's URL. (WHO ICTRP
	exports the home registry's web address — e.g. a clinicaltrials.gov URL for an
	``nct`` trial — which lands under the same key as the registry's own importer;
	keeping the first non-empty value avoids churn between equivalent URL formats.)
	"""
	merged = dict(existing_links or {})
	key = registry_from_url(url)
	if key and not merged.get(key):
		merged[key] = url
	return merged


def canonical_link(links: dict | None, identifiers: dict | None, fallback: str | None = None) -> str | None:
	"""Pick the canonical URL for a trial, deterministically.

	Priority: the trial's home registry (ClinicalTrials.gov for ``nct`` trials,
	EU CTIS for ``euct``/``ctis``, EU CTR for ``eudract``/``euctr``), then any
	other registry of record (alphabetical for stability), then aggregators
	(WHO ICTRP), then *fallback* (the currently stored link).
	"""
	links = links or {}
	identifiers = identifiers or {}

	home_keys = []
	if identifiers.get('nct'):
		home_keys.append('ctgov')
	if identifiers.get('euct') or identifiers.get('ctis'):
		home_keys.append('ctis')
	if identifiers.get('eudract') or identifiers.get('euctr'):
		home_keys.append('euctr')

	for key in home_keys:
		if links.get(key):
			return links[key]
	for key in sorted(links):
		if key not in _AGGREGATOR_KEYS and links[key]:
			return links[key]
	for key in sorted(_AGGREGATOR_KEYS):
		if links.get(key):
			return links[key]
	return fallback
