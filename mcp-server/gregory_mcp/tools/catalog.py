"""Catalog tools: subjects, categories, sponsors.

Slow-changing reference data. `list_subjects` is the discovery entry point —
every row carries `team_id`, which most other tools' filters need.
"""

from __future__ import annotations

from ..client import get_client
from ..pagination import clamp_page, clamp_page_size


async def list_subjects(team_id: int | None = None, search: str | None = None) -> dict:
	"""List research subjects, each carrying its `team_id`.

	Call this first when you don't already know a subject's ID or its
	team_id — most article/trial filters need one or both.

	Args:
		team_id: Restrict to subjects belonging to this team.
		search: Case-insensitive substring match against the subject name.
	"""
	results = await get_client().get_all_pages("/subjects/", {"team_id": team_id, "search": search})
	return {
		"count": len(results),
		"subjects": [
			{"id": s.get("id"), "subject_name": s.get("subject_name"), "team_id": s.get("team_id")}
			for s in results
		],
	}


async def list_categories(
	team_id: int | None = None,
	subject_id: int | None = None,
	search: str | None = None,
) -> dict:
	"""List research categories (topic tags attached to articles/trials).

	Args:
		team_id: Restrict to categories belonging to this team.
		subject_id: Restrict to categories used within this subject.
		search: Case-insensitive substring match against category name/terms.

	Note: results are NOT sorted by author count by default — that sort is
	expensive on this endpoint and is deliberately not exposed here.
	"""
	results = await get_client().get_all_pages(
		"/categories/", {"team_id": team_id, "subject_id": subject_id, "search": search}
	)
	return {
		"count": len(results),
		"categories": [
			{
				"id": c.get("id"),
				"category_name": c.get("category_name"),
				"category_slug": c.get("category_slug"),
				"modality": c.get("modality"),
				"article_count_total": c.get("article_count_total"),
				"trials_count_total": c.get("trials_count_total"),
			}
			for c in results
		],
	}


async def list_sponsors(
	search: str | None = None,
	sponsor_type: str | None = None,
	page: int = 1,
	page_size: int = 25,
) -> dict:
	"""List canonical, deduplicated clinical trial sponsors.

	Unlike subjects/categories, sponsors can number in the thousands, so
	this is paginated rather than fetched in full — pass `search` to narrow
	it down, or page through with `page`.

	Args:
		search: Case-insensitive substring match against the sponsor name.
		sponsor_type: One of academic_medical, government, industry, nonprofit, other.
	"""
	data = await get_client().get(
		"/sponsors/",
		{"search": search, "sponsor_type": sponsor_type, "page": clamp_page(page), "page_size": clamp_page_size(page_size, 100)},
	)
	results = data.get("results", [])
	return {
		"count": data.get("count", len(results)),
		"next": data.get("next"),
		"sponsors": [
			{
				"id": s.get("id"),
				"name": s.get("name"),
				"slug": s.get("slug"),
				"sponsor_type": s.get("sponsor_type"),
				"trials_count": s.get("trials_count"),
			}
			for s in results
		],
	}
