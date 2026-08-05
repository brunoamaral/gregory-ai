"""Shared literal types for tool parameters, mirroring the API's choice fields.

Kept in one place so search_articles and search_trials can't drift from each
other on a modality list that's supposed to be identical.
"""

from __future__ import annotations

from typing import Literal

CategoryModality = Literal[
	"biologic_antibody",
	"cell_gene_therapy",
	"device_neuromodulation",
	"natural_product",
	"other",
	"rehabilitation",
	"research_topic",
	"small_molecule",
]
