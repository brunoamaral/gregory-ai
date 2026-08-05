"""Shared page/page_size clamping for tools that paginate."""

from __future__ import annotations


def clamp_page(page: int) -> int:
	"""Floor at 1 — the API's page numbering starts there; 0 or negative
	values would otherwise be forwarded upstream and 400."""
	return max(page, 1)


def clamp_page_size(page_size: int, max_size: int) -> int:
	"""Clamp to [1, max_size] — a 0 or negative page_size would otherwise be
	forwarded upstream and produce an empty or erroring page."""
	return max(1, min(page_size, max_size))
