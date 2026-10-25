"""Which rows a staff user may edit: the ones that belong to one of their teams.

The admin has two layers of scoping:

- **Seeing** is organisation-wide: a staff user sees what belongs to any team of
  their organisations (OrganizationFilterMixin in gregory/admin.py).
- **Editing** is team-wide: they change, delete and run actions only on what
  belongs to a team they are a member of (``Team.members``). This module
  answers that question for every model the admin lets staff write to.

What "belongs to a team" means per model:

- Articles, trials and patents are shared between teams and organisations. One
  belongs to a team when one of its sources is the team's (attached at
  ingestion) or a curator tagged it with the team (``teams``).
- Sources, subjects, team categories and prediction run logs have a ``team``.
- Rows that hang off content (site content, trial organisation content,
  patent-trial links) follow their article, trial or patent.
- Medicine-category links follow their category's team.

Superusers edit everything. A staff user on no active team edits nothing.
"""

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db.models import Q


def user_team_ids(user):
	"""IDs of the active teams ``user`` is a member of (empty for anonymous)."""
	from gregory.models import Team

	if not user.is_authenticated:
		return []
	# Cached on the user object, which lives for one request: the admin asks
	# this many times per page.
	cached = getattr(user, "_gregory_team_ids", None)
	if cached is None:
		cached = list(Team.objects.filter(members=user).values_list("pk", flat=True))
		user._gregory_team_ids = cached
	return cached


def _content_q(team_ids):
	return Q(sources__team__in=team_ids) | Q(teams__in=team_ids)


def team_editable(user, queryset):
	"""``queryset`` narrowed to the rows ``user`` may edit."""
	if user.is_superuser:
		return queryset
	from gregory.models import (
		ArticleSiteContent,
		Articles,
		MedicineCategoryAssignment,
		PatentSiteContent,
		PatentTrialLink,
		Patents,
		Team,
		TrialOrgContent,
		Trials,
	)

	model = queryset.model
	team_ids = user_team_ids(user)
	if not team_ids:
		return queryset.none()

	if model in (Articles, Trials, Patents):
		# Through a subquery, so the result has no duplicate rows and no
		# DISTINCT that would clash with an annotated or ordered queryset.
		owned = model._default_manager.filter(_content_q(team_ids)).values("pk")
		return queryset.filter(pk__in=owned)
	if model is Team:
		# Team settings and membership are access control: superusers only.
		return queryset.none()
	if model is ArticleSiteContent:
		return queryset.filter(article__in=team_editable(user, Articles.objects.all()))
	if model is PatentSiteContent:
		return queryset.filter(patent__in=team_editable(user, Patents.objects.all()))
	if model is TrialOrgContent:
		return queryset.filter(trial__in=team_editable(user, Trials.objects.all()))
	if model is PatentTrialLink:
		return queryset.filter(patent__in=team_editable(user, Patents.objects.all()))
	if model is MedicineCategoryAssignment:
		return queryset.filter(teamcategory__team__in=team_ids)
	try:
		model._meta.get_field("team")
	except FieldDoesNotExist:
		raise ImproperlyConfigured(
			f"gregory.team_scope doesn't know how {model.__name__} belongs to a team"
		) from None
	return queryset.filter(team__in=team_ids)


def can_edit(user, obj):
	"""Whether ``user`` may edit ``obj`` (a saved model instance)."""
	if user.is_superuser:
		return True
	if obj is None or obj.pk is None:
		return True
	model = type(obj)
	return team_editable(user, model._default_manager.filter(pk=obj.pk)).exists()
