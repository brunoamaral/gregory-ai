from django import forms
from django.contrib import admin
from django.contrib.sites.admin import SiteAdmin
from django.contrib.sites.models import Site

from .models import CustomSetting, SiteMcpDocument, SiteMcpPrompt
from gregory.models import OrganizationSite
from gregory.utils.trial_field_normalizers import TrialRecruitmentStatus


class CustomSettingAdminForm(forms.ModelForm):
	"""Render sitemap_trial_statuses as tickboxes.

	The default ArrayField widget is a comma-separated text input, which
	invites typos in values that must match TrialRecruitmentStatus
	exactly — a misspelt status silently narrows the sitemap to nothing.
	"""

	sitemap_trial_statuses = forms.MultipleChoiceField(
		choices=TrialRecruitmentStatus.choices,
		widget=forms.CheckboxSelectMultiple,
		required=False,
		label="Sitemap trial statuses",
		help_text=CustomSetting._meta.get_field("sitemap_trial_statuses").help_text,
	)

	class Meta:
		model = CustomSetting
		fields = "__all__"


class CustomSettingInline(admin.StackedInline):
	model = CustomSetting
	form = CustomSettingAdminForm
	extra = 1
	max_num = 1
	filter_horizontal = ("sitemap_subjects", "scope_subjects")
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
					"has_author_pages",
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
			"API visibility",
			{
				"fields": [
					"api_public",
					"scope_subjects",
					"rss_enabled",
				],
				"description": (
					"What this site owns and whether it is served anonymously. "
					"<b>scope_subjects</b> is the site's corpus — distinct from "
					"<b>sitemap_subjects</b> below, which is SEO curation. They are "
					"seeded from the same set but are free to diverge; removing a "
					"subject from the sitemap must not silently make its data private. "
					"<b>api_public</b> is off by default, so a new site is private "
					"until someone publishes it deliberately."
				),
			},
		),
		(
			"Sitemap",
			{
				"fields": [
					"generate_sitemap",
					"sitemap_subjects",
					"sitemap_relevant_only",
					"sitemap_include_trials",
					"sitemap_trial_statuses",
					"sitemap_include_authors",
				],
			},
		),
		(
			"About / data export",
			{
				"classes": ["collapse"],
				"fields": [
					"description",
					"contact_email",
					"data_license",
					"data_license_url",
					"citation",
				],
			},
		),
		(
			"Research assistant (MCP)",
			{
				"fields": ["mcp_enabled", "mcp_description"],
				"description": (
					"The assistant's corpus is <b>scope_subjects</b> above — there is no "
					"separate MCP subject list. Prompts and documents are edited in their "
					"own sections on this page. Not read by the MCP server yet — it starts "
					"using these settings in a later release."
				),
			},
		),
	]


class SiteMcpPromptInline(admin.StackedInline):
	model = SiteMcpPrompt
	extra = 0
	verbose_name = "MCP prompt"
	verbose_name_plural = "MCP prompts"
	fields = ["name", "title", "description", "template", "arguments", "is_active", "ordering"]

	def formfield_for_dbfield(self, db_field, request, **kwargs):
		formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
		if db_field.name == "arguments":
			formfield.help_text = (
				'JSON list, e.g. [{"name": "topic", "description": "The topic to '
				'research.", "required": true}]. Every $name placeholder in the '
				"template must have a matching entry here, and vice versa."
			)
		return formfield


class SiteMcpDocumentInline(admin.StackedInline):
	model = SiteMcpDocument
	extra = 0
	verbose_name = "MCP document"
	verbose_name_plural = "MCP documents"
	fields = ["slug", "title", "description", "mime_type", "body", "is_active", "ordering"]


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
	inlines = [
		CustomSettingInline,
		SiteMcpPromptInline,
		SiteMcpDocumentInline,
		OrganizationSiteInline,
	]


admin.site.unregister(Site)
admin.site.register(Site, SiteWithSettingsAdmin)
