"""Aggregate statistics tools."""

from __future__ import annotations

from typing import Literal

from ..client import get_client

Scope = Literal["global", "articles", "trials"]


async def get_stats(
	scope: Scope = "global",
	team_id: int | None = None,
	subject_id: int | None = None,
	search: str | None = None,
	relevant: bool | None = None,
) -> dict:
	"""Fetch aggregate counts.

	This tool only takes a narrow filter set (team_id/subject_id/search, plus
	relevant for "articles") — nothing like the full filter surface of
	search_articles/search_trials. Call those instead when you need a finer
	filter than this tool exposes.

	Args:
		scope: "global" for site-wide totals (articles, trials, subscribers,
			authors, sources, by_subject breakdown) via GET /stats/;
			"articles" for GET /articles/stats/ (total, by_access, relevant,
			retracted, missing_doi, by_subject); "trials" for GET
			/trials/stats/ (by_phase, by_region, by_country, by_sponsor, etc.).
		team_id: Scope to one team (all scopes).
		subject_id: Scope to one subject. For "articles"/"trials", also
			scopes `relevant`'s semantics the same way search_articles does.
		search: Boolean search filter (see search_articles), only meaningful
			for "articles"/"trials".
		relevant: Only meaningful for "articles".
	"""
	if scope == "global":
		params = {"team": team_id, "subject": subject_id}
		return await get_client().get("/stats/", params)
	if scope == "articles":
		params = {"team_id": team_id, "subject_id": subject_id, "search": search, "relevant": relevant}
		return await get_client().get("/articles/stats/", params)
	params = {"team_id": team_id, "subject_id": subject_id, "search": search}
	return await get_client().get("/trials/stats/", params)
