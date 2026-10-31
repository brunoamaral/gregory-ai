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

from gregory_mcp.tools import articles, authors, catalog, medicines, patents, trials

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
	# `intent` (MCP-TELEMETRY-PLAN.md Phase 4) is model-authored telemetry,
	# never forwarded to Django as a query param — a tool-only dispatch flag.
	# `include_authors` and `full_summary` shape the MCP response (what
	# compact_article keeps) and are likewise never sent to Django.
	(articles.search_articles, "/articles/", {"intent", "include_authors", "full_summary"}),
	(trials.search_trials, "/trials/", {"intent"}),
	(patents.search_patents, "/patents/", {"intent"}),
	(medicines.search_medicines, "/medicines/", set()),
	(authors.search_authors, "/authors/", set()),
	(catalog.list_subjects, "/subjects/", set()),
	(catalog.list_categories, "/categories/", set()),
	(catalog.list_sponsors, "/sponsors/", set()),
]

DETAIL_ENDPOINTS = [
	(articles.get_article, "/articles/{article_id}/"),
	(trials.get_trial, "/trials/{trial_id}/"),
	(patents.get_patent, "/patents/{patent_id}/"),
	(medicines.get_medicine, "/medicines/{id}/"),
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
		"format",
		# Editorial content (EDITORIAL-API-SPEC.md): the MCP server doesn't
		# expose per-org takeaways/summaries, so neither the opt-in nor the
		# filter on it is a tool parameter. Out of scope per the spec.
		"include",
		"has_takeaways",  # CSV — no export tool, see STAGE-2 plan "Risks"
		# Adds public sites' scopes to an *identified* caller's own scope.
		# GregoryClient sends no Authorization header (client.py) -- this
		# server is always an anonymous caller upstream, and an anonymous
		# caller's scope already IS the union of every public site's scope.
		# So the parameter is a provable no-op here; exposing it would put a
		# knob on the tool that cannot change any result.
		"include_public",
		# Not a *tool* parameter — no LLM caller ever chooses it. GregoryClient.get()
		# adds it transport-side to every upstream call from its own per-request
		# resolution (env override, else inbound Host via GET /tenants/) — see
		# gregory_mcp/tenants.py. Absent from every tool's signature by design.
		"site_id",
		"source_id",  # niche — callers don't know source IDs
		# Exact PubMed ID lookup (PubMed API source, PR 2). Deliberately not a
		# tool parameter yet: `search` and `doi` already cover finding a paper,
		# and the tool responses don't carry PMIDs. Revisit when they do.
		"pmid",
		"subjects",
		"subjects_any",  # multi-subject AND/OR; subject_id covers the common case
		"week",
		"year",  # legacy ISO-week filtering; published_date_after/before covers this better
		# Decision E, MCP multi-tenancy audit (2026-09-16): site-scoped API
		# visibility (#858-#868) means Django now scopes every caller by
		# site first, so team_id can no longer widen a result set -- it had
		# become purely cosmetic. Removed from the tool surface for
		# quality; the API still accepts and declares it.
		"team_id",
	},
	# Not exposed by search_patents. `title` and `summary` are single-field
	# substring filters that `search` (title and summary, boolean) covers;
	# `family_id` is a DOCDB id nobody searches by (`publication_number` is the
	# lookup people have). `site_id` and `include_public` are the same
	# transport-level / no-op parameters as on /articles/ and /trials/.
	"/patents/": {
		# `claims` come through get_patent(include_claims=...), which builds the
		# `include` value itself (and adds `editorial` for an editor's session).
		"include",
		"title",
		"summary",
		"family_id",
		"site_id",
		"include_public",
	},
	# Not exposed by search_medicines. The three flag filters are left out to keep
	# the tool small: every row carries `advanced_therapy`, `orphan_medicine` and
	# `is_combination`, so a model that wants them reads them off the rows (and
	# `substance`, `status` and the category filters already narrow the search).
	# `site_id` and `include_public` are the same transport-level / no-op
	# parameters as on /patents/ and /articles/.
	"/medicines/": {
		"advanced_therapy",
		"orphan_medicine",
		"is_combination",
		"site_id",
		"include_public",
		# The API got it first (drug grouping, PR 1). The MCP tool takes it in PR 3: remove
		# this entry then.
		"substance_key",
	},
	"/trials/": {
		"format",
		# Editorial content (EDITORIAL-API-SPEC.md): the MCP server doesn't
		# expose per-org takeaways/summaries, so neither the opt-in nor the
		# filter on it is a tool parameter. Out of scope per the spec.
		"include",
		"has_takeaways",
		# Adds public sites' scopes to an *identified* caller's own scope.
		# GregoryClient sends no Authorization header (client.py) -- this
		# server is always an anonymous caller upstream, and an anonymous
		# caller's scope already IS the union of every public site's scope.
		# So the parameter is a provable no-op here; exposing it would put a
		# knob on the tool that cannot change any result.
		"include_public",
		"site_id",  # see /articles/'s entry above — same transport-level injection
		"source_id",
		"subjects",
		"subjects_any",
		"trial_id",  # redundant with get_trial(trial_id)
		"source_register",  # niche — registry name (e.g. "ClinicalTrials.gov")
		"internal_number",  # niche — WHO internal number
		"identifiers",  # generic multi-registry filter; nct covers the common case
		# Partial-acronym lookup for the BRO frontend's search box. The tool's
		# `search` already covers free-text discovery; out of scope for MCP.
		"acronym_contains",
		# legacy raw-string fields superseded by a _normalized equivalent already exposed:
		"phase",  # -> phase_normalized
		"primary_sponsor",  # -> sponsor_id / sponsor_slug
		"recruitment_status",
		"status",  # -> recruitment_status_normalized
		"study_type",  # -> study_type_normalized
		"inclusion_agemin",
		"inclusion_agemax",  # -> age_eligible
		"countries",  # -> country / region
		# Decision E, MCP multi-tenancy audit (2026-09-16): same reasoning as
		# /articles/'s entry above -- team_id can no longer widen results now
		# that Django scopes by site first, so it was removed from the tool
		# surface for quality.
		"team_id",
	},
	"/authors/": {
		# Added by site-scoped API visibility Phase 2 as the scoping primitive
		# for that project. No MCP tool exposes it yet: the MCP server gains
		# subject scoping through its own multi-tenancy project, which binds a
		# subject set per tenant rather than taking one per call. Revisit when
		# that lands -- see MCP-MULTI-TENANCY-SPEC.md (local planning doc).
		"subjects_any",
		"format",
		"author_id",  # redundant with get_author(author_id)
		# Decision E, MCP multi-tenancy audit (2026-09-16): team_id can no
		# longer widen results now that Django scopes by site first, so it was
		# removed from search_authors for quality -- the stale
		# subject_id-requires-team_id guard went with it (see AuthorsViewSet
		# in django/api/views.py, which deliberately excludes subject filters
		# from that requirement).
		"team_id",
		# Task 5 restored subject_id; category/date scoping wasn't requested
		# and stays out for now:
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
		# Decision E, MCP multi-tenancy audit (2026-09-16): team_id can no
		# longer widen results now that Django scopes by site first, so it was
		# removed from list_subjects (and the team_id key dropped from every
		# returned row) for quality.
		"team_id",
	},
	"/categories/": {
		# Added by site-scoped API visibility Phase 2 as the scoping primitive
		# for that project. No MCP tool exposes it yet: the MCP server gains
		# subject scoping through its own multi-tenancy project, which binds a
		# subject set per tenant rather than taking one per call. Revisit when
		# that lands -- see MCP-MULTI-TENANCY-SPEC.md (local planning doc).
		"subjects_any",
		"format",
		"page",  # list_categories always fetches every page (get_all_pages)
		"ordering",
		"category_id",  # redundant enough with subject_id/search
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
		# Decision E, MCP multi-tenancy audit (2026-09-16): team_id can no
		# longer widen results now that Django scopes by site first, so it was
		# removed from list_categories for quality.
		"team_id",
	},
	"/sponsors/": {
		# Sponsor subject scoping arrived with site-scoped API visibility
		# Phase 2 (a sponsor is reached only through its trials, via
		# Exists() on primary_sponsor_normalized). list_sponsors stays
		# unscoped for now for the same reason as subjects_any below.
		"subject_id",
		# Added by site-scoped API visibility Phase 2 as the scoping primitive
		# for that project. No MCP tool exposes it yet: the MCP server gains
		# subject scoping through its own multi-tenancy project, which binds a
		# subject set per tenant rather than taking one per call. Revisit when
		# that lands -- see MCP-MULTI-TENANCY-SPEC.md (local planning doc).
		"subjects_any",
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


def test_site_id_declared_endpoints_match_what_this_audit_found(schema_params):
	"""Not a correctness requirement on GregoryClient.get() — it sends
	`site_id` to every endpoint regardless (see gregory_mcp/site.py's module
	docstring for why that's deliberate and safe). This is a drift detector:
	it records which endpoints declare `site_id` as of the audit that
	justified that design, so a schema regeneration that adds or removes it
	somewhere is caught and reviewed rather than silently changing which
	endpoints the parameter actually *does* something on.
	"""
	declared = {path for path, params in schema_params.items() if "site_id" in params}
	assert declared == {
		"/articles/",
		"/articles/search/",
		# EMA medicines: site_id narrows to that site's scope, and only sites with
		# medicines_enabled on count, so the client's site_id does what it should.
		"/medicines/",
		"/medicines/drugs/",
		"/medicines/events/",
		"/patents/",
		"/trials/",
		"/trials/search/",
		"/trials/sites/",
	}


def test_stats_endpoints_exist_in_schema(schema_params):
	# /articles/stats/ and /trials/stats/ are CachedStatsActionMixin-backed
	# custom @actions — schema.yml currently only declares `format` for them
	# (see STAGE-1-OPENAPI-SCHEMA-PLAN.md's "hard parts" table). Only /stats/
	# (a plain APIView with @extend_schema) is fully annotated; check its
	# params for real, and just check the other two exist.
	for path in ("/stats/", "/articles/stats/", "/trials/stats/", "/patents/stats/"):
		assert path in schema_params, f"{path} is missing from schema.yml entirely"
	assert {"team", "subject"} <= schema_params["/stats/"]


def test_the_medicine_field_lists_match_the_schema():
	"""compact.py lists the columns that belong to one regulator only, to leave the
	other regulator's nulls out. A field renamed or added in the serializer must be
	reflected there."""
	from gregory_mcp.compact import EMA_ONLY_MEDICINE_FIELDS, FDA_ONLY_MEDICINE_FIELDS

	if not SCHEMA_PATH.exists():
		pytest.skip(f"schema.yml not found at {SCHEMA_PATH} — run from within the gregory-ai checkout")
	with SCHEMA_PATH.open() as f:
		properties = yaml.safe_load(f)["components"]["schemas"]["Medicine"]["properties"]
	assert "regulator" in properties and "source" in properties
	assert set(EMA_ONLY_MEDICINE_FIELDS) <= set(properties)
	assert set(FDA_ONLY_MEDICINE_FIELDS) <= set(properties)
	assert not set(EMA_ONLY_MEDICINE_FIELDS) & set(FDA_ONLY_MEDICINE_FIELDS)
