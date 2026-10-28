"""
rss/abstract_html.py

Turn a stored abstract or trial summary into safe, readable feed HTML.

Abstracts come in with whatever markup the source used: Crossref sends JATS
(<jats:sec>, <jats:title>, <jats:p>, <jats:italic>, MathML), PubMed feeds send
HTML (<div>, <p>, <b>, <a>), registries send <br /> lists. Escaping all of it
shows the raw tags to readers, so instead we rebuild it:

- block elements (sections, paragraphs, divs, list items) become <p>
- section titles and headings become a <p><strong>…</strong></p> line
- italic, bold, sub and superscript survive as bare <i>, <b>, <sub>, <sup>
  (<em>, <strong> too), with every attribute dropped
- everything else (links, small caps, MathML…) keeps its text, loses its tag
- scripts, styles, TeX duplicates of MathML and image references are dropped

The output is built only from escaped text and the fixed tags above, so a
third-party abstract can never inject markup into a feed.

Text with no tags at all keeps the old treatment: escaped, line breaks as <br>.
"""

import re

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import Comment, Declaration, Doctype, ProcessingInstruction
from django.template.defaultfilters import linebreaksbr
from django.utils.html import escape
from django.utils.safestring import mark_safe

# Something that looks like a complete tag: <p>, </jats:p>, <br />, <a href="…">.
# A bare "<" in plain text ("aged <5 years") doesn't match.
_TAG_RE = re.compile(r"</?[A-Za-z][\w:.-]*(\s[^<>]*)?/?>")

# Tag names are compared without their namespace prefix: jats:p -> p.
_HEADINGS = {"title", "h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCKS = {
	"p", "div", "sec", "abstract", "trans-abstract", "section", "article",
	"blockquote", "list", "list-item", "ul", "ol", "li", "boxed-text", "fig",
	"caption", "table", "table-wrap", "tr", "disp-quote", "statement",
}
_INLINE_KEEP = {
	"i": "i", "italic": "i", "em": "em",
	"b": "b", "bold": "b", "strong": "strong",
	"sub": "sub", "sup": "sup",
}
_DROP = {
	"script", "style", "tex-math", "graphic", "inline-graphic", "object-id",
	"alt-text", "head",
}
# Non-text nodes BeautifulSoup hands back as strings.
_SKIP_STRINGS = (Comment, Declaration, Doctype, ProcessingInstruction)

# A heading or paragraph that only says "Abstract" repeats our own heading.
_ABSTRACT_LABEL_RE = re.compile(r"^\s*abstract\s*:?\s*$", re.IGNORECASE)


def _name(tag):
	return tag.name.rsplit(":", 1)[-1].lower()


def _text(node):
	return escape(re.sub(r"\s+", " ", str(node)))


def _inline(node):
	"""Escaped HTML for a node rendered inside a paragraph."""
	if isinstance(node, NavigableString):
		return "" if isinstance(node, _SKIP_STRINGS) else _text(node)
	name = _name(node)
	if name in _DROP:
		return ""
	if name == "br":
		return "<br>"
	content = "".join(_inline(child) for child in node.children)
	if name in _BLOCKS or name in _HEADINGS:
		return f" {content} "
	tag = _INLINE_KEEP.get(name)
	if tag and content.strip():
		return f"<{tag}>{content}</{tag}>"
	return content


def _tidy(html):
	"""Collapse whitespace, including the pretty-printing JATS puts around
	inline tags ("(\\n  <italic>n</italic>\\n  = 65)", "CD19\\n  <sup>+</sup>")."""
	html = re.sub(r"\s+", " ", html)
	html = re.sub(r"\s+(<su[bp]>)", r"\1", html)
	html = re.sub(r"([(\[])\s+", r"\1", html)
	html = re.sub(r"\s+([)\]])", r"\1", html)
	html = re.sub(r"^(\s*<br>)+|(<br>\s*)+$", "", html.strip())
	return html.strip()


def _blocks(node, out, buffer):
	"""Walk block structure, appending ("p" | "h", html) pairs to out."""

	def flush():
		html = _tidy("".join(buffer))
		buffer.clear()
		if html:
			out.append(("p", html))

	for child in node.children:
		if isinstance(child, NavigableString):
			if not isinstance(child, _SKIP_STRINGS):
				buffer.append(_text(child))
			continue
		if not isinstance(child, Tag):
			continue
		name = _name(child)
		if name in _DROP:
			continue
		if name in _HEADINGS:
			flush()
			html = _tidy(_inline(child))
			if html:
				out.append(("h", html))
		elif name in _BLOCKS:
			flush()
			_blocks(child, out, buffer)
			flush()
		else:
			buffer.append(_inline(child))
	flush()


def has_markup(text):
	return bool(text) and bool(_TAG_RE.search(text))


def clean_abstract_html(text, drop_abstract_label=True):
	"""Safe feed HTML for an abstract or summary (see the module docstring).

	Returns a SafeString of <p> elements for marked-up text, or the escaped
	text with <br> line breaks for plain text (no wrapping <p>). Use
	has_markup() to tell which shape you will get.
	"""
	if not text:
		return mark_safe("")
	if not has_markup(text):
		return linebreaksbr(text)

	out = []
	_blocks(BeautifulSoup(text, "html.parser"), out, [])

	if drop_abstract_label:
		out = [
			(kind, html) for kind, html in out
			if not _ABSTRACT_LABEL_RE.match(re.sub(r"<[^>]+>", "", html))
		]

	parts = []
	for kind, html in out:
		if kind == "h":
			parts.append(f"<p><strong>{html}</strong></p>")
		else:
			parts.append(f"<p>{html}</p>")
	return mark_safe("".join(parts))
