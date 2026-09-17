"""Catalog tools: subjects, categories, sponsors.

Slow-changing reference data. `list_subjects` is the discovery entry point
for subject IDs, which most article/trial filters need.
"""

from __future__ import annotations

from ..cache import get_all_pages_cached
from ..client import get_client
from ..pagination import clamp_page, clamp_page_size


async def list_subjects(search: str | None = None) -> dict:
	"""List research subjects.

	Call this first when you don't already know a subject's ID — most
	article/trial filters need one.

	Cached server-side for 10 minutes (subjects change rarely).

	Args:
		search: Case-insensitive substring match against the subject name.
	"""
	results = await get_all_pages_cached("/subjects/", {"search": search})
	return {
		"count": len(results),
		"subjects": [{"id": s.get("id"), "subject_name": s.get("subject_name")} for s in results],
	}


async def list_categories(
	subject_id: int | None = None,
	search: str | None = None,
) -> dict:
	"""List research categories (topic tags attached to articles/trials).

	Args:
		subject_id: Restrict to categories used within this subject.
		search: Case-insensitive substring match against category name/terms.

	Note: results are NOT sorted by author count by default — that sort is
	expensive on this endpoint and is deliberately not exposed here.

	Cached server-side for 10 minutes — /categories/ costs about a second
	per request and takes 12 requests to read in full, so this avoids a
	multi-second stall on every call.
	"""
	results = await get_all_pages_cached("/categories/", {"subject_id": subject_id, "search": search})
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
	clamped_page = clamp_page(page)
	data = await get_client().get(
		"/sponsors/",
		{"search": search, "sponsor_type": sponsor_type, "page": clamped_page, "page_size": clamp_page_size(page_size, 100)},
	)
	results = data.get("results", [])
	return {
		"count": data.get("count", len(results)),
		"next_page": clamped_page + 1 if data.get("next") else None,
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
