"""
Shared utilities for trial identity / de-duplication logic.

See docs/trials-identity-dedup.md for the full design rationale.
"""


def _norm(v) -> str:
	"""Normalise a registry-identifier value for comparison (strip + upper)."""
	return str(v).strip().upper()


def identifiers_conflict(existing_ids: dict | None, incoming_ids: dict | None) -> bool:
	"""Return True if the two identifier dicts disagree on the *same* registry key.

	A conflict means the records belong to different trials and must NOT be merged.
	Disjoint keys (e.g. one has ``nct``, the other only ``euctr``) are NOT a conflict
	— they indicate a legitimately cross-registered study.

	Truth table (where 'nct' is used as the example key):

	+-----------------------+----------------------+-----------+
	| existing_ids          | incoming_ids         | conflict? |
	+=======================+======================+===========+
	| {'nct': 'NCT001'}     | {'nct': 'NCT002'}    | True      |
	| {'nct': 'NCT001'}     | {'nct': 'NCT001'}    | False     |
	| {'nct': 'NCT001'}     | {'euctr': 'EUCTR…'}  | False     |
	| {'nct': 'NCT001'}     | {}                   | False     |
	| None                  | {'nct': 'NCT001'}    | False     |
	| {'nct': ' nct001 '}   | {'nct': 'NCT001'}    | False     |  (normalised)
	+-----------------------+----------------------+-----------+
	"""
	existing_ids = existing_ids or {}
	for key, b in (incoming_ids or {}).items():
		a = existing_ids.get(key)
		if a and b and _norm(a) != _norm(b):
			return True
	return False
