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


#: The attribution of each regulator, as the API words it in a record's `source`.
REGULATOR_SOURCES = {
	"ema": "European Medicines Agency",
	"fda": "U.S. Food and Drug Administration",
}

#: Columns that exist for one regulator only. The API sends the other regulator's as
#: `null`; the tools leave them out, so a record never shows a field that cannot apply
#: to it. Kept in step with `MEDICINE_EMA_ONLY_FIELDS` / `MEDICINE_FDA_ONLY_FIELDS` in
#: django/api/serializers/__init__.py.
EMA_ONLY_MEDICINE_FIELDS = (
	"ema_product_number",
	"opinion_status",
	"therapeutic_areas",
	"atc_code",
	"advanced_therapy",
	"orphan_medicine",
	"conditional_approval",
	"exceptional_circumstances",
	"prime_priority_medicine",
	"accelerated_assessment",
	"additional_monitoring",
	"patient_safety",
	"rolling_review_start_date",
	"evaluation_start_date",
	"opinion_date",
	"latest_ec_decision_date",
	"refusal_date",
	"application_withdrawal_date",
	"suspension_date",
	"end_date",
	"ema_first_published",
	"ema_last_updated",
)
FDA_ONLY_MEDICINE_FIELDS = (
	"fda_application_number",
	"fda_application_type",
	"fda_center",
	"brand_names",
	"fda_priority_review",
	"fda_original_class",
	"interchangeable",
)


def medicine_regulator(medicine: dict[str, Any]) -> str:
	"""`ema` or `fda`. A record from before the API had a `regulator` is EMA's."""
	return "fda" if medicine.get("regulator") == "fda" else "ema"


def medicine_source(medicine: dict[str, Any]) -> str:
	"""The attribution to show with this record: the API's own `source`, else its
	regulator's name."""
	return medicine.get("source") or REGULATOR_SOURCES[medicine_regulator(medicine)]


def drop_other_regulator_fields(medicine: dict[str, Any]) -> dict[str, Any]:
	"""`medicine` without the columns that belong to the other regulator and are
	`null` on it (an FDA row has no `therapeutic_areas`, an EMA row no `brand_names`).
	A value that is somehow set is kept."""
	other = FDA_ONLY_MEDICINE_FIELDS if medicine_regulator(medicine) == "ema" else EMA_ONLY_MEDICINE_FIELDS
	return {k: v for k, v in medicine.items() if not (k in other and v is None)}


def compact_medicine(medicine: dict[str, Any]) -> dict[str, Any]:
	"""A medicine's search row, from either regulator. `regulator` and `source` say
	which. `link` is the record's `url`: an EMA product's EPAR page on ema.europa.eu,
	or an FDA application's Drugs@FDA page (a CBER biologic: its DailyMed label).

	`authorisation_date` is the first approval at either regulator (on an FDA row, the
	original approval). An EMA row has `latest_ec_decision_date`, kept under that name
	on purpose: it is the latest European Commission decision (usually a variation),
	never the approval. `categories` are the live links the caller may see; `tracked`
	is true when there is at least one. `subjects` are the conditions EMA approved an
	EMA medicine for, which is why an untracked one is listed at all; an FDA row has
	none.

	A field only one regulator has is on that regulator's rows only: an FDA row has
	`brand_names` and `fda_application_number` and none of the EMA opinion, EC decision
	or designation fields; an EMA row has neither of the FDA ones."""
	fda = medicine_regulator(medicine) == "fda"
	row: dict[str, Any] = {
		"medicine_id": medicine.get("id"),
		"regulator": medicine_regulator(medicine),
		"source": medicine_source(medicine),
		"name": medicine.get("name"),
	}
	if fda:
		row["brand_names"] = medicine.get("brand_names")
		row["fda_application_number"] = medicine.get("fda_application_number")
	row["substances"] = medicine.get("substances")
	row["status"] = medicine.get("status")
	if not fda:
		row["opinion_status"] = medicine.get("opinion_status")
	row["authorisation_date"] = medicine.get("authorisation_date")
	if not fda:
		row["opinion_date"] = medicine.get("opinion_date")
		row["latest_ec_decision_date"] = medicine.get("latest_ec_decision_date")
	row["holder"] = medicine.get("holder")
	row["indication"] = _truncate(medicine.get("indication"))
	if not fda:
		row["advanced_therapy"] = medicine.get("advanced_therapy")
		row["orphan_medicine"] = medicine.get("orphan_medicine")
	row["is_combination"] = medicine.get("is_combination")
	row["generic"] = medicine.get("generic")
	row["biosimilar"] = medicine.get("biosimilar")
	row["tracked"] = medicine.get("tracked")
	row["categories"] = [
		{
			"category_id": c.get("id"),
			"category_name": c.get("category_name"),
			"category_slug": c.get("category_slug"),
		}
		for c in medicine.get("categories") or []
	]
	row["subjects"] = [
		{
			"subject_id": s.get("id"),
			"subject_name": s.get("subject_name"),
			"matched_on": s.get("matched_on"),
		}
		for s in medicine.get("subjects") or []
	]
	row["link"] = medicine.get("url")
	return row


def compact_author(author: dict[str, Any]) -> dict[str, Any]:
	return {
		"author_id": author.get("author_id"),
		"full_name": author.get("full_name"),
		"orcid": author.get("ORCID"),
		"country": author.get("country"),
		"articles_count": author.get("articles_count"),
		"relevant_articles_count": author.get("relevant_articles_count"),
	}
