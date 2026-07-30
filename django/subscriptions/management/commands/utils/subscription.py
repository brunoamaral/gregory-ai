# utils/subscription.py

from datetime import timedelta
from django.db.models import Exists, OuterRef, Q, F, prefetch_related_objects
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


def filter_articles_excluding_all_irrelevant(base_qs, digest_list):
	"""
	Return a list of PKs from base_qs, excluding articles that are manually
	tagged as not-relevant for ALL of their subjects that appear in digest_list.
	Articles with no relevance records are always included.

	Uses prefetch_related to load subjects and relevance records in bulk,
	avoiding N+1 / N*M queries.
	"""
	list_subject_ids = set(digest_list.subjects.values_list("id", flat=True))
	# Prefetch both relations so the inner loop hits no extra queries.
	articles = base_qs.prefetch_related("subjects", "article_subject_relevances")
	filtered_pks = []
	for article in articles:
		# In-memory filter: only subjects shared with this digest list.
		article_list_subjects = [
			s for s in article.subjects.all() if s.pk in list_subject_ids
		]
		explicit_irrelevant_count = 0
		total_relevance_records = 0
		for subject in article_list_subjects:
			# Use the prefetch cache — no extra query per subject.
			relevance = next(
				(
					r
					for r in article.article_subject_relevances.all()
					if r.subject_id == subject.pk
				),
				None,
			)
			if relevance is not None:
				total_relevance_records += 1
				if relevance.is_relevant is False:
					explicit_irrelevant_count += 1
		if (
			total_relevance_records > 0
			and explicit_irrelevant_count == total_relevance_records
		):
			continue
		filtered_pks.append(article.pk)
	return filtered_pks


def select_digest_articles(
	digest_list, days_to_look_back, all_articles=False, threshold=None
):
	"""
	Returns ``(articles, article_priority_scores)`` — the list-level candidate
	article set for a weekly digest (or a preview of one), mirroring
	``send_weekly_summary``'s three selection modes:

	- ``all_articles=True``: every subject-matched article in the window
	  (excluding articles manually tagged not-relevant for all their subjects)
	- ``digest_list.article_sort_order == "date"``: same as above
	- otherwise ("relevancy"): manually reviewed OR ML-consensus-relevant,
	  each scored by priority (manual review = 1000, +100 per ML algorithm
	  that agrees at or above ``threshold``)

	``article_priority_scores`` is a ``{pk: score}`` dict for relevancy mode,
	or ``None`` for the other two modes (which sort by date, not score).

	Used by both ``send_weekly_summary`` and the staff email preview, so the
	two can never drift on which articles a list would actually select.
	``threshold`` defaults to ``digest_list.ml_threshold``.
	"""
	if threshold is None:
		threshold = digest_list.ml_threshold

	if all_articles or digest_list.article_sort_order == "date":
		base_articles = (
			apply_article_max_age_filter(
				Articles.objects.filter(
					subjects__in=digest_list.subjects.all(),
					discovery_date__gte=now() - timedelta(days=days_to_look_back),
				),
				digest_list,
			)
			.order_by("-discovery_date")
			.distinct()
		)
		filtered_pks = filter_articles_excluding_all_irrelevant(
			base_articles, digest_list
		)
		articles = (
			Articles.objects.filter(pk__in=filtered_pks)
			.order_by("-discovery_date")
			.prefetch_related("authors")
		)
		return articles, None

	# Relevancy mode (default): manually reviewed OR ML-relevant based on
	# consensus settings.
	list_subjects = digest_list.subjects.all()
	base_subject_articles = (
		apply_article_max_age_filter(
			Articles.objects.filter(
				subjects__in=list_subjects,
				discovery_date__gte=now() - timedelta(days=days_to_look_back),
			),
			digest_list,
		)
		.order_by("-discovery_date")
		.distinct()
	)
	filtered_article_ids = filter_articles_excluding_all_irrelevant(
		base_subject_articles, digest_list
	)
	subject_articles = Articles.objects.filter(pk__in=filtered_article_ids)

	# Manually reviewed articles (tagged as relevant for at least one subject).
	manual_reviewed = apply_article_max_age_filter(
		Articles.objects.filter(
			subjects__in=list_subjects,
			article_subject_relevances__subject__in=list_subjects,
			article_subject_relevances__is_relevant=True,
			discovery_date__gte=now() - timedelta(days=days_to_look_back),
		),
		digest_list,
	).distinct()

	# ML-relevant based on consensus logic. Scoped to this list's own
	# subjects so an article tagged with an unrelated team's auto_predict
	# subject can't ride in on that subject's ML prediction.
	ml_relevant_articles = []
	for article in subject_articles:
		if article.is_ml_relevant_any_subject(
			threshold=threshold, subjects=list_subjects
		):
			ml_relevant_articles.append(article.article_id)

	article_ids = (
		list(manual_reviewed.values_list("pk", flat=True)) + ml_relevant_articles
	)
	articles = (
		Articles.objects.filter(pk__in=article_ids)
		.distinct()
		.prefetch_related("authors")
	)

	# Priority scores depend only on the article and this list's threshold —
	# never on the subscriber — so they're computed once per list here and
	# reused for every subscriber's truncation ranking (audit P3, task 5).
	manual_relevant_ids_all = set(
		Articles.objects.filter(
			pk__in=articles.values_list("pk", flat=True),
			article_subject_relevances__is_relevant=True,
		).values_list("pk", flat=True)
	)
	article_priority_scores = {}
	for article in articles.prefetch_related(None).prefetch_related(
		"subjects", "ml_predictions_detail"
	):
		priority_score = 0
		if article.pk in manual_relevant_ids_all:
			priority_score += 1000
		for subject in article.subjects.all():
			if not subject.auto_predict:
				continue
			relevant_algorithms = {
				p.algorithm
				for p in article.ml_predictions_detail.all()
				if p.subject_id == subject.pk
				and p.predicted_relevant
				and p.probability_score is not None
				and p.probability_score >= threshold
			}
			priority_score += len(relevant_algorithms) * 100
		article_priority_scores[article.pk] = priority_score

	return articles, article_priority_scores


def rank_and_limit_articles(
	candidate_articles, article_limit, sort_order, all_articles, article_priority_scores
):
	"""
	Truncate ``candidate_articles`` (a queryset or list) to ``article_limit``
	Article instances, ranked the same way a real send ranks them:

	- date-ish modes (``all_articles`` or ``sort_order == "date"``): newest
	  first
	- relevancy mode: priority score (manual review + ML consensus count,
	  from ``article_priority_scores``), then date

	Always returns a materialized list with ``authors`` prefetched. Used by
	both ``send_weekly_summary`` (to shrink a subscriber's unsent-article set)
	and the staff email preview, so article_limit is honoured identically in
	both places.
	"""
	if all_articles or sort_order == "date":
		limited_articles = candidate_articles.order_by("-discovery_date")[
			:article_limit
		]
		limited_articles = list(limited_articles)
		prefetch_related_objects(limited_articles, "authors")
		return limited_articles

	ranked_pks = sorted(
		candidate_articles.values_list("pk", "discovery_date"),
		key=lambda row: (
			-article_priority_scores.get(row[0], 0),
			-row[1].timestamp(),
		),
	)
	limited_pks = [pk for pk, _ in ranked_pks[:article_limit]]
	articles_by_pk = {a.pk: a for a in Articles.objects.filter(pk__in=limited_pks)}
	prefetch_related_objects(list(articles_by_pk.values()), "authors")
	return [articles_by_pk[pk] for pk in limited_pks]


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
