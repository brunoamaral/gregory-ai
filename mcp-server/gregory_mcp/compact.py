"""Payload shaping for search-tool results.

Search tools return a small, flat projection of each record — full abstracts
and nested author/site lists blow up context fast, and most searches are
followed by a `get_*` read of one or two records anyway. Detail tools
(`get_article`, `get_trial`, `get_patent`, `get_medicine`) return the full record, minus team data
(`strip_team_data`).

Nested author lists are left out by default and are opt-in on article search
(`include_authors`), as is the untruncated summary (`full_summary`): a model
building a dossier over a whole result set needs both for every paper, and one
`get_article` per paper runs into the per-tool request limit.
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
	later, can't bring team data back. A record's `categories` (its
	category tags) are kept.
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


def compact_article(
	article: dict[str, Any], *, include_authors: bool = False, full_summary: bool = False
) -> dict[str, Any]:
	compact = {
		"article_id": article.get("article_id"),
		"title": article.get("title"),
		"published_date": article.get("published_date"),
		"journal": article.get("container_title"),
		"doi": article.get("doi"),
		"link": article.get("link"),
		"summary": article.get("summary") if full_summary else _truncate(article.get("summary")),
		"ml_score": article.get("ml_score"),
		"access": article.get("access"),
	}
	if include_authors:
		# Always a list, even when the API row has no `authors` key, so a caller
		# counting authors across a result set never has to special-case a
		# missing one. The API's key is `ORCID`; the compact key is `orcid`, as
		# in compact_author.
		compact["authors"] = [
			{"author_id": a.get("author_id"), "full_name": a.get("full_name"), "orcid": a.get("ORCID")}
			for a in article.get("authors") or []
		]
	return compact


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


def compact_patent(patent: dict[str, Any]) -> dict[str, Any]:
	"""A patent family's search row. `link` is the Espacenet page: showing
	where a record comes from is a condition of the EPO OPS licence the data is
	fetched under, so it stays on every row. `applicants` are the names to show
	(the canonical sponsor's where one is linked, else the name as published),
	in published order and without repeats."""
	names: list[str] = []
	for applicant in patent.get("applicants") or []:
		name = applicant.get("sponsor_name") or applicant.get("raw_name")
		if name and name not in names:
			names.append(name)
	return {
		"patent_id": patent.get("patent_id"),
		"title": patent.get("title"),
		"summary": _truncate(patent.get("summary")),
		"representative_publication": patent.get("representative_publication"),
		"link": patent.get("link"),
		"earliest_priority_date": patent.get("earliest_priority_date"),
		"earliest_publication_date": patent.get("earliest_publication_date"),
		"has_grant": patent.get("has_grant"),
		"applicants": names,
		"trial_links_count": patent.get("trial_links_count"),
	}


def compact_medicine(medicine: dict[str, Any]) -> dict[str, Any]:
	"""An EMA medicine's search row. `link` is the product's EPAR page on
	ema.europa.eu. `latest_ec_decision_date` keeps that name on purpose: it is the
	latest European Commission decision (usually a variation), never the approval,
	which is `authorisation_date`. `categories` are the live links the caller may
	see; `tracked` is true when there is at least one. `subjects` are the conditions
	EMA approved it for, which is why an untracked medicine is listed at all."""
	return {
		"medicine_id": medicine.get("id"),
		"name": medicine.get("name"),
		"substances": medicine.get("substances"),
		"status": medicine.get("status"),
		"opinion_status": medicine.get("opinion_status"),
		"authorisation_date": medicine.get("authorisation_date"),
		"opinion_date": medicine.get("opinion_date"),
		"latest_ec_decision_date": medicine.get("latest_ec_decision_date"),
		"holder": medicine.get("holder"),
		"indication": _truncate(medicine.get("indication")),
		"advanced_therapy": medicine.get("advanced_therapy"),
		"orphan_medicine": medicine.get("orphan_medicine"),
		"is_combination": medicine.get("is_combination"),
		"generic": medicine.get("generic"),
		"biosimilar": medicine.get("biosimilar"),
		"tracked": medicine.get("tracked"),
		"categories": [
			{
				"category_id": c.get("id"),
				"category_name": c.get("category_name"),
				"category_slug": c.get("category_slug"),
			}
			for c in medicine.get("categories") or []
		],
		"subjects": [
			{
				"subject_id": s.get("id"),
				"subject_name": s.get("subject_name"),
				"matched_on": s.get("matched_on"),
			}
			for s in medicine.get("subjects") or []
		],
		"link": medicine.get("url"),
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
