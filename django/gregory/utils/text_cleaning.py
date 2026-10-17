"""Shared text-cleaning helpers used both when ingesting feed entries and
when reviewing upstream (CrossRef) metadata in the admin.
"""

import re
from typing import Optional

# Inline tags whose markup carries meaning and should be preserved in titles
SEMANTIC_TITLE_TAGS = {"sub", "sup", "i", "b", "em", "strong"}

# Something shaped like a tag: "<name>", "</name>", "<name attr=...>", "<name/>".
# Names may carry a namespace prefix (jats:italic, mml:mi).
_TITLE_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w.-]*(?::[\w.-]+)?)((?:\s[^<>]*)?)(/?)>")

# Tags that are markup on their own, without a closing partner
_VOID_TITLE_TAGS = {"br", "wbr"}


def _escape_non_markup_angle_brackets(title: str) -> str:
	"""Escape every ``<`` and ``>`` that is not part of real markup.

	html.parser reads any ``<`` followed by a letter as the start of a tag, so
	PubMed's "aged <or=5 years" or a flattened "a<b" would be unwrapped and
	everything up to the next ``>`` lost. A tag-shaped token only counts as
	markup when its name is used both as an opening and a closing tag in the
	title (real inline markup always comes in pairs), or when it is a void or
	self-closing tag; "T cells <in vivo> and more" stays literal.
	"""
	tokens = list(_TITLE_TAG_RE.finditer(title))
	opened = {m.group(2).lower() for m in tokens if not m.group(1)}
	closed = {m.group(2).lower() for m in tokens if m.group(1)}
	paired = opened & closed

	def is_markup(match):
		name = match.group(2).lower()
		return name in paired or name in _VOID_TITLE_TAGS or (
			not match.group(1) and match.group(4) == "/"
		)

	def escape(text):
		return text.replace("<", "&lt;").replace(">", "&gt;")

	out = []
	pos = 0
	for match in tokens:
		if not is_markup(match):
			continue
		out.append(escape(title[pos:match.start()]))
		out.append(match.group(0))
		pos = match.end()
	out.append(escape(title[pos:]))
	return "".join(out)


def clean_title(title: Optional[str]) -> Optional[str]:
	"""Normalize a title before storage.

	Publisher feeds (notably PubMed/Wiley) and CrossRef embed inline markup
	and pretty-printed newlines/indentation inside titles. We unescape HTML
	entities, keep semantically meaningful inline tags (sub, sup, i, b, em,
	strong) but strip presentational/JATS tags (e.g. <scp>, <jats:*>) while
	preserving their text, drop tag attributes, and collapse runs of
	whitespace to single spaces. A ``<`` or ``>`` that is not part of a tag
	("aged <or=5 years") is kept as a literal character.
	"""
	if not title:
		return title
	from bs4 import BeautifulSoup
	import html

	title = _escape_non_markup_angle_brackets(html.unescape(title))
	soup = BeautifulSoup(title, "html.parser")
	for tag in soup.find_all(True):
		if tag.name in SEMANTIC_TITLE_TAGS:
			tag.attrs = {}
		else:
			tag.unwrap()
	# formatter=None keeps entities unescaped (e.g. bare &) so the stored
	# title matches the human-readable form; str(soup) would re-encode & -> &amp;.
	return " ".join(soup.decode(formatter=None).split())


_TRUNCATION_ENDINGS = (
	re.compile(r"\.\.\.$"),  # ...
	re.compile(r"\[\.\.\.\]$"),  # [...]
	re.compile(r"\[…\]$"),  # [...] with the ellipsis character
	re.compile(r"…$"),  # ellipsis character
)


def is_summary_truncated(summary):
	"""True when a summary ends with a common truncation marker (``...``, ``[...]``,
	``…``). Feeds cut long abstracts off this way."""
	if not summary:
		return False
	summary = summary.strip()
	return any(pattern.search(summary) for pattern in _TRUNCATION_ENDINGS)
