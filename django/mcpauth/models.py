"""
mcpauth/models.py

The OAuth side of MCP editor access: who may edit which site (``SiteEditor``)
and the access token that carries one site and one access tier
(``AccessToken``, swapped in for django-oauth-toolkit's own through
``OAUTH2_PROVIDER_ACCESS_TOKEN_MODEL``). See MCP-AUTH-PLAN.md.
"""

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from oauth2_provider.models import AbstractAccessToken, AbstractIDToken, AbstractRefreshToken

SCOPE_READ = "articles:read"
SCOPE_EDIT = "articles:edit"

TIER_EDITOR = "editor"
TIER_PUBLIC = "public"


class SiteEditor(models.Model):
	"""A grant: this person may edit this one site over MCP (D4, D13).

	One row per (user, site) while active; a revoked row is kept as the record
	of who had access and when it ended, and a new grant is a new row.
	"""

	user = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="site_editor_grants",
	)
	site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="editor_grants")
	can_edit = models.BooleanField(
		default=True,
		help_text=(
			"Untick for read-only access: the editor read scope without the "
			"edit tools. Saving this unticked ends the person's existing edit "
			"sessions on this site."
		),
	)
	granted_by = models.ForeignKey(
		settings.AUTH_USER_MODEL,
		null=True,
		blank=True,
		on_delete=models.SET_NULL,
		related_name="+",
	)
	created_at = models.DateTimeField(auto_now_add=True)
	revoked_at = models.DateTimeField(null=True, blank=True)

	class Meta:
		constraints = [
			models.UniqueConstraint(
				fields=["user", "site"],
				condition=models.Q(revoked_at__isnull=True),
				name="unique_active_site_editor",
			)
		]
		verbose_name = "site editor"
		verbose_name_plural = "site editors"

	def __str__(self):
		state = "revoked" if self.revoked_at else "active"
		return f"{self.user} on {self.site.domain} ({state})"

	@property
	def is_active(self):
		return self.revoked_at is None

	def clean(self):
		super().clean()
		if self.user_id and self.site_id and self.revoked_at is None:
			if not grant_allowed(self.user, self.site):
				raise ValidationError(
					"Editors can only be granted a site owned by an organisation "
					"they belong to. Only a superuser (our own team) can be "
					"granted any site."
				)

	@transaction.atomic
	def save(self, *args, **kwargs):
		# Access ends immediately, not when the token expires: a revoked or
		# downgraded grant takes the person's tokens for this site with it, in
		# the same transaction as the change itself.
		previous = None
		if self.pk:
			previous = SiteEditor.objects.filter(pk=self.pk).first()
		super().save(*args, **kwargs)
		revoked_now = self.revoked_at is not None and (previous is None or previous.revoked_at is None)
		downgraded = previous is not None and previous.can_edit and not self.can_edit
		if revoked_now or downgraded:
			revoke_tokens(self.user_id, self.site_id)

	def revoke(self):
		"""End this grant now, and the person's tokens for the site with it."""
		if self.revoked_at is None:
			self.revoked_at = timezone.now()
			self.save(update_fields=["revoked_at"])


def grant_allowed(user, site) -> bool:
	"""A client editor is granted only a site owned by an organisation they
	belong to. A superuser is our own team and may be granted any site, but
	superuser status alone never gives edit access: the grant row is what does."""
	if user.is_superuser:
		return True
	from gregory.models import OrganizationSite

	return OrganizationSite.objects.filter(
		site=site, organization__organization_users__user=user
	).exists()


def revoke_tokens(user_id, site_id):
	"""Delete a user's access and refresh tokens for one site."""
	tokens = AccessToken.objects.filter(user_id=user_id, site_id=site_id)
	# Refresh tokens first: deleting the access token would null the link.
	RefreshToken.objects.filter(access_token__in=tokens).delete()
	tokens.delete()


class AccessToken(AbstractAccessToken):
	"""An OAuth access token bound to exactly one site (D4) and one tier (D15).

	``site`` is set at issue time from the RFC 8707 ``resource`` the client
	asked for; ``tier`` records whether the person held an editor grant then.
	A public-tier token never carries ``articles:edit``. Rows without a site
	are DOT's own bookkeeping tokens (dynamic-registration management) and are
	never accepted by the MCP server.
	"""

	TIER_CHOICES = [(TIER_EDITOR, "Editor"), (TIER_PUBLIC, "Public")]

	site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
	tier = models.CharField(max_length=10, blank=True, choices=TIER_CHOICES)

	class Meta(AbstractAccessToken.Meta):
		pass


class RefreshToken(AbstractRefreshToken):
	"""DOT's refresh token, unchanged. It lives here because DOT wants the
	access, refresh and ID token models swapped into one app: they reference each
	other, and models in different apps make a circular migration dependency
	(system check ``oauth2_provider.W011``)."""

	class Meta(AbstractRefreshToken.Meta):
		pass


class IDToken(AbstractIDToken):
	"""DOT's ID token, unchanged, for the same reason as RefreshToken. OIDC is
	not enabled; nothing is ever issued into this table."""

	class Meta(AbstractIDToken.Meta):
		pass
