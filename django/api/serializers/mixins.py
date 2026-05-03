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
"""


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

		# --- teams ---
		if 'teams' in ret and hasattr(instance, 'teams'):
			visible_team_ids = set(
				instance.teams.filter(organization_id__in=visible)
				.values_list('id', flat=True)
			)
			ret['teams'] = [t for t in ret['teams'] if t.get('id') in visible_team_ids]

		# --- subjects ---
		if 'subjects' in ret and hasattr(instance, 'subjects'):
			visible_subject_ids = set(
				instance.subjects.filter(team__organization_id__in=visible)
				.values_list('id', flat=True)
			)
			ret['subjects'] = [s for s in ret['subjects'] if s.get('id') in visible_subject_ids]

		# --- team_categories ---
		if 'team_categories' in ret and hasattr(instance, 'team_categories'):
			visible_cat_ids = set(
				instance.team_categories.filter(team__organization_id__in=visible)
				.values_list('id', flat=True)
			)
			ret['team_categories'] = [c for c in ret['team_categories'] if c.get('id') in visible_cat_ids]

		# --- ml_predictions (source='ml_predictions_detail') ---
		if 'ml_predictions' in ret and hasattr(instance, 'ml_predictions_detail'):
			visible_pred_ids = set(
				instance.ml_predictions_detail.filter(
					subject__team__organization_id__in=visible
				).values_list('id', flat=True)
			)
			ret['ml_predictions'] = [p for p in ret['ml_predictions'] if p.get('id') in visible_pred_ids]

		return ret
