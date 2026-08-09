"""Model-authored `intent` capture for search_articles/search_trials.

Phase 4 of MCP-TELEMETRY-PLAN.md. Unlike Phase 1/2's bagged, closed-vocabulary
telemetry, `intent` is stored as the full phrase the model supplied — bagging
would destroy exactly what makes it useful (the shape of the ask). The
protection here is exposure duration and review, not content reduction:

- its own log stream (see logging_config.INTENT_LOGGER_NAME /
  IntentJsonFormatter) — no field this module emits is also emitted on a
  telemetry.py `mcp_request` line, and neither carries a request id or
  session id, so the two streams have nothing to join on
- a 90-day rolling retention / hard delete window (deploy-side, Phase 6)
- a heuristic PII scan on write that flags without blocking, feeding the
  scheduled review in the plan's Phase 5

Not collected on search_authors — an intent phrased around a named person
would reintroduce exactly what that tool's own carve-out removes (see the
plan's "not logged at any phase").
"""

from __future__ import annotations

import logging
import re

from .logging_config import INTENT_LOGGER_NAME

logger = logging.getLogger(INTENT_LOGGER_NAME)

_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+")
_LONG_DIGIT_RUN_RE = re.compile(r"\d{7,}")
_FIRST_PERSON_MEDICAL_RE = re.compile(
	r"\b(my|our)\s+(son|daughter|child|wife|husband|mother|father)\b"
	r"|\bi\s*(?:'m|\s+am|\s+was)\s+(?:being\s+)?(diagnosed|treated)\b",
	re.IGNORECASE,
)
_AGE_RE = re.compile(r"\b\d{1,3}\s*-?\s*years?\s*-?\s*old\b|\baged\s+\d{1,3}\b", re.IGNORECASE)

# Mirrors tools/trials.py's Region literal plus a few generic locational
# nouns. Deliberately coarse — a cheap heuristic, not an exhaustive gazetteer
# — and expected to be retuned against real flag-rate data during the
# Phase 5 review rather than upfront.
_GEOGRAPHY_HINTS = (
	"africa", "asia", "europe", "oceania",
	"hospital", "clinic", "city", "town", "village", "region",
	"province", "county", "state", "country", "district",
)
_GEOGRAPHY_RE = re.compile(r"\b(" + "|".join(_GEOGRAPHY_HINTS) + r")\b", re.IGNORECASE)


async def _matches_a_category_term(text: str) -> bool:
	"""Reuses query_shape's taxonomy match (imported lazily — same
	telemetry -> query_shape -> cache -> client -> telemetry cycle this
	module sits inside of) to ask "does this text contain a term from
	Gregory's own public category vocabulary?" without duplicating the
	tokenizer/index. A taxonomy-fetch failure degrades to "no match" rather
	than raising — this is one signal among several, not load-bearing.
	"""
	from . import query_shape

	try:
		shape = await query_shape.analyze(text)
	except Exception:
		return False
	return bool(shape and shape.matched_category_slugs)


async def scan_for_pii(text: str) -> list[str]:
	"""Heuristic flags for `text`. Never blocks — only annotates the record
	so the Phase 5 review can find it. Order matches the plan's pattern
	table.
	"""
	flags: list[str] = []
	if _EMAIL_RE.search(text):
		flags.append("email")
	if _LONG_DIGIT_RUN_RE.search(text):
		flags.append("long_digit_run")
	if _FIRST_PERSON_MEDICAL_RE.search(text):
		flags.append("first_person_medical")

	has_age = bool(_AGE_RE.search(text))
	if has_age:
		flags.append("age_specificity")

	has_geography = bool(_GEOGRAPHY_RE.search(text))
	if (has_age or has_geography) and await _matches_a_category_term(text):
		flags.append("specificity_co_occurrence")

	return flags


async def record(tool: str, text: str) -> None:
	"""Logs one intent event to its own stream. Best-effort: a failure here
	(e.g. the taxonomy fetch inside the PII scan) must never fail the tool
	call this intent was attached to.
	"""
	if not text or not text.strip():
		return
	try:
		flags = await scan_for_pii(text)
	except Exception:
		logger.warning("intent_pii_scan_failed", exc_info=True)
		flags = []
	logger.info("mcp_intent", extra={"tool": tool, "intent": text, "pii_flags": flags})
