"""Regression test for the recent_trials_for_subject ordering="-date_registration" bug.

date_registration was never a valid `ordering` value on /trials/ (see
TrialViewSet.ordering_fields in django/api/views.py) — DRF's OrderingFilter
silently ignores an unrecognised value rather than rejecting it, so the prompt
promised registration-recency order and quietly delivered the default order
instead. This scans prompts.py and the tool modules for any ordering="..."
literal and checks the field name against every endpoint's real
ordering_fields, so the next typo fails a test instead of failing silently at
runtime.

This is a hardcoded allowlist rather than a read from django/schema.yml
because, as of this test, the schema does not yet declare a real enum for
`ordering` (see HANDOVER-MCP-FIXES-PLAN.md Task 1). Once that lands, prefer
sourcing this from the schema's declared enum the way
test_schema_contract.py does for filters — see Task 5.
"""

from __future__ import annotations

import inspect
import re

from gregory_mcp import prompts
from gregory_mcp.tools import articles, catalog, trials

# Mirrors each ViewSet's ordering_fields in django/api/views.py.
VALID_ORDERING_FIELDS = {
	# ArticleViewSet
	"discovery_date",
	"published_date",
	"title",
	"article_id",
	"ml_score",
	# TrialViewSet (discovery_date/published_date/title shared with articles)
	"trial_id",
	"last_updated",
	"recruiting_first",
	# CategoryViewSet
	"category_name",
	"id",
	"article_count_annotated",
	"trials_count_annotated",
	"authors_count_annotated",
	# SubjectsViewSet (id shared with categories)
	"subject_name",
	"team",
	# SponsorViewSet
	"name",
	"trials_count",
}

_ORDERING_LITERAL_RE = re.compile(r"""ordering\s*=\s*["']([^"']+)["']""")


def _ordering_values_in(*modules_or_functions) -> set[str]:
	values: set[str] = set()
	for obj in modules_or_functions:
		source = inspect.getsource(obj)
		values.update(_ORDERING_LITERAL_RE.findall(source))
	return values


def _assert_all_valid(values: set[str], where: str) -> None:
	for value in values:
		field = value.lstrip("-")
		assert field in VALID_ORDERING_FIELDS, (
			f"{where} references ordering={value!r}, but {field!r} is not a valid "
			"ordering field on any endpoint (see VALID_ORDERING_FIELDS)."
		)


def test_prompts_only_reference_valid_ordering_fields():
	_assert_all_valid(_ordering_values_in(prompts), "prompts.py")


def test_tool_source_only_references_valid_ordering_fields():
	found = _ordering_values_in(
		articles.search_articles,
		trials.search_trials,
		catalog.list_subjects,
		catalog.list_categories,
		catalog.list_sponsors,
	)
	_assert_all_valid(found, "a tool docstring or source")


def test_regression_date_registration_is_rejected():
	"""The exact bug: date_registration was never in ordering_fields."""
	assert "date_registration" not in VALID_ORDERING_FIELDS
