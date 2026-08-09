from __future__ import annotations

import gregory_mcp.query_shape as query_shape

_CATEGORIES = [
	{
		"category_slug": "encephalitis",
		"category_name": "Encephalitis",
		"category_terms": ["encephalitis", "brain inflammation"],
	},
	{
		"category_slug": "rituximab-therapy",
		"category_name": "Rituximab Therapy",
		"category_terms": ["rituximab", "anti-cd20"],
	},
	{
		# No slug — a category_slug can be blank per the model (SlugField(blank=True)) — must be skipped.
		"category_slug": None,
		"category_name": "Unslugged",
		"category_terms": ["orphanterm"],
	},
]


def _patch_categories(monkeypatch, categories=_CATEGORIES):
	async def fake_get_all_pages_cached(path, params=None):
		assert path == "/categories/"
		return categories

	monkeypatch.setattr(query_shape, "get_all_pages_cached", fake_get_all_pages_cached)


async def test_blank_text_returns_none(monkeypatch):
	_patch_categories(monkeypatch)
	assert await query_shape.analyze("") is None
	assert await query_shape.analyze("   ") is None


async def test_email_guardrail_returns_none(monkeypatch):
	_patch_categories(monkeypatch)
	assert await query_shape.analyze("contact jane@example.com about encephalitis") is None


async def test_long_digit_run_guardrail_returns_none(monkeypatch):
	_patch_categories(monkeypatch)
	assert await query_shape.analyze("phone 5551234567 encephalitis") is None


async def test_short_digit_run_is_not_guarded(monkeypatch):
	_patch_categories(monkeypatch)
	# 6 digits (e.g. a year range or short code) stays under the 7-digit guardrail.
	shape = await query_shape.analyze("study 202601 encephalitis")
	assert shape is not None


async def test_over_length_guardrail_returns_none(monkeypatch):
	_patch_categories(monkeypatch)
	assert await query_shape.analyze("x" * 401) is None


async def test_term_count_drops_stopwords_and_short_tokens(monkeypatch):
	_patch_categories(monkeypatch)
	shape = await query_shape.analyze("the of a rituximab and encephalitis")
	assert shape is not None
	# "the", "of", "a", "and" are stopwords/too short; "rituximab" and "encephalitis" remain.
	assert shape.term_count == 2


async def test_has_boolean_ops_detects_uppercase_operators(monkeypatch):
	_patch_categories(monkeypatch)
	shape = await query_shape.analyze("encephalitis AND rituximab")
	assert shape.has_boolean_ops is True


async def test_has_boolean_ops_false_for_lowercase(monkeypatch):
	_patch_categories(monkeypatch)
	shape = await query_shape.analyze("encephalitis and rituximab")
	assert shape.has_boolean_ops is False


async def test_has_quoted_phrase(monkeypatch):
	_patch_categories(monkeypatch)
	shape = await query_shape.analyze('"acute encephalitis"')
	assert shape.has_quoted_phrase is True

	shape2 = await query_shape.analyze("acute encephalitis")
	assert shape2.has_quoted_phrase is False


async def test_length_bucket_boundaries(monkeypatch):
	_patch_categories(monkeypatch)
	assert (await query_shape.analyze("x" * 20)).length_bucket == "1-20"
	assert (await query_shape.analyze("x" * 21)).length_bucket == "21-50"
	assert (await query_shape.analyze("x" * 50)).length_bucket == "21-50"
	assert (await query_shape.analyze("x" * 51)).length_bucket == "51-100"
	assert (await query_shape.analyze("x" * 100)).length_bucket == "51-100"
	assert (await query_shape.analyze("x" * 101)).length_bucket == "101-400"
	assert (await query_shape.analyze("x" * 400)).length_bucket == "101-400"


async def test_taxonomy_match_returns_slugs_and_unmatched_count(monkeypatch):
	_patch_categories(monkeypatch)
	shape = await query_shape.analyze("encephalitis rituximab paediatric")
	assert shape.matched_category_slugs == ["encephalitis", "rituximab-therapy"]
	# "paediatric" doesn't match any category term/name.
	assert shape.unmatched_term_count == 1


async def test_category_without_a_slug_is_excluded_from_the_index(monkeypatch):
	_patch_categories(monkeypatch)
	shape = await query_shape.analyze("orphanterm")
	assert shape.matched_category_slugs == []
	assert shape.unmatched_term_count == 1


async def test_a_term_matching_two_categories_reports_both_slugs(monkeypatch):
	categories = [
		{"category_slug": "a", "category_name": "Group A", "category_terms": ["shared"]},
		{"category_slug": "b", "category_name": "Group B", "category_terms": ["shared"]},
	]
	_patch_categories(monkeypatch, categories)
	shape = await query_shape.analyze("shared")
	assert shape.matched_category_slugs == ["a", "b"]
	assert shape.unmatched_term_count == 0


async def test_no_categories_matches_nothing(monkeypatch):
	_patch_categories(monkeypatch, categories=[])
	shape = await query_shape.analyze("encephalitis")
	assert shape.matched_category_slugs == []
	assert shape.unmatched_term_count == 1
