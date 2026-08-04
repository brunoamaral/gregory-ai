"""Article search and detail tools."""

from __future__ import annotations

from typing import Literal

from ..client import get_client
from ..compact import compact_article

CategoryModality = Literal[
	"biologic_antibody",
	"cell_gene_therapy",
	"device_neuromodulation",
	"natural_product",
	"other",
	"rehabilitation",
	"research_topic",
	"small_molecule",
]

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25


async def search_articles(
	search: str | None = None,
	title: str | None = None,
	summary: str | None = None,
	team_id: int | None = None,
	subject_id: int | None = None,
	category_slug: str | None = None,
	category_id: int | None = None,
	category_modality: CategoryModality | None = None,
	journal_slug: str | None = None,
	doi: str | None = None,
	author_id: int | None = None,
	relevant: bool | None = None,
	ml_threshold: float | None = None,
	open_access: bool | None = None,
	has_clinical_trials: bool | None = None,
	published_date_after: str | None = None,
	published_date_before: str | None = None,
	ordering: str | None = None,
	page: int = 1,
	page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
	"""Search research articles. Returns a compact projection — use
	get_article for the full record (authors, ML predictions, linked trials).

	`search` is boolean over title + summary: space-separated terms are
	AND-ed, uppercase OR/NOT for alternatives/exclusion, "quoted phrases"
	match contiguously, (parentheses) group. Use `title=`/`summary=` instead
	of `search=` to match only one field.

	`relevant` and `ml_threshold` are scoped to `subject_id` when it is
	given — without a subject_id, relevance is checked across all subjects.

	Dates are YYYY-MM-DD. `ordering` accepts discovery_date, published_date,
	title, article_id, ml_score (prefix `-` for descending).
	"""
	params = {
		"search": search,
		"title": title,
		"summary": summary,
		"team_id": team_id,
		"subject_id": subject_id,
		"category_slug": category_slug,
		"category_id": category_id,
		"category_modality": category_modality,
		"journal_slug": journal_slug,
		"doi": doi,
		"author_id": author_id,
		"relevant": relevant,
		"ml_threshold": ml_threshold,
		"open_access": open_access,
		"has_clinical_trials": has_clinical_trials,
		"published_date_after": published_date_after,
		"published_date_before": published_date_before,
		"ordering": ordering,
		"page": page,
		"page_size": min(page_size, MAX_PAGE_SIZE),
	}
	data = await get_client().get("/articles/", params)
	results = data.get("results", [])
	return {
		"count": data.get("count", len(results)),
		"next": data.get("next"),
		"articles": [compact_article(a) for a in results],
	}


async def get_article(article_id: int) -> dict:
	"""Fetch the full record for one article by ID, including authors,
	ML predictions, linked clinical trials, and the untruncated summary."""
	return await get_client().get(f"/articles/{article_id}/")
