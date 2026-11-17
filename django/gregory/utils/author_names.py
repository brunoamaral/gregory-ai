"""Loose author-name keys, so spelling variants of one name share a key.

"Belgin Koçer", "Belgin Kocer" and "Belgin KOÇER", or "J Helen Cross" and
"J. Helen Cross", all give the same key. The key is stored in
``Authors.name_key`` and used by the importers as a fallback when no exact
given and family name match exists.

Not initials-only matching: "J Cross" and "Jane Cross" keep different keys.
"""

import unicodedata

# Characters that separate words rather than disappear: "Jean-Luc" and
# "Jean Luc" must agree, "O'Brien" and "O Brien" too.
_SEPARATORS = "-‐‑‒–—−.'’‘ʼ`´"
_SEPARATOR_TABLE = {ord(c): " " for c in _SEPARATORS}


def _normalise_part(value):
	text = unicodedata.normalize("NFKD", value or "")
	text = "".join(c for c in text if not unicodedata.combining(c))
	text = text.casefold().translate(_SEPARATOR_TABLE)
	# Keep letters of any script (Greek, Cyrillic, CJK...) and spaces.
	text = "".join(c if (c.isalpha() or c.isspace()) else "" for c in text)
	return " ".join(text.split())


def name_key(given_name, family_name):
	"""Return the normalised ``"given|family"`` key, or "" when either part
	normalises to nothing (such a key must never be matched on)."""
	given = _normalise_part(given_name)
	family = _normalise_part(family_name)
	if not given or not family:
		return ""
	return f"{given}|{family}"
