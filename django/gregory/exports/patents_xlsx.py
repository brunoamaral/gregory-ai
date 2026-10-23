"""XLSX export of patent families.

``build_patents_workbook`` is shared by the ``export_patents_xlsx`` command and the
admin actions, so both produce the same file. Spec: specs/patent-export.md.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from django.db.models import F, Prefetch, GeneratedField
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from gregory.exports.xlsx_common import (
	_FORMULA_TRIGGER_CHARS,
	_apply_header,
	_build_about_sheet,
	_build_categories_sheet,
	_cell_value,
	_sanitise_sheet_name,
	_write_safe_text_cell,
)
from gregory.management.commands.detect_patent_trial_links import trial_window
from gregory.management.commands.feedreader_patents_ops import espacenet_url, is_grant_kind
from gregory.models import (
	PatentApplicant,
	PatentCategoryAssignment,
	PatentPublication,
	PatentTrialLink,
	Patents,
	Sources,
	Subject,
	Team,
	TeamCategory,
)

# --- Column plan -------------------------------------------------------------------

IDENTITY_COLS = [
	"patent_id",
	"family_id",
	"title",
	"representative_publication",
	"link",
	"links_json",
	"earliest_priority_date",
	"earliest_publication_date",
	"has_grant",
	"discovery_date",
	"last_updated",
]
TEXT_COLS = ["summary", "claims", "claims_length", "claims_truncated"]
CLASSIFICATION_COLS = [
	"cpc_classes",
	"cpc_count",
	"ipc_classes",
	"inventors",
	"inventor_count",
]
APPLICANT_COLS = [
	"applicants",
	"applicants_raw",
	"applicants_individual",
	"applicant_count",
	"has_individual_applicant",
	"applicant_sponsor_ids",
	"applicant_sponsor_slugs",
	"applicant_sponsor_names",
	"applicant_sponsor_types",
	"applicant_sponsor_type_sources",
	"has_unmatched_applicant",
]
PUBLICATION_SUMMARY_COLS = [
	"publication_numbers",
	"publication_count",
	"publication_offices",
	"granted_publications",
	"application_numbers",
	"earliest_application_date",
]
RELATION_COLS = [
	"subjects",
	"teams",
	"sources",
	"categories",
	"trial_link_count",
	"trial_links_summary",
]

# Model fields that are never exported (internal bookkeeping), and model fields that are
# exported under another column name.
EXCLUDED_FIELDS = frozenset({"utitle", "usummary", "family_next_check", "family_attempts"})
_RENAMED_FIELDS = frozenset({"links"})

_PLACED_COLS = (
	IDENTITY_COLS
	+ TEXT_COLS
	+ CLASSIFICATION_COLS
	+ APPLICANT_COLS
	+ PUBLICATION_SUMMARY_COLS
	+ RELATION_COLS
)

TRIAL_LINK_COLS = [
	"link_id",
	"timing",
	"days_after_completion",
	"basis",
	"suppressed",
	"discovered_date",
	"sponsor_id",
	"sponsor_name",
	"sponsor_type",
	"shared_subjects",
	"shared_categories",
	"patent_id",
	"family_id",
	"patent_title",
	"representative_publication",
	"patent_link",
	"earliest_priority_date",
	"has_grant",
	"trial_id",
	"trial_title",
	"acronym",
	"trial_link",
	"identifiers_normalized",
	"phase_normalized",
	"recruitment_status_normalized",
	"study_type",
	"condition",
	"intervention",
	"primary_outcome",
	"secondary_outcome",
	"date_enrollement",
	"published_date",
	"primary_completion_date",
	"completion_date",
	"completion_date_type",
	"trial_window_start",
	"trial_window_end",
]
PUBLICATION_COLS = [
	"patent_id",
	"family_id",
	"publication_number",
	"is_representative",
	"country",
	"doc_number",
	"kind",
	"is_grant_kind",
	"publication_date",
	"application_number",
	"application_date",
	"sources",
	"lens_id",
	"espacenet_link",
]

FIXED_SHEET_NAMES = [
	"About this file",
	"Trial links",
	"Publications",
	"Categories",
	"Glossary",
	"Data source",
	"No subject",
]
NO_SUBJECT_SHEET = "No subject"
CLAIMS_CELL_LIMIT = 32767

CATEGORY_COLUMNS = [
	"Subject",
	"Team",
	"Category",
	"Slug",
	"Description",
	"Search terms",
	"Terms (count)",
	"Excluded terms",
	"Category type",
	"Modality",
	"Match scope",
	"Min score (patents)",
	"Field weights (patents)",
	"Patents (this subject)",
	"Last synced",
]

CATEGORY_MATCH_PROSE_PATENTS = (
	"Each row is one category assigned to one subject. Automatic categories are populated "
	"by the rebuild_categories command: each search term is matched case-insensitively as a "
	"whole word against the fields listed in \"Field weights (patents)\" (title, abstract "
	"and claims; only those fields are searched, per the category's match scope); every "
	"matching field adds its weight, plus a flat 2 points per unique matched term, and a "
	"patent is assigned once the total reaches the \"Min score (patents)\" threshold. A "
	"patent must share a subject with the category to be considered. Patent matching has its "
	"own configuration, so it can be changed without re-matching articles and trials. A "
	"patent containing any \"Excluded terms\" entry in one of those fields is never "
	"assigned, whatever its score. Manual categories are curated by hand and ignore the "
	"term list."
)

EPO_ATTRIBUTION = (
	"Bibliographic data, abstracts and claims from the European Patent Office's Open Patent "
	"Services (OPS). Families, applicant matching, subjects, categories and trial links are "
	"Gregory's own processing."
)
TERMS_OF_USE = "For research use; do not republish the patent text."

WHATS_INSIDE_FIXED_ROWS = [
	(
		"Trial links",
		"Patents filed by a trial sponsor during or shortly after one of its trials, with the "
		"trial's details. Includes dismissed links.",
	),
	("Publications", "Every published document of every patent family, one row each."),
	("Categories", "How patents are tagged, with each category's search terms."),
	("Glossary", "What every column in the other sheets means."),
	(
		"Data source",
		"A plain-language introduction to patents, how Gregory collects them and what the "
		"data can and cannot tell you.",
	),
]

# --- Glossary ----------------------------------------------------------------------

PATENT_GLOSSARY = {
	# Identity
	"patent_id": ("Patent ID", "Gregory's own number for this patent family. It is only meaningful inside Gregory."),
	"family_id": (
		"Family ID",
		"The DOCDB simple family number given by the European Patent Office. All documents "
		"that claim the same first filings share it.",
	),
	"title": ("Title", "The invention's title, taken from the representative publication."),
	"representative_publication": (
		"Representative publication",
		"The one document in the family whose title, abstract, claims and classification this "
		"row shows, e.g. EP4123456A1. EP and WO documents are preferred because they carry claims.",
	),
	"link": (
		"Espacenet link",
		"Link to the representative publication on Espacenet, the EPO's free patent search, "
		"where you can read the full text and legal status.",
	),
	"links_json": (
		"All links (JSON)",
		"Every link Gregory holds for this family, keyed by source, as raw JSON.",
	),
	"earliest_priority_date": (
		"Earliest priority date",
		"The date of the first filing anywhere in the world for this invention. It is the date "
		"that matters for who filed what, and when.",
	),
	"earliest_publication_date": (
		"Earliest publication date",
		"The date the first document of the family was published. This is normally about 18 "
		"months after the priority date.",
	),
	"has_grant": (
		"Has grant",
		"True when any document in the family has a kind code that signals a granted patent. "
		"This is a guess from kind codes, not legal status: a grant may have lapsed or been revoked.",
	),
	"discovery_date": ("Discovery date", "When Gregory first found this family."),
	"last_updated": ("Last updated", "When Gregory last changed this row."),
	# Text
	"summary": ("Abstract", "The abstract from the representative publication. Can be empty."),
	"claims": (
		"Claims",
		"The full claims: the legal definition of what the patent protects. Only EP, WO, CA and "
		"some European national documents have claims in our source, so many families have none. "
		"Cut off at 32,767 characters, Excel's cell limit.",
	),
	"claims_length": (
		"Claims length",
		"Length of the stored claims in characters, so you can tell when the claims cell was cut off.",
	),
	"claims_truncated": (
		"Claims truncated",
		"True when the claims were longer than Excel allows in one cell and were cut off.",
	),
	# Classification and people
	"cpc_classes": (
		"CPC classes",
		"Cooperative Patent Classification symbols: a subject index of the invention, e.g. A61K 39/395.",
	),
	"cpc_count": ("CPC count", "Number of CPC classes listed."),
	"ipc_classes": (
		"IPC classes",
		"International Patent Classification symbols, an older and coarser version of CPC.",
	),
	"inventors": (
		"Inventors",
		"The people credited with the invention, as published and in publication order. "
		"They are not linked to authors in Gregory.",
	),
	"inventor_count": ("Inventor count", "Number of inventors listed."),
	# Applicants
	"applicants": (
		"Applicants",
		"Who filed the application, one entry per applicant, in published order. Format: "
		"published name → Gregory's sponsor name (sponsor id; type; where the type came from). "
		"Individuals are marked (individual); companies Gregory could not match are marked (no sponsor match).",
	),
	"applicants_raw": ("Applicants (as published)", "The applicant names exactly as the patent office published them."),
	"applicants_individual": (
		"Individual applicants",
		"Applicants treated as private persons because they are also listed as inventors.",
	),
	"applicant_count": ("Applicant count", "Number of applicants listed."),
	"has_individual_applicant": ("Has individual applicant", "True when at least one applicant is a private person."),
	"applicant_sponsor_ids": (
		"Sponsor IDs",
		"Gregory ids of the sponsors the applicants were matched to. The same sponsor ids are used "
		"for clinical trials. Listed once each, aligned with the next four columns.",
	),
	"applicant_sponsor_slugs": ("Sponsor slugs", "URL-friendly names of the matched sponsors."),
	"applicant_sponsor_names": ("Sponsor names", "Gregory's standard names for the matched sponsors."),
	"applicant_sponsor_types": (
		"Sponsor types",
		"Kind of organisation, e.g. industry or academic, where known. Empty when unknown.",
	),
	"applicant_sponsor_type_sources": (
		"Sponsor type sources",
		"Where each type came from: curated by hand, a trial registry, or a keyword guess from the name.",
	),
	"has_unmatched_applicant": (
		"Has unmatched applicant",
		"True when a company applicant could not be matched to a sponsor, e.g. an IP-holding "
		"subsidiary or an unusual spelling.",
	),
	# Publications (summary)
	"publication_numbers": (
		"Publication numbers",
		"Every document in the family, e.g. EP4123456A1; WO2023123456A1, oldest first.",
	),
	"publication_count": ("Publication count", "Number of documents in the family."),
	"publication_offices": (
		"Patent offices",
		"The patent offices that published a document in the family: EP, WO, US and others.",
	),
	"granted_publications": (
		"Granted publications",
		"Documents whose kind code signals a granted patent. See the Data source sheet.",
	),
	"application_numbers": ("Application numbers", "Filing numbers of the documents, each listed once."),
	"earliest_application_date": ("Earliest application date", "The earliest filing date among the documents."),
	# Gregory relations
	"subjects": ("Subjects", "The research subjects Gregory files this family under. Only subjects you can see are shown."),
	"teams": ("Teams", "The Gregory teams the family belongs to."),
	"sources": (
		"Sources",
		"Subject sheets: the Gregory patent searches that found this family. Publications sheet: "
		"the data feed that supplied the document (epo_ops is the EPO).",
	),
	"categories": (
		"Categories",
		"Categories the family was matched to, each tagged (auto) when matched by search terms or "
		"(manual) when an editor assigned it.",
	),
	"trial_link_count": (
		"Trial links",
		"Number of sponsor-timing links to clinical trials, dismissed ones included.",
	),
	"trial_links_summary": (
		"Trial links summary",
		"Links by basis and timing, e.g. category: 2 during, 1 after; subject: 4 during; suppressed: 1. "
		"See the Data source sheet for what these terms mean.",
	),
	# Trial links
	"link_id": ("Link ID", "Gregory's number for this link."),
	"timing": (
		"Timing",
		"during: the patent's priority date falls inside the trial's running period. after: it falls "
		"within 24 months after the trial ended.",
	),
	"days_after_completion": (
		"Days after completion",
		"For 'after' links, days between the trial's end and the patent's priority date.",
	),
	"basis": (
		"Basis",
		"category: the patent and the trial share a category (usually the same molecule), the strong "
		"signal. subject: they only share a research subject, which is noisy for large sponsors.",
	),
	"suppressed": ("Dismissed", "True when an editor dismissed the link as not meaningful."),
	"discovered_date": ("Discovered", "When Gregory created the link."),
	"sponsor_id": ("Sponsor ID", "Gregory id of the sponsor that is both a patent applicant and the trial's sponsor."),
	"sponsor_name": ("Sponsor", "Name of that shared sponsor."),
	"sponsor_type": ("Sponsor type", "Kind of organisation, where known."),
	"shared_subjects": ("Shared subjects", "Research subjects that both the patent and the trial are filed under."),
	"shared_categories": ("Shared categories", "Categories that both the patent and the trial are matched to."),
	"patent_title": ("Patent title", "Title of the patent family."),
	"patent_link": ("Patent Espacenet link", "Espacenet link of the patent family's representative publication."),
	"trial_id": ("Trial ID", "Gregory's number for the clinical trial."),
	"trial_title": ("Trial title", "Title of the clinical trial."),
	"acronym": ("Trial acronym", "Short name of the trial, where it has one."),
	"trial_link": ("Trial link", "Link to the trial's record."),
	"identifiers_normalized": ("Trial identifiers", "Registry numbers of the trial (NCT, EudraCT, CTIS and others)."),
	"phase_normalized": ("Trial phase", "Development phase of the trial, e.g. Phase 2."),
	"recruitment_status_normalized": ("Trial status", "Whether the trial is recruiting, completed, withdrawn, etc."),
	"study_type": ("Study type", "Interventional or observational."),
	"condition": ("Condition", "The disease or condition the trial studies."),
	"intervention": ("Intervention", "The treatment being tested."),
	"primary_outcome": ("Primary outcome", "What the trial mainly measures to judge success."),
	"secondary_outcome": ("Secondary outcome", "Other outcomes the trial measures."),
	"date_enrollement": ("Trial start", "Date the trial started enrolling participants."),
	"published_date": ("Trial published", "When the trial was first published in a registry."),
	"primary_completion_date": ("Primary completion date", "When data collection for the primary outcome finished or is expected to."),
	"completion_date": ("Completion date", "When the whole trial finished or is expected to."),
	"completion_date_type": ("Completion date type", "Whether the completion date is actual or anticipated."),
	"trial_window_start": (
		"Trial window start",
		"Start of the period Gregory compares the patent's priority date with: the trial's start, or "
		"its publication date when no start is known.",
	),
	"trial_window_end": (
		"Trial window end",
		"End of that period: primary completion, else completion date. Empty while the trial is still "
		"ongoing, which means the period has no end yet.",
	),
	# Publications
	"publication_number": ("Publication number", "Office, number and kind code of one document, e.g. EP4123456A1."),
	"is_representative": (
		"Is representative",
		"True for the document the family's title, abstract and claims come from.",
	),
	"country": ("Office", "Two-letter code of the patent office that published the document."),
	"doc_number": ("Document number", "The office's number for the document, without office or kind code."),
	"kind": ("Kind code", "Letter and digit saying what type of document this is, e.g. A1 or B1."),
	"is_grant_kind": ("Grant kind", "True when the kind code signals a granted patent (not for WO)."),
	"publication_date": ("Publication date", "When the document was published."),
	"application_number": ("Application number", "The filing number of the application behind the document."),
	"application_date": ("Application date", "When the application was filed."),
	"lens_id": ("Lens ID", "Identifier in The Lens, where known. Empty unless the document came from there."),
	"espacenet_link": ("Espacenet link", "Link to this document on Espacenet."),
}

# --- Data source prose -------------------------------------------------------------

KIND_CODE_TABLE = (
	["Document", "Meaning"],
	[
		("EP A1", "European application, published with the search report."),
		("EP A2", "European application, published without the search report."),
		("EP A3", "The search report for an application, published separately."),
		("EP B1", "Granted European patent, with no earlier publication of the application."),
		("EP B2", "Granted European patent, amended after an opposition."),
		("WO A1", "International (PCT) application, published with the search report. Never a grant."),
		("WO A2", "International (PCT) application, published without the search report."),
		("US A1", "US application publication."),
		("US B1", "Granted US patent, with no earlier publication."),
		("US B2", "Granted US patent that was published as an application first."),
		(
			"B, C, T",
			"Gregory counts a kind code starting with B, C or T as a grant (B in EP, US and most "
			"offices, C in CA, CN and DE, T for translations of granted European patents), except "
			"for WO. This sets has_grant and granted_publications.",
		),
	],
)
OFFICES_TABLE = (
	["Code", "Office"],
	[
		("EP", "European Patent Office. One application can lead to a patent in many European countries."),
		("WO", "World Intellectual Property Organization. An international (PCT) application: it reserves a place in many countries but is never granted itself."),
		("US", "United States Patent and Trademark Office."),
		("CN", "China National Intellectual Property Administration."),
		("JP", "Japan Patent Office."),
		("KR", "Korean Intellectual Property Office."),
		("CA", "Canadian Intellectual Property Office."),
		("DE", "German Patent and Trade Mark Office."),
	],
)
COVERAGE_TABLE = (
	["Field", "EP and WO", "Other offices"],
	[
		("Title, applicants, inventors", "Yes", "Yes"),
		("English abstract", "Usually", "Varies by office and document"),
		("Claims", "Yes", "Only CA and some European national offices. Not US, CN, JP or KR."),
		("CPC classification", "Yes", "Yes"),
	],
)

DATA_SOURCE_SECTIONS = [
	(
		"1. What a patent is",
		"A patent is a time-limited right to stop others from making or using an invention. In "
		"exchange, the inventor has to publish how the invention works. For this reason patents "
		"are a window into what organisations are developing: next to clinical trials and "
		"research papers, they show who is protecting which treatment, and when.",
		None,
	),
	(
		"2. Application, publication and grant",
		"A patent starts as an application. About 18 months after the first filing it is "
		"published, whether or not it will ever be granted. A grant comes later, if at all, "
		"after an examiner has reviewed it. Most rows in this workbook are applications, not "
		"granted patents.",
		None,
	),
	(
		"3. Publication numbers and kind codes",
		"A publication number such as EP4123456A1 has three parts: the office (EP), the "
		"document number (4123456) and the kind code (A1). The kind code says what type of "
		"document it is. The common ones:",
		KIND_CODE_TABLE,
	),
	(
		"4. Patent offices",
		"Each office publishes its own documents. The ones you will meet most:",
		OFFICES_TABLE,
	),
	(
		"5. Families",
		"One invention is usually filed in several countries. All those filings and their "
		"publications form a family, and each row in the subject sheets is one family. Gregory "
		"uses the DOCDB simple family: documents that share exactly the same priority filings. "
		"That is narrower than the INPADOC extended family that Espacenet sometimes shows, so "
		"Espacenet's family can list more documents than the Publications sheet.",
		None,
	),
	(
		"6. The priority date",
		"The priority date is the date of the first filing anywhere for the invention. It is "
		"the date that matters for \"who filed what, and when\". A later international or "
		"regional filing does not change it. It is different from the publication date, which is "
		"usually about 18 months later.",
		None,
	),
	(
		"7. The representative publication",
		"Each family shows the title, abstract, claims and classification of one member, the "
		"representative publication. Gregory uses the one the EPO marks as representative, unless "
		"that office has no full text in our source; then it prefers the EP member and then the WO "
		"member, because those carry claims.",
		None,
	),
	(
		"8. Claims",
		"The claims are the legal definition of what the patent protects. They decide its scope, "
		"unlike the abstract, which only summarises. Our source has claims only for EP, WO, CA and "
		"European national offices, so families filed only in the US, China, Japan or Korea have "
		"none. Very long claims are cut off in the export at Excel's cell limit; the claims_length "
		"column shows the full size and claims_truncated says when it happened.",
		None,
	),
	(
		"9. Applicants and inventors",
		"The applicant owns the application, usually a company or a university. Inventors are the "
		"people who made the invention. Gregory matches each applicant to the same sponsor records "
		"it uses for clinical trials, so a company that sponsors a trial and files a patent is one "
		"entity. Some applicants stay unmatched: IP-holding subsidiaries and unusual spellings do "
		"not always resolve. An applicant who is also listed as an inventor is treated as an "
		"individual and is not matched to a sponsor.",
		None,
	),
	(
		"10. Classification",
		"Examiners file every patent under classification symbols. CPC (Cooperative Patent "
		"Classification) is the main scheme and IPC is an older, coarser one. A symbol such as "
		"A61K 39/395 reads: section A (human necessities), class 61 (medical), subclass K "
		"(preparations for medical purposes), group 39/395 (antibodies). The two classes most "
		"relevant here are A61K (preparations for medical purposes) and A61P (therapeutic activity "
		"of chemical compounds). Browse the scheme at "
		"https://www.cooperativepatentclassification.org/browse.",
		None,
	),
	(
		"11. has_grant is a guess",
		"has_grant comes from kind codes only. Gregory does not fetch legal status yet. A granted "
		"patent may have lapsed or been revoked, and an application may have been withdrawn. Check "
		"Espacenet's legal status before relying on either.",
		None,
	),
	(
		"12. How Gregory finds patents",
		"Gregory runs one EPO search per subject, with a query written by the team's editors. The "
		"search favours recall over precision, so expect some unrelated patents. A subject without "
		"a patent search has no patents here. Backfills go back to priority dates from 2016; see "
		"the project's patents documentation for the subjects currently covered.",
		None,
	),
	(
		"13. Categories",
		"Categories tag patents by search terms matched against the title, abstract and claims, "
		"with a weight per field (see the Categories sheet). Beware that patents on a new compound "
		"filed before the drug has a name list it only by its chemical formula or a company code, so "
		"category terms find later patents (new uses, formulations) much better than the original "
		"compound patent. Adding company codes to a category's terms helps.",
		None,
	),
	(
		"14. The sponsor-timing signal",
		"The question patents are collected to answer is whether a trial sponsor files a patent "
		"while one of its trials is running or recently over. Gregory compares each patent's "
		"priority date with the period of each trial run by an applicant. \"during\" means the "
		"date falls inside the trial's period; \"after\" means it falls up to 24 months after the "
		"trial ended. The basis column says how strong the link is: \"category\" means the patent "
		"and the trial share a category, usually the same molecule; \"subject\" means they only "
		"share a research subject, which is noisy for sponsors with many trials. Dismissed "
		"(suppressed) links are links an editor judged not meaningful; they stay in the Trial links "
		"sheet, flagged. Two built-in delays apply: a patent is published about 18 months after "
		"filing, so a recent filing may not be visible yet; and the priority date is fixed at the "
		"first filing, so later filings of the same invention never move it.",
		None,
	),
	(
		"15. Looking a patent up",
		"Open the link or espacenet_link column. Espacenet has the full text, the legal status "
		"and the INPADOC extended family.",
		None,
	),
	(
		"16. Sources and credit",
		"Bibliographic data, abstracts and claims come from the European Patent Office's Open "
		"Patent Services (OPS); terms: https://www.epo.org/en/service-support/ordering/open-patent-services. "
		"Gregory's own processing adds the families, applicant matching, subjects, categories and "
		"trial links. For research use; do not republish the patent text.",
		None,
	),
	(
		"17. Field coverage by office",
		"Which fields exist depends on the office that published the representative document:",
		COVERAGE_TABLE,
	),
]

# --- Scope --------------------------------------------------------------------------


@dataclass
class ExportScope:
	"""Limits the export to what a staff member's organisations can see (admin)."""

	org_ids: list


# --- Cell helpers -------------------------------------------------------------------

_WIDE_COLS = {
	"title",
	"summary",
	"claims",
	"applicants",
	"applicants_raw",
	"applicants_individual",
	"applicant_sponsor_names",
	"inventors",
	"publication_numbers",
	"granted_publications",
	"categories",
	"trial_links_summary",
	"patent_title",
	"trial_title",
	"condition",
	"intervention",
	"primary_outcome",
	"secondary_outcome",
	"shared_categories",
	"shared_subjects",
}
_MEDIUM_COLS = {
	"link",
	"links_json",
	"patent_link",
	"trial_link",
	"espacenet_link",
	"cpc_classes",
	"ipc_classes",
}


def _set_column_widths(ws, columns):
	for col_idx, name in enumerate(columns, 1):
		letter = get_column_letter(col_idx)
		if name in _WIDE_COLS:
			ws.column_dimensions[letter].width = 50
		elif name in _MEDIUM_COLS:
			ws.column_dimensions[letter].width = 40
		else:
			ws.column_dimensions[letter].width = max(12, min(40, len(name) + 4))


def _write_row(ws, row_idx, values, wrap_cols=()):
	"""Write one data row. Strings go through the formula-defusing writer; dates, numbers
	and bools stay typed."""
	for col_idx, value in enumerate(values, 1):
		if isinstance(value, str) and value != "":
			_write_safe_text_cell(ws, row_idx, col_idx, value, wrap=col_idx in wrap_cols)
		else:
			ws.cell(row=row_idx, column=col_idx, value=value)


def _join(values):
	return "; ".join(str(v) for v in values)


def _dedupe(values):
	seen, out = set(), []
	for v in values:
		if v not in seen:
			seen.add(v)
			out.append(v)
	return out


def _build_patent_scalar_columns():
	"""Concrete Patents fields that are neither placed nor excluded, appended at the end so
	a new model field shows up in the export (and fails the glossary test until described)."""
	known = set(_PLACED_COLS) | EXCLUDED_FIELDS | _RENAMED_FIELDS
	unknown = []
	for f in Patents._meta.get_fields():
		if f.is_relation or isinstance(f, GeneratedField):
			continue
		if f.name in known:
			continue
		unknown.append(f.name)
	return sorted(unknown)


def _applicant_cell(applicant):
	if applicant.is_individual:
		return f"{applicant.raw_name} (individual)"
	sponsor = applicant.sponsor
	if sponsor is None:
		return f"{applicant.raw_name} (no sponsor match)"
	return (
		f"{applicant.raw_name} → {sponsor.name} (sponsor {sponsor.pk}; "
		f"{sponsor.sponsor_type or 'type unknown'}; {sponsor.sponsor_type_source or 'no source'})"
	)


def _trial_links_summary(links):
	counts = {}
	suppressed = 0
	for link in links:
		if link.suppressed:
			suppressed += 1
			continue
		counts.setdefault(link.basis, {}).setdefault(link.timing, 0)
		counts[link.basis][link.timing] += 1
	parts = []
	for basis in (PatentTrialLink.BASIS_CATEGORY, PatentTrialLink.BASIS_SUBJECT):
		timings = counts.get(basis)
		if timings:
			bits = [
				f"{timings[t]} {t}"
				for t in (PatentTrialLink.TIMING_DURING, PatentTrialLink.TIMING_AFTER)
				if t in timings
			]
			parts.append(f"{basis}: {', '.join(bits)}")
	if suppressed:
		parts.append(f"suppressed: {suppressed}")
	return "; ".join(parts)


def _inventor_names(patent):
	inventors = patent.inventors
	if not isinstance(inventors, list):
		return []
	return [i if isinstance(i, str) else str(i) for i in inventors]


# --- Querysets ----------------------------------------------------------------------


def _scoped_prefetches(scope):
	"""Prefetch lookups for the Gregory relations, limited to the scope's organisations."""
	subjects_qs = None
	teams_qs = None
	sources_qs = None
	assignments_qs = PatentCategoryAssignment.objects.select_related("teamcategory")
	links_qs = PatentTrialLink.objects.all()
	if scope is not None:
		ids = scope.org_ids
		subjects_qs = Subject.objects.filter(team__organization__id__in=ids)
		teams_qs = Team.objects.filter(organization__id__in=ids)
		sources_qs = Sources.objects.filter(team__organization__id__in=ids)
		assignments_qs = assignments_qs.filter(
			teamcategory__team__organization__id__in=ids
		)
		links_qs = links_qs.filter(trial__sources__team__organization__id__in=ids).distinct()

	def pf(lookup, qs):
		return Prefetch(lookup, queryset=qs) if qs is not None else lookup

	return [
		"publications",
		Prefetch(
			"patent_applicants",
			queryset=PatentApplicant.objects.select_related("sponsor").order_by("sequence"),
		),
		pf("subjects", subjects_qs),
		pf("teams", teams_qs),
		pf("sources", sources_qs),
		Prefetch("category_assignments", queryset=assignments_qs),
		Prefetch("trial_links", queryset=links_qs),
	]


def _sheet_queryset(base, prefetches):
	return (
		base.order_by(F("earliest_priority_date").desc(nulls_last=True), "-patent_id")
		.distinct()
		# the admin queryset may carry prefetches of its own; ours replace them
		.prefetch_related(None)
		.prefetch_related(*prefetches)
	)


# --- Subject sheet rows ---------------------------------------------------------------


def _patent_row(patent, columns, scalar_set, context):
	"""Return the cell values of one patent, in ``columns`` order. Also records the
	patent's visible subjects and categories for the Trial links sheet."""
	pubs = list(patent.publications.all())
	applicants = list(patent.patent_applicants.all())
	subjects = list(patent.subjects.all())
	assignments = list(patent.category_assignments.all())
	links = list(patent.trial_links.all())
	inventors = _inventor_names(patent)
	claims = patent.claims or ""

	context["subjects"][patent.pk] = {s.pk: s.subject_name for s in subjects}
	context["categories"][patent.pk] = {
		a.teamcategory_id: a.teamcategory.category_name for a in assignments
	}

	sponsors = _dedupe_sponsors(applicants)
	individuals = [a for a in applicants if a.is_individual]
	app_dates = [p.application_date for p in pubs if p.application_date]

	values = {
		"links_json": json.dumps(patent.links, ensure_ascii=False) if patent.links else "",
		"claims": claims,
		"claims_length": len(claims),
		# the formula-defusing apostrophe counts towards the cell limit
		"claims_truncated": len(claims) + claims.startswith(_FORMULA_TRIGGER_CHARS)
		> CLAIMS_CELL_LIMIT,
		"cpc_count": len(patent.cpc_classes or []),
		"inventors": _join(inventors),
		"inventor_count": len(inventors),
		"applicants": _join(_applicant_cell(a) for a in applicants),
		"applicants_raw": _join(a.raw_name for a in applicants),
		"applicants_individual": _join(a.raw_name for a in individuals),
		"applicant_count": len(applicants),
		"has_individual_applicant": bool(individuals),
		"applicant_sponsor_ids": _join(s.pk for s in sponsors),
		"applicant_sponsor_slugs": _join(s.slug for s in sponsors),
		"applicant_sponsor_names": _join(s.name for s in sponsors),
		"applicant_sponsor_types": _join(s.sponsor_type or "" for s in sponsors),
		"applicant_sponsor_type_sources": _join(s.sponsor_type_source or "" for s in sponsors),
		"has_unmatched_applicant": any(
			a.sponsor_id is None and not a.is_individual for a in applicants
		),
		"publication_numbers": _join(p.publication_number for p in pubs),
		"publication_count": len(pubs),
		"publication_offices": _join(_dedupe(p.country for p in pubs if p.country)),
		"granted_publications": _join(
			p.publication_number for p in pubs if is_grant_kind(p.country, p.kind)
		),
		"application_numbers": _join(
			_dedupe(p.application_number for p in pubs if p.application_number)
		),
		"earliest_application_date": min(app_dates) if app_dates else "",
		"subjects": _join(s.subject_name for s in subjects),
		"teams": _join(t.name for t in patent.teams.all()),
		"sources": _join(src.name or "" for src in patent.sources.all()),
		"categories": _join(
			f"{a.teamcategory.category_name} ({'auto' if a.source == 'automatic' else a.source})"
			for a in assignments
		),
		"trial_link_count": len(links),
		"trial_links_summary": _trial_links_summary(links),
	}
	row = []
	for col in columns:
		if col in values:
			row.append(_cell_value(values[col]) if col not in ("claims",) else values[col])
		elif col in scalar_set:
			row.append(_cell_value(getattr(patent, col, None)))
		else:
			row.append("")
	return row


def _dedupe_sponsors(applicants):
	seen, out = set(), []
	for a in applicants:
		if a.sponsor is not None and a.sponsor.pk not in seen:
			seen.add(a.sponsor.pk)
			out.append(a.sponsor)
	return out


# --- Other sheets -----------------------------------------------------------------------


def _build_trial_links_sheet(wb, patent_ids, scope, context):
	ws = wb.create_sheet(title="Trial links")
	qs = PatentTrialLink.objects.filter(patent_id__in=patent_ids)
	if scope is not None:
		qs = qs.filter(trial__sources__team__organization__id__in=scope.org_ids).distinct()
	qs = (
		qs.select_related("patent", "trial", "sponsor")
		.prefetch_related("trial__subjects", "trial__categories")
		.order_by(
			# category first, then not-suppressed first, then newest priority date
			F("basis").asc(),
			"suppressed",
			F("patent__earliest_priority_date").desc(nulls_last=True),
			"patent_id",
			"trial_id",
		)
	)
	_apply_header(ws, TRIAL_LINK_COLS)
	ws.freeze_panes = "A2"
	ws.auto_filter.ref = f"A1:{get_column_letter(len(TRIAL_LINK_COLS))}1"
	row_idx = 2
	for link in qs:
		patent, trial, sponsor = link.patent, link.trial, link.sponsor
		patent_subjects = context["subjects"].get(patent.pk, {})
		patent_categories = context["categories"].get(patent.pk, {})
		shared_subjects = [
			patent_subjects[s.pk] for s in trial.subjects.all() if s.pk in patent_subjects
		]
		shared_categories = [
			patent_categories[c.pk]
			for c in trial.categories.all()
			if c.pk in patent_categories
		]
		window = trial_window(trial)
		w_start = window[0] if window else ""
		w_end = window[1] if window and window[1] else ""
		values = {
			"link_id": link.pk,
			"timing": link.timing,
			"days_after_completion": link.days_after_completion,
			"basis": link.basis,
			"suppressed": link.suppressed,
			"discovered_date": link.discovered_date,
			"sponsor_id": sponsor.pk,
			"sponsor_name": sponsor.name,
			"sponsor_type": sponsor.sponsor_type,
			"shared_subjects": _join(shared_subjects),
			"shared_categories": _join(shared_categories),
			"patent_id": patent.pk,
			"family_id": patent.family_id,
			"patent_title": patent.title,
			"representative_publication": patent.representative_publication,
			"patent_link": patent.link,
			"earliest_priority_date": patent.earliest_priority_date,
			"has_grant": patent.has_grant,
			"trial_id": trial.pk,
			"trial_title": trial.title,
			"trial_link": trial.link,
			"trial_window_start": w_start,
			"trial_window_end": w_end,
		}
		row = []
		for col in TRIAL_LINK_COLS:
			if col in values:
				row.append(_cell_value(values[col]))
			else:
				row.append(_cell_value(getattr(trial, col, None)))
		_write_row(ws, row_idx, row)
		row_idx += 1
	if row_idx == 2:
		ws.cell(row=2, column=1, value="No trial links found.")
	_set_column_widths(ws, TRIAL_LINK_COLS)
	return row_idx - 2


def _build_publications_sheet(wb, patent_ids):
	ws = wb.create_sheet(title="Publications")
	qs = (
		PatentPublication.objects.filter(patent_id__in=patent_ids)
		.select_related("patent")
		.order_by("patent_id", "publication_date", "publication_number")
	)
	_apply_header(ws, PUBLICATION_COLS)
	ws.freeze_panes = "A2"
	ws.auto_filter.ref = f"A1:{get_column_letter(len(PUBLICATION_COLS))}1"
	row_idx = 2
	for pub in qs:
		patent = pub.patent
		values = {
			"patent_id": patent.pk,
			"family_id": patent.family_id,
			"publication_number": pub.publication_number,
			"is_representative": pub.publication_number == patent.representative_publication,
			"country": pub.country,
			"doc_number": pub.doc_number,
			"kind": pub.kind,
			"is_grant_kind": is_grant_kind(pub.country, pub.kind),
			"publication_date": pub.publication_date,
			"application_number": pub.application_number,
			"application_date": pub.application_date,
			"sources": _join(pub.sources or []) if isinstance(pub.sources, list) else "",
			"lens_id": pub.lens_id,
			"espacenet_link": espacenet_url(pub),
		}
		_write_row(ws, row_idx, [_cell_value(values[c]) for c in PUBLICATION_COLS])
		row_idx += 1
	if row_idx == 2:
		ws.cell(row=2, column=1, value="No publications found.")
	_set_column_widths(ws, PUBLICATION_COLS)


def _glossary_entry(col, model_help):
	if col in PATENT_GLOSSARY:
		return PATENT_GLOSSARY[col]
	return col, model_help.get(col, "")


def _build_glossary_sheet(wb, sheet_columns):
	"""``sheet_columns`` is an ordered list of (sheet label, columns)."""
	model_help = {
		f.name: f.help_text
		for f in Patents._meta.get_fields()
		if not f.is_relation and getattr(f, "help_text", "")
	}
	ws = wb.create_sheet(title="Glossary")
	_apply_header(ws, ["Field", "Label", "Description", "Sheet"])
	ws.freeze_panes = "A2"
	for letter, width in zip("ABCD", [32, 32, 70, 32]):
		ws.column_dimensions[letter].width = width

	order, sheets = [], {}
	for label, columns in sheet_columns:
		for col in columns:
			if col not in sheets:
				order.append(col)
				sheets[col] = []
			if label not in sheets[col]:
				sheets[col].append(label)

	for row_idx, col in enumerate(order, 2):
		label, desc = _glossary_entry(col, model_help)[:2]
		ws.cell(row=row_idx, column=1, value=col)
		ws.cell(row=row_idx, column=2, value=label)
		ws.cell(row=row_idx, column=3, value=desc).alignment = Alignment(wrap_text=True)
		ws.cell(row=row_idx, column=4, value=", ".join(sheets[col]))


def _build_data_source_sheet(wb):
	ws = wb.create_sheet(title="Data source")
	for letter, width in zip("ABCD", [30, 60, 40, 40]):
		ws.column_dimensions[letter].width = width
	section_font = Font(bold=True, size=13)
	row = 1
	ws.cell(row=row, column=1, value="About patents and this data").font = section_font
	row += 2
	for heading, paragraph, table in DATA_SOURCE_SECTIONS:
		ws.cell(row=row, column=1, value=heading).font = Font(bold=True)
		row += 1
		cell = ws.cell(row=row, column=1, value=paragraph)
		cell.alignment = Alignment(wrap_text=True, vertical="top")
		ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
		ws.row_dimensions[row].height = 15 * (len(paragraph) // 150 + 1)
		row += 1
		if table:
			headers, rows = table
			row += 1
			_apply_header(ws, headers, row=row)
			row += 1
			for values in rows:
				for col_idx, value in enumerate(values, 1):
					c = ws.cell(row=row, column=col_idx, value=value)
					c.alignment = Alignment(wrap_text=True, vertical="top")
				if len(values) == 2:
					ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=4)
					ws.row_dimensions[row].height = 15 * (len(values[1]) // 120 + 1)
				row += 1
		row += 1


# --- Entry point ------------------------------------------------------------------------


def build_patents_workbook(
	subjects,
	attribution,
	*,
	patent_qs=None,
	scope=None,
	include_unassigned=False,
	options_summary="",
):
	"""Return an openpyxl Workbook of patent families, one sheet per subject.

	``patent_qs`` is the base set of patents (default: every patent). ``scope`` limits
	relation columns, trial links and categories to a staff member's organisations.
	``include_unassigned`` adds a "No subject" sheet for patents in ``patent_qs`` that
	belong to none of ``subjects``.
	"""
	base = Patents.objects.all() if patent_qs is None else patent_qs
	prefetches = _scoped_prefetches(scope)
	scalar_cols = _build_patent_scalar_columns()
	columns = (
		IDENTITY_COLS
		+ TEXT_COLS
		+ CLASSIFICATION_COLS
		+ APPLICANT_COLS
		+ PUBLICATION_SUMMARY_COLS
		+ RELATION_COLS
		+ scalar_cols
	)
	scalar_set = {"patent_id", "family_id", "title", "representative_publication", "link"}
	scalar_set |= {
		"earliest_priority_date",
		"earliest_publication_date",
		"has_grant",
		"discovery_date",
		"last_updated",
		"summary",
		"cpc_classes",
		"ipc_classes",
	} | set(scalar_cols)
	wrap_cols = {i for i, c in enumerate(columns, 1) if c in _WIDE_COLS}

	wb = Workbook()
	wb.remove(wb.active)
	used = set(FIXED_SHEET_NAMES)
	ws_about = wb.create_sheet(title="About this file")
	context = {"subjects": {}, "categories": {}}
	patent_ids = set()
	sheet_entries = []

	def fill(ws, qs, empty_text):
		_apply_header(ws, columns)
		ws.freeze_panes = "A2"
		ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}1"
		patents = list(qs)
		if not patents:
			ws.cell(row=2, column=1, value=empty_text)
		for row_idx, patent in enumerate(patents, 2):
			patent_ids.add(patent.pk)
			_write_row(ws, row_idx, _patent_row(patent, columns, scalar_set, context), wrap_cols)
		_set_column_widths(ws, columns)
		return len(patents)

	for subject in subjects:
		sheet_name = _sanitise_sheet_name(subject.subject_name, used)
		ws = wb.create_sheet(title=sheet_name)
		qs = _sheet_queryset(base.filter(subjects=subject), prefetches)
		count = fill(ws, qs, "No patents found for this subject.")
		sheet_entries.append((sheet_name, subject, count))

	unassigned_count = 0
	if include_unassigned:
		unassigned = base.exclude(subjects__in=[s.pk for s in subjects])
		if unassigned.exists():
			ws = wb.create_sheet(title=NO_SUBJECT_SHEET)
			unassigned_count = fill(
				ws, _sheet_queryset(unassigned, prefetches), "No patents found."
			)

	_build_trial_links_sheet(wb, patent_ids, scope, context)
	_build_publications_sheet(wb, patent_ids)

	category_qs = count_qs = None
	if scope is not None:
		category_qs = TeamCategory.objects.filter(team__organization__id__in=scope.org_ids)
		count_qs = Patents.objects.filter(sources__team__organization__id__in=scope.org_ids)
	_build_categories_sheet(
		wb,
		subjects,
		content_type="patent",
		columns=CATEGORY_COLUMNS,
		prose=CATEGORY_MATCH_PROSE_PATENTS,
		category_qs=category_qs,
		count_qs=count_qs,
	)
	_build_glossary_sheet(
		wb,
		[
			("Subject sheets", columns),
			("Trial links", TRIAL_LINK_COLS),
			("Publications", PUBLICATION_COLS),
		],
	)
	_build_data_source_sheet(wb)

	fixed_rows = list(WHATS_INSIDE_FIXED_ROWS)
	if unassigned_count:
		fixed_rows.insert(
			0,
			(
				NO_SUBJECT_SHEET,
				f"{unassigned_count} patent families that belong to none of the subjects above.",
			),
		)
	_build_about_sheet(
		ws_about,
		attribution,
		sheet_entries,
		options_summary,
		item_noun=lambda n: f"{n} patent famil{'y' if n == 1 else 'ies'}",
		citation_label="Patents export",
		fixed_rows=fixed_rows,
		extra_source_rows=[
			("Patent data", EPO_ATTRIBUTION),
			("Terms of use", TERMS_OF_USE),
		],
		licence_label="Data licence (Gregory's processing)",
	)
	return wb


def default_output_name():
	return f"patents_export_{datetime.now().strftime('%Y%m%d')}.xlsx"
