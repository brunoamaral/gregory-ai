import logging

from simple_history.signals import (
	post_create_historical_record,
	pre_create_historical_record,
)
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from organizations.models import Organization

MAX_AUTHOR_HISTORY = 5

logger = logging.getLogger(__name__)


@receiver(pre_create_historical_record)
def stamp_api_access_scheme_on_history(sender, history_instance, **kwargs):
	"""Populate api_access_scheme and api_access_scheme_label on any historical
	model that carries ApiKeyHistoryMixin fields.

	Reads request.api_access_scheme set by ApiKeyMiddleware via simple-history's
	HistoryRequestMiddleware context. No-ops silently for admin/shell
	saves where the request or the field is absent.
	"""
	if not hasattr(history_instance, "api_access_scheme_id"):
		return
	try:
		from simple_history.models import HistoricalRecords

		request = getattr(HistoricalRecords.context, "request", None)
		if request is None:
			return
		# api_access_scheme is a SimpleLazyObject; resolving it here is safe
		# because ApiKeyMiddleware has already run.
		scheme = getattr(request, "api_access_scheme", None)
		if scheme is None:
			return
		history_instance.api_access_scheme = scheme
		history_instance.api_access_scheme_label = (scheme.client_name or "")[:200]
	except Exception:  # noqa: S110
		# Never let a signal failure break a save.
		pass


@receiver(pre_create_historical_record)
def stamp_editor_on_history(sender, history_instance, **kwargs):
	"""Populate editor_user, editor_label and via on any historical model that
	carries EditorHistoryMixin fields.

	The answer comes from gregory.editor_history.current_editor(): an explicit
	``editing_as()`` scope, else the request simple-history's middleware
	stashed. A change made with neither (a command, the shell) is left blank.
	"""
	if not hasattr(history_instance, "editor_user_id"):
		return
	try:
		from simple_history.models import HistoricalRecords

		from gregory.editor_history import current_editor

		editor = current_editor(getattr(HistoricalRecords.context, "request", None))
		if editor is None:
			return
		history_instance.editor_user = editor.user
		history_instance.editor_label = editor.label
		history_instance.via = editor.via
	except Exception:
		# Never let a signal failure break a save, but never lose it silently
		# either: a history row without its editor is a gap in the audit trail.
		logger.exception(
			"Failed to stamp the editor on a %s history row; saved without attribution.",
			type(history_instance).__name__,
		)


@receiver(post_create_historical_record)
def trim_author_history(sender, instance, history_instance, **kwargs):
	from gregory.models import Authors

	if not isinstance(instance, Authors):
		return
	keep_ids = list(
		instance.history.order_by("-history_date").values_list("pk", flat=True)[
			:MAX_AUTHOR_HISTORY
		]
	)
	instance.history.exclude(pk__in=keep_ids).delete()


@receiver(post_save, sender=Organization)
def create_organization_api_settings(sender, instance, created, **kwargs):
	"""Create an OrganizationApiSettings row for every newly created Organisation."""
	if created:
		from gregory.models import OrganizationApiSettings

		OrganizationApiSettings.objects.get_or_create(organization=instance)


def _recompute_article_ml_score(article_id):
	"""Recompute and persist ml_score for the given article.

	Thin wrapper around the scoped recompute in gregory.relevance so the
	per-article signal path and the bulk pipeline path can't drift apart.
	"""
	from gregory.relevance import recompute_article_ml_scores

	recompute_article_ml_scores(article_ids=[article_id])


@receiver(post_save, sender="gregory.MLPredictions")
def update_article_ml_score_on_save(sender, instance, **kwargs):
	"""Recompute ml_score when a prediction is created or updated."""
	if instance.article_id is not None:
		_recompute_article_ml_score(instance.article_id)
		from gregory.relevance import recompute_article_relevance

		recompute_article_relevance(article_ids=[instance.article_id])


@receiver(post_delete, sender="gregory.MLPredictions")
def update_article_ml_score_on_delete(sender, instance, **kwargs):
	"""Recompute ml_score when a prediction is deleted."""
	if instance.article_id is not None:
		_recompute_article_ml_score(instance.article_id)
		from gregory.relevance import recompute_article_relevance

		recompute_article_relevance(article_ids=[instance.article_id])


@receiver(post_save, sender="gregory.ArticleSubjectRelevance")
@receiver(post_delete, sender="gregory.ArticleSubjectRelevance")
def update_article_relevance_flag(sender, instance, **kwargs):
	"""Recompute the denormalized relevant flag when manual relevance changes."""
	from gregory.relevance import recompute_article_relevance

	recompute_article_relevance(article_ids=[instance.article_id])
