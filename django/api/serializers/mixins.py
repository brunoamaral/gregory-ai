"""
api/serializers/mixins.py

ScopedSerializerMixin — strips nested associations the caller cannot see.

A row reaching the serializer has already passed the viewset's scope filter,
which means *the row* is visible. That says nothing about the associations
hanging off it: an article carrying two subjects, one of them out of scope,
is visible on the strength of the first and must not disclose the second.
Stripping here is what keeps the row's fields consistent with the rule that
selected the row.

Applied fields (when present on the serialised object):
  - ``subjects``                  → Subject entries outside
                                     ``request.visible_subject_ids``
  - ``ml_predictions``            → MLPredictions entries whose subject is
                                     outside it
  - ``article_subject_relevances``→ ArticleSubjectRelevance entries whose
                                     nested subject is outside it (same
                                     shape/reasoning as ml_predictions)
  - ``team_categories``           → TeamCategory entries whose ``subjects``
                                     M2M does not intersect it
  - ``teams``                     → Team entries that are neither
                                     ``api_listed`` nor owners of a subject in
                                     the caller's scope

Teams are the odd one out, and deliberately so. A Team carries no subject of
its own, so it is gated by exactly the rule ``/teams/`` applies: listed by the
explicit ``Team.api_listed`` flag, OR owning a subject already in the caller's
scope. A team's name therefore appears nested inside an article exactly when it
would appear in the team directory. Using two different answers for "may I see
this team's name" depending on which endpoint asked would be a leak in
whichever direction was laxer, so these two must move together -- if you change
one, change the other.

The mixin is intentionally a no-op when:
  - There is no ``request`` in the serializer context, OR
  - ``request.visible_subject_ids`` has not been set (middleware not active).

Query strategy (avoiding N+1 on list endpoints)
------------------------------------------------
- ``subjects`` / ``ml_predictions``:  no query at all. ``visible_subject_ids``
  IS the answer, so these are set-membership tests on data already loaded.
  The org version had to resolve orgs → teams → subjects and cache the result
  per request; that machinery is gone.
- ``team_categories``:  a category's ``subjects`` M2M has to be consulted, so
  the qualifying category IDs are resolved once per request and cached on
  ``request._scoped_mixin_category_ids``.
- ``teams``:  ``team.api_listed`` is a direct column, so the first half costs
  nothing and respects ``prefetch_related('teams')``. The second half needs the
  team ids owning an in-scope subject, resolved once per request and cached on
  ``request._scoped_mixin_listed_team_ids``.

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


def _request_scope_owning_team_ids(request, visible_subject_ids: set) -> set:
	"""Team IDs that own at least one subject in the caller's scope.

	The second half of the team-listing rule (see module docstring and
	TeamsViewSet, which must agree). Cached once per request so a list
	endpoint does not repeat it per serialised row.
	"""
	cache_attr = "_scoped_mixin_listed_team_ids"
	if not hasattr(request, cache_attr):
		from gregory.models import Subject

		setattr(
			request,
			cache_attr,
			set(
				Subject.objects.filter(id__in=visible_subject_ids)
				.exclude(team_id__isnull=True)
				.values_list("team_id", flat=True)
				.distinct()
			),
		)
	return getattr(request, cache_attr)


def _request_visible_category_ids(request, visible_subject_ids: set) -> set:
	"""TeamCategory IDs whose subjects intersect the caller's scope.

	Cached once per request: without it a list endpoint would issue this
	query per serialised row. Unlike subjects and ml_predictions, a category
	reaches subjects through its own M2M, so there is no way to answer it
	from data already in hand.
	"""
	cache_attr = "_scoped_mixin_category_ids"
	if not hasattr(request, cache_attr):
		from gregory.models import TeamCategory

		setattr(
			request,
			cache_attr,
			set(
				TeamCategory.objects.filter(
					subjects__id__in=visible_subject_ids
				)
				.values_list("id", flat=True)
				.distinct()
			),
		)
	return getattr(request, cache_attr)


class ScopedSerializerMixin:
	"""
	Mixin for DRF serializers that strips nested associations the caller
	cannot see.

	Usage::

	    class ArticleSerializer(ScopedSerializerMixin,
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

		if request is None or not hasattr(request, "visible_subject_ids"):
			return ret

		visible = request.visible_subject_ids

		# --- teams: the same rule /teams/ applies, kept in step with it ---
		if "teams" in ret and hasattr(instance, "teams"):
			scope_owning = _request_scope_owning_team_ids(request, visible)
			listed_team_ids = {
				t.id
				for t in instance.teams.all()
				if t.api_listed or t.id in scope_owning
			}
			ret["teams"] = [t for t in ret["teams"] if t.get("id") in listed_team_ids]

		# --- subjects: a set-membership test, no query ---
		if "subjects" in ret:
			ret["subjects"] = [s for s in ret["subjects"] if s.get("id") in visible]

		# --- team_categories: reached through the category's own subjects M2M ---
		if "team_categories" in ret and hasattr(instance, "team_categories"):
			visible_cat_ids = _request_visible_category_ids(request, visible)
			ret["team_categories"] = [
				c for c in ret["team_categories"] if c.get("id") in visible_cat_ids
			]

		# --- ml_predictions: filter directly on already-serialised data ---
		# Each item in ret['ml_predictions'] has a 'subject' key that is either a
		# nested dict {id, subject_name, description} (from MLPredictionsSerializer)
		# or a bare integer FK (from legacy/test serializers).  Filtering here avoids
		# hitting instance.ml_predictions_detail.all() a second time, which would
		# cause an extra per-object query when the relation is not prefetched.
		if "ml_predictions" in ret:
			def _subject_id(p):
				s = p.get("subject")
				if isinstance(s, dict):
					return s.get("id")
				return s if isinstance(s, int) else None

			ret["ml_predictions"] = [
				p for p in ret["ml_predictions"] if _subject_id(p) in visible
			]

		# --- article_subject_relevances: same shape/reasoning as ml_predictions ---
		# ArticleSubjectRelevanceSerializer nests a full subject (id, name, ...)
		# plus is_relevant. An article tagged with one visible and one hidden
		# subject would otherwise still disclose the hidden subject's identity
		# and relevance verdict through this relation (PR #863 review finding 3).
		if "article_subject_relevances" in ret:
			def _relevance_subject_id(r):
				s = r.get("subject")
				if isinstance(s, dict):
					return s.get("id")
				return s if isinstance(s, int) else None

			ret["article_subject_relevances"] = [
				r
				for r in ret["article_subject_relevances"]
				if _relevance_subject_id(r) in visible
			]

		return ret
