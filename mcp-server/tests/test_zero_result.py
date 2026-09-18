from __future__ import annotations

from gregory_mcp.zero_result import (
	ARTICLE_SEARCH_FIELDS,
	TRIAL_SEARCH_FIELDS,
	guidance_for,
)


def test_applied_filters_excludes_pagination_and_ordering():
	guidance = guidance_for(
		{"search": "x", "page": 1, "page_size": 10, "ordering": "-published_date"},
		"articles",
	)
	assert guidance["applied_filters"] == ["search"]


def test_applied_filters_excludes_none_values():
	guidance = guidance_for(
		{"search": "x", "team_id": None, "subject_id": 3}, "articles"
	)
	assert guidance["applied_filters"] == ["search", "subject_id"]


def test_applied_filters_sorted():
	guidance = guidance_for(
		{"subject_id": 1, "doi": "10.1/x", "search": "x"}, "articles"
	)
	assert guidance["applied_filters"] == ["doi", "search", "subject_id"]


def test_relevant_or_ml_threshold_triggers_relevance_suggestion():
	guidance = guidance_for({"relevant": True}, "articles")
	assert any("relevant" in s.lower() for s in guidance["suggestions"])

	guidance2 = guidance_for({"ml_threshold": 0.9}, "articles")
	assert guidance["suggestions"] == guidance2["suggestions"]


def test_date_filters_trigger_date_range_suggestion():
	for key in ("published_date_after", "published_date_before", "last_days"):
		guidance = guidance_for({key: "2026-01-01"}, "articles")
		assert any("date" in s.lower() for s in guidance["suggestions"])
	for key in ("date_registration_after", "date_registration_before"):
		guidance = guidance_for({key: "2026-01-01"}, "trials")
		assert any("date" in s.lower() for s in guidance["suggestions"])


def test_taxonomy_id_filters_trigger_taxonomy_suggestion():
	for tool in ("articles", "trials"):
		for key in ("subject_id", "category_id", "category_slug", "category_modality", "team_id"):
			guidance = guidance_for({key: "x"}, tool)
			assert any("list_subjects" in s or "list_categories" in s for s in guidance["suggestions"])


def test_registry_id_filters_trigger_registry_suggestion():
	for key in ("nct", "euct", "eudract", "ctis"):
		guidance = guidance_for({key: "x"}, "trials")
		assert any("registry" in s.lower() for s in guidance["suggestions"])


def test_sponsor_filters_trigger_sponsor_suggestion():
	for key in ("sponsor_id", "sponsor_slug"):
		guidance = guidance_for({key: "x"}, "trials")
		assert any("sponsor" in s.lower() for s in guidance["suggestions"])


def test_acronym_fires_its_own_rule_not_the_registry_id_one():
	guidance = guidance_for({"acronym": "x"}, "trials")
	assert any("acronym" in s.lower() for s in guidance["suggestions"])
	assert not any("registry-id" in s.lower() for s in guidance["suggestions"])
	# And the reverse: a registry-ID filter doesn't pull in the acronym text.
	registry_guidance = guidance_for({"nct": "x"}, "trials")
	assert not any("acronym" in s.lower() for s in registry_guidance["suggestions"])


def test_search_filter_triggers_boolean_search_suggestion():
	guidance = guidance_for({"search": "a AND b"}, "articles")
	assert any("AND" in s or "loosen" in s.lower() for s in guidance["suggestions"])

	trial_guidance = guidance_for({"search": "a AND b"}, "trials")
	assert any("AND" in s or "loosen" in s.lower() for s in trial_guidance["suggestions"])


def test_trials_search_guidance_names_the_scientific_title():
	guidance = guidance_for({"search": "x"}, "trials")
	assert any("scientific title" in s.lower() for s in guidance["suggestions"])


def test_articles_search_guidance_does_not_mention_the_scientific_title():
	guidance = guidance_for({"search": "x"}, "articles")
	assert not any("scientific title" in s.lower() for s in guidance["suggestions"])


def test_multiple_rules_can_fire_together_and_stay_ranked():
	guidance = guidance_for({"relevant": True, "subject_id": 1, "search": "x"}, "articles")
	# relevance, then taxonomy_id, then search — in the plan's stated priority order.
	assert len(guidance["suggestions"]) == 3
	assert "relevant" in guidance["suggestions"][0].lower()


def test_no_filters_falls_back_to_generic_suggestion():
	guidance = guidance_for({"page": 1, "page_size": 10}, "articles")
	assert guidance["applied_filters"] == []
	assert len(guidance["suggestions"]) == 1


def test_suggestions_never_contain_filter_values():
	guidance = guidance_for({"search": "SENSITIVE-marker-value", "subject_id": 42}, "articles")
	assert "SENSITIVE-marker-value" not in repr(guidance)
	assert "42" not in repr(guidance["suggestions"])
	assert "SENSITIVE-marker-value" not in repr(guidance["fields_read"])


# ---------------------------------------------------------------------------
# fields_read — the MCP-side drift guard mirroring
# api.utils.search.ARTICLE_SEARCH_FIELDS / TRIAL_SEARCH_FIELDS.
# ---------------------------------------------------------------------------

def test_fields_read_only_lists_applied_filters():
	guidance = guidance_for({"search": "x", "subject_id": 1}, "trials")
	assert set(guidance["fields_read"]) == {"search"}


def test_fields_read_empty_when_no_field_coverage_filters_applied():
	guidance = guidance_for({"subject_id": 1}, "trials")
	assert guidance["fields_read"] == {}


def test_trials_search_fields_read_is_the_trial_search_fields_constant():
	guidance = guidance_for({"search": "x"}, "trials")
	assert guidance["fields_read"]["search"] == list(TRIAL_SEARCH_FIELDS)
	assert TRIAL_SEARCH_FIELDS == ("title", "summary", "scientific_title")


def test_articles_search_fields_read_is_the_article_search_fields_constant():
	guidance = guidance_for({"search": "x"}, "articles")
	assert guidance["fields_read"]["search"] == list(ARTICLE_SEARCH_FIELDS)
	assert ARTICLE_SEARCH_FIELDS == ("title", "summary")


def test_acronym_fields_read():
	guidance = guidance_for({"acronym": "x"}, "trials")
	assert guidance["fields_read"]["acronym"] == ["acronym"]


def test_registry_id_fields_read_is_identifiers():
	for key in ("nct", "euct", "eudract", "ctis"):
		guidance = guidance_for({key: "x"}, "trials")
		assert guidance["fields_read"][key] == ["identifiers"]
