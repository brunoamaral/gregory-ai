"""
Service logic for applying an ORCID API record onto an Authors instance.

Used by both the ``update_orcid`` management command (batch refresh) and the
Authors admin's per-author "Recheck ORCID" button (single-author refresh).
"""

from dataclasses import dataclass, field
from typing import List

from django.utils import timezone
from simple_history.utils import update_change_reason


@dataclass
class OrcidSyncResult:
	changed_fields: List[str] = field(default_factory=list)
	has_address: bool = False
	has_biography: bool = False


def apply_orcid_record_to_author(author, record, change_reason_suffix=""):
	"""
	Update ``author``'s country/biography from an ORCID API ``record``, save it,
	and record a simple_history change reason if anything changed.
	"""
	person = record.get("person", {}) or {}
	addresses = person.get("addresses", {}).get("address", [])
	biography_content = (person.get("biography") or {}).get("content")

	initial_country = author.country
	initial_biography = author.biography

	author.orcid_check = timezone.now()

	if addresses:
		author.country = addresses[0].get("country", {}).get("value")
	if biography_content:
		author.biography = biography_content

	author.save()

	result = OrcidSyncResult(
		has_address=bool(addresses), has_biography=bool(biography_content)
	)
	if initial_country != author.country:
		result.changed_fields.append("country")
	if initial_biography != author.biography:
		result.changed_fields.append("biography")

	if result.changed_fields:
		reason = f"Updated {' and '.join(result.changed_fields)} from ORCID API."
		if change_reason_suffix:
			reason = f"{reason} {change_reason_suffix}"
		update_change_reason(author, reason)

	return result
