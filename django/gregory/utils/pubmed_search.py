"""The PubMed search term a source runs, shared by the admin form and the importer.

The form checks the term it would build over a 30-day window and the importer runs the
same one, so what an editor was shown is what runs.
"""

from datetime import date

# Above this many new papers in 30 days the admin warns that the search is probably
# broader than intended (all 14 PubMed RSS sources together bring in ~1,370 a month).
PUBMED_VOLUME_WARNING_PER_30_DAYS = 500


def _pubmed_date(day: date) -> str:
	return day.strftime("%Y/%m/%d")


def build_pubmed_term(query, excluded_types, start: date, end: date) -> str:
	"""``(<query>) NOT ("<type>"[pt] OR …) AND <start>:<end>[crdt]``.

	``excluded_types`` are publication type names. ``[pt]`` expands down PubMed's
	hierarchy, so excluding Review also excludes Systematic Review. PubMed reads
	boolean operators left to right, so the date window narrows the whole of the
	query and exclusions.
	"""
	term = f"({query.strip()})"
	names = [name.replace('"', "") for name in excluded_types if name]
	if names:
		term += " NOT (" + " OR ".join(f'"{name}"[pt]' for name in names) + ")"
	return f"{term} AND {_pubmed_date(start)}:{_pubmed_date(end)}[crdt]"


_CLOSERS = {")": "(", "]": "["}


def unbalanced_query(query):
	"""A message when the search has an unclosed or unmatched quote, bracket or field tag.

	The importer wraps the search in brackets and appends the date window; an unclosed
	``(`` or ``"`` would let that window attach to only part of the search, or be swallowed
	by it, and the rest would return papers from every year.
	"""
	stack, in_quote = [], False
	for char in query:
		if char == '"':
			in_quote = not in_quote
		elif in_quote:
			continue
		elif char in "([":
			stack.append(char)
		elif char in _CLOSERS:
			if not stack or stack.pop() != _CLOSERS[char]:
				return f"There is a stray \"{char}\" with nothing to close."
	if in_quote:
		return "A quotation mark is never closed."
	if stack:
		return f"A \"{stack[-1]}\" is never closed."
	return None
