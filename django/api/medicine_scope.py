"""
Who may see which EMA medicine in the JSON API (``/medicines/``, ``/medicines/events/``
and ``medicines_count`` on ``/categories/``).

A medicine is global reference data with no subject of its own. It is visible by one
of two routes, both counted against ``request.visible_medicine_subject_ids``
(``gregory.visibility``), which only counts sites with ``CustomSetting.medicines_enabled``
on. With the switch off the set is empty, so every list is empty and every detail is a
404.

- a NON-SUPPRESSED link to a category that has a subject in the scope (a medicine an
  editor tracks), or
- a NON-SUPPRESSED ``MedicineSubjectLink`` to a subject in the scope (a medicine EMA
  approved for one of the subject's conditions, with or without a category).

The two suppressions are independent: a suppressed subject link alone hides nothing that
a live category link still shows, and a suppressed category link alone hides nothing that
a live subject link still shows. A medicine is hidden only when neither route is live.

Everything here is an ``Exists()``, never a join plus ``distinct()``: a medicine reached
by several links must not repeat, and the outer query must stay cheap to count, order and
slice (the 946 ms DISTINCT count in the API performance diagnosis).
"""

from django.db.models import Exists, OuterRef

from gregory.models import (
	MedicineCategoryAssignment,
	MedicineSubjectLink,
	Regulator,
	TeamCategory,
)

#: Shown on every medicine and event row, and in the API docs. EMA's terms for reusing
#: its data ask for the source to be named wherever the data is shown.
EMA_SOURCE = "European Medicines Agency"

#: The request attribute ``VisibleOrgMiddleware`` fills in (a lazy set of subject ids).
VISIBLE_ATTR = "visible_medicine_subject_ids"

_CATEGORY_SUBJECTS = TeamCategory.subjects.through


def public_medicines(queryset, prefix=""):
	"""Keep only the medicines the API and the feeds may show: EMA's.

	TEMPORARY GATE, PR 3 removes it. The matcher already links FDA medicines to categories,
	but every read path still labels a row "European Medicines Agency" and has no FDA
	fields, so an FDA medicine, its events and its links stay out of the API, the feeds and
	the MCP tools (which read the API) until they can describe it. The admin is not gated.

	``prefix`` is the lookup path from ``queryset``'s rows to the medicine: ``""`` for
	``Medicine`` itself, ``"medicine__"`` for a link or an event.
	"""
	return queryset.filter(**{prefix + "regulator": Regulator.EMA})


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
	links = public_medicines(
		MedicineCategoryAssignment.objects.filter(suppressed=False), "medicine__"
	)
	ids = visible_subject_ids_of(request)
	if ids is None:
		return links
	if not ids:
		return links.none()
	return links.filter(category_has_subject(ids))


def visible_subject_links_queryset(request):
	"""Non-suppressed subject links the caller may see: the subject is in the scope.

	Feeds the ``subjects`` on a medicine row and the second visibility route. Same
	``None`` / empty-scope contract as ``visible_links_queryset``.
	"""
	links = public_medicines(MedicineSubjectLink.objects.filter(suppressed=False), "medicine__")
	ids = visible_subject_ids_of(request)
	if ids is None:
		return links
	if not ids:
		return links.none()
	return links.filter(subject_id__in=ids)


def tracked_condition(request, medicine_ref):
	"""``Exists()``: the medicine has a live link to a category the caller can see.

	What ``tracked`` filters on. It is exactly "the row's ``categories`` is not empty",
	so another organisation's categories never decide whether a medicine looks tracked
	to this caller.
	"""
	return Exists(visible_links_queryset(request).filter(medicine=OuterRef(medicine_ref)))


def scope_to_visible(queryset, request, medicine_ref):
	"""Keep rows whose medicine has a visible category link or a visible subject link.

	``medicine_ref`` is the outer column holding the medicine's id: ``"pk"`` on
	``Medicine``, ``"medicine_id"`` on ``MedicineEvent``.
	"""
	# The gate comes first: with no middleware (ids is None) nothing else scopes the rows.
	queryset = public_medicines(queryset, "" if medicine_ref == "pk" else "medicine__")
	ids = visible_subject_ids_of(request)
	if ids is None:
		return queryset
	if not ids:
		return queryset.none()
	return queryset.filter(
		tracked_condition(request, medicine_ref)
		| Exists(
			visible_subject_links_queryset(request).filter(medicine=OuterRef(medicine_ref))
		)
	)
