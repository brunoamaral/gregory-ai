from __future__ import annotations

from gregory_mcp.pagination import clamp_page, clamp_page_size


def test_clamp_page_floors_at_one():
	assert clamp_page(1) == 1
	assert clamp_page(5) == 5
	assert clamp_page(0) == 1
	assert clamp_page(-5) == 1


def test_clamp_page_size_clamps_both_ends():
	assert clamp_page_size(10, 25) == 10
	assert clamp_page_size(999, 25) == 25
	assert clamp_page_size(0, 25) == 1
	assert clamp_page_size(-5, 25) == 1
