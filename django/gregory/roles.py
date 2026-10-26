"""The Editor role: Django model permissions for organisation editors.

Model permissions decide *what kind* of row a staff user may touch; the admin
code (OrganizationFilterMixin and friends in gregory/admin.py) decides *which*
rows. This module is the single source of truth for the first half. The
"Editor" group is synced to exactly this list by ``manage.py setup_roles``,
so changing a permission means changing this file, not clicking in the
Groups admin.

Rules for adding to it (gregory/tests/test_roles.py enforces them):

- Only models whose admin is scoped to the user's organisation, with writes
  narrowed to the user's own teams (gregory/team_scope.py), may get add,
  change or delete. Shared reference data (authors, sponsors, entities, MeSH,
  publication types) stays view-only: changing it changes it for everyone.
- Articles, trials and patents are shared between organisations, so editors
  never get add or delete on them.
- Nothing from the apps in FORBIDDEN_APPS: user accounts and groups (anyone who
  can edit users can make themselves a superuser), organisations, sites, API
  keys, OAuth/MCP grants, and newsletters (several subscription admins are not
  organisation-scoped yet and show subscribers' personal data).
"""

from django.contrib.auth.models import Group, Permission
from django.db import transaction

EDITOR_GROUP = "Editor"

VIEW = ("view",)
VIEW_CHANGE = ("view", "change")
VIEW_ADD_CHANGE = ("view", "add", "change")
FULL = ("view", "add", "change", "delete")

# "app_label.model" (lower case, as in ContentType) -> actions.
EDITOR_PERMISSIONS = {
	# Content shared between organisations: curate it, never create or delete it.
	"gregory.articles": VIEW_CHANGE,
	"gregory.trials": VIEW_CHANGE,
	"gregory.patents": VIEW_CHANGE,
	# The organisation's own layer on that content.
	"gregory.articlesitecontent": VIEW_ADD_CHANGE,
	"gregory.patentsitecontent": VIEW_ADD_CHANGE,
	"gregory.trialorgcontent": VIEW_ADD_CHANGE,
	"gregory.articlesubjectrelevance": VIEW_ADD_CHANGE,
	"gregory.articlecategoryassignment": FULL,
	"gregory.trialcategoryassignment": FULL,
	"gregory.patentcategoryassignment": FULL,
	"gregory.patenttriallink": VIEW_CHANGE,
	# Removing an automatic medicine link from a category suppresses it rather than
	# deleting it, but the category page's inline still needs add and delete.
	"gregory.medicinecategoryassignment": FULL,
	"gregory.teamcategory": FULL,
	# The organisation's set-up: visible, changed by a superuser.
	"gregory.sources": VIEW,
	"gregory.subject": VIEW,
	"gregory.team": VIEW,
	"gregory.predictionrunlog": VIEW,
	# Read-only rows shown in the article, trial and patent pages' inlines.
	"gregory.articletrialreference": VIEW,
	"gregory.articlemeshheading": VIEW,
	"gregory.trialcountry": VIEW,
	"gregory.trialsite": VIEW,
	"gregory.patentpublication": VIEW,
	"gregory.patentapplicant": VIEW,
	# Shared reference data.
	"gregory.authors": VIEW,
	"gregory.sponsor": VIEW,
	"gregory.entities": VIEW,
	"gregory.publicationtype": VIEW,
	"gregory.meshterm": VIEW,
	# EMA's medicines and their events: written only by sync_ema_medicines.
	"gregory.medicine": VIEW,
	"gregory.medicineevent": VIEW,
	# The medicines approved for a subject's condition. Derived by match_medicines, so
	# editors only suppress or restore one, from the Medicine subject links page; they
	# can't add or delete a link, and the Subject page stays a superuser's.
	"gregory.medicinesubjectlink": VIEW_CHANGE,
}

FORBIDDEN_APPS = frozenset(
	{
		"admin",
		"api",
		"auth",
		"contenttypes",
		"mcpauth",
		"oauth2_provider",
		"organizations",
		"sessions",
		"sites",
		"sitesettings",
		"subscriptions",
	}
)


def editor_permission_keys():
	"""Yield (app_label, codename) for every permission in the role."""
	for label, actions in EDITOR_PERMISSIONS.items():
		app_label, model = label.split(".")
		for action in actions:
			yield app_label, f"{action}_{model}"


def editor_permissions():
	"""The role's Permission rows. Raises if any of them doesn't exist, so a
	renamed model fails loudly instead of silently dropping out of the role."""
	keys = set(editor_permission_keys())
	found = {
		(p.content_type.app_label, p.codename): p
		for p in Permission.objects.select_related("content_type").filter(
			codename__in={codename for _, codename in keys}
		)
	}
	missing = sorted(f"{a}.{c}" for a, c in keys - found.keys())
	if missing:
		raise LookupError(f"Editor role names permissions that don't exist: {', '.join(missing)}")
	return [found[key] for key in sorted(keys)]


@transaction.atomic
def sync_editor_group():
	"""Create or update the Editor group to hold exactly the role's permissions.

	Returns (group, added, removed) where added/removed are lists of
	"app_label.codename" strings.
	"""
	wanted = {p.pk: p for p in editor_permissions()}
	group, _ = Group.objects.get_or_create(name=EDITOR_GROUP)
	current = set(group.permissions.values_list("pk", flat=True))
	to_add = [wanted[pk] for pk in wanted.keys() - current]
	to_remove = list(
		Permission.objects.select_related("content_type").filter(pk__in=current - wanted.keys())
	)
	group.permissions.add(*to_add)
	group.permissions.remove(*to_remove)

	def names(perms):
		return sorted(f"{p.content_type.app_label}.{p.codename}" for p in perms)

	return group, names(to_add), names(to_remove)


def editor_group_drift():
	"""(missing, extra) between the Editor group and the role, without writing.
	A missing group counts as every permission missing."""
	wanted = {p.pk: p for p in editor_permissions()}
	group = Group.objects.filter(name=EDITOR_GROUP).first()
	current = set(group.permissions.values_list("pk", flat=True)) if group else set()
	missing = sorted(
		f"{p.content_type.app_label}.{p.codename}" for pk, p in wanted.items() if pk not in current
	)
	extra = sorted(
		f"{p.content_type.app_label}.{p.codename}"
		for p in Permission.objects.select_related("content_type").filter(
			pk__in=current - wanted.keys()
		)
	)
	return missing, extra
