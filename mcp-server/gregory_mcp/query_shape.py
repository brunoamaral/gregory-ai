"""Query-shape and taxonomy-match analysis for free-text search arguments.

Phase 2 of MCP-TELEMETRY-PLAN.md. The query string itself is never logged —
only shape counts and matches against Gregory's own public category
vocabulary (`category_terms` + `category_name` on `/categories/`). Splitting
the query into individual terms and matching each independently destroys
co-occurrence (the thing that actually identifies a caller — see the plan's
"search — bagged" section), while the taxonomy match keeps the output
alphabet limited to the site's own public category slugs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cache import get_all_pages_cached

# A query tripping any of these is dropped from shape analysis entirely
# rather than attempted-and-redacted — cheap insurance against a pasted
# email or phone number, per the plan's guardrail.
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+")
_LONG_DIGIT_RUN_RE = re.compile(r"\d{7,}")
_MAX_QUERY_LENGTH = 400

_BOOLEAN_OP_RE = re.compile(r"\b(AND|OR|NOT)\b")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A small, deliberately generic English stopword list — dropping these
# keeps term_count and the taxonomy match on content words, not glue words.
# Not biomedical-domain-aware by design: a domain stopword list would need
# tuning against real query volume, which is exactly what Phase 2 is gated
# on not having yet (see MCP-TELEMETRY-PLAN.md).
_STOPWORDS = frozenset(
	{
		"the", "and", "or", "not", "for", "with", "from", "into", "onto",
		"this", "that", "these", "those", "has", "have", "had", "was", "were",
		"are", "is", "be", "been", "being", "its", "of", "in", "on", "to",
		"as", "at", "by", "if", "so", "than", "then", "all", "any", "can",
	}
)

# (upper length inclusive, label) — checked in order, so keep it ascending.
_LENGTH_BUCKETS = ((20, "1-20"), (50, "21-50"), (100, "51-100"), (400, "101-400"))


@dataclass(frozen=True)
class QueryShape:
	term_count: int
	has_boolean_ops: bool
	has_quoted_phrase: bool
	length_bucket: str
	matched_category_slugs: list[str]
	unmatched_term_count: int


def _length_bucket(length: int) -> str:
	for ceiling, label in _LENGTH_BUCKETS:
		if length <= ceiling:
			return label
	return _LENGTH_BUCKETS[-1][1]  # pragma: no cover — unreachable below the guardrail's own cap


def _looks_identifying(text: str) -> bool:
	return len(text) > _MAX_QUERY_LENGTH or bool(_EMAIL_RE.search(text) or _LONG_DIGIT_RUN_RE.search(text))


def _tokenize(text: str) -> list[str]:
	return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 3 and t not in _STOPWORDS]


class TaxonomyIndex:
	"""term -> owning category slugs, built from /categories/'s public
	category_terms + category_name.

	Rebuilt per call rather than cached separately: the categories list it's
	built from already rides the 10-minute get_all_pages_cached entry every
	catalog tool shares, and at the volumes Phase 2 is gated on this is a
	few hundred string operations — not worth a second cache layer ahead of
	seeing what real traffic looks like.
	"""

	def __init__(self, categories: list[dict]):
		self._term_to_slugs: dict[str, set[str]] = {}
		for category in categories:
			slug = category.get("category_slug")
			if not slug:
				continue
			terms = [*(category.get("category_terms") or []), category.get("category_name") or ""]
			for term in terms:
				for token in _tokenize(term):
					self._term_to_slugs.setdefault(token, set()).add(slug)

	def match(self, tokens: list[str]) -> tuple[list[str], int]:
		matched_slugs: set[str] = set()
		unmatched = 0
		for token in tokens:
			slugs = self._term_to_slugs.get(token)
			if slugs:
				matched_slugs.update(slugs)
			else:
				unmatched += 1
		return sorted(matched_slugs), unmatched


async def analyze(text: str) -> QueryShape | None:
	"""Shape + taxonomy match for `text`, or None if it's blank or looks
	like it might carry an identifier — analysis is skipped entirely in
	that case, not attempted-and-redacted.
	"""
	if not text or not text.strip() or _looks_identifying(text):
		return None

	tokens = _tokenize(text)
	categories = await get_all_pages_cached("/categories/")
	matched_slugs, unmatched_term_count = TaxonomyIndex(categories).match(tokens)

	return QueryShape(
		term_count=len(tokens),
		has_boolean_ops=bool(_BOOLEAN_OP_RE.search(text)),
		has_quoted_phrase='"' in text,
		length_bucket=_length_bucket(len(text)),
		matched_category_slugs=matched_slugs,
		unmatched_term_count=unmatched_term_count,
	)
