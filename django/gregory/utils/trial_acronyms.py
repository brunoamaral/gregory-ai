"""
Trial-acronym matching for article text.

A trial's acronym (``CLARITY``, ``MS-STAT2``) is a weaker signal than a registry
id, so a mention only counts when it sits next to trial wording ("the CLARITY
trial", "(TEMSO) extension study"). The rules below were measured on real data:
do not loosen them without measuring again.

Pure functions, no database.
"""

import re

TOKEN = re.compile(r"(?<![A-Za-z0-9-])[A-Za-z0-9][A-Za-z0-9+-]{2,29}(?![A-Za-z0-9-])")
AFTER = re.compile(
	r"^[\s\)\]]*\s*(extension\s+|phase\s+\w+\s+|randomi[sz]ed\s+|clinical\s+|core\s+|pivotal\s+|open-label\s+)*"
	r"(trial|study|studies|cohort|program(me)?|registry)\b",
	re.IGNORECASE,
)
BEFORE = re.compile(r"(trial|study|program(me)?|registry)\s*[\(\[,:]?\s*$", re.IGNORECASE)


def eligible_acronym(value):
	"""The acronym to match on, or None.

	Only single tokens of 3 to 30 characters that contain a letter qualify. That
	drops multi-word acronyms, which cannot be matched as one token.
	"""
	if not value:
		return None
	value = value.strip()
	if not TOKEN.fullmatch(value):
		return None
	if not any(ch.isalpha() for ch in value):
		return None
	return value


def find_mentions(text, acronyms):
	"""Which of ``acronyms`` the text mentions: ``{acronym: adjacent}``.

	Matching is exact and case-sensitive. ``adjacent`` is true when any
	occurrence has trial wording right before or after it.
	"""
	mentions = {}
	if not text:
		return mentions
	for match in TOKEN.finditer(text):
		token = match.group(0)
		if token not in acronyms:
			continue
		if mentions.get(token):
			continue
		start, end = match.span()
		adjacent = bool(
			AFTER.match(text[end : end + 50]) or BEFORE.search(text[max(0, start - 25) : start])
		)
		mentions[token] = adjacent
	return mentions
