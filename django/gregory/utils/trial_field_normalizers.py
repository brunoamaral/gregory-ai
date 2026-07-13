"""
Pure normalisation functions for Trials fields with multi-registry vocabularies.

Each source registry (WHO ICTRP, ClinicalTrials.gov, EU CTIS) writes its own free-text
values into the same Trials columns, so the same real-world value shows up under dozens
of spellings (e.g. "Phase III", "phase3", "iii", the EudraCT yes/no matrix string). The
raw column stays untouched for source-sync fidelity; a `<field>_normalized` companion
column stores the canonical value computed here, recomputed on every model save (see
Trials.save() in gregory/models.py).

This module intentionally does NOT import gregory.models — models.py imports from here,
not the other way round.

See docs/trials-field-normalization.md for the full mapping rationale and the extension
recipe for the next field (study_type, ...).
"""

import logging
import re

from django.db import models

logger = logging.getLogger(__name__)


class TrialPhase(models.TextChoices):
	EARLY_PHASE_1 = "early_phase_1", "Early Phase 1"
	PHASE_1 = "phase_1", "Phase 1"
	PHASE_1_2 = "phase_1_2", "Phase 1/2"
	PHASE_2 = "phase_2", "Phase 2"
	PHASE_2_3 = "phase_2_3", "Phase 2/3"
	PHASE_3 = "phase_3", "Phase 3"
	PHASE_3_4 = "phase_3_4", "Phase 3/4"
	PHASE_4 = "phase_4", "Phase 4"
	POST_MARKET = "post_market", "Post-market"
	NOT_APPLICABLE = "not_applicable", "Not applicable"
	OTHER = "other", "Other"


# Exact-match lookup for every distinct raw `phase` value currently observed in the DB.
# Keys are the whitespace-collapsed, casefolded raw value. Extend this table (rather than
# the generic fallback) whenever a new *known* registry spelling shows up in the "other"
# review list — see the admin "Recompute normalized phase" action.
_EXACT_MATCHES: dict[str, str] = {
	# Early Phase 1 / Phase 0
	"phase 0": TrialPhase.EARLY_PHASE_1,
	"0": TrialPhase.EARLY_PHASE_1,
	"0 (exploratory trials)": TrialPhase.EARLY_PHASE_1,
	"early phase 1": TrialPhase.EARLY_PHASE_1,
	"early_phase1": TrialPhase.EARLY_PHASE_1,
	# Phase 1
	"phase 1": TrialPhase.PHASE_1,
	"phase i": TrialPhase.PHASE_1,
	"phase1": TrialPhase.PHASE_1,
	"1": TrialPhase.PHASE_1,
	"i (phase i study)": TrialPhase.PHASE_1,
	"human pharmacology (phase i)- first administration to humans": TrialPhase.PHASE_1,
	"human pharmacology (phase i)- other": TrialPhase.PHASE_1,
	# Phase 1/2
	"phase 1/phase 2": TrialPhase.PHASE_1_2,
	"phase 1/ phase 2": TrialPhase.PHASE_1_2,
	"phase 1 / phase 2": TrialPhase.PHASE_1_2,
	"phase i,ii": TrialPhase.PHASE_1_2,
	"phase i and phase ii (integrated)- other": TrialPhase.PHASE_1_2,
	"phase1, phase2": TrialPhase.PHASE_1_2,
	"1-2": TrialPhase.PHASE_1_2,
	"i+ii (phase i+phase ii)": TrialPhase.PHASE_1_2,
	# Phase 2
	"phase 2": TrialPhase.PHASE_2,
	"phase ii": TrialPhase.PHASE_2,
	"phase2": TrialPhase.PHASE_2,
	"2": TrialPhase.PHASE_2,
	"ii (phase ii study)": TrialPhase.PHASE_2,
	"therapeutic exploratory (phase ii)": TrialPhase.PHASE_2,
	# Phase 2/3
	"phase 2/phase 3": TrialPhase.PHASE_2_3,
	"phase 2/ phase 3": TrialPhase.PHASE_2_3,
	"phase 2 / phase 3": TrialPhase.PHASE_2_3,
	"phase ii/iii": TrialPhase.PHASE_2_3,
	"phase ii,iii": TrialPhase.PHASE_2_3,
	"phase ii and phase iii (integrated)": TrialPhase.PHASE_2_3,
	"phase2, phase3": TrialPhase.PHASE_2_3,
	"2-3": TrialPhase.PHASE_2_3,
	# Phase 3
	"phase 3": TrialPhase.PHASE_3,
	"phase iii": TrialPhase.PHASE_3,
	"phase3": TrialPhase.PHASE_3,
	"3": TrialPhase.PHASE_3,
	"iii": TrialPhase.PHASE_3,
	"therapeutic confirmatory (phase iii)": TrialPhase.PHASE_3,
	# Phase 3/4
	"phase 3/ phase 4": TrialPhase.PHASE_3_4,
	"phase 3 / phase 4": TrialPhase.PHASE_3_4,
	# Phase 4
	"phase 4": TrialPhase.PHASE_4,
	"phase iv": TrialPhase.PHASE_4,
	"phase4": TrialPhase.PHASE_4,
	"4": TrialPhase.PHASE_4,
	"iv (phase iv study)": TrialPhase.PHASE_4,
	"therapeutic use (phase iv)": TrialPhase.PHASE_4,
	# Post-market
	"post-marketing clinical trial": TrialPhase.POST_MARKET,
	"post-market": TrialPhase.POST_MARKET,
	# Not applicable
	"not applicable": TrialPhase.NOT_APPLICABLE,
	"n/a": TrialPhase.NOT_APPLICABLE,
	"na": TrialPhase.NOT_APPLICABLE,
	"not selected": TrialPhase.NOT_APPLICABLE,
	"not specified": TrialPhase.NOT_APPLICABLE,
	# Other: registry categories with no phase equivalent
	"others": TrialPhase.OTHER,
	"other": TrialPhase.OTHER,
	"retrospective study": TrialPhase.OTHER,
	"pilot study": TrialPhase.OTHER,
	"pilot clinical trial": TrialPhase.OTHER,
	"new treatment measure clinical study": TrialPhase.OTHER,
	"diagnostic new technique clincal study": TrialPhase.OTHER,  # sic, registry typo
	"bioequivalence": TrialPhase.OTHER,
	"basic science": TrialPhase.OTHER,
}

_ROMAN_TO_INT = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

# Sets of phase ints that resolve to a single canonical value. Anything else (empty,
# non-adjacent pairs, 0 combined with another phase, or 3+ phases) is not a coherent
# span and falls back to OTHER.
_SPAN_MAP: dict[frozenset, str] = {
	frozenset({0}): TrialPhase.EARLY_PHASE_1,
	frozenset({1}): TrialPhase.PHASE_1,
	frozenset({2}): TrialPhase.PHASE_2,
	frozenset({3}): TrialPhase.PHASE_3,
	frozenset({4}): TrialPhase.PHASE_4,
	frozenset({1, 2}): TrialPhase.PHASE_1_2,
	frozenset({2, 3}): TrialPhase.PHASE_2_3,
	frozenset({3, 4}): TrialPhase.PHASE_3_4,
}

# EudraCT/CTIS "trial phase" is reported as a yes/no matrix across four phase labels, e.g.
# "Human pharmacology (Phase I): no Therapeutic exploratory (Phase II): yes ...". Detect it
# by the first label, then pull every "(phase <roman>): <yes|no|>" pair out regardless of
# the label text in front (labels vary: "Therapeutic use (Phase IV)" vs "... - (Phase IV)").
_MATRIX_TRIGGER = "human pharmacology (phase i)"
_MATRIX_RE = re.compile(r"\(phase (i{1,3}|iv)\)[^:]*:\s*(yes|no)?")


def _token_to_int(token: str) -> int:
	"""Convert a matched phase token (roman numeral or digit string) to an int."""
	return _ROMAN_TO_INT[token] if token in _ROMAN_TO_INT else int(token)


def _span_to_phase(phases: set) -> str:
	"""Apply the shared span rule to a set of phase ints (see _SPAN_MAP)."""
	return _SPAN_MAP.get(frozenset(phases), TrialPhase.OTHER)


def _resolve(result: str, raw: str, field_label: str = "trial phase") -> str:
	"""Log unmapped raw values so the admin 'other' filter has something to review."""
	if result == TrialPhase.OTHER:
		logger.info("Unmapped %s value: %r", field_label, raw)
	return result


def normalize_phase(raw: str | None) -> str | None:
	"""
	Map a raw Trials.phase value to a canonical TrialPhase value.

	Returns None for missing/blank input, otherwise always a TrialPhase value (falling
	back to TrialPhase.OTHER for anything unrecognised — never raises).
	"""
	if raw is None or not raw.strip():
		return None

	# Collapse whitespace runs (including newlines/tabs) to a single space, then match
	# case-insensitively. Registry feeds pretty-print this field with arbitrary
	# indentation and double spaces (e.g. "confirmatory  (phase iii)").
	cleaned = re.sub(r"\s+", " ", raw).strip().casefold()

	matrix_start = cleaned.find(_MATRIX_TRIGGER)
	if matrix_start != -1 and ":" in cleaned[matrix_start:]:
		pairs = _MATRIX_RE.findall(cleaned)
		if pairs:
			phases = {_token_to_int(label) for label, value in pairs if value == "yes"}
			return _resolve(_span_to_phase(phases), raw)

	if cleaned in _EXACT_MATCHES:
		return _EXACT_MATCHES[cleaned]

	# Conservative fallback for formatting variants not yet in _EXACT_MATCHES: pull every
	# "phase <roman|digit>" token out and apply the same span rule.
	tokens = re.findall(r"phase\s*/?\s*(iv|i{1,3}|[0-4])\b", cleaned)
	phases = {_token_to_int(token) for token in tokens}
	return _resolve(_span_to_phase(phases), raw)


class TrialRecruitmentStatus(models.TextChoices):
	NOT_YET_RECRUITING = "not_yet_recruiting", "Not yet recruiting"
	RECRUITING = "recruiting", "Recruiting"
	ENROLLING_BY_INVITATION = "enrolling_by_invitation", "Enrolling by invitation"
	ACTIVE_NOT_RECRUITING = "active_not_recruiting", "Active, not recruiting"
	NOT_RECRUITING = "not_recruiting", "Not recruiting"
	SUSPENDED = "suspended", "Suspended"
	COMPLETED = "completed", "Completed"
	TERMINATED = "terminated", "Terminated"
	WITHDRAWN = "withdrawn", "Withdrawn"
	UNKNOWN = "unknown", "Unknown"
	OTHER = "other", "Other"


# Exact-match lookup for every distinct raw `recruitment_status` value observed in the DB.
# Keys are the whitespace-collapsed, casefolded raw value. Unlike _EXACT_MATCHES for phase,
# there is deliberately no generic token fallback below: the recruitment-status vocabulary
# is small enough that exact matching is safer than guessing at a new registry spelling.
_RECRUITMENT_STATUS_EXACT_MATCHES: dict[str, str] = {
	# Not yet recruiting
	"not_yet_recruiting": TrialRecruitmentStatus.NOT_YET_RECRUITING,  # CT.gov
	"authorised, recruitment pending": TrialRecruitmentStatus.NOT_YET_RECRUITING,  # EU CTIS
	# Recruiting
	"recruiting": TrialRecruitmentStatus.RECRUITING,  # CT.gov + WHO "Recruiting"
	"ongoing, recruiting": TrialRecruitmentStatus.RECRUITING,  # EU CTIS
	"authorised, recruiting": TrialRecruitmentStatus.RECRUITING,  # EU CTIS
	# Enrolling by invitation
	"enrolling_by_invitation": TrialRecruitmentStatus.ENROLLING_BY_INVITATION,  # CT.gov
	# Active, not recruiting
	"active_not_recruiting": TrialRecruitmentStatus.ACTIVE_NOT_RECRUITING,  # CT.gov
	"ongoing, recruitment ended": TrialRecruitmentStatus.ACTIVE_NOT_RECRUITING,  # EU CTIS
	# Not recruiting: WHO ICTRP's generic status is deliberately its own bucket — it does
	# not say whether the trial is pre-start, ongoing, or done, so folding it into
	# active_not_recruiting would overclaim.
	"not recruiting": TrialRecruitmentStatus.NOT_RECRUITING,  # WHO ICTRP
	# Suspended
	"suspended": TrialRecruitmentStatus.SUSPENDED,  # CT.gov
	"temporarily halted": TrialRecruitmentStatus.SUSPENDED,  # WHO
	"temporarily_not_available": TrialRecruitmentStatus.SUSPENDED,  # CT.gov expanded access
	# Completed
	"completed": TrialRecruitmentStatus.COMPLETED,  # CT.gov / WHO
	"ended": TrialRecruitmentStatus.COMPLETED,  # EU CTIS
	# Terminated
	"terminated": TrialRecruitmentStatus.TERMINATED,  # CT.gov
	# Withdrawn
	"withdrawn": TrialRecruitmentStatus.WITHDRAWN,  # CT.gov
	# Unknown: registry states that stop short of naming an actual recruitment state.
	"unknown": TrialRecruitmentStatus.UNKNOWN,  # CT.gov — not verified in >2 years
	"authorised": TrialRecruitmentStatus.UNKNOWN,  # EUCTR/CTIS: approved, recruitment state unstated
	"not available": TrialRecruitmentStatus.UNKNOWN,  # WHO: status not available
	# Other: CT.gov expanded-access program statuses, not trial recruitment states.
	# Deliberately left in "other" so the raw string shows on display surfaces.
	"available": TrialRecruitmentStatus.OTHER,
	"no_longer_available": TrialRecruitmentStatus.OTHER,
	"approved_for_marketing": TrialRecruitmentStatus.OTHER,
}


def normalize_recruitment_status(raw: str | None) -> str | None:
	"""
	Map a raw Trials.recruitment_status value to a canonical TrialRecruitmentStatus value.

	Returns None for missing/blank input, otherwise always a TrialRecruitmentStatus value
	(falling back to TrialRecruitmentStatus.OTHER for anything unrecognised — never raises).
	No generic token fallback here (unlike normalize_phase): the vocabulary is small and
	exact matching is safer.
	"""
	if raw is None or not raw.strip():
		return None

	cleaned = re.sub(r"\s+", " ", raw).strip().casefold()

	if cleaned in _RECRUITMENT_STATUS_EXACT_MATCHES:
		return _RECRUITMENT_STATUS_EXACT_MATCHES[cleaned]

	return _resolve(TrialRecruitmentStatus.OTHER, raw, field_label="trial recruitment status")


# Drives Trials.save() and the generalized backfill/admin-recompute commands: each entry is
# (raw field name, derived field name, normalizer function). Add a new entry here — plus the
# matching model field in gregory/models.py — for the next derived field (study_type, ...).
NORMALIZED_TRIAL_FIELDS = (
	("phase", "phase_normalized", normalize_phase),
	("recruitment_status", "recruitment_status_normalized", normalize_recruitment_status),
)
