"""Guidance attached to zero-hit search_articles/search_trials/search_patents/search_medicines responses.

Phase 3 of MCP-TELEMETRY-PLAN.md. A product change independent of the
telemetry work: `{"count": 0, "articles": []}` is a dead end for the model
calling the tool, so a zero-hit response gets a `guidance` key naming which
filters were applied and ranking suggestions for what's most likely
over-constraining the search — never the filter *values*, only which
filter names were set, the same boundary telemetry.py holds for
`params_used`.

It is tool-aware (`tool` is "articles" or "trials") and adds `fields_read`,
so a caller doesn't have to parse prose to learn which fields a filter
actually searched.
"""

from __future__ import annotations

from typing import Any

# Mirrors api.utils.search.ARTICLE_SEARCH_FIELDS / TRIAL_SEARCH_FIELDS in the
# Django repo (django/api/utils/search.py). The MCP server runs in its own
# container and can't import Django, so these are kept in sync by hand.
ARTICLE_SEARCH_FIELDS = ("title", "summary")
TRIAL_SEARCH_FIELDS = ("title", "summary", "scientific_title")
PATENT_SEARCH_FIELDS = ("title", "summary")  # claims are never searched

# Pagination/ordering controls aren't filters that can cause a zero-hit
# result on their own — excluded from the "applied filters" list so it
# only ever names things that narrow the result set.
#
# `include_subject_links` is a display option, not a filter: on its own it
# narrows nothing (it only widens which links `has_trial_link` /
# `has_patent_link` count), so it is neither an "applied filter" nor a reason
# for a suggestion.
_NON_FILTER_ARGS = frozenset({"page", "page_size", "ordering", "include_subject_links"})

# `fields_read` covers only the text-matching filters: `search` (both
# tools), and for trials also `acronym` and the registry-ID filters (`nct`,
# `euct`, `eudract`, `ctis`). Those are where a zero hit usually means the
# term sits in a field the filter doesn't read. Every other filter is
# deliberately left out — being absent from this map says nothing about
# which fields it reads, and several don't read a same-named field at all:
# trials' `has_results` reads results_posted/results_date_completed/
# results_url_link/results_yes_no (TrialFilter.filter_has_results), and
# `country` matches related TrialCountry rows (TrialFilter.filter_country),
# not a field on Trial itself. Keyed by tool because `search`'s coverage
# differs between the two — see TRIAL_SEARCH_FIELDS above.
#
# The registry-ID filters match `identifiers_normalized`, which Django
# derives from three raw columns: `identifiers`, `secondary_id` and
# `ctg_secondary_ids` (see normalize_trial_identifiers in
# django/gregory/utils/trial_identifiers.py).
_TRIAL_IDENTIFIER_FIELDS = ("identifiers", "secondary_id", "ctg_secondary_ids")
_FIELDS_READ_BY_TOOL: dict[str, dict[str, tuple[str, ...]]] = {
	"articles": {
		"search": ARTICLE_SEARCH_FIELDS,
	},
	"trials": {
		"search": TRIAL_SEARCH_FIELDS,
		"acronym": ("acronym",),
		"nct": _TRIAL_IDENTIFIER_FIELDS,
		"euct": _TRIAL_IDENTIFIER_FIELDS,
		"eudract": _TRIAL_IDENTIFIER_FIELDS,
		"ctis": _TRIAL_IDENTIFIER_FIELDS,
	},
	"patents": {
		"search": PATENT_SEARCH_FIELDS,
		# Every member publication of the family, not only the representative one.
		"publication_number": ("publications",),
	},
}

# (matching arg names, suggestion). Never quotes the filter *values* — only
# names which filter args are involved, the same boundary telemetry.py holds
# for `params_used`.
_RELEVANCE_RULE = (
	("relevant", "ml_threshold", "ml_score_min"),
	"Drop `relevant`/`ml_threshold`/`ml_score_min` — ML relevance predictions don't "
	"cover every row, and a high threshold can exclude rows that would otherwise match.",
)

_DATE_RULE = (
	("published_date_after", "published_date_before", "last_days",
	 "date_registration_after", "date_registration_before"),
	"Widen or drop the date filter — a narrow or recent-only window is the most "
	"common cause of an empty page.",
)

_TAXONOMY_RULE = (
	("subject_id", "category_id", "category_slug", "category_modality"),
	"Call list_subjects/list_categories first to confirm the ID or slug is "
	"correct — a typo'd or stale one silently returns zero rows rather than an error.",
)

_REGISTRY_ID_RULE = (
	("nct", "euct", "eudract", "ctis"),
	"Registry-ID filters match IDs from the trial's registry record, its "
	"secondary IDs and the sponsor's study code, in any common format. No "
	"match usually means the corpus has no record linked to that ID — try "
	"`search` with the trial's name or acronym.",
)

_ACRONYM_RULE = (
	("acronym",),
	"`acronym` is empty for most trials from registries other than "
	"ClinicalTrials.gov. Try `search` with the acronym instead — it reads "
	"the scientific title, where those registries usually put it.",
)

_SPONSOR_RULE = (
	("sponsor_id", "sponsor_slug"),
	"Double-check the sponsor — an unrecognized sponsor_id/sponsor_slug "
	"returns zero rows rather than an error.",
)

_PATENT_DATE_RULE = (
	("priority_date_after", "priority_date_before", "publication_date_after", "publication_date_before"),
	"Widen or drop the priority/publication date range — a narrow window is the "
	"most common cause of an empty page. Priority date is when the invention was "
	"first filed; publication date is usually 18 months later.",
)

_GRANT_RULE = (
	("has_grant",),
	"Drop `has_grant` — it is a heuristic on kind codes, and most families are "
	"still applications.",
)

_CPC_RULE = (
	("cpc",),
	"`cpc` is a prefix match on a class like A61K31 or A61P25/28, with no spaces. "
	"Try a shorter prefix (A61K) or drop it.",
)

_PUBLICATION_NUMBER_RULE = (
	("publication_number",),
	"`publication_number` matches any publication of the family. Without a kind "
	"code (EP4123456) it matches every kind; a number with letters inside it "
	"(MXPA02011163A) must be given whole. Check the country code and digits.",
)

def _link_rule_text(name: str):
	"""The suggestion for a trial-link filter, worded for whether the caller
	already widened it with `include_subject_links` (a rule's text may be a
	function of the call's params)."""

	def text(params: dict[str, Any]) -> str:
		if params.get("include_subject_links"):
			return (
				f"Drop `{name}` — with `include_subject_links` it already counts links on a "
				"shared subject too, so no sponsor-timing link exists for what is left."
			)
		return (
			f"Drop `{name}` — only links on a shared category count unless "
			"`include_subject_links` is true, so one linked through a shared subject "
			"alone does not match."
		)

	return text


_TRIAL_LINK_RULE = (("has_trial_link",), _link_rule_text("has_trial_link"))

_PATENT_LINK_RULE = (
	("has_patent_link",),
	lambda params: _link_rule_text("has_patent_link")(params)
	+ " On a site that has no patents it matches nothing.",
)

_APPLICANT_RULE = (
	("applicant", "applicant_sponsor_id", "applicant_sponsor_slug"),
	"`applicant` is a case-insensitive substring of the name as published; "
	"`applicant_sponsor_id`/`applicant_sponsor_slug` match only applicants linked "
	"to a canonical sponsor. Try a shorter name, or the other filter.",
)

_PATENT_SEARCH_RULE = (
	("search",),
	"`search` reads title and summary only — never the claims. If the query "
	"uses AND/parentheses, try loosening it — an OR, or a narrower single term.",
)

_ARTICLE_SEARCH_RULE = (
	("search",),
	"`search` reads title and summary only. If the query uses AND/parentheses, "
	"try loosening it — an OR, a narrower single term, or title=/summary= "
	"instead.",
)

_TRIAL_SEARCH_RULE = (
	("search",),
	"`search` reads title, summary and scientific title only — not condition, "
	"intervention or eligibility text. For a disease or drug, try `condition=` "
	"or `intervention=`. If the query uses AND/parentheses, try loosening it.",
)

def _looks_like_a_brand(value: Any) -> bool:
	"""An INN is written in lowercase, one word; a brand name is usually
	capitalised, or has a space, digit or hyphen. A hint only, never a filter."""
	if not isinstance(value, str) or not value.strip():
		return False
	value = value.strip()
	return value[0].isupper() or any(ch.isdigit() or ch in " -/" for ch in value)


def _medicine_substance_text(params: dict[str, Any]) -> str:
	exact = (
		"`substance` is an exact match (ignoring case) on one active substance as the regulator names it, "
		"the INN at EMA (e.g. ocrelizumab) or the ingredient at the FDA, not part of a name."
	)
	if params.get("regulator") != "ema":
		# The FDA writes an ingredient as on its label, often with the salt.
		exact += (
			" The FDA names an ingredient as on its label, sometimes with the salt (fingolimod "
			"hydrochloride, where EMA has fingolimod), so to find the FDA's record also try the salt "
			"form, or `name` with the brand."
		)
	if _looks_like_a_brand(params.get("substance")):
		return (
			"The `substance` value looks like a brand or product name: use `name` instead, which matches "
			"part of the product name, ignoring case. " + exact
		)
	return exact + " If it is a brand or product name, use `name`."


_MEDICINE_SUBSTANCE_RULE = (("substance",), _medicine_substance_text)

_MEDICINE_NAME_RULE = (
	("name",),
	"`name` matches part of the product name the regulator publishes (a brand name, sometimes with a "
	"former name in brackets at EMA; an FDA application sold under several brands lists them all), "
	"ignoring case. If the value is an active substance, use `substance` instead "
	"(the INN, e.g. ocrelizumab).",
)

_MEDICINE_GENERICS_RULE = (
	("substance", "name", "status", "category_id", "category_slug", "subject_id"),
	lambda params: (
		"Generics, biosimilars and duplicate products are already included, so they are not the cause."
		if params.get("include_generics")
		else "Generic and biosimilar products, and duplicate products (further products of the same "
		"regulator for the same medicine as a lead product), are hidden unless `include_generics` is true: "
		"a substance whose only listed products are copies would otherwise look absent."
	),
)

_MEDICINE_STATUS_RULE = (
	("status", "opinion_status"),
	"`status` is the regulator's own wording: EMA's is Authorised, Withdrawn, Refused, Opinion, ...; "
	"the FDA's is Approved, Tentatively approved, Discontinued, Revoked. A value from one does not "
	"match the other's records. `opinion_status` is EMA-only and filled only while a product is at the "
	"opinion stage, so most products have none, and it leaves out every FDA record: drop it, or use "
	"`status`.",
)

_MEDICINE_REGULATOR_RULE = (
	("regulator",),
	"`regulator` keeps only EMA's or only the FDA's records. A medicine can be in one regulator's list "
	"and not the other's, and an FDA record shows only when it is filed under a category of this site: "
	"drop `regulator` to search both.",
)

_MEDICINE_DATE_RULE = (
	("authorised_after", "authorised_before"),
	"These bound the date of FIRST authorisation, not the latest European Commission "
	"decision. Widen or drop them.",
)

_MEDICINE_COVERAGE_RULE = (
	("substance", "name"),
	"EMA lists only medicines authorised centrally for the whole EU, and the FDA data has approved "
	"new drugs and biologics (NDAs and BLAs) but not generics (ANDAs) or applications under review. "
	"This site shows only those filed under its categories or approved for one of its "
	"conditions (EMA records only). A drug that is not here may still be authorised in individual countries or approved "
	"under another route: do not conclude it is not approved.",
)

_MEDICINE_TRACKED_RULE = (
	("tracked",),
	"`tracked=false` keeps only medicines that no category of this site covers (EMA ones only: an FDA "
	"record shows only when filed under a category), and `tracked=true` only those filed under a "
	"category. Drop it to see both.",
)

# Checked in order per tool, so the categories most likely to silently zero
# out an otherwise-good search rank first. Every matching rule fires; `search`
# is last in both because it's the broadest filter, so its suggestion ranks
# after the narrower ones.
_RULES_BY_TOOL: dict[str, tuple[tuple[tuple[str, ...], Any], ...]] = {
	"articles": (
		_RELEVANCE_RULE,
		_DATE_RULE,
		_TAXONOMY_RULE,
		_ARTICLE_SEARCH_RULE,
	),
	"trials": (
		_DATE_RULE,
		_TAXONOMY_RULE,
		_REGISTRY_ID_RULE,
		_ACRONYM_RULE,
		_SPONSOR_RULE,
		_PATENT_LINK_RULE,
		_TRIAL_SEARCH_RULE,
	),
	"patents": (
		_PATENT_DATE_RULE,
		_TAXONOMY_RULE,
		_GRANT_RULE,
		_CPC_RULE,
		_PUBLICATION_NUMBER_RULE,
		_TRIAL_LINK_RULE,
		_APPLICANT_RULE,
		_PATENT_SEARCH_RULE,
	),
	"medicines": (
		_MEDICINE_DATE_RULE,
		_TAXONOMY_RULE,
		_MEDICINE_REGULATOR_RULE,
		_MEDICINE_STATUS_RULE,
		_MEDICINE_TRACKED_RULE,
		_MEDICINE_SUBSTANCE_RULE,
		_MEDICINE_NAME_RULE,
		_MEDICINE_GENERICS_RULE,
		_MEDICINE_COVERAGE_RULE,
	),
}

_FALLBACK_SUGGESTION = "Try a broader search term, or drop optional filters one at a time to see which is excluding everything."


def guidance_for(params: dict[str, Any], tool: str) -> dict[str, Any]:
	"""(applied filter names, ranked suggestions, fields read) for a zero-hit response.

	`params` is the tool's own param dict before None-pruning — only the
	*names* of the non-None entries matter here, never their values.
	`tool` is `"articles"`, `"trials"`, `"patents"` or `"medicines"`: it selects which rule
	set and `fields_read` mapping apply, since `search`'s coverage (and the
	filters specific to each) differ between them.
	"""
	applied = sorted(k for k, v in params.items() if v is not None and k not in _NON_FILTER_ARGS)
	applied_set = set(applied)
	rules = _RULES_BY_TOOL.get(tool, ())
	suggestions = [
		text(params) if callable(text) else text
		for arg_names, text in rules
		if applied_set.intersection(arg_names)
	]
	if not suggestions:
		suggestions = [_FALLBACK_SUGGESTION]
	fields_map = _FIELDS_READ_BY_TOOL.get(tool, {})
	fields_read = {name: list(fields_map[name]) for name in applied if name in fields_map}
	return {
		"applied_filters": applied,
		"suggestions": suggestions,
		"fields_read": fields_read,
	}
