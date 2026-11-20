"""Visibility rules and display names for research labs in the API.

One module, so ``/labs/`` and anything that later shows a lab (the author include,
MCP) apply the same rules. ``scope`` is the caller's set of visible subject IDs
(``request.visible_subject_ids``), or ``None`` when no middleware ran, which skips
only the subject scoping: hidden links never show in either case. An empty set is
not ``None``: it means the caller can see nothing.
"""

from django.db.models import CharField, Exists, Func, OuterRef, Prefetch, Q, Value
from django.db.models.functions import Cast, Coalesce, Concat, NullIf, Trim

from api.editorial import lab_requested
from gregory.models import Lab, LabCategory, LabMembership, LabSubject, TeamCategory
from gregory.utils.author_display import author_name

_CATEGORY_SUBJECTS = TeamCategory.subjects.through


def request_scope(request):
	"""The caller's visible subject IDs as a set, or None when the request carries no scope."""
	ids = getattr(request, "visible_subject_ids", None)
	return None if ids is None else set(ids)


def visible_subject_links(scope):
	"""LabSubject rows the caller may see: not hidden, and the subject is in scope."""
	links = LabSubject.objects.filter(is_hidden=False)
	if scope is not None:
		links = links.filter(subject_id__in=scope)
	return links


def category_subjects_visible_q(scope, category_ref="pk"):
	"""Q (on TeamCategory, or on a model pointing at it through ``category_ref``) that is
	true when the category has at least one subject and every one of them is in scope.

	Deliberately stricter than ``/categories/``: the stored link counts include the
	articles of every subject of the category, so showing a category that also covers a
	subject the caller cannot see would leak counts from another site's articles."""
	rows = _CATEGORY_SUBJECTS.objects.filter(teamcategory_id=OuterRef(category_ref))
	visible = Exists(rows)
	if scope is not None:
		visible = visible & ~Exists(rows.exclude(subject_id__in=scope))
	return Q(visible)


def visible_category_links(scope):
	"""LabCategory rows the caller may see: not hidden, and all the category's subjects
	are in scope (a category with no subjects is never visible)."""
	return LabCategory.objects.filter(is_hidden=False).filter(
		category_subjects_visible_q(scope, "category_id")
	)


def visible_labs_q(scope):
	"""Q for Lab: it has a visible subject link or a visible category link.
	Exists() subqueries only, so it never joins and never needs DISTINCT."""
	return Q(Exists(visible_subject_links(scope).filter(lab_id=OuterRef("pk")))) | Q(
		Exists(visible_category_links(scope).filter(lab_id=OuterRef("pk")))
	)


def head_name_expr(prefix="head__"):
	"""SQL twin of ``author_name``: full name, else given plus family; NULL when empty."""
	return NullIf(
		Trim(
			Coalesce(
				NullIf(Trim(f"{prefix}full_name"), Value("")),
				Concat(f"{prefix}given_name", Value(" "), f"{prefix}family_name"),
			)
		),
		Value(""),
	)


def lab_display_name_expr():
	"""SQL twin of ``lab_display_name``, for annotating a Lab queryset (so name
	ordering and search run in the database)."""
	# Concat treats NULL as empty; the SQL || operator keeps it NULL, which is what lets
	# the Coalesce fall through when the lab has no head name.
	head = head_name_expr()
	head_lab = Func(
		head, Value(" lab"), arg_joiner=" || ", template="(%(expressions)s)", output_field=CharField()
	)
	return Coalesce(
		NullIf(Trim("name"), Value("")),
		head_lab,
		Concat(Value("Lab "), Cast("pk", CharField())),
		output_field=CharField(),
	)


def lab_display_name(lab):
	"""The lab's name, else its head's name followed by 'lab', else 'Lab <id>'. Never blank."""
	if lab.name and lab.name.strip():
		return lab.name.strip()
	if lab.head_id:
		# Trim before falling back, as head_name_expr does in SQL.
		head = lab.head
		name = (head.full_name or "").strip() or f"{head.given_name} {head.family_name}".strip()
		if name:
			return f"{name} lab"
	return f"Lab {lab.pk}"


def author_lab_summary(membership, lab_visible):
	"""The ``lab`` object of ``/authors/?include=lab``, or None.

	``membership`` is the author's LabMembership (or None), with its lab and the lab's
	head loaded; ``lab_visible`` says whether the caller can see that lab. A collaborator,
	an author pinned to no lab and an author whose lab is invisible all give None."""
	if membership is None or membership.lab_id is None or not lab_visible:
		return None
	if membership.role not in ("head", "member"):
		return None
	lab = membership.lab
	head = lab.head
	return {
		"id": lab.pk,
		"name": lab_display_name(lab),
		"role": membership.role,
		"head": None if head is None else {"author_id": head.pk, "full_name": author_name(head)},
	}


def with_author_lab(queryset, request):
	"""Add what ``/authors/?include=lab`` needs to an Authors queryset: the membership,
	lab and head (one extra query), and whether the caller can see the lab (the same
	``visible_labs_q`` as ``/labs/``). Nothing is added unless the caller asked."""
	if not lab_requested(request):
		return queryset
	visible = Exists(
		Lab.objects.filter(memberships__author_id=OuterRef("pk")).filter(
			visible_labs_q(request_scope(request))
		)
	)
	# The membership is prefetched, not joined, so the paginator's COUNT(*) stays join-free.
	return queryset.annotate(lab_visible=visible).prefetch_related(
		Prefetch(
			"lab_membership",
			queryset=LabMembership.objects.select_related("lab__head"),
		)
	)
