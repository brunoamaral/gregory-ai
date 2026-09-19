"""Payload shaping for search-tool results.

Search tools return a small, flat projection of each record — full abstracts
and nested author/site lists blow up context fast, and most searches are
followed by a `get_*` read of one or two records anyway. Detail tools
(`get_article`, `get_trial`) return the full record, minus team data
(`strip_team_data`).
"""

from __future__ import annotations

from typing import Any

SUMMARY_TRUNCATE_CHARS = 400

# Team plumbing in API records: an article's `teams`, and the `team_id` on
# every nested subject — including the one inside each of an article's
# `article_subject_relevances`. #871 took `team_id` off every tool parameter
# and catalog row (a tenant sees its site's scope, and no tool accepts a
# team), so records returned whole must not carry it either.
_TEAM_KEYS = frozenset({"teams", "team_id"})


def strip_team_data(value: Any) -> Any:
	"""A copy of `value` with every `teams` / `team_id` key removed, at any
	depth — so a more deeply nested record, or a serializer field added
	later, can't bring team data back.

	`team_categories` is kept: despite the name it holds the record's
	category tags, which the tools expose as categories.
	"""
	if isinstance(value, dict):
		return {key: strip_team_data(item) for key, item in value.items() if key not in _TEAM_KEYS}
	if isinstance(value, list):
		return [strip_team_data(item) for item in value]
	return value


def _truncate(text: str | None, limit: int = SUMMARY_TRUNCATE_CHARS) -> str | None:
	if not text:
		return text
	if len(text) <= limit:
		return text
	return text[:limit].rstrip() + "…"


def compact_article(article: dict[str, Any]) -> dict[str, Any]:
	return {
		"article_id": article.get("article_id"),
		"title": article.get("title"),
		"published_date": article.get("published_date"),
		"journal": article.get("container_title"),
		"doi": article.get("doi"),
		"link": article.get("link"),
		"summary": _truncate(article.get("summary")),
		"ml_score": article.get("ml_score"),
		"access": article.get("access"),
	}


def compact_trial(trial: dict[str, Any]) -> dict[str, Any]:
	sponsor = trial.get("sponsor") or {}
	return {
		"trial_id": trial.get("trial_id"),
		"title": trial.get("title"),
		"published_date": trial.get("published_date"),
		"recruitment_status": trial.get("recruitment_status_normalized"),
		"phase": trial.get("phase_normalized"),
		"study_type": trial.get("study_type_normalized"),
		"sponsor": sponsor.get("name") or trial.get("primary_sponsor"),
		"countries": trial.get("countries_normalized"),
		"link": trial.get("link"),
		"summary": _truncate(trial.get("summary")),
		"identifiers": trial.get("identifiers"),
		"identifiers_normalized": trial.get("identifiers_normalized"),
	}


def compact_author(author: dict[str, Any]) -> dict[str, Any]:
	return {
		"author_id": author.get("author_id"),
		"full_name": author.get("full_name"),
		"orcid": author.get("ORCID"),
		"country": author.get("country"),
		"articles_count": author.get("articles_count"),
		"relevant_articles_count": author.get("relevant_articles_count"),
	}
