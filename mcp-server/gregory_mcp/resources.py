"""Reference-data resources: subjects, categories catalogs.

Registered with server-level `ttlMs`/`cacheScope` hints (see server.py) so
clients stop refetching this slow-changing data every conversation turn.

No sponsors resource: at ~8,000 rows / ~700 KB, sponsors are not
catalog-shaped the way subjects (7 rows) and categories (~120 rows) are —
resources are cached and injected wholesale. The `list_sponsors` tool
(search + pagination) is the right shape for that data; use it instead.
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
