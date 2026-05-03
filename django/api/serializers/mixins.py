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
  Visible subject IDs are built in Python from the already-iterated subjects
  manager (uses prefetch cache when ``prefetch_related('subjects')`` is active).
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

		# --- ml_predictions: one-level join using cached visible team IDs ---
		# (subject__team_id__in=vt is simpler than subject__team__organization_id__in=visible;
		# a full zero-query solution requires prefetch_related('ml_predictions_detail__subject')
		# with select_related('team'), which is a viewset concern.)
		if 'ml_predictions' in ret and hasattr(instance, 'ml_predictions_detail'):
			vt = _request_visible_team_ids(request, visible)
			visible_pred_ids = set(
				instance.ml_predictions_detail.filter(
					subject__team_id__in=vt
				).values_list('id', flat=True)
			)
			ret['ml_predictions'] = [p for p in ret['ml_predictions'] if p.get('id') in visible_pred_ids]

		return ret
