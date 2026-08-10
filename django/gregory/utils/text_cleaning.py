"""Shared text-cleaning helpers used both when ingesting feed entries and
when reviewing upstream (CrossRef) metadata in the admin.
"""

from typing import Optional

# Inline tags whose markup carries meaning and should be preserved in titles
SEMANTIC_TITLE_TAGS = {"sub", "sup", "i", "b", "em", "strong"}


def clean_title(title: Optional[str]) -> Optional[str]:
	"""Normalize a title before storage.

	Publisher feeds (notably PubMed/Wiley) and CrossRef embed inline markup
	and pretty-printed newlines/indentation inside titles. We unescape HTML
	entities, keep semantically meaningful inline tags (sub, sup, i, b, em,
	strong) but strip presentational/JATS tags (e.g. <scp>, <jats:*>) while
	preserving their text, drop tag attributes, and collapse runs of
	whitespace to single spaces.
	"""
	if not title:
		return title
	from bs4 import BeautifulSoup
	import html

	title = html.unescape(title)
	soup = BeautifulSoup(title, "html.parser")
	for tag in soup.find_all(True):
		if tag.name in SEMANTIC_TITLE_TAGS:
			tag.attrs = {}
		else:
			tag.unwrap()
	# formatter=None keeps entities unescaped (e.g. bare &) so the stored
	# title matches the human-readable form; str(soup) would re-encode & -> &amp;.
	return " ".join(soup.decode(formatter=None).split())
