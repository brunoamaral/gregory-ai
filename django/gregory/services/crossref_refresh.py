"""
Service layer behind the Articles admin's "Refresh from CrossRef" button.

Builds a diff between what an article holds and what CrossRef (+ Unpaywall,
via SciencePaper.refresh()) currently returns for its DOI, and applies only
the rows the admin user ticks. No admin imports here — the admin view
(gregory/admin.py) owns the request/response plumbing, permissions, and the
signed round-trip payload; this module is the pure data layer underneath it.
"""

import difflib
import logging
from dataclasses import dataclass
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from gregory.classes import SciencePaper
from gregory.functions import normalize_orcid
from gregory.models import Articles, Authors
from gregory.services.doi_import import resolve_author
from gregory.utils.enrichment import clear_marker
from gregory.utils.text_cleaning import clean_title

logger = logging.getLogger(__name__)

CHANGE_REASON_PREFIX = "CrossRef: "
MAX_REASON_LEN = 100

# (Articles field name, human label) — also the order fields are diffed/shown in.
FIELD_SPECS = [
	("title", "Title"),
	("summary", "Summary"),
	("publisher", "Publisher"),
	("container_title", "Container title"),
	("published_date", "Published date"),
	("retracted", "Retracted"),
	("access", "Open access (via Unpaywall)"),
	("pdf_link", "PDF link (via Unpaywall)"),
]

SUMMARY_DIFF_THRESHOLD = 400


@dataclass
class FieldDiff:
	field: str
	label: str
	current: Any
	proposed: Any
	raw: Any  # JSON-safe value carried in the signed payload
	preselect: bool
	warning: Optional[str] = None
	extra: Optional[str] = None  # unified diff text, for long summaries


@dataclass
class AuthorDiff:
	action: str  # "add" | "remove"
	label: str
	raw: dict  # CrossRef author dict, or {"author_id": N} for removals
	preselect: bool


@dataclass
class CrossrefDiff:
	fetch_error: Optional[str]
	fields: list
	authors: list


@dataclass
class AppliedResult:
	updated_fields: list
	authors_added: list  # full labels, e.g. "Jane Doe (ORCID 0000-...)"
	authors_removed: list
	change_reason: str


def _is_empty_text(value) -> bool:
	return value is None or value == ""


def _is_empty_access(value) -> bool:
	return value is None or value == "unknown"


def _unified_diff(old: str, new: str) -> str:
	lines = difflib.unified_diff(
		(old or "").splitlines(), (new or "").splitlines(), lineterm="", n=1
	)
	return "\n".join(lines)


def _date_precision_warning(paper: SciencePaper) -> Optional[str]:
	"""CrossRef's `issued` field is sometimes only a year, or a year+month.
	refresh() silently pads missing parts to 1 January, so a bare year would
	otherwise look exactly like a precise date next to a real feed date."""
	work = paper._work or {}
	try:
		date_parts = work["issued"]["date-parts"][0]
	except (KeyError, IndexError, TypeError):
		return None

	year = paper.published_date.year
	if len(date_parts) <= 1:
		return f"CrossRef gave only a year ({year}) — padded to 1 January."
	if len(date_parts) == 2:
		month = date_parts[1]
		return f"CrossRef gave only a year and month ({year}-{month:02d}) — padded to day 1."
	return None


def _normalize_orcid_or_none(raw):
	return normalize_orcid(raw) if raw else None


def _existing_author_key(author: Authors):
	orcid = _normalize_orcid_or_none(author.ORCID)
	if orcid:
		return ("orcid", orcid)
	return (
		"name",
		(author.given_name or "").strip().lower(),
		(author.family_name or "").strip().lower(),
	)


def _crossref_author_key(author_data: dict):
	orcid = _normalize_orcid_or_none(author_data.get("ORCID"))
	if orcid:
		return ("orcid", orcid)
	return (
		"name",
		(author_data.get("given") or "").strip().lower(),
		(author_data.get("family") or "").strip().lower(),
	)


def _build_author_diffs(article: Articles, crossref_authors: list) -> list:
	existing_authors = list(article.authors.all())
	has_no_authors = not existing_authors
	matched_existing_ids = set()

	diffs = []
	for author_data in crossref_authors:
		given = author_data.get("given")
		family = author_data.get("family")
		if not given or not family:
			continue

		key = _crossref_author_key(author_data)
		match = next(
			(a for a in existing_authors if _existing_author_key(a) == key), None
		)
		if match is not None:
			matched_existing_ids.add(match.pk)
			continue

		orcid = _normalize_orcid_or_none(author_data.get("ORCID"))
		label = f"{given} {family}" + (f" (ORCID {orcid})" if orcid else "")
		diffs.append(
			AuthorDiff(action="add", label=label, raw=author_data, preselect=has_no_authors)
		)

	for author in existing_authors:
		if author.pk not in matched_existing_ids:
			diffs.append(
				AuthorDiff(
					action="remove",
					label=f"{author} (not returned by CrossRef)",
					raw={"author_id": author.pk},
					preselect=False,
				)
			)

	return diffs


def build_crossref_diff(article: Articles) -> CrossrefDiff:
	"""Fetch CrossRef+Unpaywall data for `article.doi` and diff it against what
	the article currently holds. Read-only — never writes to the database."""
	paper = SciencePaper(doi=article.doi)
	try:
		result = paper.refresh()
	except Exception as exc:
		logger.exception(
			"CrossRef refresh failed for article %s (DOI %s)", article.pk, article.doi
		)
		return CrossrefDiff(fetch_error=str(exc), fields=[], authors=[])

	if SciencePaper.is_crossref_failed(result):
		return CrossrefDiff(fetch_error=result, fields=[], authors=[])

	fields = []

	proposed_title = clean_title(paper.title) if paper.title else None
	if proposed_title and proposed_title != article.title:
		fields.append(
			FieldDiff(
				field="title",
				label="Title",
				current=article.title,
				proposed=proposed_title,
				raw=proposed_title,
				preselect=_is_empty_text(article.title),
			)
		)

	proposed_summary = paper.clean_abstract() if paper.abstract else None
	if proposed_summary and proposed_summary != article.summary:
		extra = None
		if len(proposed_summary) > SUMMARY_DIFF_THRESHOLD or len(
			article.summary or ""
		) > SUMMARY_DIFF_THRESHOLD:
			extra = _unified_diff(article.summary, proposed_summary)
		fields.append(
			FieldDiff(
				field="summary",
				label="Summary",
				current=article.summary,
				proposed=proposed_summary,
				raw=proposed_summary,
				preselect=_is_empty_text(article.summary),
				extra=extra,
			)
		)

	if paper.publisher and paper.publisher != article.publisher:
		fields.append(
			FieldDiff(
				field="publisher",
				label="Publisher",
				current=article.publisher,
				proposed=paper.publisher,
				raw=paper.publisher,
				preselect=_is_empty_text(article.publisher),
			)
		)

	if paper.journal and paper.journal != article.container_title:
		fields.append(
			FieldDiff(
				field="container_title",
				label="Container title",
				current=article.container_title,
				proposed=paper.journal,
				raw=paper.journal,
				preselect=_is_empty_text(article.container_title),
			)
		)

	if paper.published_date and paper.published_date != article.published_date:
		fields.append(
			FieldDiff(
				field="published_date",
				label="Published date",
				current=article.published_date,
				proposed=paper.published_date,
				raw=paper.published_date.isoformat(),
				preselect=article.published_date is None,
				warning=_date_precision_warning(paper),
			)
		)

	if paper.retracted and not article.retracted:
		fields.append(
			FieldDiff(
				field="retracted",
				label="Retracted",
				current=article.retracted,
				proposed=True,
				raw=True,
				preselect=True,
			)
		)

	# "unknown" just means Unpaywall had nothing to say — never propose it as a
	# downgrade over an already-determined "open"/"restricted" value.
	access_is_real_downgrade = paper.access == "unknown" and not _is_empty_access(
		article.access
	)
	if (
		paper.access
		and paper.access != article.access
		and not access_is_real_downgrade
	):
		fields.append(
			FieldDiff(
				field="access",
				label="Open access (via Unpaywall)",
				current=article.access,
				proposed=paper.access,
				raw=paper.access,
				preselect=_is_empty_access(article.access),
			)
		)

	if paper.pdf_link and paper.pdf_link != article.pdf_link:
		fields.append(
			FieldDiff(
				field="pdf_link",
				label="PDF link (via Unpaywall)",
				current=article.pdf_link,
				proposed=paper.pdf_link,
				raw=paper.pdf_link,
				preselect=_is_empty_text(article.pdf_link),
			)
		)

	authors = _build_author_diffs(article, paper.authors or [])

	return CrossrefDiff(fetch_error=None, fields=fields, authors=authors)


FIELD_DESERIALIZERS = {
	"published_date": lambda raw: parse_datetime(raw) if raw else None,
}


def _deserialize_field(field_name: str, raw):
	deserializer = FIELD_DESERIALIZERS.get(field_name)
	return deserializer(raw) if deserializer else raw


def _author_family_name(author: Authors) -> str:
	return author.family_name or str(author)


def _author_full_label(author: Authors) -> str:
	if author.ORCID:
		return f"{author.given_name} {author.family_name} (ORCID {author.ORCID})"
	return f"{author.given_name} {author.family_name}"


def build_change_reason(
	changed_fields: list, added_authors: list, removed_authors: list
) -> str:
	"""Compose the simple_history change reason within the 100-char column
	budget (history_change_reason is a CharField — Postgres errors, doesn't
	truncate, on overflow). Degrades deterministically when too long: drop
	author names into "+N more" one at a time (added first, then removed),
	then collapse the scalar field list to "N fields"."""
	if not changed_fields and not added_authors and not removed_authors:
		return ""

	added = list(added_authors)
	removed = list(removed_authors)
	added_dropped = 0
	removed_dropped = 0
	fields_text = ", ".join(changed_fields)

	def render() -> str:
		segments = []
		if fields_text:
			segments.append(fields_text)
		if added or added_dropped:
			if added:
				text = "+authors " + ", ".join(added)
				if added_dropped:
					text += f" +{added_dropped} more"
			else:
				text = f"+{added_dropped} authors"
			segments.append(text)
		if removed or removed_dropped:
			if removed:
				text = "-authors " + ", ".join(removed)
				if removed_dropped:
					text += f" +{removed_dropped} more"
			else:
				text = f"-{removed_dropped} authors"
			segments.append(text)
		return CHANGE_REASON_PREFIX + "; ".join(segments)

	reason = render()

	while len(reason) > MAX_REASON_LEN and (added or removed):
		if added:
			added.pop()
			added_dropped += 1
		else:
			removed.pop()
			removed_dropped += 1
		reason = render()

	if len(reason) > MAX_REASON_LEN and changed_fields:
		fields_text = f"{len(changed_fields)} fields"
		reason = render()

	if len(reason) > MAX_REASON_LEN:
		reason = reason[: MAX_REASON_LEN - 1] + "…"

	return reason


def apply_crossref_diff(
	article: Articles, selected_fields: dict, selected_authors: list
) -> AppliedResult:
	"""Apply exactly the ticked rows from a CrossrefDiff.

	`selected_fields` maps Articles field name -> raw value, and
	`selected_authors` is the subset of author rows the user ticked — both as
	carried in the signed diff payload built from `build_crossref_diff`'s
	output. Never re-fetches CrossRef: the caller is responsible for
	round-tripping the exact values that were shown on the review page, so
	what's applied always matches what was displayed.
	"""
	update_fields = []
	changed_field_names = []
	for field_name, raw in selected_fields.items():
		setattr(article, field_name, _deserialize_field(field_name, raw))
		update_fields.append(field_name)
		changed_field_names.append(field_name)

	article.crossref_check = timezone.now()
	update_fields.append("crossref_check")
	if "retracted" in changed_field_names:
		article.crossref_retraction_check = timezone.now()
		update_fields.append("crossref_retraction_check")

	added_family, added_full = [], []
	removed_family, removed_full = [], []

	with transaction.atomic():
		for row in selected_authors:
			action = row.get("action")
			raw = row.get("raw") or {}
			if action == "add":
				author_obj = resolve_author(raw)
				if author_obj is None:
					continue
				article.authors.add(author_obj)
				added_family.append(_author_family_name(author_obj))
				added_full.append(_author_full_label(author_obj))
			elif action == "remove":
				author_obj = Authors.objects.filter(pk=raw.get("author_id")).first()
				if author_obj is None:
					continue
				article.authors.remove(author_obj)
				removed_family.append(_author_family_name(author_obj))
				removed_full.append(_author_full_label(author_obj))

		reason = build_change_reason(changed_field_names, added_family, removed_family)
		if reason:
			article._change_reason = reason

		if changed_field_names or added_family or removed_family:
			# save=False: fold this into the single save() below rather than a
			# second one, which would otherwise write a duplicate history row
			# carrying the same change reason (every save() creates one,
			# regardless of update_fields — see build_change_reason's docstring).
			clear_marker(article, "details", save=False)
			update_fields += ["details_attempts", "details_next_check"]

		article.save(update_fields=update_fields)

	return AppliedResult(
		updated_fields=changed_field_names,
		authors_added=added_full,
		authors_removed=removed_full,
		change_reason=reason,
	)
