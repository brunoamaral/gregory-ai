"""
mcpauth/admin.py

The editor grant, managed from the Site admin page. ``SiteEditorInline`` is
attached to the Site admin in sitesettings/admin.py, next to the MCP prompt
and document inlines.
"""

from django import forms
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from mcpauth.access import editor_address
from mcpauth.models import SiteEditor


class SiteEditorForm(forms.ModelForm):
	"""A tickbox instead of a date field for ending a grant."""

	revoke = forms.BooleanField(
		required=False,
		label="Revoke",
		help_text="End this person's access now. Their MCP sessions for this site stop immediately.",
	)

	class Meta:
		model = SiteEditor
		fields = ["user", "can_edit", "can_manage_sources"]

	def save(self, commit=True):
		instance = super().save(commit=False)
		if self.cleaned_data.get("revoke") and instance.revoked_at is None:
			instance.revoked_at = timezone.now()
		if commit:
			instance.save()
		return instance


class SiteEditorInline(admin.TabularInline):
	model = SiteEditor
	form = SiteEditorForm
	extra = 0
	autocomplete_fields = ["user"]
	fields = ["user", "can_edit", "can_manage_sources", "granted_by", "created_at", "revoked_at", "revoke"]
	readonly_fields = ["granted_by", "created_at", "revoked_at"]
	verbose_name = "MCP editor"
	verbose_name_plural = "MCP editors"

	def has_change_permission(self, request, obj=None):
		return request.user.is_superuser

	def has_add_permission(self, request, obj=None):
		return request.user.is_superuser

	def has_delete_permission(self, request, obj=None):
		# Grants are revoked, not deleted: the row is the record of who had access.
		return False

	def has_view_permission(self, request, obj=None):
		return request.user.is_superuser


def editor_address_display(site):
	"""The address a new editor adds to their MCP client, for the Site admin page."""
	if not site or not site.pk:
		return "-"
	return format_html("<code>{}</code>", editor_address(site))


# --- django-oauth-toolkit's own admin pages, superuser-only ---------------
#
# The toolkit registers admins for applications, tokens, grants and ID tokens
# that list every user's rows, and its "Revoke" actions declare no permission,
# so any staff account holding the model permissions could revoke anyone's
# MCP access. These rows are infrastructure, not organisation content: only
# superusers see or touch them, as with the editor grants above.
#
# Importing the toolkit's admin module here makes sure its classes are
# registered before they are replaced, whatever order autodiscover runs in;
# autodiscover later finds the module already loaded and registers nothing twice.
from django.core.exceptions import ImproperlyConfigured
from oauth2_provider import admin as _oauth_admin  # noqa: F401
from oauth2_provider.models import (
	get_access_token_model,
	get_application_model,
	get_grant_model,
	get_id_token_model,
	get_refresh_token_model,
)


def _superuser_only(admin_class):
	attrs = {
		"__module__": __name__,
		"has_module_permission": lambda self, request: request.user.is_superuser,
		"has_view_permission": lambda self, request, obj=None: request.user.is_superuser,
		"has_add_permission": lambda self, request, *args: (
			request.user.is_superuser and admin_class.has_add_permission(self, request, *args)
		),
		"has_change_permission": lambda self, request, obj=None: (
			request.user.is_superuser and admin_class.has_change_permission(self, request, obj)
		),
		"has_delete_permission": lambda self, request, obj=None: (
			request.user.is_superuser and admin_class.has_delete_permission(self, request, obj)
		),
	}
	revoke = getattr(admin_class, "revoke_tokens", None)
	if revoke is not None:

		def revoke_tokens(self, request, queryset):
			return revoke(self, request, queryset)

		attrs["revoke_tokens"] = admin.action(
			permissions=["change"],
			description=getattr(revoke, "short_description", "Revoke selected tokens"),
		)(revoke_tokens)
	return type(f"SuperuserOnly{admin_class.__name__}", (admin_class,), attrs)


for _model in (
	get_application_model(),
	get_access_token_model(),
	get_grant_model(),
	get_id_token_model(),
	get_refresh_token_model(),
):
	if not admin.site.is_registered(_model):
		# Fail loudly: a silent skip would drop the restriction unnoticed.
		raise ImproperlyConfigured(f"Expected django-oauth-toolkit to register an admin for {_model}")
	_registered = type(admin.site.get_model_admin(_model))
	admin.site.unregister(_model)
	admin.site.register(_model, _superuser_only(_registered))
