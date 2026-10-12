"""Resolve patent applicant names to canonical Sponsor rows.

Applicants go through the same machinery as trial sponsors (normalize_sponsor_key ->
SponsorAlias -> Sponsor), so a patent and a trial can share a sponsor entity. Two
differences from trials:

- OPS gives two name formats. ``original`` is the name as filed and is the closest to
  how sponsors write their names in trial registries, so it is used when present;
  ``epodoc`` (uppercase, abbreviated, country suffix like ``[GB]``) is the fallback,
  and also replaces ``original`` when an as-filed name is in a non-Latin script.
- An applicant that is also a listed inventor is an individual: the raw name is stored
  and no Sponsor is created, which keeps thousands of inventor-applicants (common in US
  filings) out of the sponsor table.
"""

import re
from dataclasses import dataclass
from typing import Optional

from gregory.models import (
	_SPONSOR_TYPE_SOURCE_PRIORITY,
	PatentApplicant,
	Sponsor,
	SponsorAlias,
	_create_sponsor_for_key,
)
from gregory.utils.trial_field_normalizers import map_sponsor_type, normalize_sponsor_key

_EPODOC_SUFFIX_RE = re.compile(r"\s*\[[A-Z]{2}\]\s*$")


@dataclass
class ResolvedApplicant:
	raw_name: str
	sponsor: Optional[Sponsor]
	is_individual: bool


def strip_epodoc_suffix(name: str) -> str:
	"""``TDK SYSTEMS EUROP LTD [GB]`` -> ``TDK SYSTEMS EUROP LTD``."""
	return _EPODOC_SUFFIX_RE.sub("", name or "").strip()


def applicant_names(biblio) -> list:
	"""The applicant names of a Biblio, original format first, deduplicated by
	normalized key (OPS repeats one applicant with different casing).

	An as-filed name in a non-Latin script (e.g. a Chinese company) normalizes to
	an empty key, so it can't be matched to a sponsor. OPS's epodoc names are
	romanised, so the whole epodoc list is used instead. The two lists can't be
	paired name by name: ``original`` repeats an applicant in different casings
	under different sequence numbers. A name that still has no key is kept as a
	raw, unresolved applicant rather than dropped."""
	originals = list(biblio.applicants_original)
	epodoc = [strip_epodoc_suffix(n) for n in biblio.applicants_epodoc]
	if not originals or (epodoc and any(not normalize_sponsor_key(n) for n in originals)):
		names = epodoc or originals
	else:
		names = originals
	seen = set()
	unique = []
	for name in names:
		key = normalize_sponsor_key(name) or name.strip()
		if key and key not in seen:
			seen.add(key)
			unique.append(name)
	return unique


def _token_key(name: str) -> str:
	"""Order-insensitive key so ``Smith, John`` matches ``SMITH JOHN [US]``."""
	key = normalize_sponsor_key(strip_epodoc_suffix(name)) or ""
	return " ".join(sorted(key.split()))


def _apply_rules_type(sponsor: Sponsor) -> None:
	"""Set a name-keyword sponsor_type, never overriding a higher-priority source."""
	if sponsor.sponsor_type_source == "curated":
		return
	new_type, new_source = map_sponsor_type(None, None, sponsor.name)
	if new_type is None:
		return
	current = _SPONSOR_TYPE_SOURCE_PRIORITY.get(sponsor.sponsor_type_source, -1)
	if _SPONSOR_TYPE_SOURCE_PRIORITY.get(new_source, -1) < current:
		return
	if sponsor.sponsor_type == new_type and sponsor.sponsor_type_source == new_source:
		return
	sponsor.sponsor_type = new_type
	sponsor.sponsor_type_source = new_source
	sponsor.save(update_fields=["sponsor_type", "sponsor_type_source"])


def resolve_applicant(name: str, inventor_keys: set) -> ResolvedApplicant:
	if _token_key(name) in inventor_keys:
		return ResolvedApplicant(raw_name=name[:500], sponsor=None, is_individual=True)
	key = normalize_sponsor_key(name)
	if key is None:
		# Nothing to match on (non-Latin script with no romanised form): keep the
		# applicant on the patent without inventing a sponsor for it.
		return ResolvedApplicant(raw_name=name[:500], sponsor=None, is_individual=False)
	alias = SponsorAlias.objects.select_related("sponsor").filter(key=key).first()
	if alias is None:
		alias = _create_sponsor_for_key(key, name[:500])
	_apply_rules_type(alias.sponsor)
	return ResolvedApplicant(raw_name=name[:500], sponsor=alias.sponsor, is_individual=False)


def resolve_applicants(biblio) -> list:
	"""Resolve every applicant of a Biblio, in order."""
	inventor_keys = {_token_key(n) for n in biblio.inventors}
	return [resolve_applicant(name, inventor_keys) for name in applicant_names(biblio)]


def save_applicants(patent, biblio) -> list:
	"""Create the PatentApplicant rows for a patent (idempotent per sequence)."""
	rows = []
	for sequence, resolved in enumerate(resolve_applicants(biblio), start=1):
		row, _ = PatentApplicant.objects.update_or_create(
			patent=patent,
			sequence=sequence,
			defaults={
				"sponsor": resolved.sponsor,
				"raw_name": resolved.raw_name,
				"is_individual": resolved.is_individual,
			},
		)
		rows.append(row)
	return rows
