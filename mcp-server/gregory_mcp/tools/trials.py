"""Clinical trial search and detail tools."""

from __future__ import annotations

from typing import Literal

from ..client import get_client
from ..compact import compact_trial
from ..enums import CategoryModality
from ..pagination import clamp_page, clamp_page_size

SexEligibility = Literal["all", "female", "male"]
StudyType = Literal["basic_science", "expanded_access", "interventional", "observational", "other"]
Region = Literal["africa", "asia", "europe", "north_america", "oceania", "south_america"]

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25


async def search_trials(
	search: str | None = None,
	title: str | None = None,
	summary: str | None = None,
	team_id: int | None = None,
	subject_id: int | None = None,
	category_slug: str | None = None,
	category_id: int | None = None,
	category_modality: CategoryModality | None = None,
	condition: str | None = None,
	intervention: str | None = None,
	recruitment_status_normalized: str | None = None,
	phase_normalized: str | None = None,
	study_type_normalized: StudyType | None = None,
	country: str | None = None,
	region: Region | None = None,
	sponsor_id: int | None = None,
	sponsor_slug: str | None = None,
	age_eligible: float | None = None,
	inclusion_gender_normalized: SexEligibility | None = None,
	date_registration_after: str | None = None,
	date_registration_before: str | None = None,
	nct: str | None = None,
	euct: str | None = None,
	eudract: str | None = None,
	ctis: str | None = None,
	acronym: str | None = None,
	has_results: bool | None = None,
	therapeutic_areas: str | None = None,
	ordering: str | None = None,
	page: int = 1,
	page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
	"""Search clinical trials. Returns a compact projection — use get_trial
	for the full record (trial_sites, eligibility text, results detail).

	`search` is boolean over title + summary (see search_articles for the
	syntax). `recruitment_status_normalized` and `phase_normalized` accept a
	comma-separated list matched with OR (e.g. "recruiting,not_recruiting").
	Dates are YYYY-MM-DD.

	Registry IDs — each accepts a single value or a comma-separated list,
	matched case-insensitively against any: `nct` (ClinicalTrials.gov),
	`euct` (EU CT / EUCTR — matches either identifier key), `eudract`
	(legacy EudraCT, pre-2025 EU trials), `ctis` (EU Clinical Trials
	Information System number). A European trial commonly only has
	euct/eudract/ctis, not an nct — use whichever registry the caller
	already has an ID from.
	"""
	clamped_page = clamp_page(page)
	params = {
		"search": search,
		"title": title,
		"summary": summary,
		"team_id": team_id,
		"subject_id": subject_id,
		"category_slug": category_slug,
		"category_id": category_id,
		"category_modality": category_modality,
		"condition": condition,
		"intervention": intervention,
		"recruitment_status_normalized": recruitment_status_normalized,
		"phase_normalized": phase_normalized,
		"study_type_normalized": study_type_normalized,
		"country": country,
		"region": region,
		"sponsor_id": sponsor_id,
		"sponsor_slug": sponsor_slug,
		"age_eligible": age_eligible,
		"inclusion_gender_normalized": inclusion_gender_normalized,
		"date_registration_after": date_registration_after,
		"date_registration_before": date_registration_before,
		"nct": nct,
		"euct": euct,
		"eudract": eudract,
		"ctis": ctis,
		"acronym": acronym,
		"has_results": has_results,
		"therapeutic_areas": therapeutic_areas,
		"ordering": ordering,
		"page": clamped_page,
		"page_size": clamp_page_size(page_size, MAX_PAGE_SIZE),
	}
	data = await get_client().get("/trials/", params)
	results = data.get("results", [])
	return {
		"count": data.get("count", len(results)),
		"next_page": clamped_page + 1 if data.get("next") else None,
		"trials": [compact_trial(t) for t in results],
	}


async def get_trial(trial_id: int) -> dict:
	"""Fetch the full record for one clinical trial by ID, including
	trial_sites (detail-only), eligibility criteria, and results detail."""
	return await get_client().get(f"/trials/{trial_id}/")
