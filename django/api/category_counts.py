"""Category aggregate helpers shared by api.views and api.serializers.

They live here rather than in api.views so CategorySerializer can use them
without importing the views module (which imports the serializers).
"""

from django.db.models import Count, Q

from gregory.models import ArticleCategoryAssignment


def _visible_content_q(content_prefix, visible_subject_ids):
	"""Q object: the content reached via *content_prefix* carries at least
	one subject in ``visible_subject_ids``.

	*content_prefix* is the ORM path to Articles/Trials -- ``""`` when the
	queryset being filtered IS Articles/Trials, or e.g. ``"articles__"``
	when filtering a related model that reaches them through that relation.

	The category-aggregate counterpart of the list rule (PR #863 review
	finding 4): a category shared between a visible and a hidden subject
	must not have its counts inflated, or its authors disclosed, by rows the
	caller cannot otherwise see.

	Content with NO subject is excluded, exactly as
	SubjectVisibilityMixin.get_queryset excludes it -- an Exists() over the
	subjects through table fails for a row with no through rows, which
	api/tests/test_visibility_org_filter_edge_cases.py pins as
	test_subject_less_article_stays_invisible. Counting it here would make a
	category report rows that its own list endpoint will not return, which
	is a worse failure than under-counting: the numbers stop describing the
	data the caller can actually reach.

	Returns ``None`` when ``visible_subject_ids`` is ``None`` (no middleware
	/ no scoping active), so callers can skip filtering entirely -- same
	fallback contract as SubjectVisibilityMixin.
	"""
	if visible_subject_ids is None:
		return None
	return Q(**{f"{content_prefix}subjects__id__in": visible_subject_ids})


def category_authors_counts(category_ids, visible_subject_ids=None):
	"""Distinct authors behind each category's visible articles, as
	``{category_id: count}``, in one grouped query.

	Same count as api.views._category_authors_count_subquery (the form used
	when a client sorts by it), but for an explicit list of ids -- a page of
	results -- so the cost stays bounded by page size instead of running
	once per category (issue #816). Categories with no visible authored
	articles are reported as 0.
	"""
	category_ids = list(category_ids)
	if not category_ids:
		return {}
	qs = ArticleCategoryAssignment.objects.filter(teamcategory_id__in=category_ids)
	visible_q = _visible_content_q("articles__", visible_subject_ids)
	if visible_q is not None:
		qs = qs.filter(visible_q)
	rows = (
		qs.order_by()
		.values("teamcategory_id")
		.annotate(c=Count("articles__authors", distinct=True))
		.values_list("teamcategory_id", "c")
	)
	counts = dict.fromkeys(category_ids, 0)
	counts.update(rows)
	return counts
