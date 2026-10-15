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
