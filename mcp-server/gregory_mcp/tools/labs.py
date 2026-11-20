"""Research lab search and detail tools."""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError

from ..client import GregoryAPIError, get_client
from ..compact import compact_lab
from ..pagination import clamp_page

LabOrdering = Literal[
	"name",
	"-name",
	"articles",
	"-articles",
	"relevant_articles",
	"-relevant_articles",
	"recent_articles",
	"-recent_articles",
	"member_count",
	"-member_count",
]

# The orderings the API refuses (400) without a topic filter.
_COUNT_ORDERINGS = frozenset({"articles", "relevant_articles", "recent_articles"})


async def search_labs(
	subject_id: int | None = None,
	category_id: int | None = None,
	search: str | None = None,
	country: str | None = None,
	author_id: int | None = None,
	min_recent_articles: int | None = None,
	ordering: LabOrdering | None = None,
	page: int = 1,
) -> dict:
	"""Find research labs: a head (a senior author) plus the people who
	repeatedly publish with them. Use it for "which labs work on X?".

	Counts cover the articles of the head and the members only, not their
	other collaborators. `relevant_articles` means relevant by a human or by
	AI. `recent_articles` counts papers from the start of the calendar year
	four years ago onwards. Results are compact: use `get_lab` for the members
	and every topic link.

	To rank labs on a topic, filter by `subject_id` or `category_id` and use
	`ordering="-relevant_articles"`. Find the IDs with `list_subjects` and
	`list_categories`. Ordering by `articles`, `relevant_articles` or
	`recent_articles`, and `min_recent_articles`, are counts for one topic, so
	they need `subject_id` or `category_id`; without one the call is refused
	before it is sent. With both topic filters, labs linked to both are kept.

	Each lab has `top_categories` (at most 3, each with that lab's `articles`,
	`relevant_articles` and `recent_articles` in the category). When you filter
	by `category_id`, that category comes first. When you filter by
	`subject_id` and the lab has that subject, `subject_link` carries its
	counts.

	`search` matches the lab's, its head's or its institution's name.
	`country` is a two-letter code (e.g. `PT`). `author_id` finds the lab that
	author heads or belongs to. Pages hold 10 labs; page through with `page`.
	A site with no labs linked to its topics gets an empty list.
	"""
	field = (ordering or "").lstrip("-")
	if field in _COUNT_ORDERINGS and subject_id is None and category_id is None:
		raise ToolError(
			f"ordering={ordering!r} ranks labs by their articles in one topic, so it needs "
			"subject_id or category_id. Add one, or order by name or member_count."
		)
	if min_recent_articles is not None and subject_id is None and category_id is None:
		raise ToolError(
			"min_recent_articles counts a lab's recent articles in one topic, so it needs "
			"subject_id or category_id. Add one, or leave min_recent_articles out."
		)
	clamped_page = clamp_page(page)
	params = {
		"subject_id": subject_id,
		"category_id": category_id,
		"search": search,
		"country": country,
		"author_id": author_id,
		"min_recent_articles": min_recent_articles,
		"ordering": ordering,
		"page": clamped_page,
	}
	data = await get_client().get("/labs/", params)
	results = data.get("results", [])
	return {
		"count": data.get("count", len(results)),
		"next_page": clamped_page + 1 if data.get("next") else None,
		"labs": [compact_lab(lab, category_id=category_id, subject_id=subject_id) for lab in results],
	}


async def get_lab(lab_id: int) -> dict:
	"""Fetch the full record for one research lab by ID: head, members,
	institution, and every subject and category link with its counts.

	A lab is a head (a senior author) plus the people who repeatedly publish
	with them. Collaborators are never listed.

	`curated` is true when a person corrected the lab by hand (created, named,
	re-headed, re-homed or merged it); false means it was grouped
	automatically from authorship data.

	The counts (`articles`, `relevant_articles`, `recent_articles`) are
	articles by the head and the members only, not their collaborators.
	`relevant_articles` means relevant by a human or by AI. `recent_articles`
	counts papers from the start of the calendar year four years ago onwards.
	Categories that include a subject outside this site's scope are left out.
	To list the lab's papers, call `search_articles` with `lab_id`.

	Args:
		lab_id: The lab's ID, from `search_labs` or an author's `lab`.

	Raises:
		ToolError: If the lab can't be found.
	"""
	try:
		return await get_client().get(f"/labs/{lab_id}/")
	except GregoryAPIError as exc:
		# See get_author's identical comment — Django's 404 deliberately
		# doesn't distinguish "doesn't exist" from "out of this site's scope".
		if exc.status_code == 404:
			raise ToolError(f"Lab {lab_id} was not found.") from exc
		raise
