from __future__ import annotations

from gregory_mcp.zero_result import (
	ARTICLE_SEARCH_FIELDS,
	PATENT_SEARCH_FIELDS,
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


def test_ml_score_min_triggers_the_relevance_suggestion():
	# A search whose only ML filter is ml_score_min still gets the relevance
	# advice: articles with no ml_score are left out, so a high cut-off can
	# empty the page just as a high ml_threshold does.
	guidance = guidance_for({"ml_score_min": 0.9}, "articles")
	assert guidance["applied_filters"] == ["ml_score_min"]
	assert any("ml_score_min" in s for s in guidance["suggestions"])
	assert guidance["suggestions"] == guidance_for({"relevant": True}, "articles")["suggestions"]


def test_date_filters_trigger_date_range_suggestion():
	for key in ("published_date_after", "published_date_before", "last_days"):
		guidance = guidance_for({key: "2026-01-01"}, "articles")
		assert any("date" in s.lower() for s in guidance["suggestions"])
	for key in ("date_registration_after", "date_registration_before"):
		guidance = guidance_for({key: "2026-01-01"}, "trials")
		assert any("date" in s.lower() for s in guidance["suggestions"])


def test_taxonomy_id_filters_trigger_taxonomy_suggestion():
	for tool in ("articles", "trials"):
		for key in ("subject_id", "category_id", "category_slug", "category_modality"):
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
	# And the reverse: a registry-ID filter fires its own rule, not the
	# acronym-specific one. The registry-ID rule's own text legitimately
	# points at trying `search` with the acronym as a next step, so check the
	# acronym rule's distinctive phrasing, not the bare word "acronym".
	registry_guidance = guidance_for({"nct": "x"}, "trials")
	assert not any(
		"empty for most trials" in s.lower() for s in registry_guidance["suggestions"]
	)


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


def test_non_text_filters_never_appear_in_fields_read():
	# has_results, subject_id and country all read fields other than the one
	# their name suggests (or several fields, or a related model) — being
	# left out of fields_read isn't a claim they read a same-named field.
	guidance = guidance_for(
		{"has_results": True, "subject_id": 1, "country": "DE"}, "trials"
	)
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


def test_registry_id_fields_read_includes_secondary_id_sources():
	"""A registry-ID filter matches identifiers_normalized, which is derived
	from all three of these raw fields."""
	for key in ("nct", "euct", "eudract", "ctis"):
		guidance = guidance_for({key: "x"}, "trials")
		assert guidance["fields_read"][key] == [
			"identifiers",
			"secondary_id",
			"ctg_secondary_ids",
		]


def test_patent_search_fields_never_include_claims():
	assert PATENT_SEARCH_FIELDS == ("title", "summary")


def test_patents_fields_read_names_search_and_publication_number():
	guidance = guidance_for({"search": "x", "publication_number": "ZZ1", "has_grant": True}, "patents")
	assert guidance["fields_read"] == {
		"search": ["title", "summary"],
		"publication_number": ["publications"],
	}


def test_patents_has_no_fields_read_for_filters_that_read_no_text():
	guidance = guidance_for({"cpc": "A61K", "has_grant": False}, "patents")
	assert guidance["fields_read"] == {}


def test_each_patent_filter_triggers_its_own_suggestion():
	cases = {
		"priority_date_after": "date range",
		"publication_date_before": "date range",
		"has_grant": "has_grant",
		"cpc": "cpc",
		"publication_number": "publication_number",
		"has_trial_link": "has_trial_link",
		"applicant": "applicant",
		"applicant_sponsor_slug": "applicant",
		"category_slug": "list_categories",
		"subject_id": "list_subjects",
		"search": "claims",
	}
	for name, hint in cases.items():
		guidance = guidance_for({name: "x"}, "patents")
		assert any(hint in s for s in guidance["suggestions"]), (name, guidance["suggestions"])


def test_patents_suggestions_never_quote_a_value():
	guidance = guidance_for({"cpc": "H99Z77", "applicant": "Zorblax", "search": "qwxy"}, "patents")
	assert not any(v in str(guidance) for v in ("H99Z77", "Zorblax", "qwxy"))


def test_patents_with_no_matching_rule_gets_the_fallback():
	guidance = guidance_for({"family": "x"}, "patents")
	assert len(guidance["suggestions"]) == 1


def test_include_subject_links_alone_is_not_an_applied_filter_or_a_reason():
	guidance = guidance_for({"search": "x", "include_subject_links": True}, "patents")
	assert guidance["applied_filters"] == ["search"]
	assert not any("has_trial_link" in s for s in guidance["suggestions"])


def test_trials_ignore_include_subject_links_the_same_way():
	guidance = guidance_for({"search": "x", "include_subject_links": True}, "trials")
	assert guidance["applied_filters"] == ["search"]
	assert not any("has_patent_link" in s for s in guidance["suggestions"])


def test_trial_link_suggestion_depends_on_include_subject_links():
	plain = guidance_for({"has_trial_link": True}, "patents")["suggestions"][0]
	widened = guidance_for({"has_trial_link": True, "include_subject_links": True}, "patents")["suggestions"][0]
	assert "unless `include_subject_links` is true" in plain
	# The wrong explanation ("only category-basis links count") must not be given
	# to a caller who already asked for subject links.
	assert "unless" not in widened
	assert "already counts links on a shared subject" in widened


def test_has_trial_link_false_still_gets_the_suggestion():
	guidance = guidance_for({"has_trial_link": False}, "patents")
	assert guidance["applied_filters"] == ["has_trial_link"]
	assert "Drop `has_trial_link`" in guidance["suggestions"][0]


def test_has_patent_link_on_trials_has_a_suggestion():
	guidance = guidance_for({"has_patent_link": True}, "trials")
	assert guidance["applied_filters"] == ["has_patent_link"]
	(suggestion,) = guidance["suggestions"]
	assert "Drop `has_patent_link`" in suggestion
	assert "no patents it matches nothing" in suggestion
