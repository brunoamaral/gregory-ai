from simple_history.signals import (
	post_create_historical_record,
	pre_create_historical_record,
)
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from organizations.models import Organization

MAX_AUTHOR_HISTORY = 5


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

	Averages the most recent probability_score per (algorithm, subject) pair.
	Uses .update() to avoid bumping Articles.last_updated.
	distinct(fields) + aggregate() is unsupported by the Django ORM, so scores
	are materialised and averaged in Python — the list is tiny (≤ algorithms × subjects).
	"""
	from gregory.models import Articles, MLPredictions

	scores = list(
		MLPredictions.objects.filter(
			article_id=article_id,
			probability_score__isnull=False,
		)
		.order_by("algorithm", "subject_id", "-created_date")
		.distinct("algorithm", "subject_id")
		.values_list("probability_score", flat=True)
	)
	score = sum(scores) / len(scores) if scores else None
	Articles.objects.filter(article_id=article_id).update(ml_score=score)


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
