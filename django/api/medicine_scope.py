"""
Who may see which EMA medicine in the JSON API (``/medicines/``, ``/medicines/events/``
and ``medicines_count`` on ``/categories/``).

A medicine is global reference data with no subject of its own. It is visible when it
has a NON-SUPPRESSED link to a category that has a subject in
``request.visible_medicine_subject_ids`` (``gregory.visibility``), which only counts
sites with ``CustomSetting.medicines_enabled`` on. With the switch off the set is
empty, so every list is empty and every detail is a 404.

Everything here is an ``Exists()`` over ``MedicineCategoryAssignment``, never a join plus
``distinct()``: a medicine in several categories must not repeat, and the outer query
must stay cheap to count, order and slice (the 946 ms DISTINCT count in the API
performance diagnosis).
"""

from django.db.models import Exists, OuterRef

from gregory.models import MedicineCategoryAssignment, TeamCategory

#: Shown on every medicine and event row, and in the API docs. EMA's terms for reusing
#: its data ask for the source to be named wherever the data is shown.
EMA_SOURCE = "European Medicines Agency"

#: The request attribute ``VisibleOrgMiddleware`` fills in (a lazy set of subject ids).
VISIBLE_ATTR = "visible_medicine_subject_ids"

_CATEGORY_SUBJECTS = TeamCategory.subjects.through


def category_has_subject(subject_ids, link_ref="teamcategory_id"):
	"""``Exists()``: the link's category has at least one of ``subject_ids``.

	One condition per call, so two of them on the same link are independent: the
	category may satisfy them with different subjects.
	"""
	return Exists(
		_CATEGORY_SUBJECTS.objects.filter(
			teamcategory_id=OuterRef(link_ref), subject_id__in=subject_ids
		)
	)


def visible_subject_ids_of(request):
	"""The caller's medicine scope as a list, or ``None`` when nothing scopes the
	request (no middleware: tests and commands that bypass it, as for the other
	``SubjectVisibilityMixin`` endpoints). An empty list means "sees nothing"."""
	ids = getattr(request, VISIBLE_ATTR, None)
	return None if ids is None else list(ids)


def visible_links_queryset(request):
	"""Non-suppressed links the caller may see: the category has a visible subject.

	The same rule decides whether a medicine is listed at all, which categories a
	medicine shows, and what ``medicines_count`` counts.
	"""
	links = MedicineCategoryAssignment.objects.filter(suppressed=False)
	ids = visible_subject_ids_of(request)
	if ids is None:
		return links
	if not ids:
		return links.none()
	return links.filter(category_has_subject(ids))


def scope_to_visible(queryset, request, medicine_ref):
	"""Keep rows whose medicine has a visible, non-suppressed link.

	``medicine_ref`` is the outer column holding the medicine's id: ``"pk"`` on
	``Medicine``, ``"medicine_id"`` on ``MedicineEvent``.
	"""
	ids = visible_subject_ids_of(request)
	if ids is None:
		return queryset
	if not ids:
		return queryset.none()
	return queryset.filter(
		Exists(visible_links_queryset(request).filter(medicine=OuterRef(medicine_ref)))
	)
