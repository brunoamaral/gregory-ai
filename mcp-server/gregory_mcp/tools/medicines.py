"""Medicine search and detail tools, for both regulators.

Medicines are shown only on sites that turn them on (`medicines_enabled`); on any
other site these two tools are hidden (tool_access.py) and the instructions do not
mention them. The data is two regulators' lists, and each record says which one it
is from (`regulator`, and `source` in the regulator's own name): the European
Medicines Agency's list of centrally authorised human medicines, and the U.S. Food and
Drug Administration's approved new drugs and biologics (NDAs and BLAs). EMA's terms
for reusing its data ask for the source to be named wherever it is shown, and the FDA
data comes through openFDA with label text from DailyMed, so every response names its
source. Each list covers only what its regulator publishes, so a drug missing from it
may still be approved elsewhere, or under another route; every response says that too.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError

from ..client import GregoryAPIError, get_client
from ..compact import (
	REGULATOR_SOURCES,
	compact_medicine,
	drop_other_regulator_fields,
	medicine_regulator,
	medicine_source,
	strip_team_data,
)
from ..pagination import clamp_page, clamp_page_size
from ..zero_result import guidance_for

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25

Regulator = Literal["ema", "fda"]

#: The order a search of both regulators (or of the FDA) gets when the caller gives none.
DEFAULT_MIXED_ORDERING = "-authorisation_date"

EMA_NOTE = (
	"Source: European Medicines Agency (EMA), medicines authorised centrally for the whole EU. "
	"A drug missing from this list may still be authorised in individual EU countries, so "
	"'not in EMA's data' does not mean 'not approved'."
)

FDA_NOTE = (
	"Source: U.S. Food and Drug Administration (FDA), its approved new drugs and biologics "
	"(NDAs and BLAs), through openFDA; label text from DailyMed. Generic drugs (ANDAs) and "
	"applications under review are not covered, so 'not in FDA's data' does not mean 'not "
	"approved'. Not for clinical decisions."
)

#: Shown when a `substance` search found no FDA record: the FDA names an ingredient as on
#: its label, which is often the salt.
FDA_SALT_HINT = (
	"No FDA record matched this `substance`. The FDA names an ingredient as on its label, "
	"sometimes with the salt (fingolimod hydrochloride, where EMA has fingolimod), and "
	"`substance` is an exact match: to find the FDA's record, also try `name` (the brand) "
	"or the salt form."
)

#: Attached to every response, so the caveat travels with the data wherever the
#: model repeats it. A response that is certainly about one regulator carries only
#: that regulator's part (EMA_NOTE or FDA_NOTE).
COVERAGE_NOTE = f"{EMA_NOTE} {FDA_NOTE} The same medicine can have a record from each regulator."


def coverage_note(regulators: Iterable[str]) -> str:
	"""The caveat for a response about `regulators` (`ema` / `fda`): one regulator's
	part when it is the only one, else both."""
	wanted = set(regulators)
	if wanted == {"ema"}:
		return EMA_NOTE
	if wanted == {"fda"}:
		return FDA_NOTE
	return COVERAGE_NOTE


def _source_line(sources: Iterable[str]) -> str:
	"""Source names, once each and in the order given, joined with " and "."""
	return " and ".join(dict.fromkeys(sources))


async def search_medicines(
	name: str | None = None,
	substance: str | None = None,
	regulator: Regulator | None = None,
	category_id: int | None = None,
	category_slug: str | None = None,
	subject_id: int | None = None,
	status: str | None = None,
	opinion_status: str | None = None,
	authorised_after: str | None = None,
	authorised_before: str | None = None,
	include_generics: bool | None = None,
	tracked: bool | None = None,
	ordering: str | None = None,
	page: int = 1,
	page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
	"""Search the medicines two regulators have approved for human use: the European
	Medicines Agency (EMA, medicines authorised centrally for the EU, one record per EMA
	product) and the U.S. Food and Drug Administration (FDA, its approved new drugs and
	biologics, one record per FDA application). A medicine shows either because it is
	filed under one of this site's categories, or (EMA only) because EMA approved it for
	one of the site's conditions (its subjects). Returns a compact row each; use
	get_medicine for the full record with its regulatory history.

	Every row has `regulator` ("ema" or "fda") and `source` (the regulator's name):
	show the source with the medicine. One medicine can appear twice, once per regulator,
	as two separate records with their own ids. The FDA names an ingredient as on its
	label, sometimes with the salt (fingolimod hydrochloride, where EMA has fingolimod),
	so to show one entry per drug group the two regulators' records by the substance
	without the salt, not by exact `substances`. Pass `regulator` to search one regulator
	only; leave it out for both. With no `ordering`, a search of both regulators (or of the
	FDA) lists records by `-authorisation_date`, newest approvals first, so both regulators
	are mixed; a search of EMA only lists them by `-ema_last_updated`, newest update first.

	The FDA's data writes brand names, ingredients and companies in capitals (KESIMPTA,
	NOVARTIS): write them in normal case (Kesimpta, Novartis) when you present them.

	The data covers only what each regulator publishes. EMA lists centrally authorised
	medicines, and a drug missing from it may still be authorised in individual EU
	countries. The FDA data has approved new drugs and biologics (NDAs and BLAs) but not
	generic drugs (ANDAs) or applications under review. So "not in EMA's data" or "not in
	FDA's data" is not "not approved": say so when a search finds nothing.

	Use `name` for a brand or product name: it matches part of the name the regulator
	publishes, ignoring case ("ocrevus" finds Ocrevus; an EMA product renamed from
	another name also matches on the former name in brackets; an FDA application sold
	under several brands lists them all in its name). Use `substance` for the active
	substance: the INN as EMA names it (e.g. "ocrelizumab"), or the ingredient as the FDA
	names it, matched exactly but ignoring case, not part of a name. The FDA writes an
	ingredient as on its label, sometimes with the salt ("fingolimod hydrochloride",
	"rivastigmine tartrate"), so `substance="fingolimod"` finds EMA's Gilenya but not the
	FDA's: to find a drug's FDA record, also try `name` (the brand) or the salt form. A
	combination product matches on any of its substances. They combine with each other and with the
	filters below. `category_id` / `category_slug` / `subject_id` (see
	list_categories, list_subjects) keep products linked to that category, or to a
	category of that subject; `subject_id` on its own also keeps EMA products EMA approved
	for that subject's condition, whether or not a category covers them.

	Each row has `tracked` (true when the medicine is filed under at least one of this
	site's categories) and `subjects` (the conditions EMA approved it for; always empty
	for an FDA record, which shows only when it is filed under a category, so is always
	tracked). Pass `tracked=false` to list only the EMA medicines no category covers yet:
	they show because EMA approved them for one of the site's conditions, and they are the
	candidates for a new category. `tracked=true` lists only the filed ones.

	`status` is the regulator's own wording, ignoring case. EMA: Authorised, Withdrawn,
	Refused, Opinion, ... FDA: Approved, Tentatively approved, Discontinued, Revoked.
	`opinion_status` is EMA-only, Positive or Negative, and is filled only while a
	product is at the opinion stage, before the European Commission decides; using it
	leaves out every FDA record. `authorised_after` / `authorised_before` are YYYY-MM-DD
	and bound the date of first authorisation (the FDA's original approval, for an FDA
	record).

	Generic and biosimilar products, and duplicate products (a further product of the
	same regulator for the same medicine as a lead product), are hidden unless
	`include_generics` is true.

	Each row has `authorisation_date` (the first approval). An EMA row also has
	`latest_ec_decision_date`, the date of the most recent European Commission
	decision, usually a variation such as a new indication; never present it as the
	approval date. An FDA row has `brand_names` and `fda_application_number` (NDA022561,
	BLA761053) and none of EMA's opinion, EC decision or designation fields. `link` is
	the product's EPAR page on ema.europa.eu for an EMA record, and the application's
	Drugs@FDA page for an FDA one: always show it, and name the regulator as the source.

	`ordering` is one of authorisation_date, latest_ec_decision_date,
	ema_last_updated, with an optional leading `-` for descending. The default is
	`-authorisation_date`, except for `regulator="ema"`, where it is `-ema_last_updated`.
	The last two are EMA's dates, so FDA records sort last on them.

	A zero-hit response adds a `guidance` key: which filters were applied and ranked
	suggestions for what is most likely over-constraining the search.
	"""
	clamped_page = clamp_page(page)
	params = {
		"name": name,
		"substance": substance,
		"regulator": regulator,
		"category_id": category_id,
		"category_slug": category_slug,
		"subject_id": subject_id,
		"status": status,
		"opinion_status": opinion_status,
		"authorised_after": authorised_after,
		"authorised_before": authorised_before,
		"include_generics": include_generics,
		"tracked": tracked,
		"ordering": ordering,
		"page": clamped_page,
		"page_size": clamp_page_size(page_size, MAX_PAGE_SIZE),
	}
	# Left alone, the API lists every EMA record before every FDA one (`-ema_last_updated`),
	# which buries the FDA ones past a first page. Unless the caller chose an order, or is
	# searching EMA alone, ask for the first approval, which both regulators have. Only the
	# request carries it: the zero-result guidance reads `params`, the caller's own choices.
	sent = dict(params)
	if ordering is None and regulator != "ema":
		sent["ordering"] = DEFAULT_MIXED_ORDERING
	data = await get_client().get("/medicines/", sent)
	results = data.get("results", [])
	count = data.get("count", len(results))
	# The sources of the rows shown; with none (a zero-hit search), those the search
	# was about. The note is narrowed to one regulator only when the caller asked for
	# it: an unfiltered search can reach the other one on a later page.
	regulators = [regulator] if regulator else ["ema", "fda"]
	sources = [medicine_source(m) for m in results] or [REGULATOR_SOURCES[r] for r in regulators]
	response: dict[str, Any] = {
		"source": _source_line(sources),
		"note": coverage_note(regulators),
		"count": count,
		"next_page": clamped_page + 1 if data.get("next") else None,
		"medicines": [compact_medicine(m) for m in results],
	}
	if count == 0:
		response["guidance"] = guidance_for(params, "medicines")
	elif (
		substance
		and regulator != "ema"
		and not data.get("next")
		and not any(medicine_regulator(m) == "fda" for m in results)
	):
		# Records came back but none is the FDA's: `substance` is an exact match, and the
		# FDA often names the ingredient with its salt, so its record may simply not match.
		response["fda_hint"] = FDA_SALT_HINT
	return response


async def get_medicine(medicine_id: int) -> dict:
	"""Fetch the full record for one medicine, from the European Medicines Agency
	(EMA) or the U.S. Food and Drug Administration (FDA): `regulator` and `source` say
	which. It has the substances, indication, status, every date, the categories it is
	linked to on this site (`categories`; `tracked` is true when there is at least one),
	and its regulatory history (`events`, newest first).

	An EMA record also has the flags EMA publishes (orphan, advanced therapy,
	conditional approval, ...), the conditions EMA approved it for (`subjects`), and
	the opinion, European Commission and withdrawal dates. An FDA record has instead
	`fda_application_number` and `fda_application_type` (NDA or BLA), `fda_center`
	(CDER or CBER), `brand_names`, `fda_priority_review`, `fda_original_class` (TYPE 1 is
	a new molecular entity) and `interchangeable` (a biologic the FDA allows to be
	substituted for its reference product); fields only the other regulator has are
	left out. `generic` and `biosimilar` apply to both. A medicine can have a record at
	each regulator: they are separate records with their own ids. The FDA names an
	ingredient as on its label, sometimes with the salt (fingolimod hydrochloride, where
	EMA has fingolimod), so to find the other regulator's record of a drug use
	search_medicines with `name` (the brand) or the salt form of `substance`, and group the
	two by the substance without the salt, not by exact `substances`.

	The FDA's data writes brand names, ingredients and companies in capitals (KESIMPTA,
	NOVARTIS): write them in normal case (Kesimpta, Novartis) when you present them.

	For a lead product, `grouped_products` lists the further products of the same
	regulator for the same medicine, and `grouped_under` on a duplicate gives its lead.

	Each event has `kind_label`, the sentence to use ("CHMP positive opinion",
	"Authorised in the EU", "New European Commission decision", "Indication changed" for
	EMA; "Approved by the FDA", "FDA approved a new indication or use" for the FDA), plus its `event_date`, `kind`,
	`basis` and, for a status or indication change, `from_value` and `to_value`. `basis`
	"observed" means the change was seen between two syncs; "reconstructed" means it was
	read off the regulator's date fields the first time the product was seen: real
	history, but not news. CHMP is EMA's scientific committee, so a CHMP opinion belongs
	to EMA records only.

	The data covers only what each regulator publishes. EMA lists centrally authorised
	medicines, and a drug missing from it may still be authorised in individual EU
	countries. The FDA data has approved new drugs and biologics (NDAs and BLAs) but not
	generic drugs (ANDAs) or applications under review. So "not in EMA's data" or "not in
	FDA's data" is not "not approved". On an EMA record `latest_ec_decision_date` is the
	most recent European Commission decision, usually a variation; the first approval is
	`authorisation_date` (the FDA's original approval, on an FDA record). Always show the
	record's `url` (an EMA record's EPAR page on ema.europa.eu, or an FDA record's
	Drugs@FDA page) and name the regulator in `source`.

	Raises:
		ToolError: If the medicine can't be found.
	"""
	try:
		medicine = await get_client().get(f"/medicines/{medicine_id}/")
	except GregoryAPIError as exc:
		# Django's 404 deliberately doesn't distinguish "doesn't exist" from "not
		# visible on this site" (no live category link or subject link).
		if exc.status_code == 404:
			raise ToolError(f"Medicine {medicine_id} was not found.") from exc
		raise
	return _shape_detail(strip_team_data(medicine))


def _shape_detail(medicine: dict) -> dict:
	"""`id` as `medicine_id` (the name the editor tool takes), the events without
	their row ids, the other regulator's empty fields left out, `source` always set,
	and the coverage note for the record's regulator."""
	regulator = medicine_regulator(medicine)
	record = drop_other_regulator_fields(medicine)
	shaped = {"medicine_id": record.get("id")}
	shaped.update({k: v for k, v in record.items() if k not in ("id", "events")})
	shaped["regulator"] = regulator
	shaped["source"] = medicine_source(medicine)
	shaped["events"] = [
		{k: v for k, v in event.items() if k != "id"} for event in medicine.get("events") or []
	]
	shaped["note"] = coverage_note([regulator])
	return shaped
