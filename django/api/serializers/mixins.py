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
- ``ml_predictions``:  ``prediction.subject_id`` is a direct FK column.
  Visible subject IDs are resolved once per request and cached on
  ``request._org_scoped_mixin_subject_ids`` so the query runs at most once
  per request regardless of list length.  Iterating ``.all()`` in Python
  then respects any ``prefetch_related('ml_predictions_detail')`` cache.
"""


def _request_visible_team_ids(request, visible_org_ids: set) -> set:
	"""Return all team IDs belonging to visible orgs, cached once per request.

	Caching avoids issuing a team-lookup query for every object in a list
	endpoint response.
	"""
	cache_attr = '_org_scoped_mixin_team_ids'
	if not hasattr(request, cache_attr):
		from gregory.models import Team
		setattr(
			request,
			cache_attr,
			set(Team.objects.filter(organization_id__in=visible_org_ids).values_list('id', flat=True)),
		)
	return getattr(request, cache_attr)


def _request_visible_subject_ids(request, visible_org_ids: set) -> set:
	"""Return all subject IDs belonging to visible orgs, cached once per request.

	Caching avoids issuing a subject-lookup query for every object in a list
	endpoint response (used when filtering ml_predictions in Python).
	"""
	cache_attr = '_org_scoped_mixin_subject_ids'
	if not hasattr(request, cache_attr):
		from gregory.models import Subject
		vt = _request_visible_team_ids(request, visible_org_ids)
		setattr(
			request,
			cache_attr,
			set(Subject.objects.filter(team_id__in=vt).values_list('id', flat=True)),
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
	"""

	def to_representation(self, instance):
		ret = super().to_representation(instance)

		request = self.context.get('request')
		if request is None or not hasattr(request, 'visible_org_ids'):
			return ret

		visible = request.visible_org_ids

		# --- teams: iterate Python, uses prefetch_related cache ---
		if 'teams' in ret and hasattr(instance, 'teams'):
			visible_team_ids = {t.id for t in instance.teams.all() if t.organization_id in visible}
			ret['teams'] = [t for t in ret['teams'] if t.get('id') in visible_team_ids]

		# --- subjects: team_id is a direct FK attribute; team IDs cached per request ---
		if 'subjects' in ret and hasattr(instance, 'subjects'):
			vt = _request_visible_team_ids(request, visible)
			visible_subject_ids = {s.id for s in instance.subjects.all() if s.team_id in vt}
			ret['subjects'] = [s for s in ret['subjects'] if s.get('id') in visible_subject_ids]

		# --- team_categories: same approach as subjects ---
		if 'team_categories' in ret and hasattr(instance, 'team_categories'):
			vt = _request_visible_team_ids(request, visible)
			visible_cat_ids = {c.id for c in instance.team_categories.all() if c.team_id in vt}
			ret['team_categories'] = [c for c in ret['team_categories'] if c.get('id') in visible_cat_ids]

		# --- ml_predictions: iterate Python, uses prefetch_related cache ---
		if 'ml_predictions' in ret and hasattr(instance, 'ml_predictions_detail'):
			vs = _request_visible_subject_ids(request, visible)
			visible_pred_ids = {p.id for p in instance.ml_predictions_detail.all() if p.subject_id in vs}
			ret['ml_predictions'] = [p for p in ret['ml_predictions'] if p.get('id') in visible_pred_ids]

		return ret
