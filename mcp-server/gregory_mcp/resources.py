"""Reference-data resources: subjects, categories, sponsors catalogs.

Registered with server-level `ttlMs`/`cacheScope` hints (see server.py) so
clients stop refetching this slow-changing data every conversation turn.
"""

from __future__ import annotations

import json

from .client import get_client


def register_resources(server) -> None:
	@server.resource(
		"gregory://subjects",
		name="subjects_catalog",
		title="Subjects catalog",
		description="Every research subject, with its team_id, across the connected GregoryAI instance.",
		mime_type="application/json",
	)
	async def subjects_catalog() -> str:
		results = await get_client().get_all_pages("/subjects/")
		return json.dumps(
			[{"id": s.get("id"), "subject_name": s.get("subject_name"), "team_id": s.get("team_id")} for s in results]
		)

	@server.resource(
		"gregory://categories",
		name="categories_catalog",
		title="Categories catalog",
		description="Every research category (topic tag) across the connected GregoryAI instance.",
		mime_type="application/json",
	)
	async def categories_catalog() -> str:
		results = await get_client().get_all_pages("/categories/")
		return json.dumps(
			[
				{"id": c.get("id"), "category_name": c.get("category_name"), "category_slug": c.get("category_slug")}
				for c in results
			]
		)

	@server.resource(
		"gregory://sponsors",
		name="sponsors_catalog",
		title="Sponsors catalog",
		description="Every canonical, deduplicated clinical trial sponsor.",
		mime_type="application/json",
	)
	async def sponsors_catalog() -> str:
		# Sponsors can number in the thousands (unlike subjects/categories);
		# get_all_pages caps at 20 pages (~2000 rows at page_size=100) as a
		# safety net rather than fetching an unbounded catalog.
		results = await get_client().get_all_pages("/sponsors/", {"page_size": 100})
		return json.dumps(
			[{"id": s.get("id"), "name": s.get("name"), "slug": s.get("slug")} for s in results]
		)
