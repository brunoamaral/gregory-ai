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
	("nct", re.compile(r"(?<![A-Za-z0-9])NCT[\s-]?(\d{8})(?![A-Za-z0-9])", _FLAGS)),
	("isrctn", re.compile(r"(?<![A-Za-z0-9])ISRCTN(\d{8})(?![A-Za-z0-9])", _FLAGS)),
	("actrn", re.compile(r"(?<![A-Za-z0-9])ACTRN(\d{14})(?![A-Za-z0-9])", _FLAGS)),
	("drks", re.compile(r"(?<![A-Za-z0-9])DRKS(\d{8})(?![A-Za-z0-9])", _FLAGS)),
	# Middle segment is 2 or 3 digits (CTRI/2020/01/012345 vs CTRI/2009/091/000088).
	("ctri", re.compile(r"(?<![A-Za-z0-9])CTRI/(\d{4}/\d{2,3}/\d{6})(?![A-Za-z0-9])", _FLAGS)),
	("pactr", re.compile(r"(?<![A-Za-z0-9])PACTR(\d{15})(?![A-Za-z0-9])", _FLAGS)),
	("rpcec", re.compile(r"(?<![A-Za-z0-9])RPCEC(\d{8})(?![A-Za-z0-9])", _FLAGS)),
	("tctr", re.compile(r"(?<![A-Za-z0-9])TCTR(\d{11})(?![A-Za-z0-9])", _FLAGS)),
	("slctr", re.compile(r"(?<![A-Za-z0-9])SLCTR/(\d{4}/\d{3})(?![A-Za-z0-9])", _FLAGS)),
	("itmctr", re.compile(r"(?<![A-Za-z0-9])ITMCTR(\d{10})(?![A-Za-z0-9])", _FLAGS)),
	("umin", re.compile(r"(?<![A-Za-z0-9])UMIN(\d{9})(?![A-Za-z0-9])", _FLAGS)),
	# jRCT: an optional single letter (the register sub-prefix, e.g. the "s" in
	# jRCTs031180248) precedes the 9-10 digit number. The canonical value is the
	# whole match upper-cased (see _normalize_match) rather than prefix+group,
	# since that letter needs upper-casing too, not just the "jRCT" literal.
	("jrct", re.compile(r"(?<![A-Za-z0-9])jRCT[a-z]?\d{9,10}(?![A-Za-z0-9])", _FLAGS)),
	("rbr", re.compile(r"(?<![A-Za-z0-9])RBR-?([A-Za-z0-9]{6,8})(?![A-Za-z0-9])", _FLAGS)),
	("irct", re.compile(r"(?<![A-Za-z0-9])IRCT([A-Za-z0-9]{10,20})(?![A-Za-z0-9])", _FLAGS)),
	("chictr", re.compile(r"(?<![A-Za-z0-9])ChiCTR[-A-Za-z0-9]{5,20}", _FLAGS)),
	# Dutch Trial Register: NL-OMON must be tried before the bare "nl" pattern
	# below so the more specific form is obvious, though the two can't actually
	# collide — "NL" is never immediately followed by "-OMON" AND 4 digits at
	# once.
	("nl_omon", re.compile(r"(?<![A-Za-z0-9])NL-OMON(\d+)(?![A-Za-z0-9])", _FLAGS)),
	# Dutch trial register (LTR) ids are NL + 4 digits. CCMO ethics-committee
	# dossier numbers share the prefix (NL67805.068.18: 5 digits, then
	# ".<digits>") but aren't trial ids, so take exactly 4 digits and reject a
	# following ".<digit>".
	("nl", re.compile(r"(?<![A-Za-z0-9])NL(\d{4})(?![A-Za-z0-9]|\.\d)", _FLAGS)),
	("ntr", re.compile(r"(?<![A-Za-z0-9])NTR(\d{1,5})(?![A-Za-z0-9])", _FLAGS)),
	# Peru (REPEC — Registro Peruano de Ensayos Clínicos).
	("repec", re.compile(r"(?<![A-Za-z0-9])PER-(\d{3}-\d{2})(?![A-Za-z0-9])", _FLAGS)),
	("lbctr", re.compile(r"(?<![A-Za-z0-9])LBCTR(\d{10})(?![A-Za-z0-9])", _FLAGS)),
	# WHO Universal Trial Number.
	("utn", re.compile(r"(?<![A-Za-z0-9])U1111-(\d{4}-\d{4})(?![A-Za-z0-9])", _FLAGS)),
	# JAPIC (Japan Pharmaceutical Information Center) — matches the "JapicCTI-…"
	# form WHO ICTRP records carry in free text. The separate domain rule for
	# ctg_secondary_ids REGISTRY entries (bare 6-digit ids) lives in
	# normalize_trial_identifiers, not here — this pattern only ever sees the
	# prefixed form.
	("japic", re.compile(r"(?<![A-Za-z0-9])JapicCTI-(\d{6})(?![A-Za-z0-9])", _FLAGS)),
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
	"nl_omon": "NL-OMON",
	"nl": "NL",
	"ntr": "NTR",
	"repec": "PER-",
	"lbctr": "LBCTR",
	"utn": "U1111-",
	"japic": "JAPICCTI-",
}


def _normalize_match(canonical_type: str, match: re.Match) -> str:
	"""Rebuild a canonical identifier string from a regex match, independent
	of whatever casing/spacing/dashes appeared in the source text."""
	if canonical_type in ("eudract", "ctis"):
		return match.group(0)
	if canonical_type == "jrct":
		return match.group(0).upper()
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


# --- Trials.identifiers_normalized -------------------------------------------------
#
# Feeds gregory.utils.trial_field_normalizers.NORMALIZED_TRIAL_FIELDS as the
# (("identifiers", "secondary_id", "ctg_secondary_ids"), "identifiers_normalized", …)
# entry. See docs/trials-field-normalization.md for the full trust-tier
# rationale.

# ctg_secondary_ids[].type values that are sponsor-declared registrations of this
# study — always counted, never dropped by the free-text clash rule.
_CTG_REGISTRY_TYPES = frozenset({"EUDRACT_NUMBER", "CTIS", "REGISTRY"})

# ctg_secondary_ids[].type values that are grant/funding numbers, never registry
# identifiers — skipped outright, whatever their value looks like.
_CTG_GRANT_TYPES = frozenset({"NIH", "OTHER_GRANT", "AHRQ", "FDA", "SAMHSA", "VA", "CDC"})

# JAPIC domain rule: a REGISTRY-typed ctg_secondary_ids entry whose domain
# names the Japan Pharmaceutical Information Center registry, and whose id is a
# bare 6-digit number (no "JapicCTI-" prefix for extract_identifiers to match),
# is still a JAPIC clinical trial id.
_JAPIC_DOMAIN_RE = re.compile(r"japi?c|japac", re.IGNORECASE)
_SIX_DIGITS_RE = re.compile(r"\d{6}")


def _extract_from_value(value) -> set[tuple[str, str]]:
	"""extract_identifiers on one identifiers/ctg_secondary_ids value, tolerating
	non-string input (JSON can hold anything) the way
	extract_identifiers_from_trial_identifiers already does for dict values."""
	if not value:
		return set()
	return extract_identifiers(str(value))


def _japic_from_registry_entry(entry: dict) -> set[tuple[str, str]]:
	"""The JAPIC domain-rule half of a REGISTRY-typed ctg_secondary_ids entry —
	see _JAPIC_DOMAIN_RE above. Additive to _extract_from_value(entry["id"]): a
	bare 6-digit id has no prefix for the "japic" text pattern to match, so this
	is the only way such an entry is ever recognised."""
	domain = str(entry.get("domain") or "")
	if not _JAPIC_DOMAIN_RE.search(domain):
		return set()
	digits = str(entry.get("id") or "").strip()
	if not _SIX_DIGITS_RE.fullmatch(digits):
		return set()
	return {("japic", f"JAPICCTI-{digits}")}


def normalize_trial_identifiers(
	identifiers: dict | None,
	secondary_id: str | None,
	ctg_secondary_ids: list | None,
) -> list[str] | None:
	"""Compute Trials.identifiers_normalized: the sorted, de-duplicated list of
	every canonical registry id a trial carries, as "type:VALUE" strings.

	Sources are trusted differently:

	- **Registry-sourced — always counts:** every ``identifiers`` value except
	  ``org_study_id`` (a sponsor-assigned code, not a registry's own record),
	  plus ``ctg_secondary_ids`` entries ClinicalTrials.gov itself typed as a
	  registration (EUDRACT_NUMBER/CTIS/REGISTRY — including the JAPIC domain
	  rule for a REGISTRY entry's bare-digit id).
	- **Free text — counts unless it clashes:** ``secondary_id``,
	  ``identifiers["org_study_id"]``, and ``ctg_secondary_ids`` entries typed
	  OTHER or left untyped. A free-text id is dropped only when the
	  registry-sourced set already holds a *different* id of the same
	  canonical type — this excludes a mismatched foreign NCT a sponsor
	  mistakenly reused as their study code, while still admitting a free-text
	  id of a type nothing registry-sourced claims (e.g. OCTOPUS's EudraCT
	  number, sitting only in ``secondary_id``).
	- **Skipped:** ``ctg_secondary_ids`` entries typed as grants — never
	  registry identifiers, whatever their value looks like.

	Every value — from either tier — goes through ``extract_identifiers``, so
	the value's own *shape* decides its canonical type; a source's own type
	label (e.g. ClinicalTrials.gov's "EUDRACT_NUMBER") is only used to sort
	values into a trust tier, never to decide the canonical type itself. This
	is why a CTIS-shaped value typed EUDRACT_NUMBER still lands as ``ctis:…``.

	Returns None when nothing was found (the empty-value convention
	normalize_regions also uses), never an empty list.
	"""
	registry_sourced: set[tuple[str, str]] = set()
	free_text: set[tuple[str, str]] = set()

	for key, value in (identifiers or {}).items():
		if key == "org_study_id":
			continue
		registry_sourced |= _extract_from_value(value)

	org_study_id = (identifiers or {}).get("org_study_id")
	free_text |= _extract_from_value(org_study_id)

	free_text |= _extract_from_value(secondary_id)

	for entry in ctg_secondary_ids or []:
		if not isinstance(entry, dict):
			continue
		entry_type = str(entry.get("type") or "").strip().upper()
		if entry_type in _CTG_GRANT_TYPES:
			continue
		if entry_type in _CTG_REGISTRY_TYPES:
			registry_sourced |= _extract_from_value(entry.get("id"))
			if entry_type == "REGISTRY":
				registry_sourced |= _japic_from_registry_entry(entry)
		else:
			# OTHER, untyped, or any type outside the vocabulary observed so
			# far — same free-text trust tier as secondary_id/org_study_id.
			free_text |= _extract_from_value(entry.get("id"))

	registry_values_by_type: dict[str, set[str]] = {}
	for canonical_type, canonical_value in registry_sourced:
		registry_values_by_type.setdefault(canonical_type, set()).add(canonical_value)

	# A free-text id is dropped only when the registry-sourced set already holds
	# a DIFFERENT id of the same type — no registry-sourced entry of that type
	# at all, or one that agrees with this exact value, both keep it.
	kept_free_text = set()
	for canonical_type, canonical_value in free_text:
		same_type_registry_values = registry_values_by_type.get(canonical_type)
		if same_type_registry_values is None or canonical_value in same_type_registry_values:
			kept_free_text.add((canonical_type, canonical_value))

	combined = registry_sourced | kept_free_text
	if not combined:
		return None
	return sorted(f"{canonical_type}:{canonical_value}" for canonical_type, canonical_value in combined)
