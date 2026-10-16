"""Patent family search and detail tools.

Patents are shown only on sites that turn them on (`patents_enabled`); on any
other site these two tools are hidden (tool_access.py) and the instructions
do not mention them. The data comes from the EPO's Open Patent Services, whose
terms allow showing processed records in a product but not republishing "the
data as such": claims are therefore opt-in and capped, and every record keeps
its Espacenet link.
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from .. import intent as intent_module
from ..client import GregoryAPIError, get_client
from ..compact import compact_patent, strip_team_data
from ..enums import CategoryModality
from ..pagination import clamp_page, clamp_page_size
from ..site_context import get_editor_context
from ..zero_result import guidance_for

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25

#: `get_patent(include_claims=True)` returns at most this many characters of
#: claims. Claims average ~12,500 characters and run past 200,000; 20,000 gives
#: the full claims for 83% of the families that have them (about 5k tokens).
CLAIMS_CAP = 20_000

#: How many families to list when a publication number matches several.
_AMBIGUOUS_LIST = 10


async def search_patents(
	search: str | None = None,
	subject_id: int | None = None,
	category_slug: str | None = None,
	category_id: int | None = None,
	category_modality: CategoryModality | None = None,
	applicant: str | None = None,
	applicant_sponsor_id: int | None = None,
	applicant_sponsor_slug: str | None = None,
	priority_date_after: str | None = None,
	priority_date_before: str | None = None,
	publication_date_after: str | None = None,
	publication_date_before: str | None = None,
	has_grant: bool | None = None,
	cpc: str | None = None,
	publication_number: str | None = None,
	has_trial_link: bool | None = None,
	include_subject_links: bool | None = None,
	ordering: str | None = None,
	intent: str | None = None,
	page: int = 1,
	page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
	"""Search patent families (one record per DOCDB simple family: all the
	published documents that share the same priority filings). Returns a
	compact projection — use get_patent for the full record (every
	publication, inventors, linked trials, optionally the claims).

	`search` is boolean over title and summary only — never the claims (see
	search_articles for the syntax). `applicant` is a case-insensitive
	substring of the applicant name as published; `applicant_sponsor_id` /
	`applicant_sponsor_slug` match applicants linked to a canonical sponsor
	(see list_sponsors with include_patent_applicants). Dates are YYYY-MM-DD:
	priority date is when the invention was first filed, publication date
	usually follows about 18 months later.

	`cpc` is a class prefix, e.g. "A61K31" or "A61P25/28"; a comma-separated
	list matches any. `publication_number` matches any publication in the
	family, in any common format ("EP4123456A1", "EP 4123456 A1"); without a
	kind code ("EP4123456") it matches every kind. A comma-separated list
	matches any. `has_grant` is a heuristic on kind codes, not legal status.

	`has_trial_link` keeps families linked to a clinical trial of the same
	sponsor: filed while the trial was running or shortly after it ended. Only
	links on a shared category (typically the molecule) count, unless
	`include_subject_links` is true — links on a shared subject alone are
	numerous and noisy for large sponsors.

	Always show a patent's `link` (its Espacenet page) when you present it.

	`ordering` is one of earliest_priority_date, earliest_publication_date,
	discovery_date, last_updated, with an optional leading `-` for descending.

	A zero-hit response adds a `guidance` key: which filters were applied,
	ranked suggestions for what's most likely over-constraining the search,
	and `fields_read` — which fields `search` and `publication_number`
	actually looked at.

	Args:
		intent: One short phrase describing the information need. Recorded
			separately to identify gaps in the corpus. Do not include
			personal or identifying details.
	"""
	if intent:
		await intent_module.record("search_patents", intent)
	clamped_page = clamp_page(page)
	params = {
		"search": search,
		"subject_id": subject_id,
		"category_slug": category_slug,
		"category_id": category_id,
		"category_modality": category_modality,
		"applicant": applicant,
		"applicant_sponsor_id": applicant_sponsor_id,
		"applicant_sponsor_slug": applicant_sponsor_slug,
		"priority_date_after": priority_date_after,
		"priority_date_before": priority_date_before,
		"publication_date_after": publication_date_after,
		"publication_date_before": publication_date_before,
		"has_grant": has_grant,
		"cpc": cpc,
		"publication_number": publication_number,
		"has_trial_link": has_trial_link,
		"include_subject_links": include_subject_links,
		"ordering": ordering,
		"page": clamped_page,
		"page_size": clamp_page_size(page_size, MAX_PAGE_SIZE),
	}
	data = await get_client().get("/patents/", params)
	results = data.get("results", [])
	count = data.get("count", len(results))
	response = {
		"count": count,
		"next_page": clamped_page + 1 if data.get("next") else None,
		"patents": [compact_patent(p) for p in results],
	}
	if count == 0:
		response["guidance"] = guidance_for(params, "patents")
	return response


def _detail_params(include_claims: bool, include_subject_links: bool) -> dict | None:
	"""`include` for the detail call: the claims when asked for, and for an
	editor the site's editorial content (the takeaways and summary they may be
	about to edit), which Django leaves out without ``?include=editorial``.
	Anonymous and public-tier reads add nothing else."""
	include = []
	if include_claims:
		include.append("claims")
	session = get_editor_context()
	if session is not None and session.is_editor:
		include.append("editorial")
	params = {}
	if include:
		params["include"] = ",".join(include)
	if include_subject_links:
		params["include_subject_links"] = True
	return params or None


async def _resolve_publication_number(publication_number: str) -> int:
	"""The `patent_id` of the one family a publication number belongs to."""
	data = await get_client().get(
		"/patents/", {"publication_number": publication_number, "page_size": _AMBIGUOUS_LIST}
	)
	results = data.get("results", [])
	if not results:
		raise ToolError(f"Patent {publication_number} was not found.")
	if len(results) > 1:
		ids = ", ".join(str(r.get("patent_id")) for r in results)
		raise ToolError(
			f"More than one patent matches {publication_number} (patent_id {ids}). "
			"Call again with the patent_id, or give the full number with its kind code."
		)
	return results[0]["patent_id"]


async def get_patent(
	patent_id: int | None = None,
	publication_number: str | None = None,
	include_claims: bool = False,
	include_subject_links: bool = False,
) -> dict:
	"""Fetch the full record for one patent family: every published document
	(publications), inventors, classification, applicants, categories and the
	clinical trials it is linked to (trial_links). Give exactly one of
	`patent_id` or `publication_number` (any member publication, in any common
	format, e.g. "EP4123456A1").

	Claims are left out by default: they average ~12,500 characters and run past
	200,000. Pass `include_claims=true` to get the first 20,000 characters as
	`claims`, with `claims_length` (the full length) and `claims_truncated`. A
	family with no full text (US-, CN- or JP-only families: the EPO has none)
	returns `claims: null` and a `claims_note`.

	`trial_links` lists trials of the same sponsor the patent was filed during
	or shortly after, on a shared category (typically the molecule). Pass
	`include_subject_links=true` to also list links on a shared subject alone —
	numerous and noisy for large sponsors.

	Always show the record's `link` (its Espacenet page) when you present it.

	Raises:
		ToolError: If neither or both identifiers are given, or the patent
			can't be found, or a publication number matches more than one family.
	"""
	if (patent_id is None) == (publication_number is None):
		raise ToolError("Give exactly one of patent_id or publication_number.")
	label = f"Patent {patent_id}" if patent_id is not None else f"Patent {publication_number}"
	try:
		if patent_id is None:
			patent_id = await _resolve_publication_number(publication_number)
		patent = await get_client().get(
			f"/patents/{patent_id}/", _detail_params(include_claims, include_subject_links)
		)
	except GregoryAPIError as exc:
		# See get_article's identical comment — Django's 404 deliberately
		# doesn't distinguish "doesn't exist" from "out of this site's scope".
		if exc.status_code == 404:
			raise ToolError(f"{label} was not found.") from exc
		raise
	return _shape_claims(strip_team_data(patent), include_claims)


def _shape_claims(patent: dict, include_claims: bool) -> dict:
	"""Apply the claims cap, or note that there are none to return."""
	if not include_claims:
		patent.pop("claims", None)
		return patent
	claims = patent.get("claims")
	if not claims:
		patent["claims"] = None
		patent["claims_note"] = (
			"No claims text is available for this family: the EPO's Open Patent "
			"Services have full text only for some patent offices (EP, WO, CA and a "
			"few others), so a family filed only at the US, CN or JP offices has none."
		)
		return patent
	patent["claims_length"] = len(claims)
	patent["claims_truncated"] = len(claims) > CLAIMS_CAP
	patent["claims"] = claims[:CLAIMS_CAP]
	return patent
