# utils/subscription.py

from datetime import timedelta
from django.db.models import Exists, OuterRef
from django.utils.timezone import now
from gregory.models import Articles, ArticleSubjectRelevance, Subject, Trials


def get_trials_for_list(lst):
	"""Returns trials discovered in the last 30 days for the given list."""
	return Trials.objects.filter(
		subjects__in=lst.subjects.all(), discovery_date__gte=now() - timedelta(days=30)
	).distinct()


def get_articles_for_list(lst):
	"""Returns articles discovered in the last 30 days for the given list
	that are missing at least one human review across the list's subjects."""
	list_subjects = lst.subjects.all()

	# Sub-subquery: has this (article, subject) pair been reviewed?
	has_review = ArticleSubjectRelevance.objects.filter(
		article_id=OuterRef(OuterRef('pk')),
		subject_id=OuterRef('pk'),
		is_relevant__isnull=False,
	)

	# Subquery: does this article have at least one list-subject with no review?
	has_unreviewed_subject = Exists(
		Subject.objects.filter(
			pk__in=list_subjects,
			articles__pk=OuterRef('pk'),
		).exclude(Exists(has_review))
	)

	return (
		Articles.objects.filter(
			subjects__in=list_subjects,
			discovery_date__gte=now() - timedelta(days=30),
		)
		.filter(has_unreviewed_subject)
		.distinct()
	)


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
			category.articles.filter(discovery_date__gte=now() - timedelta(days=days))
			.order_by("-discovery_date")
			.distinct()[:20]
		)

		if latest_articles.exists():
			result[category] = latest_articles

	return result
