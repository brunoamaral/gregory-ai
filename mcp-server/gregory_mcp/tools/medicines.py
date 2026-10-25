"""EMA medicine search and detail tools.

Medicines are shown only on sites that turn them on (`medicines_enabled`); on any
other site these two tools are hidden (tool_access.py) and the instructions do not
mention them. The data is the European Medicines Agency's list of centrally
authorised human medicines, and EMA's terms for reusing it ask for the source to be
named wherever it is shown: every response says so. It covers only what EMA
authorises for the whole EU, so a drug missing from it may still be authorised in
single countries; every response says that too.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ..client import GregoryAPIError, get_client
from ..compact import compact_medicine, strip_team_data
from ..pagination import clamp_page, clamp_page_size
from ..zero_result import guidance_for

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25

SOURCE = "European Medicines Agency"

#: Attached to every response, so the caveat travels with the data wherever the
#: model repeats it.
COVERAGE_NOTE = (
	"Source: European Medicines Agency (EMA), medicines authorised centrally for the whole EU. "
	"A drug missing from this list may still be authorised in individual EU countries, so "
	"'not in EMA's data' does not mean 'not approved'."
)


async def search_medicines(
	name: str | None = None,
	substance: str | None = None,
	category_id: int | None = None,
	category_slug: str | None = None,
	subject_id: int | None = None,
	status: str | None = None,
	opinion_status: str | None = None,
	authorised_after: str | None = None,
	authorised_before: str | None = None,
	include_generics: bool | None = None,
	ordering: str | None = None,
	page: int = 1,
	page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
	"""Search the medicines the European Medicines Agency (EMA) has authorised for
	human use in the EU, one record per EMA product, limited to those linked to this
	site's categories. Returns a compact row each; use get_medicine for the full
	record with its regulatory history.

	The data is from the European Medicines Agency and covers only centrally
	authorised medicines. A drug missing here may still be authorised in individual
	EU countries, so "not in EMA's data" is not "not approved": say so when a search
	finds nothing.

	Use `name` for a brand or product name: it matches part of the name EMA
	publishes, ignoring case ("ocrevus" finds Ocrevus; a product renamed from
	another name also matches on the former name in brackets). Use `substance` for the
	active substance: the INN as EMA names it (e.g. "ocrelizumab"), matched exactly
	but ignoring case, not part of a name. A combination product matches on any of
	its substances. They combine with each other and with the filters below.
	`category_id` / `category_slug` / `subject_id` (see
	list_categories, list_subjects) keep products linked to that category, or to a
	category of that subject.

	`status` is EMA's own wording, ignoring case: Authorised, Withdrawn, Refused,
	Opinion, ... `opinion_status` is Positive or Negative and is filled only while a
	product is at the opinion stage, before the European Commission decides.
	`authorised_after` / `authorised_before` are YYYY-MM-DD and bound the date of
	first authorisation.

	Generic and biosimilar products are hidden unless `include_generics` is true.

	Each row has `authorisation_date` (the first approval) and
	`latest_ec_decision_date`, the date of the most recent European Commission
	decision. The latter is usually a variation such as a new indication; never
	present it as the approval date. `link` is the product's EPAR page on
	ema.europa.eu: always show it, and name EMA as the source.

	`ordering` is one of authorisation_date, latest_ec_decision_date,
	ema_last_updated (the default, newest first), with an optional leading `-` for
	descending.

	A zero-hit response adds a `guidance` key: which filters were applied and ranked
	suggestions for what is most likely over-constraining the search.
	"""
	clamped_page = clamp_page(page)
	params = {
		"name": name,
		"substance": substance,
		"category_id": category_id,
		"category_slug": category_slug,
		"subject_id": subject_id,
		"status": status,
		"opinion_status": opinion_status,
		"authorised_after": authorised_after,
		"authorised_before": authorised_before,
		"include_generics": include_generics,
		"ordering": ordering,
		"page": clamped_page,
		"page_size": clamp_page_size(page_size, MAX_PAGE_SIZE),
	}
	data = await get_client().get("/medicines/", params)
	results = data.get("results", [])
	count = data.get("count", len(results))
	response: dict[str, Any] = {
		"source": SOURCE,
		"note": COVERAGE_NOTE,
		"count": count,
		"next_page": clamped_page + 1 if data.get("next") else None,
		"medicines": [compact_medicine(m) for m in results],
	}
	if count == 0:
		response["guidance"] = guidance_for(params, "medicines")
	return response


async def get_medicine(medicine_id: int) -> dict:
	"""Fetch the full record for one EMA medicine: its substances, indication,
	status, the flags EMA publishes (orphan, advanced therapy, conditional approval,
	generic, biosimilar, ...), every date, the categories it is linked to on this
	site (`categories`) and its regulatory history (`events`, newest first).

	Each event has `kind_label`, the sentence to use ("CHMP positive opinion",
	"Authorised in the EU", "New EC decision", "Indication changed"), plus its
	`event_date`, `kind`, `basis` and, for a status or indication change,
	`from_value` and `to_value`. `basis` "observed" means the change was seen between
	two syncs; "reconstructed" means it was read off EMA's date fields the first time
	the product was seen: real history, but not news.

	The data is from the European Medicines Agency and covers only centrally
	authorised medicines. A drug missing from it may still be authorised in
	individual EU countries, so "not in EMA's data" is not "not approved".
	`latest_ec_decision_date` is the most recent European Commission decision,
	usually a variation; the first approval is `authorisation_date`. Always show the
	record's `url` (its EPAR page on ema.europa.eu) and name EMA as the source.

	Raises:
		ToolError: If the medicine can't be found.
	"""
	try:
		medicine = await get_client().get(f"/medicines/{medicine_id}/")
	except GregoryAPIError as exc:
		# Django's 404 deliberately doesn't distinguish "doesn't exist" from "not
		# visible on this site" (no live link to one of its categories).
		if exc.status_code == 404:
			raise ToolError(f"Medicine {medicine_id} was not found.") from exc
		raise
	return _shape_detail(strip_team_data(medicine))


def _shape_detail(medicine: dict) -> dict:
	"""`id` as `medicine_id` (the name the editor tool takes), the events without
	their row ids, and the coverage note."""
	shaped = {"medicine_id": medicine.get("id")}
	shaped.update({k: v for k, v in medicine.items() if k not in ("id", "events")})
	shaped["events"] = [
		{k: v for k, v in event.items() if k != "id"} for event in medicine.get("events") or []
	]
	shaped["note"] = COVERAGE_NOTE
	return shaped
