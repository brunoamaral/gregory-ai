"""
api/editorial.py

Opt-in editorial content (``?include=editorial``) for article and trial
responses -- see EDITORIAL-API-SPEC.md.

"Editorial" is ``takeaways`` and ``summary_plain_english``, written per
organisation (``ArticleOrgContent`` / ``TrialOrgContent``). Which
organisation's content a caller gets is decided by *who the caller is*, never
by a parameter, so nobody can ask for another organisation's content:

  - Valid API key         -> the key's organisation.
  - Logged-in user        -> every organisation the user belongs to.
  - Anonymous             -> the organisation that owns the ``api_public``
                             site ``resolve_anonymous_site()`` picks
                             (``?site_id=`` -> Origin -> Referer -> the one
                             public site). None when there is no public site.

``?team_id=`` plays no part. Both helpers cache their answer on the request,
so a list endpoint resolves once however many rows it serialises.
"""

from rest_framework.exceptions import ValidationError

#: Values ``?include=`` accepts. Comma-separated, so it can grow.
INCLUDE_CHOICES = ("editorial",)

_INCLUDE_ATTR = "_editorial_requested"
_ORG_IDS_ATTR = "_editorial_org_ids"
_ORG_NAMES_ATTR = "_editorial_org_names"


def _is_csv(request) -> bool:
	"""CSV output never carries editorial content (spec requirement 8)."""
	if request.GET.get("format", "").lower() == "csv":
		return True
	renderer = getattr(request, "accepted_renderer", None)
	return getattr(renderer, "format", None) == "csv"


def parse_include(request) -> set:
	"""Parse ``?include=`` into a set, raising a 400 on an unknown value."""
	raw = request.GET.get("include", "")
	values = {part.strip().lower() for part in raw.split(",") if part.strip()}
	unknown = sorted(values - set(INCLUDE_CHOICES))
	if unknown:
		raise ValidationError(
			{
				"include": (
					f"Unknown value(s): {', '.join(unknown)}. "
					f"Accepted values: {', '.join(INCLUDE_CHOICES)}."
				)
			}
		)
	return values


def editorial_requested(request) -> bool:
	"""True when the caller asked for ``?include=editorial`` and the output is
	not CSV. Validates ``include`` even for CSV, so typos still fail loudly."""
	if request is None:
		return False
	cached = getattr(request, _INCLUDE_ATTR, None)
	if cached is not None:
		return cached
	values = parse_include(request)
	result = "editorial" in values and not _is_csv(request)
	setattr(request, _INCLUDE_ATTR, result)
	return result


def editorial_org_ids(request) -> list:
	"""Organisation ids whose editorial content the caller may read, sorted."""
	if request is None:
		return []
	cached = getattr(request, _ORG_IDS_ATTR, None)
	if cached is not None:
		return cached

	from gregory.models import OrganizationSite
	from gregory.site_resolution import resolve_anonymous_site
	from gregory.visibility import _resolve_api_scheme

	org_ids = set()
	scheme = _resolve_api_scheme(request)
	if scheme is not None:
		org_ids.add(scheme.organization_id)
	elif getattr(request, "user", None) is not None and request.user.is_authenticated:
		org_ids.update(
			request.user.organizations_organizationuser.values_list(
				"organization_id", flat=True
			)
		)
	else:
		# Ambiguity (two public sites, no indicator) was already refused with
		# a 400 by the visibility layer before any serializer runs.
		site_id, _varies, ambiguous = resolve_anonymous_site(request)
		if site_id is not None and not ambiguous:
			org_ids.update(
				OrganizationSite.objects.filter(site_id=site_id).values_list(
					"organization_id", flat=True
				)
			)

	result = sorted(org_ids)
	setattr(request, _ORG_IDS_ATTR, result)
	return result


def editorial_org_names(request) -> dict:
	"""``{org_id: name}`` for the caller's editorial orgs, one query per request."""
	cached = getattr(request, _ORG_NAMES_ATTR, None)
	if cached is not None:
		return cached
	from organizations.models import Organization

	org_ids = editorial_org_ids(request)
	names = dict(
		Organization.objects.filter(pk__in=org_ids).values_list("pk", "name")
	)
	setattr(request, _ORG_NAMES_ATTR, names)
	return names
