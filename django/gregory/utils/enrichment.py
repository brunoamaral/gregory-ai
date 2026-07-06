"""
Backoff helpers for the pipeline enrichment tasks (find_doi, get_authors,
update_articles_info).

Contract (PIPELINE-AUDIT-PLAN.md, phase 2):
- A marker only advances on a COMPLETED attempt — the external API responded,
  even if it had nothing for us. A network error/timeout advances nothing, so
  an outage cannot silently delay real work.
- A fruitless completed attempt pushes next_check out by min(2^attempts, 30)
  days: 2d, 4d, 8d, 16d, then steady-state 30d. Never a hard stop — nothing is
  permanently abandoned; the steady-state cost is one attempt a month.
- Success clears the marker.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

BACKOFF_CAP_DAYS = 30


def backoff_delta(attempts: int) -> timedelta:
	"""Days until the next attempt after `attempts` fruitless completed tries."""
	return timedelta(days=min(2 ** max(attempts, 1), BACKOFF_CAP_DAYS))


def due_filter(field: str) -> Q:
	"""Queryset filter: marker never set, or due now."""
	return Q(**{f"{field}__isnull": True}) | Q(**{f"{field}__lte": timezone.now()})


def record_fruitless_attempt(article, task: str):
	"""API responded but yielded nothing; push the next check out and save."""
	attempts_field = f"{task}_attempts"
	next_check_field = f"{task}_next_check"
	attempts = getattr(article, attempts_field) + 1
	setattr(article, attempts_field, attempts)
	setattr(article, next_check_field, timezone.now() + backoff_delta(attempts))
	article.save(update_fields=[attempts_field, next_check_field])


def clear_marker(article, task: str, save: bool = True):
	"""Enrichment succeeded; reset the marker (optionally deferring the save)."""
	attempts_field = f"{task}_attempts"
	next_check_field = f"{task}_next_check"
	setattr(article, attempts_field, 0)
	setattr(article, next_check_field, None)
	if save:
		article.save(update_fields=[attempts_field, next_check_field])
