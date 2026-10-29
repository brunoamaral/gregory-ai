"""
Linking an article to a clinical trial by hand.

The one place that knows the rules for an editor's link, shared by the MCP editor
endpoints (``api/editor_views.py``) and the Django admin article page:

- Adding makes a ``source="manual"`` row with ``identifier_type="manual"``. A pair that
  already has a visible link (manual or auto) gets nothing new.
- Removing deletes a manual row. An auto-detected row is kept and marked ``suppressed``,
  so ``detect_trial_references`` does not recreate the link.

These functions do not choose who gets recorded in the history: the caller does. The
editor endpoints wrap them in ``editing_as(user, "mcp")``; the admin calls them inside
the request, where simple-history's middleware records ``via="admin"``.
"""

from django.db import transaction
from django.db.models import Case, When

from gregory.models import ArticleTrialReference

#: Preferred order when picking a trial's primary identifier for a manual link.
PRIMARY_IDENTIFIER_KEYS = ("nct", "euct", "eudract", "euctr", "isrctn", "ctis")


def primary_identifier(trial) -> str:
	"""The identifier stored on a manual link: the trial's registry id."""
	identifiers = trial.identifiers or {}
	for key in PRIMARY_IDENTIFIER_KEYS:
		if identifiers.get(key):
			return str(identifiers[key])[:100]
	for value in identifiers.values():
		if value:
			return str(value)[:100]
	return f"trial:{trial.trial_id}"


def trial_label(trial) -> str:
	"""How a trial reads in the admin picker and the link list: ``Trials.__str__`` is
	only the database id, which says nothing to an editor.

	``"<registry id> · <acronym> · <title, truncated>"``, leaving out what is empty.
	"""
	title = (trial.title or "").strip()
	if len(title) > 90:
		title = title[:89].rstrip() + "…"
	acronym = (trial.acronym or "").strip()
	return " · ".join(part for part in (primary_identifier(trial), acronym, title) if part)


def visible_link(article, trial):
	"""The visible (not suppressed) link between the pair, or None. A manual link comes
	first: it carries the editor's attribution."""
	return (
		ArticleTrialReference.objects.filter(article=article, trial=trial, suppressed=False)
		.order_by(
			Case(When(source=ArticleTrialReference.SOURCE_MANUAL, then=0), default=1),
			"id",
		)
		.first()
	)


def link_trial(article, trial, user):
	"""Link ``trial`` to ``article`` by hand. Returns ``(reference, created)``.

	Idempotent: a pair that already has a visible link returns that link and creates
	nothing. A hidden auto row does not count, so re-adding a hidden pair creates a manual
	row next to it.
	"""
	with transaction.atomic():
		existing = visible_link(article, trial)
		if existing is not None:
			return existing, False
		# get_or_create on the unique (article, trial, identifier_type) key,
		# so two identical requests at once both answer instead of one 500ing.
		return ArticleTrialReference.objects.get_or_create(
			article=article,
			trial=trial,
			identifier_type="manual",
			defaults={
				"identifier_value": primary_identifier(trial),
				"source": ArticleTrialReference.SOURCE_MANUAL,
				"created_by": user,
			},
		)


def remove_link(link):
	"""Remove one link: delete a manual row, hide an auto one (``suppressed=True``)."""
	if link.source == ArticleTrialReference.SOURCE_MANUAL:
		link.delete()
	else:
		link.suppressed = True
		link.save()


def unlink_trial(article, trial):
	"""Remove the visible links between ``article`` and ``trial``. A manual link is
	deleted; an auto-detected one is hidden. Returns the source of each link removed
	(empty when the pair was not linked)."""
	removed = []
	with transaction.atomic():
		links = list(ArticleTrialReference.objects.filter(article=article, trial=trial, suppressed=False))
		for link in links:
			remove_link(link)
			removed.append(link.source)
	return removed
