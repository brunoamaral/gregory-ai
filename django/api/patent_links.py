"""
api/patent_links.py

The one place that decides which patent-trial links a caller may see. Used by
``/patents/`` (``trial_links``, ``trial_links_count``, ``has_trial_link``),
``/trials/`` (``patent_links``, ``has_patent_link``) and the stats, so the
two directions cannot drift apart.

A link is visible when ALL of these hold:

  - it is not suppressed (an editor dismissed it);
  - its trial carries a subject in ``request.visible_subject_ids``;
  - its patent carries a subject in ``request.visible_patent_subject_ids``,
    which only counts sites with ``patents_enabled`` on;
  - ``basis == "category"``, unless the caller passed
    ``include_subject_links=true``. Subject-basis links are 82% of all links
    and are noisy for large sponsors, so they are opt-in.

The trial and patent checks are not redundant with the viewset's own scoping.
``/trials/`` selects a trial on the strength of one of its subjects, which says
nothing about whether a patent hanging off it is visible too, and the other
way round. A trial on a site without the patents switch must show no patent
links at all. Same shape as ``visible_article_references_queryset`` (PR #863
review finding 2): filtered in the queryset, with Exists(), so a prefetch of
it costs no query per row and a join cannot fan a link out once per subject.

Either attribute being absent on a request means "no middleware / no
scoping", the same fallback as ``SubjectVisibilityMixin``. No request at all
(a serializer used outside a view: an export, a digest, a command) is not
that fallback: it fails closed and returns no links, since nothing says who
is asking.
"""

from django.db.models import Exists, OuterRef

from gregory.models import PatentTrialLink, Patents, Trials


def flag_requested(request, name) -> bool:
	"""True when ``?<name>=`` is ``true`` or ``1`` (any case).

	The same values django-filter's ``BooleanWidget`` reads, so a parameter
	that is also declared as a ``BooleanFilter`` (for validation and the
	schema) means the same thing wherever it is read by hand: ``yes`` is not
	true in either place.
	"""
	if request is None:
		return False
	return request.GET.get(name, "").strip().lower() in ("true", "1")


def include_subject_links_requested(request) -> bool:
	"""True for ``?include_subject_links=true`` (or ``1``)."""
	return flag_requested(request, "include_subject_links")


def visible_patent_trial_links_queryset(request, include_subject_links=None):
	"""``PatentTrialLink`` rows the caller may see; see the module docstring.

	``include_subject_links=None`` reads the request's own query parameter.
	"""
	if request is None:
		return PatentTrialLink.objects.none()
	if include_subject_links is None:
		include_subject_links = include_subject_links_requested(request)

	qs = PatentTrialLink.objects.filter(suppressed=False)
	if not include_subject_links:
		qs = qs.filter(basis=PatentTrialLink.BASIS_CATEGORY)

	trial_subject_ids = getattr(request, "visible_subject_ids", None)
	if trial_subject_ids is not None:
		qs = qs.filter(
			Exists(
				Trials.objects.filter(
					pk=OuterRef("trial_id"), subjects__id__in=trial_subject_ids
				)
			)
		)

	patent_subject_ids = getattr(request, "visible_patent_subject_ids", None)
	if patent_subject_ids is not None:
		qs = qs.filter(
			Exists(
				Patents.objects.filter(
					pk=OuterRef("patent_id"), subjects__id__in=patent_subject_ids
				)
			)
		)
	return qs
