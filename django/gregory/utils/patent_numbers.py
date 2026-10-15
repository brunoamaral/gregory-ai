"""Patent publication numbers as people type them.

Stored numbers are the canonical DOCDB form (``EP4123456A1``) with ``country``,
``doc_number`` and ``kind`` kept alongside (``PatentPublication``). People type
``EP 4123456 A1``, ``EP-4123456-A1``, ``ep4123456`` or Espacenet's
``EP4123456.A1``. ``normalise_publication_number`` turns any of those into the
parts a lookup needs.

Matching is on the parts, never ``startswith`` on the stored string:
``EP123`` must not match ``EP1234567``.
"""

import re

from django.db.models import Q

# country, digits, optional kind (a letter, plus a digit for B1/A2/...). Covers the
# overwhelming majority of stored numbers.
_NUMERIC_RE = re.compile(r"^([A-Z]{2})(\d+)([A-Z]\d?)?$")


def clean_publication_number(raw: str) -> str:
	"""Upper-case and drop everything that is not a letter or digit."""
	return re.sub(r"[^A-Z0-9]", "", (raw or "").upper())


def normalise_publication_number(raw: str) -> tuple[str, str, str | None] | None:
	"""Split a typed publication number into ``(country, doc_number, kind)``.

	``kind`` is None when the caller left it off, which means "every kind".
	Returns None for anything that is not country + digits + optional kind.

	Some stored numbers have letters inside the number itself
	(``MXPA02011163A``, ``HUP0303203A2``). Those do not parse here; callers
	match the cleaned string exactly as well (``clean_publication_number``).
	"""
	cleaned = clean_publication_number(raw)
	match = _NUMERIC_RE.match(cleaned)
	if not match:
		return None
	country, doc_number, kind = match.groups()
	return country, doc_number, kind


def publication_number_q(raw_values) -> Q:
	"""A ``Q`` over ``PatentPublication`` matching any of the typed numbers.

	Matches on the stored parts (country, doc_number, kind), never
	``startswith`` on the string: ``EP123`` must not find ``EP1234567``. A
	number without a kind code matches every kind, and a letter alone (``B``)
	matches that letter with or without a digit (``B``, ``B1``, ``B2``). A
	number that does not split into country + digits + kind
	(``MXPA02011163A``, with letters inside the number) matches the cleaned
	string exactly instead. Empty when nothing usable was given.

	Shared by the ``publication_number`` filter on ``/patents/`` and the
	editor's resolver, so the two cannot disagree on what a number means.
	"""
	condition = Q()
	for raw in raw_values or []:
		cleaned = clean_publication_number(raw)
		if not cleaned:
			continue
		token_q = Q(publication_number=cleaned)
		parts = normalise_publication_number(raw)
		if parts is not None:
			country, doc_number, kind = parts
			parts_q = Q(country=country, doc_number=doc_number)
			if kind and len(kind) == 1:
				# "EP4123456B" is how people type "EP4123456B1": the stored kind
				# is the letter plus a digit, so match the letter.
				parts_q &= Q(kind__startswith=kind)
			elif kind:
				parts_q &= Q(kind=kind)
			token_q |= parts_q
		condition |= token_q
	return condition
