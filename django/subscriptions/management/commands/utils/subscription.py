# utils/subscription.py

from datetime import timedelta
from django.db.models import Exists, OuterRef, Q, F
from django.db.models.functions import Coalesce, TruncDate
from django.utils.timezone import now
from gregory.models import Articles, ArticleSubjectRelevance, Subject, Trials


def get_trials_for_list(lst, days=30):
	"""
	Returns trials discovered in the last `days` days for the given list.

	Also applies ``lst.trial_max_age_days`` against the trial's own
	registration/publication date (not discovery_date, which only records
	when GregoryAI first saw the row): a bulk import can stamp thousands of
	historical trials with a fresh discovery_date, and this guards against
	that flooding a newsletter. Trials with no usable date are kept — the
	per-email trial_limit bounds them regardless. Skipped entirely when
	trial_max_age_days is NULL.
	"""
	qs = Trials.objects.filter(
		subjects__in=lst.subjects.all(),
		discovery_date__gte=now() - timedelta(days=days),
	)

	max_age_days = getattr(lst, "trial_max_age_days", None)
	if max_age_days:
		cutoff = now().date() - timedelta(days=max_age_days)
		qs = qs.annotate(
			effective_date=Coalesce(F("date_registration"), TruncDate("published_date"))
		).filter(Q(effective_date__gte=cutoff) | Q(effective_date__isnull=True))

	return qs.distinct()


def apply_article_max_age_filter(qs, lst):
	"""
	Exclude articles whose own ``published_date`` is older than
	``lst.article_max_age_days``.

	Mirrors the trial counterpart of this guard
	(``trial_max_age_days`` in ``get_trials_for_list``): ``discovery_date``
	only records when GregoryAI first saw the row, so a bulk import can
	stamp thousands of historical articles with a fresh discovery_date and
	flood a digest. Articles with no ``published_date`` are always kept —
	the per-email ``article_limit`` bounds them regardless. No-op when
	``article_max_age_days`` is NULL.
	"""
	max_age_days = getattr(lst, "article_max_age_days", None)
	if not max_age_days:
		return qs
	cutoff = now() - timedelta(days=max_age_days)
	return qs.filter(Q(published_date__gte=cutoff) | Q(published_date__isnull=True))


def get_articles_for_list(lst, days=30):
	"""Returns articles discovered in the last `days` days for the given list
	that are missing at least one human review across the list's subjects."""
	list_subjects = lst.subjects.all()

	# Sub-subquery: has this (article, subject) pair been reviewed?
	has_review = Exists(
		ArticleSubjectRelevance.objects.filter(
			article_id=OuterRef(OuterRef("pk")),
			subject_id=OuterRef("pk"),
			is_relevant__isnull=False,
		)
	)

	# True when at least one list-subject the article belongs to has no review
	has_unreviewed_subject = Exists(
		Subject.objects.filter(
			pk__in=list_subjects,
			articles__pk=OuterRef("pk"),
		)
		.alias(reviewed=has_review)
		.filter(reviewed=False)
	)

	qs = (
		Articles.objects.filter(
			subjects__in=list_subjects,
			discovery_date__gte=now() - timedelta(days=days),
		)
		.alias(has_unreviewed=has_unreviewed_subject)
		.filter(has_unreviewed=True)
	)
	return apply_article_max_age_filter(qs, lst).distinct()


def get_latest_research_by_category(lst, days=30):
	"""
	Returns latest articles for each team category in the latest_research_categories,
	grouped by team category. Uses the team_categories M2M relationship populated
	by rebuild_categories, so only articles whose titles/summaries matched the
	category's terms are included.

	Args:
	    lst: Lists object
	    days: Number of days to look back (default: 30)

	Returns:
	    dict: Dictionary with team categories as keys and lists of articles as values
	         Each category will have a maximum of 20 articles
	"""
	result = {}

	# Check if the list has any latest research categories
	if not lst.latest_research_categories.exists():
		return result

	# Get articles for each team category via the team_categories M2M
	for category in lst.latest_research_categories.all():
		latest_articles = (
			apply_article_max_age_filter(
				category.articles.filter(
					discovery_date__gte=now() - timedelta(days=days)
				),
				lst,
			)
			.order_by("-discovery_date")
			.distinct()[:20]
		)

		if latest_articles.exists():
			result[category] = latest_articles

	return result
