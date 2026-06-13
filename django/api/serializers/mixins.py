"""
api/serializers/mixins.py

OrgScopedSerializerMixin — strips nested associations that belong to
organisations outside ``request.visible_org_ids``.

Applied fields (when present on the serialised object):
  - ``teams``           → Team entries whose org is not visible
  - ``subjects``        → Subject entries whose team's org is not visible
  - ``team_categories`` → TeamCategory entries whose team's org is not visible
  - ``ml_predictions``  → MLPredictions entries whose subject's team's org
                           is not visible

The mixin is intentionally a no-op when:
  - There is no ``request`` in the serializer context, OR
  - ``request.visible_org_ids`` has not been set (middleware not active).

This guarantees zero behaviour change until the viewsets are explicitly
opted in (PR 4 onwards).

Query strategy (avoiding N+1 on list endpoints)
------------------------------------------------
- ``teams``:  ``team.organization_id`` is a direct FK column; iterating
  ``.all()`` in Python respects ``prefetch_related('teams')`` with zero
  extra DB queries.
- ``subjects`` / ``team_categories``:  ``subject.team_id`` is a direct FK
  column.  Visible team IDs are resolved once per request and cached on
  ``request._org_scoped_mixin_team_ids`` so the query runs at most once per
  request regardless of list length.
- ``ml_predictions``:  Each serialised item already contains a ``subject``
  key (the FK integer) from ``MLPredictionsSerializer``.  Visible subject IDs
  are resolved once per request and cached on
  ``request._org_scoped_mixin_subject_ids``.  Filtering is done entirely on
  ``ret['ml_predictions']`` — no extra DB query, no dependency on whether
  ``ml_predictions_detail`` was prefetched.

Per-org fields (``_per_org_fields``)
-------------------------------------
Subclasses may declare a list of field names that should only appear in
the response when an organisation context is available.  When no org can
be resolved (anonymous request, no public-org filter), those keys are
removed from the serialised output entirely — not just set to ``null``.

Resolution order (spec §6):
  1. Valid API key on the request → that key's organisation.
  2. ``?team_id=<id>`` query-param on a request where that team's org has
     ``OrganizationApiSettings.make_api_public=True`` → that organisation.
  3. Otherwise → no org, per-org fields omitted.
"""

# Sentinel for "not yet cached" — distinct from None ("no org").
_ORG_CACHE_MISSING = object()
_ORG_CACHE_ATTR = "_per_org_fields_org_cache"


def _resolve_per_org_fields_org(request):
	"""Return the Organisation whose per-org content should be exposed, or None.

	See mixin docstring for the full resolution order.  Returns None when there
	is no organisation context, which tells the caller to omit per-org fields.

	The result is cached on the request object so the team/api_settings DB
	lookup is issued at most once per request, regardless of how many objects
	the serializer processes.
	"""
	if request is None:
		return None

	cached = getattr(request, _ORG_CACHE_ATTR, _ORG_CACHE_MISSING)
	if cached is not _ORG_CACHE_MISSING:
		return cached

	org = None

	# 1. API key path (set by ApiKeyMiddleware as a SimpleLazyObject)
	scheme = getattr(request, "api_access_scheme", None)
	if scheme is not None:
		org = getattr(scheme, "organization", None)

	# 2. Public org via ?team_id filter
	if org is None:
		team_id = request.GET.get("team_id")
		if team_id:
			from gregory.models import Team

			try:
				team = Team.objects.select_related("organization__api_settings").get(
					pk=int(team_id)
				)
			except (ValueError, TypeError, Team.DoesNotExist):
				# Bad/unknown team_id — treat as "no org context".
				team = None
			if team is not None:
				api_settings = getattr(team.organization, "api_settings", None)
				if api_settings and api_settings.make_api_public:
					org = team.organization

	setattr(request, _ORG_CACHE_ATTR, org)
	return org


def _request_visible_team_ids(request, visible_org_ids: set) -> set:
	"""Return all team IDs belonging to visible orgs, cached once per request.

	Caching avoids issuing a team-lookup query for every object in a list
	endpoint response.
	"""
	cache_attr = "_org_scoped_mixin_team_ids"
	if not hasattr(request, cache_attr):
		from gregory.models import Team

		setattr(
			request,
			cache_attr,
			set(
				Team.objects.filter(organization_id__in=visible_org_ids).values_list(
					"id", flat=True
				)
			),
		)
	return getattr(request, cache_attr)


def _request_visible_subject_ids(request, visible_org_ids: set) -> set:
	"""Return all subject IDs belonging to visible orgs, cached once per request.

	Caching avoids issuing a subject-lookup query for every object in a list
	endpoint response (used when filtering ml_predictions in Python).
	"""
	cache_attr = "_org_scoped_mixin_subject_ids"
	if not hasattr(request, cache_attr):
		from gregory.models import Subject

		vt = _request_visible_team_ids(request, visible_org_ids)
		setattr(
			request,
			cache_attr,
			set(Subject.objects.filter(team_id__in=vt).values_list("id", flat=True)),
		)
	return getattr(request, cache_attr)


class OrgScopedSerializerMixin:
	"""
	Mixin for DRF serializers that strips nested associations belonging to
	organisations the caller cannot see.

	Usage::

	    class ArticleSerializer(OrgScopedSerializerMixin,
	                            serializers.HyperlinkedModelSerializer):
	        ...

	Set ``_per_org_fields`` to a list of field names that should be omitted
	entirely when there is no organisation context (spec §6.2).
	"""

	#: Field names to omit from the response when no org context is available.
	_per_org_fields: list = []

	def to_representation(self, instance):
		ret = super().to_representation(instance)

		request = self.context.get("request")

		# ---- Per-org field omission (spec §6.2) ----------------------------
		if self._per_org_fields:
			org = _resolve_per_org_fields_org(request)
			if org is None:
				for field in self._per_org_fields:
					ret.pop(field, None)

		if request is None or not hasattr(request, "visible_org_ids"):
			return ret

		visible = request.visible_org_ids

		# --- teams: iterate Python, uses prefetch_related cache ---
		if "teams" in ret and hasattr(instance, "teams"):
			visible_team_ids = {
				t.id for t in instance.teams.all() if t.organization_id in visible
			}
			ret["teams"] = [t for t in ret["teams"] if t.get("id") in visible_team_ids]

		# --- subjects: team_id is a direct FK attribute; team IDs cached per request ---
		if "subjects" in ret and hasattr(instance, "subjects"):
			vt = _request_visible_team_ids(request, visible)
			visible_subject_ids = {
				s.id for s in instance.subjects.all() if s.team_id in vt
			}
			ret["subjects"] = [
				s for s in ret["subjects"] if s.get("id") in visible_subject_ids
			]

		# --- team_categories: same approach as subjects ---
		if "team_categories" in ret and hasattr(instance, "team_categories"):
			vt = _request_visible_team_ids(request, visible)
			visible_cat_ids = {
				c.id for c in instance.team_categories.all() if c.team_id in vt
			}
			ret["team_categories"] = [
				c for c in ret["team_categories"] if c.get("id") in visible_cat_ids
			]

		# --- ml_predictions: filter directly on already-serialised data ---
		# Each item in ret['ml_predictions'] already has a 'subject' key (the FK
		# integer) from MLPredictionsSerializer.  Filtering here avoids hitting
		# instance.ml_predictions_detail.all() a second time, which would cause
		# an extra per-object query when the relation is not prefetched.
		if "ml_predictions" in ret:
			vs = _request_visible_subject_ids(request, visible)
			ret["ml_predictions"] = [
				p for p in ret["ml_predictions"] if p.get("subject") in vs
			]

		return ret
