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


# Schema-declared params no tool exposes, reviewed and judged out of scope
# for now — not a permanent exemption, revisit if a use case shows up. Keeps
# the reverse check below from flagging *known* gaps on every run while
# still failing loudly the moment a *new*, unreviewed one appears (a filter
# added to a FilterSet that nobody thought to add to the matching tool).
KNOWN_UNEXPOSED_PARAMS = {
	"/articles/": {
		"format",  # CSV — no export tool, see STAGE-2 plan "Risks"
		"site_id",  # Django Site scoping; MCP client is already scoped to one instance
		"source_id",  # niche — callers don't know source IDs
		"subjects",
		"subjects_any",  # multi-subject AND/OR; subject_id covers the common case
		"week",
		"year",  # legacy ISO-week filtering; published_date_after/before covers this better
	},
	"/trials/": {
		"format",
		"site_id",
		"source_id",
		"subjects",
		"subjects_any",
		"trial_id",  # redundant with get_trial(trial_id)
		"source_register",  # niche — registry name (e.g. "ClinicalTrials.gov")
		"internal_number",  # niche — WHO internal number
		"identifiers",  # generic multi-registry filter; nct covers the common case
		# legacy raw-string fields superseded by a _normalized equivalent already exposed:
		"phase",  # -> phase_normalized
		"primary_sponsor",  # -> sponsor_id / sponsor_slug
		"recruitment_status",
		"status",  # -> recruitment_status_normalized
		"study_type",  # -> study_type_normalized
		"inclusion_agemin",
		"inclusion_agemax",  # -> age_eligible
		"countries",  # -> country / region
	},
	"/authors/": {
		"format",
		"author_id",  # redundant with get_author(author_id)
		# Task 5 restored team_id/subject_id; category/date scoping wasn't
		# requested and stays out for now:
		"category_id",
		"category_slug",
		"date_from",
		"date_to",
		"timeframe",
	},
	"/subjects/": {
		"format",
		"page",  # list_subjects always fetches every page (get_all_pages)
		"ordering",  # 7 rows total — nothing to usefully sort
	},
	"/categories/": {
		"format",
		"page",  # list_categories always fetches every page (get_all_pages)
		"ordering",
		"category_id",  # redundant enough with team_id/subject_id/search
		"category_terms",
		"get_categories",  # comma-separated ID lookup; niche
		# HANDOVER-MCP-FIXES-PLAN.md Task 5: "if useful" — deferred, not requested:
		"include_authors",
		"max_authors",
		"monthly_counts",
		"ml_threshold",
		"date_from",
		"date_to",
		"timeframe",
	},
	"/sponsors/": {
		"format",
		"ordering",  # list_sponsors doesn't expose a sort; search narrows results instead
	},
}


@pytest.mark.parametrize("fn,path,non_filter_args", TOOL_ENDPOINTS, ids=[fn.__name__ for fn, _, _ in TOOL_ENDPOINTS])
def test_no_new_unreviewed_schema_params(fn, path, non_filter_args, schema_params):
	"""Reverse of test_tool_filters_are_declared_in_schema: flags a schema
	param no tool exposes and no one has reviewed yet (KNOWN_UNEXPOSED_PARAMS
	above). Failing here doesn't mean the tool is broken — it means a new
	filter landed on the Django side that this file hasn't looked at. Either
	add it to the tool or, if it's deliberately out of scope, add it to the
	allowlist with a reason.
	"""
	if path not in schema_params:
		pytest.skip(f"{path} missing from schema.yml — covered by the forward check")
	declared = schema_params[path]
	tool_args = set(inspect.signature(fn).parameters) - non_filter_args
	reviewed = KNOWN_UNEXPOSED_PARAMS.get(path, set())

	unreviewed = declared - tool_args - reviewed
	assert not unreviewed, (
		f"schema.yml declares {sorted(unreviewed)} on GET {path}, which "
		f"{fn.__name__} doesn't expose and KNOWN_UNEXPOSED_PARAMS doesn't "
		"account for. Add it to the tool, or to KNOWN_UNEXPOSED_PARAMS with "
		"a reason if it's deliberately out of scope."
	)


def test_stats_endpoints_exist_in_schema(schema_params):
	# /articles/stats/ and /trials/stats/ are CachedStatsActionMixin-backed
	# custom @actions — schema.yml currently only declares `format` for them
	# (see STAGE-1-OPENAPI-SCHEMA-PLAN.md's "hard parts" table). Only /stats/
	# (a plain APIView with @extend_schema) is fully annotated; check its
	# params for real, and just check the other two exist.
	for path in ("/stats/", "/articles/stats/", "/trials/stats/"):
		assert path in schema_params, f"{path} is missing from schema.yml entirely"
	assert {"team", "subject"} <= schema_params["/stats/"]
