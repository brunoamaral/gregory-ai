"""Contract test against the Stage 1 OpenAPI schema (../django/schema.yml).

Every filter parameter a tool function accepts must be a real, declared
query parameter on the endpoint it calls. This is what "Stage 2 builds
against the schema, not against prose" (STAGE-2-MCP-SERVER-PLAN.md) means in
practice: run this after regenerating schema.yml, and an unannotated or
renamed filter fails here instead of at runtime against a live instance.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import yaml

from gregory_mcp.tools import articles, authors, catalog, trials

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "django" / "schema.yml"


@pytest.fixture(scope="module")
def schema_params() -> dict[str, set[str]]:
	if not SCHEMA_PATH.exists():
		pytest.skip(f"schema.yml not found at {SCHEMA_PATH} — run from within the gregory-ai checkout")
	with SCHEMA_PATH.open() as f:
		doc = yaml.safe_load(f)
	result = {}
	for path, methods in doc["paths"].items():
		get = methods.get("get")
		if not get:
			continue
		result[path] = {p["name"] for p in get.get("parameters", [])}
	return result


# (tool function, endpoint path, arg names that are NOT query filters — path
# params, tool-only dispatch flags, etc.)
TOOL_ENDPOINTS = [
	(articles.search_articles, "/articles/", set()),
	(trials.search_trials, "/trials/", set()),
	(authors.search_authors, "/authors/", set()),
	(catalog.list_subjects, "/subjects/", set()),
	(catalog.list_categories, "/categories/", set()),
	(catalog.list_sponsors, "/sponsors/", set()),
]

DETAIL_ENDPOINTS = [
	(articles.get_article, "/articles/{article_id}/"),
	(trials.get_trial, "/trials/{trial_id}/"),
	(authors.get_author, "/authors/{id}/"),
]


@pytest.mark.parametrize("fn,path,non_filter_args", TOOL_ENDPOINTS, ids=[fn.__name__ for fn, _, _ in TOOL_ENDPOINTS])
def test_tool_filters_are_declared_in_schema(fn, path, non_filter_args, schema_params):
	assert path in schema_params, f"{path} is missing from schema.yml entirely"
	declared = schema_params[path]
	tool_args = set(inspect.signature(fn).parameters) - non_filter_args

	unknown = tool_args - declared
	assert not unknown, (
		f"{fn.__name__} passes {sorted(unknown)} to GET {path}, but schema.yml "
		f"does not declare {sorted(unknown)} as a query parameter there. Either "
		"the tool is out of date or schema.yml needs regenerating."
	)


@pytest.mark.parametrize("fn,path", DETAIL_ENDPOINTS, ids=[fn.__name__ for fn, _ in DETAIL_ENDPOINTS])
def test_detail_endpoint_exists_in_schema(fn, path, schema_params):
	assert path in schema_params, f"{path} is missing from schema.yml entirely"


def test_stats_endpoints_exist_in_schema(schema_params):
	# /articles/stats/ and /trials/stats/ are CachedStatsActionMixin-backed
	# custom @actions — schema.yml currently only declares `format` for them
	# (see STAGE-1-OPENAPI-SCHEMA-PLAN.md's "hard parts" table). Only /stats/
	# (a plain APIView with @extend_schema) is fully annotated; check its
	# params for real, and just check the other two exist.
	for path in ("/stats/", "/articles/stats/", "/trials/stats/"):
		assert path in schema_params, f"{path} is missing from schema.yml entirely"
	assert {"team", "subject"} <= schema_params["/stats/"]
