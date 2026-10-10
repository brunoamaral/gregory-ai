"""Author search and detail tools."""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError

from ..client import GregoryAPIError, get_client
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
	subject_id: int | None = None,
	sort_by: SortBy | None = None,
	order: Order | None = None,
	page: int = 1,
) -> dict:
	"""Search authors by name, ORCID iD, country, or subject scope.

	`search`, `full_name`, `given_name`, and `family_name` are all
	case-insensitive substring matches. `country` and `orcid` match
	case-insensitively too, so a partial ORCID (e.g. the last 4 digits)
	also works. This endpoint has a fixed page size (10) — page through
	with `page` rather than requesting a larger one.

	`sort_by=article_count` ranks authors by their article count (add
	`subject_id` to scope which articles count); default order for it is
	descending, ascending for everything else.
	"""
	clamped_page = clamp_page(page)
	params = {
		"search": search,
		"full_name": full_name,
		"given_name": given_name,
		"family_name": family_name,
		"orcid": orcid,
		"country": country,
		"subject_id": subject_id,
		"sort_by": sort_by,
		"order": order,
		"page": clamped_page,
	}
	data = await get_client().get("/authors/", params)
	results = data.get("results", [])
	return {
		"count": data.get("count", len(results)),
		"next_page": clamped_page + 1 if data.get("next") else None,
		"authors": [compact_author(a) for a in results],
	}


async def get_author(author_id: int, include_coauthors: bool = False) -> dict:
	"""Fetch the full record for one author by ID: affiliations, ORCID
	metadata, and article counts.

	Args:
		author_id: The author's ID.
		include_coauthors: When true, also fetches this author's top 10
			co-authors (a second request), ranked by `shared_articles` — the
			number of articles they share with this author among all articles
			you can search, not only one category. `coauthors_total` says how
			many co-authors there are in all. To count co-authorship within
			one set of papers, use `search_articles` with `include_authors=true`
			instead. Off by default.

	Raises:
		ToolError: If the author can't be found.
	"""
	try:
		author = await get_client().get(f"/authors/{author_id}/")
	except GregoryAPIError as exc:
		# See get_article's identical comment — Django's 404 deliberately
		# doesn't distinguish "doesn't exist" from "out of this site's scope".
		if exc.status_code == 404:
			raise ToolError(f"Author {author_id} was not found.") from exc
		raise
	if include_coauthors:
		coauthors = await get_client().get(f"/authors/{author_id}/coauthors/")
		if isinstance(coauthors, dict):
			author["coauthors"] = coauthors.get("results", coauthors)
			# The endpoint pages its results (10 a page), so the list is only the
			# top of the ranking; `count` is how many co-authors there are in all.
			if "count" in coauthors:
				author["coauthors_total"] = coauthors["count"]
		else:
			# A bare list: nothing to say about the total, and nothing to unwrap.
			author["coauthors"] = coauthors
	return author
