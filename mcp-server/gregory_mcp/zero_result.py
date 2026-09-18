"""Guidance attached to zero-hit search_articles/search_trials responses.

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

# Pagination/ordering controls aren't filters that can cause a zero-hit
# result on their own — excluded from the "applied filters" list so it
# only ever names things that narrow the result set.
_NON_FILTER_ARGS = frozenset({"page", "page_size", "ordering"})

# Fields each filter reads, when that isn't obvious from the arg name
# itself (an id/slug/date filter reads the field it's named after; `search`
# and the registry-ID params don't). Keyed by tool because `search`'s
# coverage differs between the two — see TRIAL_SEARCH_FIELDS above.
_TRIAL_IDENTIFIER_FIELDS = ("identifiers",)
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
}

# (matching arg names, suggestion). Never quotes the filter *values* — only
# names which filter args are involved, the same boundary telemetry.py holds
# for `params_used`.
_RELEVANCE_RULE = (
	("relevant", "ml_threshold"),
	"Drop `relevant`/`ml_threshold` — ML relevance predictions don't cover every "
	"row, and a high threshold can exclude rows that would otherwise match.",
)

_DATE_RULE = (
	("published_date_after", "published_date_before", "last_days",
	 "date_registration_after", "date_registration_before"),
	"Widen or drop the date filter — a narrow or recent-only window is the most "
	"common cause of an empty page.",
)

_TAXONOMY_RULE = (
	("subject_id", "category_id", "category_slug", "category_modality", "team_id"),
	"Call list_subjects/list_categories first to confirm the ID or slug is "
	"correct — a typo'd or stale one silently returns zero rows rather than an error.",
)

_REGISTRY_ID_RULE = (
	("nct", "euct", "eudract", "ctis"),
	"Registry-ID filters match the trial's stored identifiers exactly "
	"(case-insensitive). IDs listed only among a trial's secondary IDs, or "
	"stored with a registry prefix such as EUCTR…, aren't matched.",
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

# Checked in order per tool, so the categories most likely to silently zero
# out an otherwise-good search rank first. Every matching rule fires; `search`
# is last in both because it's the broadest filter, so its suggestion ranks
# after the narrower ones.
_RULES_BY_TOOL: dict[str, tuple[tuple[tuple[str, ...], str], ...]] = {
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
		_TRIAL_SEARCH_RULE,
	),
}

_FALLBACK_SUGGESTION = "Try a broader search term, or drop optional filters one at a time to see which is excluding everything."


def guidance_for(params: dict[str, Any], tool: str) -> dict[str, Any]:
	"""(applied filter names, ranked suggestions, fields read) for a zero-hit response.

	`params` is the tool's own param dict before None-pruning — only the
	*names* of the non-None entries matter here, never their values.
	`tool` is `"articles"` or `"trials"`: it selects which rule set and
	`fields_read` mapping apply, since `search`'s coverage (and a couple of
	trials-only filters) differ between the two.
	"""
	applied = sorted(k for k, v in params.items() if v is not None and k not in _NON_FILTER_ARGS)
	applied_set = set(applied)
	rules = _RULES_BY_TOOL.get(tool, ())
	suggestions = [text for arg_names, text in rules if applied_set.intersection(arg_names)]
	if not suggestions:
		suggestions = [_FALLBACK_SUGGESTION]
	fields_map = _FIELDS_READ_BY_TOOL.get(tool, {})
	fields_read = {name: list(fields_map[name]) for name in applied if name in fields_map}
	return {
		"applied_filters": applied,
		"suggestions": suggestions,
		"fields_read": fields_read,
	}
