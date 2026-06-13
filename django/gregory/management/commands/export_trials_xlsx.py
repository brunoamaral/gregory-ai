import json
import re
from datetime import datetime, date, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.db.models import GeneratedField
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from gregory.models import Trials, Subject


EXCLUDED_SCALARS = frozenset({"utitle", "usummary"})
EXCLUDED_M2M = frozenset({"ml_predictions"})

# Ordered column groups for data sheets
IDENTITY_COLS = [
	"trial_id",
	"title",
	"acronym",
	"scientific_title",
	"link",
	"discovery_date",
	"last_updated",
	"published_date",
	"date_registration",
	"last_refreshed_on",
	"export_date",
]

SCALAR_ORDER = [
	"summary",
	"internal_number",
	"secondary_id",
	"source_register",
	"other_records",
	"prospective_registration",
	"study_type",
	"study_design",
	"phase",
	"recruitment_status",
	"target_size",
	"date_enrollement",
	"countries",
	"condition",
	"intervention",
	"primary_outcome",
	"secondary_outcome",
	"inclusion_criteria",
	"exclusion_criteria",
	"inclusion_agemin",
	"inclusion_agemax",
	"inclusion_gender",
	"primary_sponsor",
	"secondary_sponsor",
	"source_support",
	"sponsor_type",
	"contact_firstname",
	"contact_lastname",
	"contact_address",
	"contact_email",
	"contact_tel",
	"contact_affiliation",
	"ethics_review_status",
	"ethics_review_approval_date",
	"ethics_review_contact_name",
	"ethics_review_contact_address",
	"ethics_review_contact_phone",
	"ethics_review_contact_email",
	"results_posted",
	"results_date_completed",
	"results_url_link",
	"results_yes_no",
	"results_ipd_plan",
	"results_ipd_description",
	"therapeutic_areas",
	"country_status",
	"trial_region",
	"overall_decision_date",
	"countries_decision_date",
	"ctg_detailed_description",
]

RELATION_COLS = ["subjects", "teams", "sources", "team_categories", "articles"]

# Descriptions for exported columns absent from TrialAdminForm.Meta.help_texts.
# Format: field_name → (label, description, source_registries)
EXTRA_GLOSSARY = {
	"trial_id": (
		"Trial ID",
		"Internal Gregory database identifier for this trial record.",
		"",
	),
	"summary": (
		"Summary",
		"Plain-language summary of the trial.",
		"WHO ICTRP, ClinicalTrials.gov, EU CTIS",
	),
	"discovery_date": (
		"Discovery date",
		"Date this trial was first added to Gregory.",
		"",
	),
	"last_updated": (
		"Last updated",
		"Date and time this record was last modified in Gregory.",
		"",
	),
	"identifiers_json": (
		"Identifiers (raw JSON)",
		"Raw JSON dict of all registry identifiers for this trial.",
		"WHO ICTRP, ClinicalTrials.gov, EU CTIS",
	),
	"subjects": (
		"Subjects",
		"Research subjects this trial is assigned to in Gregory (semicolon-separated).",
		"",
	),
	"teams": (
		"Teams",
		"Teams this trial is assigned to in Gregory (semicolon-separated).",
		"",
	),
	"sources": (
		"Sources",
		"Registry sources that provided data for this trial (semicolon-separated).",
		"",
	),
	"team_categories": (
		"Categories",
		"Team categories assigned to this trial (semicolon-separated).",
		"",
	),
	"articles": (
		"Related articles",
		"Count of articles that reference this trial, followed by their URLs.",
		"",
	),
	"therapeutic_areas": (
		"Therapeutic areas",
		"Therapeutic areas covered by the trial.",
		"EU CTIS",
	),
	"country_status": (
		"Country status",
		"Authorisation status of the trial in each participating country.",
		"EU CTIS",
	),
	"trial_region": ("Trial region", "Geographic region of the trial.", "EU CTIS"),
	"overall_decision_date": (
		"Overall decision date",
		"Date of the overall regulatory decision for the trial.",
		"EU CTIS",
	),
	"countries_decision_date": (
		"Countries decision dates",
		"JSON map of per-country regulatory decision dates.",
		"EU CTIS",
	),
	"sponsor_type": (
		"Sponsor type",
		"Whether the sponsor is commercial or non-commercial.",
		"EU CTIS",
	),
	"ctg_detailed_description": (
		"Detailed description",
		"Extended description from ClinicalTrials.gov.",
		"ClinicalTrials.gov",
	),
}

REGISTRIES_OVERVIEW = [
	(
		"WHO ICTRP",
		"Varies (nct, euctr, chictr, nl, …)",
		"Aggregator of national/regional registries worldwide",
		"Richest field coverage: ethics review, IPD plans, enrolment dates, full sponsor "
		"info. May lag primary registries; can overwrite a field with an empty value on "
		"re-import.",
	),
	(
		"ClinicalTrials.gov",
		"nct",
		"US-hosted global registry",
		"Provides ctg_detailed_description and results_url_link. Combines exclusion into "
		"inclusion criteria. Never blanks a field on update (non-destructive).",
	),
	(
		"EU Clinical Trials (CTIS)",
		"euctr / eudract / ctis",
		"EU trials register",
		"Provides therapeutic_areas, country_status, trial_region, overall_decision_date, "
		"countries_decision_date, sponsor_type. Does not provide date_registration. "
		"May overwrite fields with empty values on re-import.",
	),
]

MERGE_PROSE = (
	"A single trial can be ingested from more than one source registry. Gregory stores "
	"one row per trial and merges data on re-import using a last-write-wins strategy. "
	"ClinicalTrials.gov never blanks a field it previously populated; WHO ICTRP and EU "
	"CTIS may overwrite existing values — including with empty ones — if the incoming "
	"record omits a field. The identifiers column is always merged non-destructively: "
	"keys are added, never removed. Fields produced by only one registry (e.g. EU CTIS "
	"therapeutic_areas or CT.gov ctg_detailed_description) are set only by their "
	"respective importer and are never in conflict."
)

REGISTRY_NAMES = ["WHO ICTRP", "ClinicalTrials.gov", "EU CTIS"]

# Column widths for data sheets
_WIDE_COLS = {
	"title",
	"scientific_title",
	"summary",
	"ctg_detailed_description",
	"inclusion_criteria",
	"exclusion_criteria",
	"intervention",
	"primary_outcome",
	"secondary_outcome",
	"study_design",
	"condition",
	"results_ipd_description",
	"therapeutic_areas",
	"country_status",
	"articles",
}
_URL_COLS = {"link", "results_url_link", "identifiers_json"}


def _sanitise_sheet_name(name, used):
	"""Return a valid, unique Excel sheet name (≤31 chars, no illegal characters)."""
	name = re.sub(r"[\\/*?\[\]:]", "_", name)[:31]
	base, suffix = name, 1
	while name in used:
		suffix += 1
		name = base[:28] + f"_{suffix}"
	used.add(name)
	return name


def _cell_value(value):
	"""Convert a Python value to an Excel-safe type (None → empty string)."""
	if value is None:
		return ""
	if isinstance(value, datetime):
		if value.tzinfo is not None:
			value = value.astimezone(dt_timezone.utc).replace(tzinfo=None)
		return value
	if isinstance(value, date):
		return value
	if isinstance(value, dict):
		return json.dumps(value, ensure_ascii=False)
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return value
	return str(value)


def _apply_header(ws, columns):
	"""Write a bold, coloured header row and return the cell count."""
	hdr_font = Font(bold=True, color="FFFFFF")
	hdr_fill = PatternFill(fill_type="solid", fgColor="2F4F8F")
	hdr_align = Alignment(vertical="center")
	for col_idx, name in enumerate(columns, 1):
		cell = ws.cell(row=1, column=col_idx, value=name)
		cell.font = hdr_font
		cell.fill = hdr_fill
		cell.alignment = hdr_align
	return len(columns)


def _set_column_widths(ws, columns):
	for col_idx, name in enumerate(columns, 1):
		letter = get_column_letter(col_idx)
		if name in _WIDE_COLS:
			ws.column_dimensions[letter].width = 50
		elif name in _URL_COLS:
			ws.column_dimensions[letter].width = 40
		elif name.startswith("id_"):
			ws.column_dimensions[letter].width = 20
		else:
			ws.column_dimensions[letter].width = max(12, min(40, len(name) + 4))


def _build_scalar_columns():
	"""
	Return an ordered list of scalar Trials column names for export.
	Known columns follow IDENTITY_COLS + SCALAR_ORDER; unrecognised columns are appended.
	Excludes: GeneratedField columns, m2m/relation fields, and 'identifiers' (expanded
	separately into id_* columns and identifiers_json).
	"""
	known_order = IDENTITY_COLS + SCALAR_ORDER
	# 'identifiers' is handled separately; EXCLUDED_* are never exported
	known_set = set(known_order) | {"identifiers"} | EXCLUDED_SCALARS | EXCLUDED_M2M
	unknown = []
	for f in Trials._meta.get_fields():
		if f.is_relation:
			continue
		if isinstance(f, GeneratedField):
			continue
		if f.name in known_set:
			continue
		unknown.append(f.name)
	return known_order + sorted(unknown)


def _parse_help_text(text):
	"""
	Split a help_text string into (description, source_registries_string).
	Looks for a trailing 'Sources?: …' clause.
	"""
	match = re.search(r"\s+Sources?:\s*(.+?)\.?\s*$", text, re.IGNORECASE)
	if match:
		return text[: match.start()].strip(), match.group(1).strip().rstrip(".")
	return text.strip(), ""


def _sources_for(col_name, admin_labels, admin_help, model_help):
	"""Return (label, description, source_str) for one exported column."""
	if col_name.startswith("id_"):
		key = col_name[3:]
		return (
			f"Identifier: {key.upper()}",
			f'Registry identifier key "{key}" extracted from the identifiers JSON.',
			"WHO ICTRP, ClinicalTrials.gov, EU CTIS",
		)
	if col_name in EXTRA_GLOSSARY:
		return EXTRA_GLOSSARY[col_name]
	if col_name in admin_help:
		label = admin_labels.get(col_name, col_name)
		desc, sources = _parse_help_text(admin_help[col_name])
		return label, desc, sources
	if col_name in model_help:
		label = admin_labels.get(col_name, col_name)
		desc, sources = _parse_help_text(model_help[col_name])
		return label, desc, sources
	return admin_labels.get(col_name, col_name), "", ""


def _build_glossary_sheet(wb, all_data_cols):
	"""Add a Glossary sheet — one row per exported column."""
	from gregory.admin import TrialAdminForm

	admin_labels = TrialAdminForm.Meta.labels
	admin_help = TrialAdminForm.Meta.help_texts
	model_help = {
		f.name: f.help_text
		for f in Trials._meta.get_fields()
		if not f.is_relation and hasattr(f, "help_text") and f.help_text
	}

	ws = wb.create_sheet(title="Glossary")
	headers = ["Field", "Label", "Description", "Source registries"]
	_apply_header(ws, headers)
	ws.freeze_panes = "A2"
	ws.column_dimensions["A"].width = 32
	ws.column_dimensions["B"].width = 32
	ws.column_dimensions["C"].width = 65
	ws.column_dimensions["D"].width = 32

	for row_idx, col_name in enumerate(all_data_cols, 2):
		label, desc, sources = _sources_for(
			col_name, admin_labels, admin_help, model_help
		)
		ws.cell(row=row_idx, column=1, value=col_name)
		ws.cell(row=row_idx, column=2, value=label)
		cell_desc = ws.cell(row=row_idx, column=3, value=desc)
		cell_desc.alignment = Alignment(wrap_text=True)
		ws.cell(row=row_idx, column=4, value=sources)


def _build_registries_sheet(wb, all_data_cols):
	"""Add a Registries sheet with a prose overview, registry table, and field matrix."""
	from gregory.admin import TrialAdminForm

	admin_labels = TrialAdminForm.Meta.labels
	admin_help = TrialAdminForm.Meta.help_texts
	model_help = {
		f.name: f.help_text
		for f in Trials._meta.get_fields()
		if not f.is_relation and hasattr(f, "help_text") and f.help_text
	}

	ws = wb.create_sheet(title="Registries")
	hdr_font = Font(bold=True, color="FFFFFF")
	hdr_fill = PatternFill(fill_type="solid", fgColor="2F4F8F")
	hdr_align = Alignment(vertical="center")
	section_font = Font(bold=True, size=13)

	# --- Part A: prose overview ---
	ws.cell(row=1, column=1, value="Registry overview").font = section_font
	prose_cell = ws.cell(row=2, column=1, value=MERGE_PROSE)
	prose_cell.alignment = Alignment(wrap_text=True)
	ws.merge_cells("A2:D2")
	ws.row_dimensions[2].height = 90

	# Overview table header (row 4)
	for col_idx, h in enumerate(
		["Registry", "Identifier key(s)", "What it is", "Notes on coverage"], 1
	):
		c = ws.cell(row=4, column=col_idx, value=h)
		c.font = hdr_font
		c.fill = hdr_fill
		c.alignment = hdr_align

	for row_offset, (reg_name, id_keys_str, what, notes) in enumerate(
		REGISTRIES_OVERVIEW, 5
	):
		ws.cell(row=row_offset, column=1, value=reg_name)
		ws.cell(row=row_offset, column=2, value=id_keys_str)
		ws.cell(row=row_offset, column=3, value=what)
		ws.cell(row=row_offset, column=4, value=notes).alignment = Alignment(
			wrap_text=True
		)

	# --- Part B: field-by-registry matrix ---
	matrix_start = 10
	ws.cell(
		row=matrix_start, column=1, value="Field coverage by registry"
	).font = section_font

	for col_idx, h in enumerate(["Field", "Label"] + REGISTRY_NAMES, 1):
		c = ws.cell(row=matrix_start + 1, column=col_idx, value=h)
		c.font = hdr_font
		c.fill = hdr_fill
		c.alignment = hdr_align

	for row_offset, col_name in enumerate(all_data_cols, matrix_start + 2):
		label, _, sources_str = _sources_for(
			col_name, admin_labels, admin_help, model_help
		)
		ws.cell(row=row_offset, column=1, value=col_name)
		ws.cell(row=row_offset, column=2, value=label)
		for reg_col, reg_name in enumerate(REGISTRY_NAMES, 3):
			# Normalise for matching: "EU CTIS" matches "EU CTIS" or "EU Clinical"
			src_lower = sources_str.lower()
			reg_lower = reg_name.lower()
			tick = "✓" if reg_lower in src_lower else ""
			if not tick and reg_name == "EU CTIS" and "eu ctis" not in src_lower:
				if "eu clinical" in src_lower:
					tick = "✓"
			ws.cell(row=row_offset, column=reg_col, value=tick)

	# Column widths
	for letter, width in zip("ABCDE", [32, 32, 22, 22, 22]):
		ws.column_dimensions[letter].width = width


class Command(BaseCommand):
	help = "Export clinical-trial data to an XLSX workbook, one sheet per subject."

	def add_arguments(self, parser):
		parser.add_argument(
			"--subjects",
			type=str,
			default="",
			help="Comma-separated subject IDs to export.",
		)
		parser.add_argument(
			"--all-subjects",
			action="store_true",
			default=False,
			help="Export every subject (one sheet each).",
		)
		parser.add_argument(
			"--output",
			type=str,
			default="",
			help="Output file path (default: trials_export_YYYYMMDD.xlsx in current directory).",
		)
		parser.add_argument(
			"--team",
			type=int,
			default=None,
			help="Optional team ID; filters which subjects are exported.",
		)

	def handle(self, *args, **options):
		# --- Resolve subjects ---
		if options["all_subjects"]:
			qs = Subject.objects.all()
			if options["team"]:
				qs = qs.filter(team_id=options["team"])
			subjects = list(qs.order_by("subject_name"))
		else:
			raw = options["subjects"].strip()
			if not raw:
				raise CommandError("Provide --subjects <ids> or --all-subjects.")
			try:
				ids = [int(x.strip()) for x in raw.split(",") if x.strip()]
			except ValueError:
				raise CommandError("--subjects must be comma-separated integers.")
			subject_qs = Subject.objects.all()
			if options["team"]:
				subject_qs = subject_qs.filter(team_id=options["team"])
			valid_ids = set(subject_qs.filter(pk__in=ids).values_list("pk", flat=True))
			missing = set(ids) - valid_ids
			if missing:
				all_subs = subject_qs.values_list("pk", "subject_name")
				valid_list = ", ".join(f"{pk} ({name})" for pk, name in all_subs)
				raise CommandError(
					f"Subject ID(s) not found: {sorted(missing)}. Valid IDs: {valid_list}"
				)
			subjects = list(subject_qs.filter(pk__in=ids).order_by("subject_name"))

		if not subjects:
			raise CommandError("No subjects found.")

		output_path = (
			options["output"]
			or f"trials_export_{datetime.now().strftime('%Y%m%d')}.xlsx"
		)

		# --- Build column plan ---
		scalar_cols = _build_scalar_columns()
		remaining_scalars = [
			c for c in scalar_cols if c not in set(IDENTITY_COLS) and c != "identifiers"
		]

		# Discover all identifier keys across every exported subject (preserves first-seen order)
		all_subject_ids = [s.pk for s in subjects]
		id_key_order, seen_keys = [], set()
		for identifiers in (
			Trials.objects.filter(subjects__in=all_subject_ids)
			.exclude(identifiers=None)
			.values_list("identifiers", flat=True)
		):
			if isinstance(identifiers, dict):
				for k in identifiers:
					if k not in seen_keys:
						id_key_order.append(k)
						seen_keys.add(k)

		id_cols = [f"id_{k}" for k in id_key_order]
		all_data_cols = (
			IDENTITY_COLS
			+ id_cols
			+ ["identifiers_json"]
			+ remaining_scalars
			+ RELATION_COLS
		)

		self.stdout.write(f"Exporting {len(subjects)} subject(s) → {output_path}")

		# --- Build workbook ---
		wb = Workbook()
		wb.remove(wb.active)  # remove the default blank sheet
		used_sheet_names: set = set()

		for subject in subjects:
			sheet_name = _sanitise_sheet_name(subject.subject_name, used_sheet_names)
			ws = wb.create_sheet(title=sheet_name)

			qs = (
				Trials.objects.filter(subjects=subject)
				.distinct()
				.order_by("-discovery_date")
				.prefetch_related(
					"subjects",
					"teams",
					"sources",
					"team_categories",
					"article_references__article",
				)
			)

			count = qs.count()
			self.stdout.write(f'  Sheet "{sheet_name}": {count} trial(s)')

			_apply_header(ws, all_data_cols)
			ws.freeze_panes = "A2"
			ws.auto_filter.ref = f"A1:{get_column_letter(len(all_data_cols))}1"

			if count == 0:
				ws.cell(row=2, column=1, value="No trials found for this subject.")
			else:
				scalar_set = set(IDENTITY_COLS) | set(remaining_scalars)
				for row_idx, trial in enumerate(qs, 2):
					identifiers = trial.identifiers or {}
					row_data = []
					for col_name in all_data_cols:
						if col_name in scalar_set:
							row_data.append(_cell_value(getattr(trial, col_name, None)))
						elif col_name.startswith("id_"):
							row_data.append(_cell_value(identifiers.get(col_name[3:])))
						elif col_name == "identifiers_json":
							row_data.append(
								json.dumps(identifiers, ensure_ascii=False)
								if identifiers
								else ""
							)
						elif col_name == "subjects":
							row_data.append(
								"; ".join(s.subject_name for s in trial.subjects.all())
							)
						elif col_name == "teams":
							row_data.append(
								"; ".join(t.name for t in trial.teams.all())
							)
						elif col_name == "sources":
							row_data.append(
								"; ".join(src.name or "" for src in trial.sources.all())
							)
						elif col_name == "team_categories":
							row_data.append(
								"; ".join(
									tc.category_name
									for tc in trial.team_categories.all()
								)
							)
						elif col_name == "articles":
							refs = list(trial.article_references.all())
							if refs:
								links = "; ".join(r.article.link for r in refs)
								row_data.append(f"{len(refs)}: {links}")
							else:
								row_data.append("")
						else:
							row_data.append("")

					for col_idx, value in enumerate(row_data, 1):
						ws.cell(row=row_idx, column=col_idx, value=value)

			_set_column_widths(ws, all_data_cols)

		_build_glossary_sheet(wb, all_data_cols)
		_build_registries_sheet(wb, all_data_cols)

		wb.save(output_path)
		self.stdout.write(self.style.SUCCESS(f"Saved: {output_path}"))
