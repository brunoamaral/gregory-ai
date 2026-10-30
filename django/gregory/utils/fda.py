"""
Parsing and downloading the FDA's public medicine files. Pure functions with no
database access, so the sync command and the tests share one reading of a record.

Two files make up the FDA side of the Medicine table:

* Drugs@FDA, the openFDA bulk file: ``{"meta": {...}, "results": [application, ...]}``.
  Every NDA, ANDA and CDER BLA, with its products and every submission. ANDAs (generics)
  are not imported. Dates are ``YYYYMMDD``.
* The Purple Book, a monthly CSV of every licensed biologic. It is the only place CBER
  biologics (vaccines, blood products, cell and gene therapies) appear. Dates read
  ``August 30, 2017``.

An application can be missing ``products``, ``submissions`` or ``openfda``, so every read
here defaults to empty.
"""

import csv
import hashlib
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field, fields
from datetime import date, datetime

from gregory.utils.regulator_http import USER_AGENT, clip as _clip, get_with_retries

logger = logging.getLogger(__name__)

DOWNLOAD_INDEX_URL = "https://api.fda.gov/download.json"
PURPLEBOOK_DOWNLOADS_URL = "https://purplebooksearch.fda.gov/downloads"

DRUGS_AT_FDA_OVERVIEW = (
	"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={digits}"
)

# Event kinds this module sets; the values of MedicineEvent.Kind. Plain strings keep this
# module free of the models.
FDA_APPROVED = "fda_approved"
FDA_TENTATIVE_APPROVAL = "fda_tentative_approval"
FDA_EFFICACY_SUPPLEMENT = "fda_efficacy_supplement"

# Medicine.status values for FDA rows.
APPROVED = "Approved"
TENTATIVELY_APPROVED = "Tentatively approved"
DISCONTINUED = "Discontinued"
REVOKED = "Revoked"

_WHITESPACE = re.compile(r"\s+")
# FDA's four-letter suffix on a biologic's name: natalizumab-sztn.
_SUFFIX = re.compile(r"-[a-z]{4}$")


# --- dates and names -----------------------------------------------------


def _parse_fda_date_strict(value):
	value = (value or "").strip()
	if not value:
		return None
	return datetime.strptime(value, "%Y%m%d").date()


def parse_fda_date(value):
	"""``YYYYMMDD`` to a date. Empty gives None quietly; anything else that does not
	parse gives None and a warning, so one bad cell never stops a sync."""
	try:
		return _parse_fda_date_strict(value)
	except ValueError:
		logger.warning("Drugs@FDA: unparseable date %r", value)
		return None


def _parse_purplebook_date_strict(value):
	value = (value or "").strip()
	if not value:
		return None
	return datetime.strptime(value, "%B %d, %Y").date()


def parse_purplebook_date(value):
	"""``August 30, 2017`` to a date; the same rules as ``parse_fda_date``."""
	try:
		return _parse_purplebook_date_strict(value)
	except ValueError:
		logger.warning("Purple Book: unparseable date %r", value)
		return None


def normalise_substance(name):
	"""The form ``Medicine.substances`` stores: lowercase, whitespace collapsed, and
	the FDA's four-letter suffix removed (``NATALIZUMAB-SZTN`` becomes ``natalizumab``).

	Only a suffix of exactly four lowercase letters after the last hyphen goes: a name
	ending in a longer hyphenated word is left alone."""
	text = _WHITESPACE.sub(" ", (name or "").lower()).strip()
	return _SUFFIX.sub("", text)


def raw_digest(raw):
	"""sha256 of ``raw`` as canonical JSON. A sync compares this instead of loading and
	comparing the stored ``raw``, which is about 40 MB of JSON across the Drugs@FDA rows."""
	canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _distinct(values):
	"""The non-empty values in order, without repeats (ignoring case)."""
	seen = set()
	result = []
	for value in values:
		key = value.lower()
		if value and key not in seen:
			seen.add(key)
			result.append(value)
	return result


# --- the parsed shape ----------------------------------------------------


@dataclass
class ParsedFdaMedicine:
	"""One FDA application in the shape of a ``Medicine``. ``raw`` is the untouched
	record; ``events`` the dated events its submissions give (``kind``, ``event_date``,
	``from_value``, ``to_value``); ``bad_dates`` the columns whose date did not parse."""

	fda_application_number: str
	fda_application_type: str
	fda_center: str
	name: str
	regulator: str = "fda"
	inn: str = ""
	active_substance: str = ""
	substances: list = field(default_factory=list)
	is_combination: bool = False
	status: str = ""
	pharmacotherapeutic_group: str = ""
	fda_pharm_classes: list = field(default_factory=list)
	holder: str = ""
	biosimilar: bool = False
	interchangeable: bool = False
	authorisation_date: date = None
	brand_names: list = field(default_factory=list)
	fda_original_class: str = ""
	fda_priority_review: bool = None
	url: str = ""
	raw: dict = field(default_factory=dict)
	raw_digest: str = ""
	events: list = field(default_factory=list)
	bad_dates: list = field(default_factory=list)

	def model_values(self):
		"""Every ``Medicine`` column this record sets, keyed by field name."""
		return {
			f.name: getattr(self, f.name)
			for f in fields(self)
			if f.name not in ("events", "bad_dates")
		}


def _event(kind, event_date, to_value=""):
	return {"kind": kind, "event_date": event_date, "from_value": "", "to_value": to_value}


def _fold_same_day(events):
	"""Two approvals of the same kind on one day are one event (the table's unique
	constraint would drop the second anyway); their submission numbers are joined."""
	folded = {}
	for event in events:
		key = (event["kind"], event["event_date"])
		if key in folded:
			if event["to_value"] and event["to_value"] not in folded[key]["to_value"].split(", "):
				joined = ", ".join(filter(None, [folded[key]["to_value"], event["to_value"]]))
				folded[key]["to_value"] = joined
		else:
			folded[key] = dict(event)
	return list(folded.values())


# --- Drugs@FDA -----------------------------------------------------------


def application_type(record):
	"""``NDA``, ``BLA`` or ``ANDA`` from the application number; "" for anything else."""
	number = (record.get("application_number") or "").strip().upper()
	for prefix in ("ANDA", "NDA", "BLA"):
		if number.startswith(prefix):
			return prefix
	return ""


def _product_status(products):
	statuses = [(p.get("marketing_status") or "").strip().lower() for p in products]
	if any(s in ("prescription", "over-the-counter") for s in statuses):
		return APPROVED
	if statuses and all("tentative" in s for s in statuses):
		return TENTATIVELY_APPROVED
	return DISCONTINUED


def _submission_to_value(submission):
	if (submission.get("submission_type") or "").strip().upper() == "SUPPL":
		number = (submission.get("submission_number") or "").strip()
		return f"S-{number}" if number else ""
	return ""


def parse_application(record):
	"""Turn one Drugs@FDA NDA or BLA record into a ParsedFdaMedicine.

	Returns None when the record has no application number or is an ANDA. Bad dates
	become None and are listed in ``bad_dates``; nothing here raises on odd data."""
	number = (record.get("application_number") or "").strip().upper()
	kind = application_type(record)
	if not number or kind in ("", "ANDA"):
		return None
	products = record.get("products") or []
	submissions = record.get("submissions") or []
	openfda = record.get("openfda") or {}

	brand_names = _distinct(_clip(p.get("brand_name"), 255) for p in products)
	ingredient_names = _distinct(
		_clip(i.get("name"), 255)
		for p in products
		for i in (p.get("active_ingredients") or [])
	)
	substances = []
	for name in ingredient_names:
		substance = normalise_substance(name)[:255]
		if substance and substance not in substances:
			substances.append(substance)
	ingredient_text = "; ".join(ingredient_names)
	classes = _distinct(_clip(c, 255) for c in (openfda.get("pharm_class_epc") or []))

	bad_dates = []
	events = []
	approvals = []  # (date, submission) of every approved original
	originals = []  # every original, approved or not
	for submission in submissions:
		sub_type = (submission.get("submission_type") or "").strip().upper()
		status = (submission.get("submission_status") or "").strip().upper()
		raw_date = submission.get("submission_status_date")
		try:
			sub_date = _parse_fda_date_strict(raw_date)
		except ValueError:
			logger.warning("Drugs@FDA: unparseable date %r for %s", raw_date, number)
			sub_date = None
			bad_dates.append("submission_status_date")
		if sub_type == "ORIG":
			originals.append((sub_date, submission))
			if status == "AP":
				approvals.append((sub_date, submission))
		if sub_date is None:
			continue
		# Decision 5: only an original and an efficacy supplement are events. A tentatively
		# approved labeling or manufacturing (CMC) supplement is not.
		is_efficacy = (submission.get("submission_class_code") or "").strip().upper() == "EFFICACY"
		if status == "TA" and (sub_type == "ORIG" or (sub_type == "SUPPL" and is_efficacy)):
			events.append(_event(FDA_TENTATIVE_APPROVAL, sub_date, _submission_to_value(submission)))
		elif status == "AP" and sub_type == "SUPPL" and is_efficacy:
			events.append(_event(FDA_EFFICACY_SUPPLEMENT, sub_date, _submission_to_value(submission)))

	# The original approval is the earliest approved ORIG that carries a date.
	dated_approvals = sorted((d, i) for i, (d, _) in enumerate(approvals) if d is not None)
	original = None
	authorisation_date = None
	if dated_approvals:
		authorisation_date, index = dated_approvals[0]
		original = approvals[index][1]
		events.append(_event(FDA_APPROVED, authorisation_date))
	elif approvals:
		original = approvals[0][1]
	elif originals:
		original = originals[0][1]

	priority = (original or {}).get("review_priority")
	priority = (priority or "").strip().upper()
	return ParsedFdaMedicine(
		fda_application_number=number,
		fda_application_type=kind,
		fda_center="CDER",
		name=" / ".join(brand_names)[:255] or ingredient_text[:255] or number,
		inn=ingredient_text[:500],
		active_substance=ingredient_text,
		substances=substances,
		is_combination=any(len(p.get("active_ingredients") or []) > 1 for p in products),
		status=_product_status(products),
		pharmacotherapeutic_group=classes[0] if classes else "",
		fda_pharm_classes=classes,
		holder=_clip(record.get("sponsor_name"), 255),
		authorisation_date=authorisation_date,
		brand_names=brand_names,
		fda_original_class=_clip((original or {}).get("submission_class_code"), 30),
		fda_priority_review=True if priority == "PRIORITY" else False if priority == "STANDARD" else None,
		url=DRUGS_AT_FDA_OVERVIEW.format(digits=number[len(kind):]),
		raw=record,
		raw_digest=raw_digest(record),
		events=_fold_same_day(events),
		bad_dates=bad_dates,
	)


_RESULTS_ARRAY = re.compile(r'"results"\s*:\s*\[')


def drugsfda_text(body):
	"""The JSON text of a Drugs@FDA download: ``body`` is the zip as served, or plain JSON."""
	if body[:2] == b"PK":
		with zipfile.ZipFile(io.BytesIO(body)) as archive:
			names = [n for n in archive.namelist() if n.lower().endswith(".json")]
			if len(names) != 1:
				raise ValueError(f"expected one JSON file in the zip, found {len(names)}")
			body = archive.read(names[0])
	return body.decode("utf-8")


def read_drugsfda(text):
	"""Split the bulk file's JSON text into ``(meta, results)``, where ``results`` is a
	generator of application dicts.

	The file is 124 MB; decoding it in one go costs about a gigabyte, so the application
	list is decoded one record at a time and the caller drops the ANDAs it does not keep.
	Raises ValueError when the text is not the expected shape or is cut off."""
	match = _RESULTS_ARRAY.search(text)
	meta = None
	if match:
		try:
			meta = json.loads(text[: match.start()].rstrip().rstrip(",") + "}").get("meta")
		except ValueError:
			meta = None
	if meta is None:
		# Keys in another order, or an unfamiliar layout: take the memory hit.
		payload = json.loads(text)
		if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
			raise ValueError("Drugs@FDA file has no 'results' list")
		return payload.get("meta") or {}, iter(payload["results"])
	return meta, _iter_array(text, match.end())


def _iter_array(text, position):
	decoder = json.JSONDecoder()
	length = len(text)
	while True:
		while position < length and text[position] in " \t\r\n,":
			position += 1
		if position >= length:
			raise ValueError("Drugs@FDA file is cut off inside the results list")
		if text[position] == "]":
			return
		record, position = decoder.raw_decode(text, position)
		yield record


# --- Purple Book ---------------------------------------------------------

PURPLEBOOK_COLUMNS = (
	"N/R/U",
	"Applicant",
	"BLA Number",
	"Proprietary Name",
	"Proper Name",
	"License Type",
	"Strength",
	"Dosage Form",
	"Route of Administration",
	"Product Presentation",
	"Marketing Status",
	"Licensure",
	"Approval Date",
	"Inter. Approval Date",
	"Ref. Product Proper Name",
	"Ref. Product Proprietary Name",
	"Supplement Number",
	"Submission Type",
	"Inter. Supplement Number",
	"License Number",
	"Product Number",
	"Center",
	"Date of First Licensure",
	"Exclusivity Expiration Date",
	"First Interchangeable Exclusivity Exp. Date",
	"Ref. Product Exclusivity Exp. Date",
	"Orphan Exclusivity Exp. Date",
	"Patent List Provided",
)
_REVOKED_LICENSURE = ("revoked", "voluntarily revoked")


def bla_key(number):
	"""``125646`` to ``BLA125646`` (zero-padded to six digits); "" when there are no digits."""
	digits = re.sub(r"\D", "", number or "")
	return f"BLA{digits.zfill(6)}" if digits else ""


def _purplebook_rows(text):
	"""The full-list rows of the Purple Book CSV as dicts keyed by column name, every
	cell stripped. Raises ValueError when the columns are not the expected ones."""
	rows = list(csv.reader(io.StringIO(text, newline="")))
	headers = [i for i, row in enumerate(rows) if row and row[0].strip() == "N/R/U"]
	if not headers:
		raise ValueError("Purple Book file has no 'N/R/U' header row")
	# The month's changes come first under one header, then the full list under a second
	# copy of it: read only what follows the last one.
	names = [cell.strip() for cell in rows[headers[-1]]]
	if set(names) != set(PURPLEBOOK_COLUMNS):
		missing = sorted(set(PURPLEBOOK_COLUMNS) - set(names))
		extra = sorted(set(names) - set(PURPLEBOOK_COLUMNS))
		raise ValueError(f"Purple Book columns changed (missing {missing}, new {extra})")
	records = []
	for row in rows[headers[-1] + 1 :]:
		if not any(cell.strip() for cell in row):
			continue
		cells = [cell.strip() for cell in row] + [""] * (len(names) - len(row))
		record = dict(zip(names, cells))
		if record["BLA Number"]:
			records.append(record)
	return records


def _cber_medicine(key, rows):
	brand_names = _distinct(row["Proprietary Name"][:255] for row in rows)
	proper_names = _distinct(row["Proper Name"][:255] for row in rows)
	# A proper name is one substance, however it reads: FDA's CBER names carry a suffix
	# and some are lists ("... and ..."). Splitting them would invent ingredients.
	substances = []
	for name in proper_names:
		substance = normalise_substance(name)[:255]
		if substance and substance not in substances:
			substances.append(substance)

	bad_dates = []
	originals = [r for r in rows if r["Submission Type"].lower() == "original"] or rows
	dates = []
	for row in originals:
		try:
			parsed = _parse_purplebook_date_strict(row["Approval Date"])
		except ValueError:
			logger.warning("Purple Book: unparseable date %r for %s", row["Approval Date"], key)
			parsed = None
			bad_dates.append("Approval Date")
		if parsed is not None:
			dates.append(parsed)
	authorisation_date = min(dates) if dates else None

	live = [r for r in rows if r["Licensure"].lower() not in _REVOKED_LICENSURE]
	if not live:
		status = REVOKED
	elif any(r["Marketing Status"].lower() in ("rx", "otc") for r in live):
		status = APPROVED
	else:
		status = DISCONTINUED

	return ParsedFdaMedicine(
		fda_application_number=key,
		fda_application_type="BLA",
		fda_center="CBER",
		name=" / ".join(brand_names)[:255] or "; ".join(proper_names)[:255] or key,
		inn="; ".join(proper_names)[:500],
		active_substance="; ".join(proper_names),
		substances=substances,
		is_combination=False,
		status=status,
		holder=_clip(rows[0]["Applicant"], 255),
		biosimilar=any(r["License Type"].lower().startswith("351(k)") for r in rows),
		interchangeable=any("interchangeable" in r["License Type"].lower() for r in rows),
		authorisation_date=authorisation_date,
		brand_names=brand_names,
		# The Purple Book has no per-product page; a later step fills it from DailyMed.
		url="",
		raw={"rows": rows},
		raw_digest=raw_digest({"rows": rows}),
		events=[_event(FDA_APPROVED, authorisation_date)] if authorisation_date else [],
		bad_dates=bad_dates,
	)


def parse_purplebook(text):
	"""Read the Purple Book CSV. Returns ``(cber, cder_flags)``.

	``cber`` is a ParsedFdaMedicine per CBER BLA (its rows grouped by BLA number, brand
	names in row order). ``cder_flags`` maps ``BLA125646`` to ``{"biosimilar",
	"interchangeable", "licensure"}`` for each CDER BLA: Drugs@FDA holds their history, the
	Purple Book only says whether they are biosimilar or interchangeable. Raises
	ValueError when the header differs from the expected columns."""
	by_bla = {}
	for record in _purplebook_rows(text):
		key = bla_key(record["BLA Number"])
		if key:
			by_bla.setdefault(key, []).append(record)

	cber = []
	cder_flags = {}
	for key, rows in by_bla.items():
		if rows[0]["Center"].upper() == "CBER":
			cber.append(_cber_medicine(key, rows))
			continue
		licensures = _distinct(r["Licensure"] for r in rows)
		live = [r["Licensure"] for r in rows if r["Licensure"].lower() not in _REVOKED_LICENSURE]
		cder_flags[key] = {
			"biosimilar": any(r["License Type"].lower().startswith("351(k)") for r in rows),
			"interchangeable": any("interchangeable" in r["License Type"].lower() for r in rows),
			"licensure": live[0] if live else (licensures[0] if licensures else ""),
		}
	return cber, cder_flags


def find_purplebook_url(html):
	"""The newest monthly CSV link on the Purple Book downloads page.

	Links look like ``.../PurpleBook/2026/purplebook-search-September-data-download.csv``;
	the month's case varies (``january``, ``February``). Raises ValueError when there is none."""
	months = {
		name: number
		for number, name in enumerate(
			"january february march april may june july august september october november december".split(),
			start=1,
		)
	}
	pattern = re.compile(
		r"https?://[^\"'\s<>]*/PurpleBook/(\d{4})/purplebook-search-([A-Za-z]+)-data-download\.csv",
		re.IGNORECASE,
	)
	best = None
	for match in pattern.finditer(html):
		month = months.get(match.group(2).lower())
		if month is None:
			continue
		candidate = (int(match.group(1)), month, match.group(0))
		if best is None or candidate[:2] > best[:2]:
			best = candidate
	if best is None:
		raise ValueError("no Purple Book CSV link on the downloads page")
	return best[2]


# --- downloading ---------------------------------------------------------


def _get(url, headers):
	return get_with_retries(url, headers, "FDA download of " + url)


def _conditional_headers(state):
	headers = {"User-Agent": USER_AGENT}
	if state is not None:
		if state.last_modified:
			headers["If-Modified-Since"] = state.last_modified
		if state.etag:
			headers["If-None-Match"] = state.etag
	return headers


def _result(response, url):
	return (
		response.status_code,
		b"" if response.status_code == 304 else response.content,
		{
			"Last-Modified": response.headers.get("Last-Modified", ""),
			"ETag": response.headers.get("ETag", ""),
			"url": url,
		},
	)


def drugsfda_url():
	"""The current Drugs@FDA bulk file, from openFDA's download index."""
	response = _get(DOWNLOAD_INDEX_URL, {"User-Agent": USER_AGENT})
	response.raise_for_status()
	return response.json()["results"]["drug"]["drugsfda"]["partitions"][0]["file"]


def download_drugsfda(state=None):
	"""Fetch the Drugs@FDA bulk file. Returns ``(status, body_bytes, headers)``.

	``state`` is the Drugs@FDA ``FdaSyncState`` (or None to skip the conditional headers):
	its ``Last-Modified`` and ``ETag`` go out as ``If-Modified-Since`` and ``If-None-Match``,
	and a 304 comes back with an empty body. ``headers`` holds ``Last-Modified``, ``ETag``
	and the ``url`` that was read."""
	url = drugsfda_url()
	response = _get(url, _conditional_headers(state))
	return _result(response, url)


def download_purplebook(state=None, url=None):
	"""Fetch the Purple Book CSV: ``url`` when given, otherwise the newest link on the
	downloads page. Returns ``(status, body_bytes, headers)`` as ``download_drugsfda`` does.

	The conditional headers go out only when the file is the one the last sync read, since
	a new month is a new file."""
	if url is None:
		page = _get(PURPLEBOOK_DOWNLOADS_URL, {"User-Agent": USER_AGENT})
		page.raise_for_status()
		url = find_purplebook_url(page.text)
	same_file = state is not None and state.source_url == url
	response = _get(url, _conditional_headers(state if same_file else None))
	return _result(response, url)
