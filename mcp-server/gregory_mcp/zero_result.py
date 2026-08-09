"""Guidance attached to zero-hit search_articles/search_trials responses.

Phase 3 of MCP-TELEMETRY-PLAN.md. A product change independent of the
telemetry work: `{"count": 0, "articles": []}` is a dead end for the model
calling the tool, so a zero-hit response gets a `guidance` key naming which
filters were applied and ranking suggestions for what's most likely
over-constraining the search — never the filter *values*, only which
filter names were set, the same boundary telemetry.py holds for
`params_used`.
"""

from __future__ import annotations

from typing import Any

# Pagination/ordering controls aren't filters that can cause a zero-hit
# result on their own — excluded from the "applied filters" list so it
# only ever names things that narrow the result set.
_NON_FILTER_ARGS = frozenset({"page", "page_size", "ordering"})

# (matching arg names, suggestion). Checked in order, so the categories
# most likely to silently zero out an otherwise-good search come first.
# Every arg name here is shared by search_articles and/or search_trials —
# see their param lists in tools/articles.py and tools/trials.py.
_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
	(
		("relevant", "ml_threshold"),
		"Drop `relevant`/`ml_threshold` — ML relevance predictions don't cover every "
		"row, and a high threshold can exclude rows that would otherwise match.",
	),
	(
		("published_date_after", "published_date_before", "last_days",
		 "date_registration_after", "date_registration_before"),
		"Widen or drop the date filter — a narrow or recent-only window is the most "
		"common cause of an empty page.",
	),
	(
		("subject_id", "category_id", "category_slug", "category_modality", "team_id"),
		"Call list_subjects/list_categories first to confirm the ID or slug is "
		"correct — a typo'd or stale one silently returns zero rows rather than an error.",
	),
	(
		("nct", "euct", "eudract", "ctis", "acronym", "sponsor_id", "sponsor_slug"),
		"Double-check the registry ID or sponsor — an unrecognized one returns zero "
		"rows rather than an error.",
	),
	(
		("search",),
		"If `search` uses AND/parentheses, try loosening it — an OR, a narrower single "
		"term, or title=/summary= instead.",
	),
)

_FALLBACK_SUGGESTION = "Try a broader search term, or drop optional filters one at a time to see which is excluding everything."


def guidance_for(params: dict[str, Any]) -> dict[str, Any]:
	"""(applied filter names, ranked suggestions) for a zero-hit response.

	`params` is the tool's own param dict before None-pruning — only the
	*names* of the non-None entries matter here, never their values.
	"""
	applied = sorted(k for k, v in params.items() if v is not None and k not in _NON_FILTER_ARGS)
	applied_set = set(applied)
	suggestions = [text for arg_names, text in _RULES if applied_set.intersection(arg_names)]
	if not suggestions:
		suggestions = [_FALLBACK_SUGGESTION]
	return {"applied_filters": applied, "suggestions": suggestions}
