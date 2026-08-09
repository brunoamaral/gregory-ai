from __future__ import annotations

from gregory_mcp.zero_result import guidance_for


def test_applied_filters_excludes_pagination_and_ordering():
	guidance = guidance_for({"search": "x", "page": 1, "page_size": 10, "ordering": "-published_date"})
	assert guidance["applied_filters"] == ["search"]


def test_applied_filters_excludes_none_values():
	guidance = guidance_for({"search": "x", "team_id": None, "subject_id": 3})
	assert guidance["applied_filters"] == ["search", "subject_id"]


def test_applied_filters_sorted():
	guidance = guidance_for({"subject_id": 1, "doi": "10.1/x", "search": "x"})
	assert guidance["applied_filters"] == ["doi", "search", "subject_id"]


def test_relevant_or_ml_threshold_triggers_relevance_suggestion():
	guidance = guidance_for({"relevant": True})
	assert any("relevant" in s.lower() for s in guidance["suggestions"])

	guidance2 = guidance_for({"ml_threshold": 0.9})
	assert guidance["suggestions"] == guidance2["suggestions"]


def test_date_filters_trigger_date_range_suggestion():
	for key in ("published_date_after", "published_date_before", "last_days", "date_registration_after"):
		guidance = guidance_for({key: "2026-01-01"})
		assert any("date" in s.lower() for s in guidance["suggestions"])


def test_taxonomy_id_filters_trigger_taxonomy_suggestion():
	for key in ("subject_id", "category_id", "category_slug", "category_modality", "team_id"):
		guidance = guidance_for({key: "x"})
		assert any("list_subjects" in s or "list_categories" in s for s in guidance["suggestions"])


def test_registry_id_filters_trigger_registry_suggestion():
	for key in ("nct", "euct", "eudract", "ctis", "acronym", "sponsor_id", "sponsor_slug"):
		guidance = guidance_for({key: "x"})
		assert any("registry" in s.lower() or "sponsor" in s.lower() for s in guidance["suggestions"])


def test_search_filter_triggers_boolean_search_suggestion():
	guidance = guidance_for({"search": "a AND b"})
	assert any("AND" in s or "loosen" in s.lower() for s in guidance["suggestions"])


def test_multiple_rules_can_fire_together_and_stay_ranked():
	guidance = guidance_for({"relevant": True, "subject_id": 1, "search": "x"})
	# relevance, then taxonomy_id, then search — in the plan's stated priority order.
	assert len(guidance["suggestions"]) == 3
	assert "relevant" in guidance["suggestions"][0].lower()


def test_no_filters_falls_back_to_generic_suggestion():
	guidance = guidance_for({"page": 1, "page_size": 10})
	assert guidance["applied_filters"] == []
	assert len(guidance["suggestions"]) == 1


def test_suggestions_never_contain_filter_values():
	guidance = guidance_for({"search": "SENSITIVE-marker-value", "subject_id": 42})
	assert "SENSITIVE-marker-value" not in repr(guidance)
	assert "42" not in repr(guidance["suggestions"])
