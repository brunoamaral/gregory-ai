from django.contrib import admin
from django.contrib.sites.admin import SiteAdmin
from django.contrib.sites.models import Site

from .models import CustomSetting
from gregory.models import OrganizationSite


class CustomSettingInline(admin.StackedInline):
	model = CustomSetting
	extra = 1
	max_num = 1
	filter_horizontal = ("sitemap_subjects",)
	fieldsets = [
		(
			None,
			{
				"fields": ["title"],
			},
		),
		(
			"Email",
			{
				"fields": ["admin_email", "sender_name", "sender_email_prefix"],
			},
		),
		(
			"API & Domain",
			{
				"fields": ["api_domain", "allowed_domains"],
			},
		),
		(
			"Postmark Integration",
			{
				"fields": ["postmark_api_token", "postmark_api_url"],
			},
		),
		(
			"Website URLs",
			{
				"fields": [
					"website_url",
					"support_url",
					"about_url",
					"contact_url",
					"privacy_policy_url",
					"terms_url",
				],
			},
		),
		(
			"Social Links",
			{
				"classes": ["collapse"],
				"fields": ["bluesky_url", "github_url", "mastodon_url"],
			},
		),
		(
			"Sitemap",
			{
				"fields": [
					"generate_sitemap",
					"sitemap_subjects",
					"sitemap_relevant_only",
				],
			},
		),
	]


class OrganizationSiteInline(admin.TabularInline):
	"""Allows changing which organisation this site belongs to (superusers only)."""

	model = OrganizationSite
	extra = 0
	fields = ("organization", "is_default")
	verbose_name = "Organisation"
	verbose_name_plural = "Organisations"

	def get_readonly_fields(self, request, obj=None):
		if not request.user.is_superuser:
			return ("organization", "is_default")
		return ()

	def has_add_permission(self, request, obj=None):
		return request.user.is_superuser

	def has_delete_permission(self, request, obj=None):
		return request.user.is_superuser


class SiteWithSettingsAdmin(SiteAdmin):
	inlines = [CustomSettingInline, OrganizationSiteInline]


admin.site.unregister(Site)
admin.site.register(Site, SiteWithSettingsAdmin)
