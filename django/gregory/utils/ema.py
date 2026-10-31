"""
Parsing and downloading EMA's public medicines export. Pure functions with no
database access, so the sync command and the tests share one reading of a row.

The export is one JSON file of every EMA medicine:
``{"meta": {"total_records", "timestamp"}, "data": [{...}, ...]}``. Every value is
a string. Dates are ``DD/MM/YYYY`` (empty when unknown) and flags are
``"Yes"`` / ``"No"``. Multi-valued fields are separated by ``;``.
"""

import html
import logging
import re
import time  # noqa: F401  (tests patch gregory.utils.ema.time.sleep)
from dataclasses import dataclass, field, fields
from datetime import date, datetime

import requests  # noqa: F401  (tests patch gregory.utils.ema.requests.get)

from gregory.utils.regulator_http import (  # noqa: F401  (re-exported)
	DOWNLOAD_RETRIES,
	DOWNLOAD_TIMEOUT,
	USER_AGENT,
	clip as _clip,
	get_with_retries,
)
from gregory.utils.substance_key import substance_key

logger = logging.getLogger(__name__)

EXPORT_URL = (
	"https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"
)
HUMAN = "Human"

_TAG_BLOCK = re.compile(r"</?(?:p|br|li|ul|ol|div|tr|td|th|h[1-6])\b[^<>]*>", re.IGNORECASE)
_TAG_ANY = re.compile(r"</?[a-zA-Z][^<>]*>")
_WHITESPACE = re.compile(r"\s+")

# Boolean columns copied one to one from the export.
FLAG_FIELDS = (
	"advanced_therapy",
	"orphan_medicine",
	"conditional_approval",
	"exceptional_circumstances",
	"prime_priority_medicine",
	"accelerated_assessment",
	"additional_monitoring",
	"biosimilar",
	"generic",
	"patient_safety",
)


def parse_date(value):
	"""``DD/MM/YYYY`` to a date. Empty gives None quietly; anything else that does not
	parse gives None and a warning, so one bad cell never stops a sync."""
	try:
		return _parse_date_strict(value)
	except ValueError:
		logger.warning("EMA export: unparseable date %r", value)
		return None


def _parse_date_strict(value):
	value = (value or "").strip()
	if not value:
		return None
	return datetime.strptime(value, "%d/%m/%Y").date()


def parse_flag(value):
	"""EMA writes flags as "Yes" / "No"."""
	return (value or "").strip().lower() == "yes"


def clean_text(value):
	"""Decode HTML entities, strip tags and collapse whitespace.

	Entities are decoded first, because some rows carry escaped tags (``&lt;p&gt;``).
	Block tags become a space so adjacent paragraphs do not run together."""
	text = html.unescape(value or "")
	text = _TAG_BLOCK.sub(" ", text)
	text = _TAG_ANY.sub("", text)
	return _WHITESPACE.sub(" ", text).strip()


def split_substances(inn, active_substance):
	"""Lowercased, whitespace-collapsed substances split on ``;``.

	Uses the INN, or the active substance when the INN is empty (95 human rows on
	2026-10-25). A combination product has several entries."""
	source = inn if (inn or "").strip() else active_substance
	substances = []
	for part in (source or "").split(";"):
		part = _WHITESPACE.sub(" ", part).strip().lower()
		# The column is varchar(255); a longer value must not stop the import.
		part = part[:255]
		if part and part not in substances:
			substances.append(part)
	return substances


def split_areas(value):
	"""``therapeutic_area_mesh`` separates MeSH headings with ``;``."""
	areas = []
	for part in (value or "").split(";"):
		part = _WHITESPACE.sub(" ", part).strip()[:255]
		if part and part not in areas:
			areas.append(part)
	return areas


def _int_or_none(value):
	try:
		return int((value or "").strip())
	except ValueError:
		return None


@dataclass
class ParsedMedicine:
	"""One export row in the shape of a ``Medicine``. ``raw`` is the untouched row."""

	ema_product_number: str
	name: str
	inn: str = ""
	active_substance: str = ""
	substances: list = field(default_factory=list)
	# "" when there are no substances: the sync gives the stored row medicine-<pk>.
	substance_key: str = ""
	is_combination: bool = False
	status: str = ""
	opinion_status: str = ""
	therapeutic_areas: list = field(default_factory=list)
	atc_code: str = ""
	pharmacotherapeutic_group: str = ""
	indication: str = ""
	holder: str = ""
	advanced_therapy: bool = False
	orphan_medicine: bool = False
	conditional_approval: bool = False
	exceptional_circumstances: bool = False
	prime_priority_medicine: bool = False
	accelerated_assessment: bool = False
	additional_monitoring: bool = False
	biosimilar: bool = False
	generic: bool = False
	patient_safety: bool = False
	rolling_review_start_date: date = None
	evaluation_start_date: date = None
	opinion_date: date = None
	authorisation_date: date = None
	latest_ec_decision_date: date = None
	refusal_date: date = None
	application_withdrawal_date: date = None
	suspension_date: date = None
	end_date: date = None
	latest_procedure: str = ""
	revision_number: int = None
	ema_first_published: date = None
	ema_last_updated: date = None
	url: str = ""
	raw: dict = field(default_factory=dict)
	# Export columns whose date did not parse. Not stored on the model.
	bad_dates: list = field(default_factory=list)

	def model_values(self):
		"""Every ``Medicine`` column this row sets, keyed by field name."""
		return {
			f.name: getattr(self, f.name)
			for f in fields(self)
			if f.name != "bad_dates"
		}


# Medicine column -> export column, for the dates.
DATE_COLUMNS = {
	"rolling_review_start_date": "start_of_rolling_review_date",
	"evaluation_start_date": "start_of_evaluation_date",
	"opinion_date": "opinion_adopted_date",
	"authorisation_date": "marketing_authorisation_date",
	"latest_ec_decision_date": "european_commission_decision_date",
	"refusal_date": "refusal_of_marketing_authorisation_date",
	"application_withdrawal_date": "withdrawal_of_application_date",
	"suspension_date": "suspension_of_marketing_authorisation_date",
	"end_date": "withdrawal_expiry_revocation_lapse_of_marketing_authorisation_date",
	"ema_first_published": "first_published_date",
	"ema_last_updated": "last_updated_date",
}


def is_human(row):
	return (row.get("category") or "").strip() == HUMAN


def parse_row(row):
	"""Turn one export row into a ParsedMedicine. Bad dates become None and are listed
	in ``bad_dates``; nothing here raises on odd data."""
	inn = _clip(row.get("international_non_proprietary_name_common_name"), 500)
	active_substance = (row.get("active_substance") or "").strip()
	substances = split_substances(inn, active_substance)

	values = {}
	bad_dates = []
	for attr, column in DATE_COLUMNS.items():
		try:
			values[attr] = _parse_date_strict(row.get(column))
		except ValueError:
			logger.warning(
				"EMA export: unparseable date %r in %s for %s",
				row.get(column),
				column,
				row.get("ema_product_number"),
			)
			values[attr] = None
			bad_dates.append(column)

	for flag in FLAG_FIELDS:
		values[flag] = parse_flag(row.get(flag))

	return ParsedMedicine(
		ema_product_number=_clip(row.get("ema_product_number"), 32),
		name=_clip(row.get("name_of_medicine"), 255),
		inn=inn,
		active_substance=active_substance,
		substances=substances,
		substance_key=substance_key(substances),
		is_combination=len(substances) > 1,
		status=_clip(row.get("medicine_status"), 50),
		opinion_status=_clip(row.get("opinion_status"), 20),
		therapeutic_areas=split_areas(row.get("therapeutic_area_mesh")),
		atc_code=_clip(row.get("atc_code_human"), 20),
		pharmacotherapeutic_group=_clip(row.get("pharmacotherapeutic_group_human"), 255),
		indication=clean_text(row.get("therapeutic_indication")),
		holder=_clip(row.get("marketing_authorisation_developer_applicant_holder"), 255),
		latest_procedure=_clip(row.get("latest_procedure_affecting_product_information"), 50),
		revision_number=_int_or_none(row.get("revision_number")),
		url=_clip(row.get("medicine_url"), 500),
		raw=row,
		bad_dates=bad_dates,
		**values,
	)


def download_export(last_modified=""):
	"""Fetch the export. Returns ``(status, body_bytes, last_modified)``.

	Sends ``If-Modified-Since`` when ``last_modified`` is given; a 304 comes back with an
	empty body and the same ``last_modified``. Retries twice on a 5xx or a connection
	problem. Any other status is returned as is for the caller to judge."""
	headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
	if last_modified:
		headers["If-Modified-Since"] = last_modified
	response = get_with_retries(EXPORT_URL, headers, "EMA export")
	if response.status_code == 304:
		return 304, b"", last_modified
	return (
		response.status_code,
		response.content,
		response.headers.get("Last-Modified", ""),
	)
