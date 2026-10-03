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
		fields = ["user", "can_edit"]

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
	fields = ["user", "can_edit", "granted_by", "created_at", "revoked_at", "revoke"]
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
