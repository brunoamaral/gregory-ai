"""
Registry-identifier extraction and normalization for matching trials against
free-text (article titles/summaries).

Trial identifier values are messy: the same registry number shows up under
different dict keys across importers (``euctr``, ``eudract``, ``euct``...) and
with different prefixes/suffixes (``EUCTR2020-001205-23-NO`` vs the bare
``2020-001205-23`` an article cites). Rather than trust the dict key, every
value — from a trial's ``identifiers`` dict *and* from article text — is run
through the same regex patterns below, so both sides land in one canonical
space and a plain dict lookup finds the match.
"""

import re

_FLAGS = re.IGNORECASE

# Ordered: more specific patterns first. CTIS numbers (4-6-2-2 digits) contain
# a valid EudraCT-shaped (4-6-2) prefix, so EudraCT must not claim that span —
# handled by the (?!-\d) lookahead in the EudraCT pattern below, but trying
# CTIS first keeps the intent obvious.
PATTERNS: list[tuple[str, re.Pattern]] = [
	("ctis", re.compile(r"(?<!\d)\d{4}-\d{6}-\d{2}-\d{2}(?!-?\d)")),
	("eudract", re.compile(r"(?<!\d)\d{4}-\d{6}-\d{2}(?!-?\d)")),
	("nct", re.compile(r"(?<![A-Za-z0-9])NCT[\s-]?(\d{8})(?![0-9])", _FLAGS)),
	("isrctn", re.compile(r"(?<![A-Za-z0-9])ISRCTN(\d{8})(?![0-9])", _FLAGS)),
	("actrn", re.compile(r"(?<![A-Za-z0-9])ACTRN(\d{14})(?![0-9])", _FLAGS)),
	("drks", re.compile(r"(?<![A-Za-z0-9])DRKS(\d{8})(?![0-9])", _FLAGS)),
	("ctri", re.compile(r"(?<![A-Za-z0-9])CTRI/(\d{4}/\d{2}/\d{6})(?![0-9])", _FLAGS)),
	("pactr", re.compile(r"(?<![A-Za-z0-9])PACTR(\d{15})(?![0-9])", _FLAGS)),
	("rpcec", re.compile(r"(?<![A-Za-z0-9])RPCEC(\d{8})(?![0-9])", _FLAGS)),
	("tctr", re.compile(r"(?<![A-Za-z0-9])TCTR(\d{11})(?![0-9])", _FLAGS)),
	("slctr", re.compile(r"(?<![A-Za-z0-9])SLCTR/(\d{4}/\d{3})(?![0-9])", _FLAGS)),
	("itmctr", re.compile(r"(?<![A-Za-z0-9])ITMCTR(\d{10})(?![0-9])", _FLAGS)),
	("umin", re.compile(r"(?<![A-Za-z0-9])UMIN(\d{9})(?![0-9])", _FLAGS)),
	("jrct", re.compile(r"(?<![A-Za-z0-9])jRCT(\d{10})(?![0-9])", _FLAGS)),
	("rbr", re.compile(r"(?<![A-Za-z0-9])RBR-?([A-Za-z0-9]{6,8})(?![A-Za-z0-9])", _FLAGS)),
	("irct", re.compile(r"(?<![A-Za-z0-9])IRCT([A-Za-z0-9]{10,20})(?![A-Za-z0-9])", _FLAGS)),
	("chictr", re.compile(r"(?<![A-Za-z0-9])ChiCTR[-A-Za-z0-9]{5,20}", _FLAGS)),
]

_PREFIXES = {
	"nct": "NCT",
	"isrctn": "ISRCTN",
	"actrn": "ACTRN",
	"drks": "DRKS",
	"ctri": "CTRI/",
	"pactr": "PACTR",
	"rpcec": "RPCEC",
	"tctr": "TCTR",
	"slctr": "SLCTR/",
	"itmctr": "ITMCTR",
	"umin": "UMIN",
	"jrct": "JRCT",
}


def _normalize_match(canonical_type: str, match: re.Match) -> str:
	"""Rebuild a canonical identifier string from a regex match, independent
	of whatever casing/spacing/dashes appeared in the source text."""
	if canonical_type in ("eudract", "ctis"):
		return match.group(0)
	if canonical_type in _PREFIXES:
		return _PREFIXES[canonical_type] + match.group(1)
	if canonical_type == "rbr":
		return "RBR-" + match.group(1).upper()
	if canonical_type == "irct":
		return "IRCT" + match.group(1).upper()
	if canonical_type == "chictr":
		suffix = match.group(0)[len("ChiCTR") :]
		suffix = re.sub(r"[^A-Za-z0-9]+", "-", suffix).strip("-").upper()
		return "CHICTR-" + suffix if suffix else "CHICTR"
	raise ValueError(f"Unhandled canonical_type: {canonical_type}")


def extract_identifiers(text: str | None) -> set[tuple[str, str]]:
	"""Return the set of (canonical_type, canonical_value) identifiers found
	in *text*. Safe to call on both article text and a trial's identifier
	values — both sides land in the same canonical space."""
	if not text:
		return set()
	found: set[tuple[str, str]] = set()
	for canonical_type, pattern in PATTERNS:
		for match in pattern.finditer(text):
			found.add((canonical_type, _normalize_match(canonical_type, match)))
	return found


def extract_identifiers_from_trial_identifiers(identifiers: dict | None) -> set[tuple[str, str]]:
	"""Extract canonical identifiers from a Trials.identifiers dict by running
	every stored value through the same patterns used on article text."""
	if not identifiers:
		return set()
	blob = " ".join(str(v) for v in identifiers.values() if v)
	return extract_identifiers(blob)
