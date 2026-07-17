"""
Service logic for applying an ORCID API record onto an Authors instance.

Used by both the ``update_orcid`` management command (batch refresh) and the
Authors admin's per-author "Recheck ORCID" button (single-author refresh).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from django.utils import timezone
from simple_history.utils import update_change_reason


@dataclass
class OrcidSyncResult:
	changed_fields: List[str] = field(default_factory=list)
	has_address: bool = False
	has_biography: bool = False
	has_keywords: bool = False
	has_affiliation: bool = False


def _parse_date_tuple(date_obj):
	"""Turn an ORCID ``{'year': {'value': ...}, 'month': ..., 'day': ...}`` blob
	into a sortable ``(year, month, day)`` int tuple, treating missing parts as 0."""
	date_obj = date_obj or {}

	def _part(key):
		value = (date_obj.get(key) or {}).get("value")
		try:
			return int(value)
		except (TypeError, ValueError):
			return 0

	return (_part("year"), _part("month"), _part("day"))


def select_current_affiliation(activities_summary) -> Optional[str]:
	"""
	Pick the organization name to store as ``current_affiliation`` from an
	ORCID ``activities-summary.employments`` structure.

	Prefers ongoing employments (``end-date`` is null) over ended ones, then
	picks the one with the latest ``start-date``.
	"""
	activities_summary = activities_summary or {}
	employments = (activities_summary.get("employments") or {}).get(
		"affiliation-group", []
	) or []

	summaries = []
	for group in employments:
		for summary in group.get("summaries", []) or []:
			employment_summary = summary.get("employment-summary")
			if employment_summary:
				summaries.append(employment_summary)

	if not summaries:
		return None

	ongoing = [s for s in summaries if not s.get("end-date")]
	candidates = ongoing or summaries

	best = max(candidates, key=lambda s: _parse_date_tuple(s.get("start-date")))
	organization = best.get("organization") or {}
	return organization.get("name")


def _extract_external_ids(person):
	raw_ids = (person.get("external-identifiers") or {}).get(
		"external-identifier", []
	) or []
	external_ids = []
	for item in raw_ids:
		external_ids.append(
			{
				"type": item.get("external-id-type"),
				"value": item.get("external-id-value"),
				"url": (item.get("external-id-url") or {}).get("value"),
			}
		)
	return external_ids


def _extract_researcher_urls(person):
	raw_urls = (person.get("researcher-urls") or {}).get("researcher-url", []) or []
	researcher_urls = []
	for item in raw_urls:
		researcher_urls.append(
			{
				"name": item.get("url-name"),
				"url": (item.get("url") or {}).get("value"),
			}
		)
	return researcher_urls


def _extract_keywords(person):
	raw_keywords = (person.get("keywords") or {}).get("keyword", []) or []
	return [
		kw.get("content") for kw in raw_keywords if kw.get("content") is not None
	]


def _extract_emails(person):
	raw_emails = (person.get("emails") or {}).get("email", []) or []
	return [e.get("email") for e in raw_emails if e.get("email")]


def apply_orcid_record_to_author(author, record, change_reason_suffix=""):
	"""
	Update ``author``'s fields from an ORCID API ``record``, save it, and
	record a simple_history change reason if anything changed.
	"""
	person = record.get("person", {}) or {}
	activities_summary = record.get("activities-summary", {}) or {}
	history = record.get("history", {}) or {}

	addresses = (person.get("addresses") or {}).get("address", [])
	biography_content = (person.get("biography") or {}).get("content")
	credit_name = ((person.get("name") or {}).get("credit-name") or {}).get("value")
	emails = _extract_emails(person)
	keywords = _extract_keywords(person)
	external_ids = _extract_external_ids(person)
	researcher_urls = _extract_researcher_urls(person)
	current_affiliation = select_current_affiliation(activities_summary)
	orcid_claimed = history.get("claimed")
	orcid_verified_email = history.get("verified-email")

	initial_values = {
		"country": author.country,
		"biography": author.biography,
		"credit_name": author.credit_name,
		"emails": author.emails,
		"orcid_keywords": author.orcid_keywords,
		"external_ids": author.external_ids,
		"researcher_urls": author.researcher_urls,
		"current_affiliation": author.current_affiliation,
		"orcid_claimed": author.orcid_claimed,
		"orcid_verified_email": author.orcid_verified_email,
	}

	author.orcid_check = timezone.now()

	if addresses:
		author.country = addresses[0].get("country", {}).get("value")
	if biography_content:
		author.biography = biography_content
	if credit_name:
		author.credit_name = credit_name
	if emails:
		author.emails = emails
	if keywords:
		author.orcid_keywords = keywords
	if external_ids:
		author.external_ids = external_ids
	if researcher_urls:
		author.researcher_urls = researcher_urls
	if current_affiliation:
		author.current_affiliation = current_affiliation
	if orcid_claimed is not None:
		author.orcid_claimed = orcid_claimed
	if orcid_verified_email is not None:
		author.orcid_verified_email = orcid_verified_email

	author.save()

	result = OrcidSyncResult(
		has_address=bool(addresses),
		has_biography=bool(biography_content),
		has_keywords=bool(keywords),
		has_affiliation=bool(current_affiliation),
	)

	field_labels = {
		"country": "country",
		"biography": "biography",
		"credit_name": "credit name",
		"emails": "emails",
		"orcid_keywords": "keywords",
		"external_ids": "external IDs",
		"researcher_urls": "researcher URLs",
		"current_affiliation": "current affiliation",
		"orcid_claimed": "claimed status",
		"orcid_verified_email": "verified email status",
	}
	for field_name, label in field_labels.items():
		if initial_values[field_name] != getattr(author, field_name):
			result.changed_fields.append(label)

	if result.changed_fields:
		reason = f"Updated {' and '.join(result.changed_fields)} from ORCID API."
		if change_reason_suffix:
			reason = f"{reason} {change_reason_suffix}"
		# simple_history's history_change_reason column is capped at 100 chars.
		if len(reason) > 100:
			reason = reason[:97] + "..."
		update_change_reason(author, reason)

	return result
