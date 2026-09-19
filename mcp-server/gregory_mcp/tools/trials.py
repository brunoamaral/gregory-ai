"""Clinical trial search and detail tools."""

from __future__ import annotations

from typing import Literal

from .. import intent as intent_module
from ..client import GregoryAPIError, get_client
from ..compact import compact_trial, strip_team_data
from ..enums import CategoryModality
from ..pagination import clamp_page, clamp_page_size
from ..zero_result import guidance_for

SexEligibility = Literal["all", "female", "male"]
StudyType = Literal["basic_science", "expanded_access", "interventional", "observational", "other"]
Region = Literal["africa", "asia", "europe", "north_america", "oceania", "south_america"]

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25


async def search_trials(
	search: str | None = None,
	title: str | None = None,
	summary: str | None = None,
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
	intent: str | None = None,
	page: int = 1,
	page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
	"""Search clinical trials. Returns a compact projection — use get_trial
	for the full record (trial_sites, eligibility text, results detail).

	`search` is boolean over title + summary + scientific title (see
	search_articles for the syntax). Registries outside ClinicalTrials.gov
	often put a trial's name only in the scientific title — the public
	title there is the registry's lay summary title, and `acronym` is
	usually empty. `recruitment_status_normalized` and `phase_normalized`
	accept a comma-separated list matched with OR (e.g.
	"recruiting,not_recruiting"). Dates are YYYY-MM-DD.

	Registry IDs — each accepts a single value or a comma-separated list, in
	any common format (bare number, EUDRACT/EUCTR/CTIS-prefixed, with or
	without dashes — the id's own shape decides what it matches, not just the
	param name): `nct` (ClinicalTrials.gov), `euct` (EU CT / EUCTR — matches
	either identifier key), `eudract` (legacy EudraCT, pre-2025 EU trials),
	`ctis` (EU Clinical Trials Information System number). Each matches IDs
	from the trial's registry record, its secondary IDs, and the sponsor's
	study code — not only the exact stored key — so `eudract=` also finds a
	trial whose EudraCT number sits only in a CTIS-shaped key or in free
	text. A European trial commonly only has euct/eudract/ctis, not an nct —
	use whichever registry the caller already has an ID from. A lookup can
	return more than one row when a trial was imported from two registries
	and hasn't been merged yet.

	A zero-hit response adds a `guidance` key: which filters were applied,
	ranked suggestions for what's most likely over-constraining the search,
	and `fields_read` — which fields the text-matching filters (`search`,
	`acronym`, and the registry IDs) actually searched — check that before
	trying a completely different query.

	Args:
		intent: One short phrase describing the information need. Recorded
			separately to identify gaps in the corpus. Do not include
			personal or identifying details.
	"""
	if intent:
		await intent_module.record("search_trials", intent)
	clamped_page = clamp_page(page)
	params = {
		"search": search,
		"title": title,
		"summary": summary,
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
	count = data.get("count", len(results))
	response = {
		"count": count,
		"next_page": clamped_page + 1 if data.get("next") else None,
		"trials": [compact_trial(t) for t in results],
	}
	if count == 0:
		response["guidance"] = guidance_for(params, "trials")
	return response


async def get_trial(trial_id: int) -> dict:
	"""Fetch the full record for one clinical trial by ID, including
	trial_sites (detail-only), eligibility criteria, and results detail.

	Raises:
		ValueError: If no trial with this ID is visible in this instance.
	"""
	try:
		trial = await get_client().get(f"/trials/{trial_id}/")
	except GregoryAPIError as exc:
		# See get_article's identical comment — Django's 404 deliberately
		# doesn't distinguish "doesn't exist" from "out of this site's scope".
		if exc.status_code == 404:
			raise ValueError(f"Trial {trial_id} was not found in this instance.") from exc
		raise
	return strip_team_data(trial)
