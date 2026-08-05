"""Author search and detail tools."""

from __future__ import annotations

from typing import Literal

from ..client import get_client
from ..compact import compact_author
from ..pagination import clamp_page

SortBy = Literal["author_id", "full_name", "country", "article_count"]
Order = Literal["asc", "desc"]


async def search_authors(
	search: str | None = None,
	full_name: str | None = None,
	given_name: str | None = None,
	family_name: str | None = None,
	orcid: str | None = None,
	country: str | None = None,
	team_id: int | None = None,
	subject_id: int | None = None,
	sort_by: SortBy | None = None,
	order: Order | None = None,
	page: int = 1,
) -> dict:
	"""Search authors by name, ORCID iD, country, or team/subject scope.

	`search`, `full_name`, `given_name`, and `family_name` are all
	case-insensitive substring matches. `country` and `orcid` match
	case-insensitively too, so a partial ORCID (e.g. the last 4 digits)
	also works. This endpoint has a fixed page size (10) — page through
	with `page` rather than requesting a larger one.

	`subject_id` requires `team_id` — without it, the API returns an empty
	page rather than an error. `sort_by=article_count` ranks authors by
	their article count (add `team_id`/`subject_id` to scope which articles
	count); default order for it is descending, ascending for everything
	else.
	"""
	params = {
		"search": search,
		"full_name": full_name,
		"given_name": given_name,
		"family_name": family_name,
		"orcid": orcid,
		"country": country,
		"team_id": team_id,
		"subject_id": subject_id,
		"sort_by": sort_by,
		"order": order,
		"page": clamp_page(page),
	}
	data = await get_client().get("/authors/", params)
	results = data.get("results", [])
	return {
		"count": data.get("count", len(results)),
		"next": data.get("next"),
		"authors": [compact_author(a) for a in results],
	}


async def get_author(author_id: int, include_coauthors: bool = False) -> dict:
	"""Fetch the full record for one author by ID: affiliations, ORCID
	metadata, and article counts.

	Args:
		author_id: The author's ID.
		include_coauthors: When true, also fetches this author's co-authors
			(a second request) — off by default since it is rarely needed.
	"""
	author = await get_client().get(f"/authors/{author_id}/")
	if include_coauthors:
		coauthors = await get_client().get(f"/authors/{author_id}/coauthors/")
		author["coauthors"] = coauthors.get("results", coauthors)
	return author
