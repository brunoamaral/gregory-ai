import re
import warnings
import stopwords
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

# Define some cleaning procedures:
REPLACE_BY_SPACE_RE = re.compile(r"[/(){}\[\]\|@,;]")
BAD_SYMBOLS_RE = re.compile(r"[^0-9a-z #+_]")
WHITESPACE_RE = re.compile(r"[\n\t\r]+")  # Newlines, tabs, carriage returns
MULTIPLE_SPACES_RE = re.compile(r"\s+")  # Multiple whitespace characters
STOPWORDS = set(stopwords.get_stopwords("en"))

# DOI and PMID patterns (matching notebook preprocessing in files_repo_PBL_nsbe)
DOI_PATTERN_RE = re.compile(r"doi:?\s?\d{2}\.\d{4}/\S+", re.IGNORECASE)
DOI_PATTERN_SHORT_RE = re.compile(r"\d{2}\.\d{4}/\S+")
PMID_PATTERN_RE = re.compile(r"pmid:?\s?\d{8}", re.IGNORECASE)
PMID_SHORT_RE = re.compile(r"\b\d{8}\b")

# Minimum word count threshold (matching notebook preprocessing)
MIN_WORD_COUNT = 10


def cleanText(text, min_words=None):
	"""
	Cleans the input text by applying the following:

	    * normalize whitespace (newlines, tabs, carriage returns to spaces)
	    * change to lowercase text
	    * remove DOI patterns (e.g., doi:10.1234/xyz or 10.1234/xyz)
	    * remove PMID patterns (e.g., pmid:12345678 or 12345678)
	    * replace common symbols with a space
	    * replace invalid symbols with empty char
	    * remove stopwords from text
	    * collapse multiple spaces into single space
	    * optionally filter out texts with fewer than min_words words

	Arguments
	---------
	text: a string
	min_words: int or None. If provided, returns None for texts with fewer
	           than this many words. Use MIN_WORD_COUNT constant (10) to match
	           notebook preprocessing. Default is None (no filtering).

	Output
	------
	return: modified initial string, or None if text is empty/too short
	"""
	if not text or not isinstance(text, str):
		return None

	# normalize whitespace: convert newlines, tabs, carriage returns to spaces
	text = WHITESPACE_RE.sub(" ", text)

	# change to lowercase text
	text = text.lower()

	# remove DOI patterns (matching notebook preprocessing)
	text = DOI_PATTERN_RE.sub("", text)
	text = DOI_PATTERN_SHORT_RE.sub("", text)

	# remove PMID patterns (matching notebook preprocessing)
	text = PMID_PATTERN_RE.sub("", text)
	text = PMID_SHORT_RE.sub("", text)

	# replace REPLACE_BY_SPACE_RE symbols by space in text
	text = REPLACE_BY_SPACE_RE.sub(" ", text)

	# remove symbols which are in BAD_SYMBOLS_RE from text
	text = BAD_SYMBOLS_RE.sub("", text)

	# collapse multiple spaces into single space
	text = MULTIPLE_SPACES_RE.sub(" ", text)

	# strip leading/trailing whitespace
	text = text.strip()

	# remove stopwords from text
	words = [word for word in text.split() if word not in STOPWORDS]
	text = " ".join(words)

	# check for empty or whitespace-only result
	if not text or text.isspace():
		return None

	# optionally filter by minimum word count (matching notebook preprocessing)
	if min_words is not None and len(words) < min_words:
		return None

	return text


def cleanHTML(input):
	"""
	Remove HTML tags and return plain text.

	Arguments
	---------
	input: a string containing HTML

	Output
	------
	return: plain text with HTML tags removed
	"""
	if not input:
		return ""
	return BeautifulSoup(input, "html.parser").get_text()


BLOCK_TAGS = ("br", "p", "li", "tr", "div")

# Tags actually seen in WHO ICTRP registry fields. Shared with backfill_clean_titles'
# sibling command (clean_trial_html) so row selection uses the same whitelist as the
# cleaner itself.
ALLOWED_TAGS = BLOCK_TAGS + (
	"b", "a", "i", "u", "em", "strong", "ul", "ol", "table", "td", "th", "hr",
	"sub", "sup", "font", "span",
)

# html.parser treats *any* "<word word...>" as a tag, which would swallow angle
# brackets some registries use as quotation marks (e.g. "criteria <the guide of
# diagnosis and treatment>"). Only whitelisted tags are parsed as markup; everything
# else is escaped to literal text before parsing so the parser can't misread it.
_ALLOWED_TAG_RE = re.compile(
	r"</?(?:" + "|".join(ALLOWED_TAGS) + r")(?:\s[^<>]*)?/?>",
	re.IGNORECASE,
)


def _escape_non_tag_angle_brackets(text):
	"""Escape < and > outside whitelisted tags so a parser treats them as literal
	characters rather than markup."""
	out = []
	pos = 0
	for match in _ALLOWED_TAG_RE.finditer(text):
		out.append(text[pos:match.start()].replace("<", "&lt;").replace(">", "&gt;"))
		out.append(match.group(0))
		pos = match.end()
	out.append(text[pos:].replace("<", "&lt;").replace(">", "&gt;"))
	return "".join(out)


def clean_field_html(value):
	"""
	Plain text from a registry field that may contain HTML.

	Block/line-break tags become whitespace before extraction so "A<br>B" yields
	"A B", not "AB" (plain get_text() would run the words together). Entities are
	unescaped by the parser. Whitespace is collapsed and the result stripped;
	returns None for empty/blank input so callers keep the "absent field" semantics.

	Uses a real parser (not a regex): several ICTRP registries use angle brackets as
	quotation marks (e.g. "criteria <the guide of diagnosis and treatment>"), which a
	tag-shaped regex would delete. See WHO-HTML-CLEANUP-PLAN.md.

	Arguments
	---------
	value: a string that may contain HTML, or None

	Output
	------
	return: plain text, or None if input is empty/blank or becomes empty after cleaning
	"""
	if not value or not isinstance(value, str) or not value.strip():
		return None

	escaped = _escape_non_tag_angle_brackets(value)
	# Most fields have no HTML at all; plain text can still trip bs4's "resembles a
	# filename/URL" heuristic, which is irrelevant here (input is always XML field
	# text, never a path to open).
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
		soup = BeautifulSoup(escaped, "html.parser")
	# unwrap (not replace_with): these tags can carry inner text (<p>, <li>, ...),
	# so only the tag markup becomes a space -- the content must stay.
	for tag in soup.find_all(BLOCK_TAGS):
		tag.insert_before(" ")
		tag.unwrap()

	text = soup.get_text(" ")
	text = MULTIPLE_SPACES_RE.sub(" ", text).strip()
	return text or None
