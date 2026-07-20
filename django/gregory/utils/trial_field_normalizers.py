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
recipe for the next derived field.

`countries`/`regions_normalized` are the first fields whose canonical value derives from
*multiple* raw columns (`countries_by_source`, `countries`, `country_status`,
`countries_decision_date`) rather than one — see docs/trials-field-normalization.md,
"countries" section. NORMALIZED_TRIAL_FIELDS entries accept either a single raw field
name (str) or a tuple of raw field names for this reason; see `raw_field_names`.
"""

import logging
import re
import unicodedata
from functools import lru_cache

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
	# EU CTIS per-country regulatory decision: the member state declined authorisation, so
	# the trial will not recruit there — distinct from "authorised" (approved, recruitment
	# state otherwise unstated) below, which maps to UNKNOWN instead.
	"not authorised": TrialRecruitmentStatus.NOT_RECRUITING,  # EU CTIS country_status
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
	# These four EU CTIS public-status codes specifically (confirmed 2026-07-19 against
	# the portal's own frontend enum, see gregory.utils.ctis_codes) do not name an actual
	# recruitment state — each is a step before/after regulatory review, unlike the CTIS
	# labels above (e.g. "ongoing, recruiting", "authorised, recruitment pending") which
	# do map onto a concrete recruitment phase. UNKNOWN (not OTHER, which is reserved for
	# CT.gov states that aren't trial recruitment states at all) is the accurate bucket
	# for these four specifically.
	"under evaluation": TrialRecruitmentStatus.UNKNOWN,  # EU CTIS: pre-authorisation, recruitment state unstated
	"expired": TrialRecruitmentStatus.UNKNOWN,  # EU CTIS: authorisation lapsed without a decision
	"revoked": TrialRecruitmentStatus.UNKNOWN,  # EU CTIS: authorisation withdrawn by the regulator
	"cancelled": TrialRecruitmentStatus.UNKNOWN,  # EU CTIS: application cancelled by the sponsor
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


class TrialStudyType(models.TextChoices):
	INTERVENTIONAL = "interventional", "Interventional"
	OBSERVATIONAL = "observational", "Observational"
	EXPANDED_ACCESS = "expanded_access", "Expanded access"
	BASIC_SCIENCE = "basic_science", "Basic science"
	OTHER = "other", "Other"


# Exact-match lookup for every distinct raw `study_type` value observed in the DB. Keys are
# the whitespace-collapsed, casefolded raw value. Like recruitment_status, the study_type
# vocabulary is small and closed, so there is deliberately no generic token fallback — see
# normalize_study_type.
_STUDY_TYPE_EXACT_MATCHES: dict[str, str] = {
	# Interventional
	"interventional": TrialStudyType.INTERVENTIONAL,  # CT.gov (INTERVENTIONAL), ANZCTR/NL-OMON/ISRCTN/JPRN, IRCT
	"intervention": TrialStudyType.INTERVENTIONAL,  # REBEC
	"interventional study": TrialStudyType.INTERVENTIONAL,  # ChiCTR
	"interventional clinical trial of medicinal product": TrialStudyType.INTERVENTIONAL,  # EU CTR / EU CTIS
	"treatment study": TrialStudyType.INTERVENTIONAL,
	# BA/BE (bioavailability/bioequivalence): subjects are dosed by protocol, interventional
	# by definition.
	"ba/be": TrialStudyType.INTERVENTIONAL,
	# Observational
	"observational": TrialStudyType.OBSERVATIONAL,  # CT.gov (OBSERVATIONAL), DRKS
	"observational study": TrialStudyType.OBSERVATIONAL,  # ChiCTR
	# NL-OMON's invasiveness qualifier is orthogonal to study type; the raw column keeps it,
	# the normalized value collapses both to observational.
	"observational non invasive": TrialStudyType.OBSERVATIONAL,  # NL-OMON
	"observational invasive": TrialStudyType.OBSERVATIONAL,  # NL-OMON
	# Diagnostic-accuracy/screening studies: ClinicalTrials.gov classifies these as
	# observational unless they assign an intervention, and the WHO registries that emit
	# these strings use them as a study *purpose*, not an assignment model.
	"diagnostic test": TrialStudyType.OBSERVATIONAL,
	"screening": TrialStudyType.OBSERVATIONAL,
	"cause": TrialStudyType.OBSERVATIONAL,
	"cause/relative factors study": TrialStudyType.OBSERVATIONAL,
	"relative factors research": TrialStudyType.OBSERVATIONAL,
	"epidemilogical research": TrialStudyType.OBSERVATIONAL,  # sic, registry typo
	"prognosis study": TrialStudyType.OBSERVATIONAL,
	# Expanded access: an access program, not a study — kept separate from both
	# interventional and observational (consistent with how the "available"/
	# "no_longer_available" recruitment statuses are handled above).
	"expanded_access": TrialStudyType.EXPANDED_ACCESS,  # CT.gov
	"expanded access": TrialStudyType.EXPANDED_ACCESS,
	# Basic science: meaningfully non-clinical, kept out of the "other" bucket so it stays
	# distinguishable from "we don't know" (same reasoning that gave phase's post_market
	# its own bucket).
	"basic science": TrialStudyType.BASIC_SCIENCE,
	"other": TrialStudyType.OTHER,
}


def normalize_study_type(raw: str | None) -> str | None:
	"""
	Map a raw Trials.study_type value to a canonical TrialStudyType value.

	Returns None for missing/blank input, otherwise always a TrialStudyType value (falling
	back to TrialStudyType.OTHER for anything unrecognised — never raises). No generic token
	fallback here (unlike normalize_phase): the vocabulary is small and closed, so exact
	matching is safer than guessing at a new registry spelling.
	"""
	if raw is None or not raw.strip():
		return None

	cleaned = re.sub(r"\s+", " ", raw).strip().casefold()

	if cleaned in _STUDY_TYPE_EXACT_MATCHES:
		return _STUDY_TYPE_EXACT_MATCHES[cleaned]

	return _resolve(TrialStudyType.OTHER, raw, field_label="trial study type")


class TrialSexEligibility(models.TextChoices):
	ALL = "all", "All sexes"
	FEMALE = "female", "Female only"
	MALE = "male", "Male only"


# Exact-match lookup for every distinct raw `inclusion_gender` value observed in the DB
# (25 raw values collapse to 18 keys after whitespace-collapse + casefold; see
# INCLUSION-GENDER-NORMALIZATION-PLAN.md for the full per-value count/provenance
# inventory). Placeholders map to None (no eligibility signal) rather than a canonical
# value. There is deliberately no "other" bucket for this field: a value literally
# rendered as `sex = other` would misleadingly read as "intersex / non-binary
# participants", which is not what an unparsed string means — see normalize_inclusion_gender.
_INCLUSION_GENDER_EXACT_MATCHES: dict[str, str | None] = {
	# All sexes
	"all": TrialSexEligibility.ALL,  # ClinicalTrials.gov
	"both": TrialSexEligibility.ALL,  # ChiCTR/IRCT/ISRCTN
	"both males and females": TrialSexEligibility.ALL,  # ANZCTR
	"female, male": TrialSexEligibility.ALL,  # EU CTIS
	"male and female": TrialSexEligibility.ALL,
	"male/female": TrialSexEligibility.ALL,
	"female: yes male: yes": TrialSexEligibility.ALL,  # EU CTR HTML fragment, tags stripped
	# HTML-wrapped variants of the yes/yes matrix, straight from the EU Clinical Trials
	# Register feed. Expected-dead once WHO-HTML-CLEANUP-PLAN.md's ingest-time stripping
	# has fully landed (this normalizer deliberately does NOT strip tags itself — see its
	# docstring), but kept here as a complete record of what the registry has historically
	# sent and as a safety net for a stale re-import of cached XML.
	"<br>female: yes<br>male: yes<br>": TrialSexEligibility.ALL,
	"<br> female: yes<br> male: yes<br>": TrialSexEligibility.ALL,
	# Female only
	"female": TrialSexEligibility.FEMALE,
	"females": TrialSexEligibility.FEMALE,
	"f": TrialSexEligibility.FEMALE,
	"female: yes male: no": TrialSexEligibility.FEMALE,
	"<br>female: yes<br>male: no<br>": TrialSexEligibility.FEMALE,  # expected-dead, see above
	# Male only
	"male": TrialSexEligibility.MALE,
	"males": TrialSexEligibility.MALE,
	"female: no male: yes": TrialSexEligibility.MALE,
	# Placeholders: no eligibility signal, not a fourth state — the original mistake was
	# storing these as if they were data.
	"-": None,
	"--": None,
	"not specified": None,
}

# "Female: no Male: no" (2 rows): logically no one could enrol — a registry data-entry
# artifact, not a genuine fourth eligibility state. Maps to None like the placeholders
# above, but logged separately so it doesn't silently vanish into the same null bucket as
# an honest "not specified" — see INCLUSION-GENDER-NORMALIZATION-PLAN.md decision 3.
_INCLUSION_GENDER_CONTRADICTORY = "female: no male: no"


def normalize_inclusion_gender(raw: str | None) -> str | None:
	"""
	Map a raw Trials.inclusion_gender value to a canonical TrialSexEligibility value.

	Returns None for missing/blank input, for explicit placeholders ("-", "Not
	Specified"), for the contradictory "Female: no Male: no" registry artifact, and for
	anything unrecognised (logged) — there is deliberately no "other" bucket, see
	INCLUSION-GENDER-NORMALIZATION-PLAN.md.

	The EU Clinical Trials Register stores this field as an HTML fragment
	("<br>Female: yes<br>Male: yes<br>"), so the exact-match table above keeps the HTML
	variants. No stripping happens here: WHO-HTML-CLEANUP-PLAN.md removes markup at
	ingest, the correct choke point (one place, and it fixes several other columns at
	once) — duplicating a stripper here would hide whether that ingest fix is working.

	No generic token fallback (unlike normalize_phase): the vocabulary is small and
	closed, and substring-matching "female" would resurrect exactly the bug this field
	fixes ("Female, Male" contains "female" but is not female-only).
	"""
	if raw is None or not raw.strip():
		return None

	cleaned = re.sub(r"\s+", " ", raw).strip().casefold()

	if cleaned == _INCLUSION_GENDER_CONTRADICTORY:
		logger.info("Contradictory trial sex eligibility value: %r", raw)
		return None

	if cleaned in _INCLUSION_GENDER_EXACT_MATCHES:
		return _INCLUSION_GENDER_EXACT_MATCHES[cleaned]

	logger.info("Unmapped trial sex eligibility value: %r", raw)
	return None


class TrialRegion(models.TextChoices):
	AFRICA = "africa", "Africa"
	ASIA = "asia", "Asia"
	EUROPE = "europe", "Europe"
	NORTH_AMERICA = "north_america", "North America"
	SOUTH_AMERICA = "south_america", "South America"
	OCEANIA = "oceania", "Oceania"


# ISO 3166-1 alpha-2 -> region slug, UN M49-ish continental grouping (Central America and
# the Caribbean fold into north_america, matching common registry usage). A handful of
# uninhabited/Antarctic-adjacent codes (AQ, BV, HM, TF, UM) are deliberately absent: they
# have no meaningful region bucket in this vocabulary and essentially never appear in trial
# country data. Generated from django_countries.data.COUNTRIES — every other ISO code is
# covered.
_COUNTRY_TO_REGION: dict[str, str] = {
	# Africa
	"AO": TrialRegion.AFRICA, "BF": TrialRegion.AFRICA, "BI": TrialRegion.AFRICA, "BJ": TrialRegion.AFRICA, "BW": TrialRegion.AFRICA, "CD": TrialRegion.AFRICA, "CF": TrialRegion.AFRICA, "CG": TrialRegion.AFRICA, "CI": TrialRegion.AFRICA, "CM": TrialRegion.AFRICA,
	"CV": TrialRegion.AFRICA, "DJ": TrialRegion.AFRICA, "DZ": TrialRegion.AFRICA, "EG": TrialRegion.AFRICA, "EH": TrialRegion.AFRICA, "ER": TrialRegion.AFRICA, "ET": TrialRegion.AFRICA, "GA": TrialRegion.AFRICA, "GH": TrialRegion.AFRICA, "GM": TrialRegion.AFRICA,
	"GN": TrialRegion.AFRICA, "GQ": TrialRegion.AFRICA, "GW": TrialRegion.AFRICA, "IO": TrialRegion.AFRICA, "KE": TrialRegion.AFRICA, "KM": TrialRegion.AFRICA, "LR": TrialRegion.AFRICA, "LS": TrialRegion.AFRICA, "LY": TrialRegion.AFRICA, "MA": TrialRegion.AFRICA,
	"MG": TrialRegion.AFRICA, "ML": TrialRegion.AFRICA, "MR": TrialRegion.AFRICA, "MU": TrialRegion.AFRICA, "MW": TrialRegion.AFRICA, "MZ": TrialRegion.AFRICA, "NA": TrialRegion.AFRICA, "NE": TrialRegion.AFRICA, "NG": TrialRegion.AFRICA, "RE": TrialRegion.AFRICA,
	"RW": TrialRegion.AFRICA, "SC": TrialRegion.AFRICA, "SD": TrialRegion.AFRICA, "SH": TrialRegion.AFRICA, "SL": TrialRegion.AFRICA, "SN": TrialRegion.AFRICA, "SO": TrialRegion.AFRICA, "SS": TrialRegion.AFRICA, "ST": TrialRegion.AFRICA, "SZ": TrialRegion.AFRICA,
	"TD": TrialRegion.AFRICA, "TG": TrialRegion.AFRICA, "TN": TrialRegion.AFRICA, "TZ": TrialRegion.AFRICA, "UG": TrialRegion.AFRICA, "YT": TrialRegion.AFRICA, "ZA": TrialRegion.AFRICA, "ZM": TrialRegion.AFRICA, "ZW": TrialRegion.AFRICA,
	# Asia
	"AE": TrialRegion.ASIA, "AF": TrialRegion.ASIA, "AM": TrialRegion.ASIA, "AZ": TrialRegion.ASIA, "BD": TrialRegion.ASIA, "BH": TrialRegion.ASIA, "BN": TrialRegion.ASIA, "BT": TrialRegion.ASIA, "CN": TrialRegion.ASIA, "CY": TrialRegion.ASIA,
	"GE": TrialRegion.ASIA, "HK": TrialRegion.ASIA, "ID": TrialRegion.ASIA, "IL": TrialRegion.ASIA, "IN": TrialRegion.ASIA, "IQ": TrialRegion.ASIA, "IR": TrialRegion.ASIA, "JO": TrialRegion.ASIA, "JP": TrialRegion.ASIA, "KG": TrialRegion.ASIA,
	"KH": TrialRegion.ASIA, "KP": TrialRegion.ASIA, "KR": TrialRegion.ASIA, "KW": TrialRegion.ASIA, "KZ": TrialRegion.ASIA, "LA": TrialRegion.ASIA, "LB": TrialRegion.ASIA, "LK": TrialRegion.ASIA, "MM": TrialRegion.ASIA, "MN": TrialRegion.ASIA,
	"MO": TrialRegion.ASIA, "MV": TrialRegion.ASIA, "MY": TrialRegion.ASIA, "NP": TrialRegion.ASIA, "OM": TrialRegion.ASIA, "PH": TrialRegion.ASIA, "PK": TrialRegion.ASIA, "PS": TrialRegion.ASIA, "QA": TrialRegion.ASIA, "SA": TrialRegion.ASIA,
	"SG": TrialRegion.ASIA, "SY": TrialRegion.ASIA, "TH": TrialRegion.ASIA, "TJ": TrialRegion.ASIA, "TL": TrialRegion.ASIA, "TM": TrialRegion.ASIA, "TR": TrialRegion.ASIA, "TW": TrialRegion.ASIA, "UZ": TrialRegion.ASIA, "VN": TrialRegion.ASIA,
	"YE": TrialRegion.ASIA,
	# Europe
	"AD": TrialRegion.EUROPE, "AL": TrialRegion.EUROPE, "AT": TrialRegion.EUROPE, "AX": TrialRegion.EUROPE, "BA": TrialRegion.EUROPE, "BE": TrialRegion.EUROPE, "BG": TrialRegion.EUROPE, "BY": TrialRegion.EUROPE, "CH": TrialRegion.EUROPE, "CZ": TrialRegion.EUROPE,
	"DE": TrialRegion.EUROPE, "DK": TrialRegion.EUROPE, "EE": TrialRegion.EUROPE, "ES": TrialRegion.EUROPE, "FI": TrialRegion.EUROPE, "FO": TrialRegion.EUROPE, "FR": TrialRegion.EUROPE, "GB": TrialRegion.EUROPE, "GG": TrialRegion.EUROPE, "GI": TrialRegion.EUROPE,
	"GR": TrialRegion.EUROPE, "HR": TrialRegion.EUROPE, "HU": TrialRegion.EUROPE, "IE": TrialRegion.EUROPE, "IM": TrialRegion.EUROPE, "IS": TrialRegion.EUROPE, "IT": TrialRegion.EUROPE, "JE": TrialRegion.EUROPE, "LI": TrialRegion.EUROPE, "LT": TrialRegion.EUROPE,
	"LU": TrialRegion.EUROPE, "LV": TrialRegion.EUROPE, "MC": TrialRegion.EUROPE, "MD": TrialRegion.EUROPE, "ME": TrialRegion.EUROPE, "MK": TrialRegion.EUROPE, "MT": TrialRegion.EUROPE, "NL": TrialRegion.EUROPE, "NO": TrialRegion.EUROPE, "PL": TrialRegion.EUROPE,
	"PT": TrialRegion.EUROPE, "RO": TrialRegion.EUROPE, "RS": TrialRegion.EUROPE, "RU": TrialRegion.EUROPE, "SE": TrialRegion.EUROPE, "SI": TrialRegion.EUROPE, "SJ": TrialRegion.EUROPE, "SK": TrialRegion.EUROPE, "SM": TrialRegion.EUROPE, "UA": TrialRegion.EUROPE,
	"VA": TrialRegion.EUROPE,
	# North America (incl. Central America & Caribbean)
	"AG": TrialRegion.NORTH_AMERICA, "AI": TrialRegion.NORTH_AMERICA, "AW": TrialRegion.NORTH_AMERICA, "BB": TrialRegion.NORTH_AMERICA, "BL": TrialRegion.NORTH_AMERICA, "BM": TrialRegion.NORTH_AMERICA, "BQ": TrialRegion.NORTH_AMERICA, "BS": TrialRegion.NORTH_AMERICA, "BZ": TrialRegion.NORTH_AMERICA, "CA": TrialRegion.NORTH_AMERICA,
	"CR": TrialRegion.NORTH_AMERICA, "CU": TrialRegion.NORTH_AMERICA, "CW": TrialRegion.NORTH_AMERICA, "DM": TrialRegion.NORTH_AMERICA, "DO": TrialRegion.NORTH_AMERICA, "GD": TrialRegion.NORTH_AMERICA, "GL": TrialRegion.NORTH_AMERICA, "GP": TrialRegion.NORTH_AMERICA, "GT": TrialRegion.NORTH_AMERICA, "HN": TrialRegion.NORTH_AMERICA,
	"HT": TrialRegion.NORTH_AMERICA, "JM": TrialRegion.NORTH_AMERICA, "KN": TrialRegion.NORTH_AMERICA, "KY": TrialRegion.NORTH_AMERICA, "LC": TrialRegion.NORTH_AMERICA, "MF": TrialRegion.NORTH_AMERICA, "MQ": TrialRegion.NORTH_AMERICA, "MS": TrialRegion.NORTH_AMERICA, "MX": TrialRegion.NORTH_AMERICA, "NI": TrialRegion.NORTH_AMERICA,
	"PA": TrialRegion.NORTH_AMERICA, "PM": TrialRegion.NORTH_AMERICA, "PR": TrialRegion.NORTH_AMERICA, "SV": TrialRegion.NORTH_AMERICA, "SX": TrialRegion.NORTH_AMERICA, "TC": TrialRegion.NORTH_AMERICA, "TT": TrialRegion.NORTH_AMERICA, "US": TrialRegion.NORTH_AMERICA, "VC": TrialRegion.NORTH_AMERICA, "VG": TrialRegion.NORTH_AMERICA,
	"VI": TrialRegion.NORTH_AMERICA,
	# South America
	"AR": TrialRegion.SOUTH_AMERICA, "BO": TrialRegion.SOUTH_AMERICA, "BR": TrialRegion.SOUTH_AMERICA, "CL": TrialRegion.SOUTH_AMERICA, "CO": TrialRegion.SOUTH_AMERICA, "EC": TrialRegion.SOUTH_AMERICA, "FK": TrialRegion.SOUTH_AMERICA, "GF": TrialRegion.SOUTH_AMERICA, "GS": TrialRegion.SOUTH_AMERICA, "GY": TrialRegion.SOUTH_AMERICA,
	"PE": TrialRegion.SOUTH_AMERICA, "PY": TrialRegion.SOUTH_AMERICA, "SR": TrialRegion.SOUTH_AMERICA, "UY": TrialRegion.SOUTH_AMERICA, "VE": TrialRegion.SOUTH_AMERICA,
	# Oceania
	"AS": TrialRegion.OCEANIA, "AU": TrialRegion.OCEANIA, "CC": TrialRegion.OCEANIA, "CK": TrialRegion.OCEANIA, "CX": TrialRegion.OCEANIA, "FJ": TrialRegion.OCEANIA, "FM": TrialRegion.OCEANIA, "GU": TrialRegion.OCEANIA, "KI": TrialRegion.OCEANIA, "MH": TrialRegion.OCEANIA,
	"MP": TrialRegion.OCEANIA, "NC": TrialRegion.OCEANIA, "NF": TrialRegion.OCEANIA, "NR": TrialRegion.OCEANIA, "NU": TrialRegion.OCEANIA, "NZ": TrialRegion.OCEANIA, "PF": TrialRegion.OCEANIA, "PG": TrialRegion.OCEANIA, "PN": TrialRegion.OCEANIA, "PW": TrialRegion.OCEANIA,
	"SB": TrialRegion.OCEANIA, "TK": TrialRegion.OCEANIA, "TO": TrialRegion.OCEANIA, "TV": TrialRegion.OCEANIA, "VU": TrialRegion.OCEANIA, "WF": TrialRegion.OCEANIA, "WS": TrialRegion.OCEANIA,
}

# Literal region/continent tokens seen verbatim in WHO ICTRP `countries` values (a trial can
# be tagged with a whole continent instead of, or alongside, specific countries). These route
# to normalize_regions rather than the country list — see normalize_countries step 2.
# "asia"/"africa" are defensive additions beyond the observed inventory (cheap, low-risk).
_REGION_TOKENS: dict[str, str] = {
	"europe": TrialRegion.EUROPE,
	"european union": TrialRegion.EUROPE,
	"north america": TrialRegion.NORTH_AMERICA,
	"south america": TrialRegion.SOUTH_AMERICA,
	"oceania": TrialRegion.OCEANIA,
	"asia(except japan)": TrialRegion.ASIA,
	"asia (except japan)": TrialRegion.ASIA,
	"asia": TrialRegion.ASIA,
	"africa": TrialRegion.AFRICA,
}

# Exact-match lookup for raw country tokens that don't round-trip through django_countries'
# own name lookup: old/alternate names, typos, UK subdivisions, and "US"/"USA" abbreviations.
# Keys are whitespace-collapsed, casefolded, trailing-punctuation-stripped tokens. Seeded
# from a token-frequency inventory of the production `countries` column (2026-07 audit).
# Extend this table (not the django_countries fallback) for the next unmapped spelling.
_COUNTRY_EXACT_MATCHES: dict[str, str] = {
	# United States
	"united states": "US",
	"united states of america": "US",
	"us": "US",
	"usa": "US",
	# United Kingdom + constituent countries (WHO reports these as separate "countries")
	"united kingdom": "GB",
	"england": "GB",
	"scotland": "GB",
	"wales": "GB",
	"northern ireland": "GB",
	"united kindgdom": "GB",  # typo, seen verbatim in WHO ICTRP export
	# Turkey / Türkiye (CTGov v2 uses the localized display name)
	"turkey (türkiye)": "TR",
	"turkey": "TR",
	"türkiye": "TR",
	"turkiye": "TR",
	# Iran
	"iran (islamic republic of)": "IR",
	"iran": "IR",
	# Orphan fragment: in a CTGov-style ", "-joined list, "Iran, Islamic Republic of" splits
	# into "Iran" (already resolves alone, so the tokenizer's adjacency re-join never fires —
	# see _tokenize_countries_value) and this leftover "Islamic Republic of" segment. Mapping
	# it directly to IR is simpler than special-casing the re-join condition; the duplicate
	# IR from "Iran" collapses away in normalize_countries' final dedup.
	"islamic republic of": "IR",
	# Czechia
	"czechia": "CZ",
	"czech republic": "CZ",
	# South Korea
	"south korea": "KR",
	"korea, republic of": "KR",
	"republic of korea": "KR",
	# North Korea (django_countries' own name, "Korea (the Democratic People's Republic of)",
	# doesn't match either spelling actually sent by WHO ICTRP)
	"korea (the democratic peoples republic of)": "KP",
	"korea, democratic people's republic of": "KP",
	# Russia
	"russia": "RU",
	"russian federation": "RU",
	# Netherlands
	"netherlands": "NL",
	"the netherlands": "NL",
	# China
	"china": "CN",
	"people's republic of china": "CN",
	"chian": "CN",  # typo, seen verbatim in WHO ICTRP export
	# Common names that differ from django_countries' ISO official long name (which the
	# _name_to_code_lookup() fallback matches against), seen verbatim in WHO ICTRP exports.
	"moldova": "MD",
	"republic of moldova": "MD",
	"moldova, republic of": "MD",
	"taiwan": "TW",  # django_countries: "Taiwan (Province of China)"
	"macedonia": "MK",  # django_countries: "North Macedonia"
	"macedonia, the former yugoslav republic of": "MK",
	"the former yugoslav republic of macedonia": "MK",
	"the former yugoslav rep of macedonia": "MK",
	"republic of serbia": "RS",
	"venezuela": "VE",  # django_countries: "Venezuela (Bolivarian Republic of)"
	"syria": "SY",  # django_countries: "Syrian Arab Republic"
	"vietnam": "VN",  # django_countries: "Viet Nam"
	# Ambiguous without a qualifier (could be US or British Virgin Islands); defaults to the
	# U.S. territory as the more commonly reported one in trial site/recruitment data.
	"virgin islands": "VI",
	# One-off WHO ICTRP noise
	"modalvia": "MD",  # typo for Moldova
	"bosnial and herzegovina": "BA",  # typo for Bosnia and Herzegovina
	"italia": "IT",
	# "Former Serbia and Montenegro" (dissolved 2006, no clean current ISO code),
	# "none"/"Other" (not a country) — deliberately absent so they log as unmapped and
	# are dropped, rather than guessing at a mapping.
}


@lru_cache(maxsize=1)
def _name_to_code_lookup() -> dict[str, str]:
	"""Casefolded django_countries display name -> alpha-2 code, built lazily (and cached)
	so importing this module never forces django_countries to render translated strings
	before Django's settings are ready."""
	from django_countries.data import COUNTRIES

	return {str(name).strip().casefold(): code for code, name in COUNTRIES.items()}


@lru_cache(maxsize=1)
def _valid_iso_codes() -> frozenset:
	from django_countries.data import COUNTRIES

	return frozenset(COUNTRIES.keys())


def _clean_token(token: str) -> str:
	"""Whitespace-collapse, casefold, and strip trailing separator punctuation from a
	single country/region token (but keep internal punctuation like the parentheses in
	"Asia(except Japan)" or "Iran (Islamic Republic of)")."""
	return re.sub(r"\s+", " ", token).strip().casefold().rstrip(" ;,.")


def _known_token(cleaned: str) -> bool:
	"""True if *cleaned* (already run through _clean_token) resolves to a country or a
	region on its own — used by the tokenizer to decide where to split a multi-value
	string and to protect names that contain an internal comma (e.g. "Korea, Republic
	of")."""
	return (
		cleaned in _COUNTRY_EXACT_MATCHES
		or cleaned in _REGION_TOKENS
		or cleaned in _name_to_code_lookup()
	)


def _tokenize_countries_value(value: str | None) -> list[str]:
	"""Split one source's raw countries string into individual country/region tokens.

	Order of operations:
	1. Whole value matches a known country/region on its own -> single token. Protects
	   comma-containing names stored alone (WHO's "Korea, Republic of").
	2. Semicolon present -> split on ";" (WHO ICTRP's separator; tolerates a trailing ";").
	3. Otherwise split on comma (CTGov's ", " separator; also tolerates a bare "," with no
	   following space), then re-join adjacent fragments that only resolve to a known
	   country/region when combined — defensively covers a comma-containing name
	   appearing inside a longer multi-country list.
	"""
	if not value:
		return []
	cleaned = re.sub(r"\s+", " ", value).strip()
	if not cleaned:
		return []

	if _known_token(_clean_token(cleaned)):
		return [cleaned]

	if ";" in cleaned:
		return [p.strip() for p in cleaned.split(";") if p.strip()]

	raw_parts = [p.strip() for p in re.split(r",\s*", cleaned) if p.strip()]
	parts: list[str] = []
	i = 0
	while i < len(raw_parts):
		current = raw_parts[i]
		if i + 1 < len(raw_parts):
			combined = f"{current}, {raw_parts[i + 1]}"
			if _known_token(_clean_token(combined)) and not _known_token(
				_clean_token(current)
			):
				parts.append(combined)
				i += 2
				continue
		parts.append(current)
		i += 1
	return parts


def _map_token(token: str) -> tuple:
	"""Map one cleaned token to (country_code, None) or (None, region_slug); (None, None)
	when unmapped (logged so the review queue can extend the tables above)."""
	cleaned = _clean_token(token)
	if not cleaned:
		return None, None
	if cleaned in _COUNTRY_EXACT_MATCHES:
		return _COUNTRY_EXACT_MATCHES[cleaned], None
	if cleaned in _REGION_TOKENS:
		return None, _REGION_TOKENS[cleaned]
	code = _name_to_code_lookup().get(cleaned)
	if code:
		return code, None
	logger.info("Unmapped trial country value: %r", token)
	return None, None


_COUNTRY_STATUS_NAME_RE = re.compile(r"([A-Za-z][A-Za-z ]*):")


def _parse_country_status(text: str | None) -> list:
	"""Parse EU CTIS `country_status` into (country name, status text) pairs.

	Format: "Spain:Authorised, recruitment pending, France:Authorised, recruitment
	pending, Italy:Ongoing, recruiting" — status text itself contains commas, so this
	cannot be comma-split. Instead every "Name:" anchor (a run of letters/spaces
	immediately followed by a colon) is located with a regex; the status text for each
	country is everything between its anchor and the next one (or end of string), with
	the trailing ", " separator trimmed off.
	"""
	if not text:
		return []
	matches = list(_COUNTRY_STATUS_NAME_RE.finditer(text))
	results = []
	for i, match in enumerate(matches):
		name = match.group(1).strip()
		start = match.end()
		end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
		value = text[start:end].strip().rstrip(", ").strip()
		if name and value:
			results.append((name, value))
	return results


def _parse_decision_date(value) -> str | None:
	"""Best-effort ISO-8601 date string from a countries_decision_date value (already a
	date/ISO-string per the EU CTIS parser — gregory/classes.py EUTrialParser.parse_summary)."""
	if not value:
		return None
	if hasattr(value, "isoformat"):
		return value.isoformat()
	text = str(value).strip()
	return text or None


def normalize_countries(
	countries_by_source: dict | None,
	countries: str | None,
	country_status: str | None,
	countries_decision_date: dict | None,
	countries_recruitment_date: dict | None = None,
) -> list | None:
	"""
	Compute the canonical per-country rows for a trial from every raw input that can
	mention a country, as a union (never a last-writer-wins overwrite — these rows become
	the per-country `TrialCountry` rows; see docs/trials-field-normalization.md):

	1. `countries_decision_date` keys — already ISO alpha-2, validated against the ISO
	   list; attaches `decision_date`, source `ctis`.
	2. `countries_by_source` — each key's value tokenized and mapped, tagged with that
	   key as the source. Falls back to the legacy `countries` column (format-detected:
	   ";" -> "ictrp", else "ctgov") for rows not yet seeded with `countries_by_source`.
	3. `country_status` — per-country status parsed out and mapped through
	   `normalize_recruitment_status` (same vocabulary as the trial-level recruitment
	   status), source `ctis`.
	4. `countries_recruitment_date` keys — already ISO alpha-2, validated against the ISO
	   list (same style as `countries_decision_date`); attaches `recruitment_start_date`,
	   source `ctis`. Only attaches to a country already present from another input, or
	   creates one — same union semantics as the other inputs.

	Returns a list of dicts sorted by country code:
	``{"country": "DE", "status": "recruiting", "status_raw": "...",
	   "decision_date": "2024-07-19", "recruitment_start_date": "2024-08-01",
	   "sources": ["ctgov", "ctis"]}``
	Returns None when every input is empty (mirrors the other normalizers' None-for-empty
	convention) rather than an empty list.
	"""
	rows: dict[str, dict] = {}

	def ensure(code: str) -> dict:
		return rows.setdefault(
			code,
			{
				"country": code,
				"status": None,
				"status_raw": None,
				"decision_date": None,
				"recruitment_start_date": None,
				"sources": [],
			},
		)

	def add_source(code: str, source: str) -> None:
		row = ensure(code)
		if source not in row["sources"]:
			row["sources"].append(source)

	# 1. countries_decision_date: already alpha-2 keys.
	valid_codes = _valid_iso_codes()
	for raw_code, date_value in (countries_decision_date or {}).items():
		code = (raw_code or "").strip().upper()
		if code not in valid_codes:
			logger.info("Unmapped trial country decision-date key: %r", raw_code)
			continue
		row = ensure(code)
		row["decision_date"] = _parse_decision_date(date_value)
		add_source(code, "ctis")

	# 2. countries_by_source, falling back to the legacy `countries` column (format
	# detection) for trials not yet seeded with countries_by_source.
	by_source = dict(countries_by_source or {})
	if not by_source and countries:
		fallback_key = "ictrp" if ";" in countries else "ctgov"
		by_source = {fallback_key: countries}

	for source_key, value in by_source.items():
		for token in _tokenize_countries_value(value):
			code, _region = _map_token(token)
			if code:
				add_source(code, source_key)

	# 3. country_status (EU CTIS): per-country recruitment status.
	for name, status_raw in _parse_country_status(country_status):
		code, _region = _map_token(name)
		if not code:
			continue
		row = ensure(code)
		row["status_raw"] = status_raw
		row["status"] = normalize_recruitment_status(status_raw)
		add_source(code, "ctis")

	# 4. countries_recruitment_date: already alpha-2 keys (same validation as #1).
	for raw_code, date_value in (countries_recruitment_date or {}).items():
		code = (raw_code or "").strip().upper()
		if code not in valid_codes:
			logger.info("Unmapped trial country recruitment-date key: %r", raw_code)
			continue
		row = ensure(code)
		row["recruitment_start_date"] = _parse_decision_date(date_value)
		add_source(code, "ctis")

	if not rows:
		return None

	result = []
	for code in sorted(rows):
		row = rows[code]
		row["sources"] = sorted(row["sources"])
		result.append(row)
	return result


def normalize_regions(
	country_codes: list | None, raw_countries: str | None
) -> list | None:
	"""
	Compute the sorted list of region slugs for a trial from its normalized country codes
	plus a secondary scan of the raw `countries` text for literal region/continent tokens
	(WHO ICTRP sometimes tags a trial with a whole continent, e.g. "Europe" or
	"Asia(except Japan)", instead of, or alongside, specific countries). This is the
	derived `regions_normalized` list — see docs/trials-field-normalization.md.

	Returns None when no region can be determined (mirrors the other normalizers).
	"""
	regions = set()
	for code in country_codes or []:
		region = _COUNTRY_TO_REGION.get(code)
		if region:
			regions.add(region)

	if raw_countries:
		cleaned = re.sub(r"\s+", " ", raw_countries).strip()
		for chunk in re.split(r"[;,]", cleaned):
			token = _clean_token(chunk)
			region = _REGION_TOKENS.get(token)
			if region:
				regions.add(region)

	return sorted(regions) if regions else None


def _compute_regions_from_raw(
	countries_by_source: dict | None,
	countries: str | None,
	country_status: str | None,
	countries_decision_date: dict | None,
	countries_recruitment_date: dict | None = None,
) -> list | None:
	"""Glue function registered in NORMALIZED_TRIAL_FIELDS: derives the country list from
	the same five raw inputs as normalize_countries, then reduces it to regions. Kept
	separate from normalize_regions so that function's own signature/tests stay in terms
	of (country_codes, raw_countries) as specified, independent of how the country list
	itself gets computed."""
	rows = normalize_countries(
		countries_by_source,
		countries,
		country_status,
		countries_decision_date,
		countries_recruitment_date,
	)
	country_codes = [row["country"] for row in rows] if rows else []
	return normalize_regions(country_codes, countries)


def raw_field_names(raw_fields) -> tuple:
	"""Normalize a NORMALIZED_TRIAL_FIELDS raw-fields spec (a single field name, or a
	tuple of field names for a multi-input derived field) to a tuple of field names."""
	return (raw_fields,) if isinstance(raw_fields, str) else tuple(raw_fields)


# Drives Trials.save() and the generalized backfill/admin-recompute commands: each entry is
# (raw field(s), derived field name, normalizer function). The raw-field slot is a single
# field name (str) for a single-input field (phase, recruitment_status), or a tuple of field
# names for a multi-input field (regions_normalized derives from four raw columns) — use
# raw_field_names() to normalize either shape, and call normalizer(*raw_values) either way.
# Add a new entry here — plus the matching model field in gregory/models.py — for the next
# derived field (study_type, ...).
NORMALIZED_TRIAL_FIELDS = (
	("phase", "phase_normalized", normalize_phase),
	("recruitment_status", "recruitment_status_normalized", normalize_recruitment_status),
	("study_type", "study_type_normalized", normalize_study_type),
	("inclusion_gender", "inclusion_gender_normalized", normalize_inclusion_gender),
	(
		(
			"countries_by_source",
			"countries",
			"country_status",
			"countries_decision_date",
			"countries_recruitment_date",
		),
		"regions_normalized",
		_compute_regions_from_raw,
	),
)


# --- Sponsor canonicalization ------------------------------------------------------
#
# Unlike the fields above, sponsor resolution is NOT registered in NORMALIZED_TRIAL_FIELDS:
# it requires DB lookups (SponsorAlias -> Sponsor) rather than a pure raw-value mapping, so
# it follows the sync_trial_countries() precedent instead (see Trials.save() and
# Trials._resolve_primary_sponsor() in gregory/models.py). Only the two pure helpers below
# — normalize_sponsor_key and map_sponsor_type — live here.


class SponsorType(models.TextChoices):
	INDUSTRY = "industry", "Industry"
	ACADEMIC_MEDICAL = "academic_medical", "Academic / medical"
	GOVERNMENT = "government", "Government"
	NONPROFIT = "nonprofit", "Non-profit / patient organisation"
	OTHER = "other", "Other"
	# null = unknown / no signal, matching the phase_normalized convention


def normalize_sponsor_key(raw: str | None) -> str | None:
	"""
	Compute the alias lookup key for a raw Trials.primary_sponsor value.

	Deliberately conservative: only merges spellings that cannot belong to different
	real-world entities (whitespace, case, diacritics, "&"/"and", punctuation anywhere
	in the string — commas, periods, hyphens, parentheses, apostrophes, slashes). It
	never strips legal suffixes (Ltd/Inc/GmbH/AG...) — "Novartis Pharma AG" and
	"Novartis Pharma GmbH" keep distinct keys; grouping them under one canonical Sponsor
	is an editorial decision that belongs in the seed table / admin merge, never in this
	function. It also never does substring matching — "University of Rochester" and
	"Roche" must never collide (see TRIALS-SPONSOR-CANONICALIZATION-PLAN.md merge traps).

	Returns None for missing/blank input.
	"""
	if raw is None or not raw.strip():
		return None

	cleaned = re.sub(r"\s+", " ", raw).strip()
	cleaned = cleaned.casefold()
	cleaned = unicodedata.normalize("NFKD", cleaned)
	cleaned = "".join(ch for ch in cleaned if not unicodedata.combining(ch))
	cleaned = cleaned.replace("&", " and ")
	cleaned = re.sub(r"[^0-9a-z ]+", " ", cleaned)
	cleaned = re.sub(r"\s+", " ", cleaned).strip()
	return cleaned[:500] or None


# CTIS sponsorType raw values are sometimes comma-duplicated ("Pharmaceutical company,
# Pharmaceutical company") — map_sponsor_type takes the first token before any comma.
_CTIS_SPONSOR_TYPE_MAP: dict[str, str] = {
	"pharmaceutical company": SponsorType.INDUSTRY,
	"industry": SponsorType.INDUSTRY,
	"hospital/clinic/other health care facility": SponsorType.ACADEMIC_MEDICAL,
	"laboratory/research/testing facility": SponsorType.ACADEMIC_MEDICAL,
	"educational institution": SponsorType.ACADEMIC_MEDICAL,
	"patient organisation/association": SponsorType.NONPROFIT,
}

# CTGov leadSponsor.class -> SponsorType, for the values that map to something. INDIV,
# NETWORK, OTHER, AMBIG, UNKNOWN (and missing) fall through to the next priority tier —
# see map_sponsor_type.
_CTGOV_CLASS_MAP: dict[str, str] = {
	"INDUSTRY": SponsorType.INDUSTRY,
	"NIH": SponsorType.GOVERNMENT,
	"FED": SponsorType.GOVERNMENT,
	"OTHER_GOV": SponsorType.GOVERNMENT,
}

# Keyword rules on the sponsor display name, applied in this fixed order — academic/medical
# first (so an NHS Foundation Trust or a teaching hospital never falls through to the noisy
# "trust"/"foundation" nonprofit bucket), industry last (its patterns are the noisiest —
# generic legal-entity suffixes like "AG"/"Inc"/"Ltd" — so it only wins when nothing more
# specific matched). Word-boundary regexes, matched case-insensitively against the
# whitespace-collapsed name.
_ACADEMIC_MEDICAL_RE = re.compile(
	r"\b(universit(?:y|e|é|aire|ario|ätsklinikum)|hospital|clinic(?:al)?|klinikum|"
	r"krankenhaus|polyclinic|medical (?:center|centre|school)|school of medicine|"
	r"college|nhs|teaching hospital|health system|faculty of medicine|academic)\b",
	re.IGNORECASE,
)
_GOVERNMENT_RE = re.compile(
	r"\b(ministry|national institutes?|\bnih\b|centers? for disease control|"
	r"\bcdc\b|federal|government|public health (?:agency|authority|service|england)|"
	r"food and drug administration|\bfda\b|health canada|department of health|"
	r"national health service|institut national)\b",
	re.IGNORECASE,
)
_NONPROFIT_RE = re.compile(
	r"\b(foundation|society|association|trust|stiftung|charity|charitable|"
	r"patients? organi[sz]ation|alliance|coalition)\b",
	re.IGNORECASE,
)
_INDUSTRY_RE = re.compile(
	r"\b(pharma(?:ceutical)?s?|biotech(?:nology)?|therapeutics|biosciences|"
	r"laborator(?:y|ies)|inc\.?|corp(?:oration)?\.?|ltd\.?|llc|gmbh|s\.?a\.?|"
	r"s\.?p\.?a\.?|ag|co\.,? ltd|pvt\.? ltd)\b",
	re.IGNORECASE,
)


def _sponsor_type_from_name(name: str | None) -> str | None:
	"""Keyword classifier over the sponsor display name — the last-resort tier of
	map_sponsor_type. Applies the four buckets above in fixed order; returns the first
	match, or None when nothing matches."""
	if not name:
		return None
	if _ACADEMIC_MEDICAL_RE.search(name):
		return SponsorType.ACADEMIC_MEDICAL
	if _GOVERNMENT_RE.search(name):
		return SponsorType.GOVERNMENT
	if _NONPROFIT_RE.search(name):
		return SponsorType.NONPROFIT
	if _INDUSTRY_RE.search(name):
		return SponsorType.INDUSTRY
	return None


def map_sponsor_type(
	ctgov_class: str | None, ctis_raw: str | None, name: str | None
) -> tuple[str | None, str | None]:
	"""
	Derive a (sponsor_type, source) pair from every signal available for a sponsor,
	applying this priority ladder (first non-None wins):

	1. CTGov ``leadSponsor.class`` (source "ctgov") — authoritative for the largest
	   source. INDIV/NETWORK/OTHER/AMBIG/UNKNOWN/missing fall through to tier 2.
	2. EU CTIS raw ``sponsor_type`` (source "ctis") — first token before any comma
	   (the raw column is sometimes comma-duplicated).
	3. Keyword rules on the sponsor name (source "rules") — see _sponsor_type_from_name.

	Returns (None, None) when no tier produces a match. Never raises.
	"""
	if ctgov_class:
		mapped = _CTGOV_CLASS_MAP.get(ctgov_class.strip().upper())
		if mapped:
			return mapped, "ctgov"

	if ctis_raw:
		first_token = ctis_raw.split(",")[0].strip().casefold()
		mapped = _CTIS_SPONSOR_TYPE_MAP.get(first_token)
		if mapped:
			return mapped, "ctis"

	rule_match = _sponsor_type_from_name(name)
	if rule_match:
		return rule_match, "rules"

	return None, None
