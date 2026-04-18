from django.contrib import admin
from django.contrib.sites.admin import SiteAdmin
from django.contrib.sites.models import Site

from .models import CustomSetting


class CustomSettingInline(admin.StackedInline):
	model = CustomSetting
	extra = 1
	max_num = 1
	fields = [
		'title', 'sender_email_prefix', 'admin_email', 'api_domain',
		'email_footer',
		'website_url', 'support_url', 'about_url', 'contact_url',
		'bluesky_url', 'github_url', 'mastodon_url',
		'postmark_api_token', 'postmark_api_url',
	]


class SiteWithSettingsAdmin(SiteAdmin):
	inlines = [CustomSettingInline]


admin.site.unregister(Site)
admin.site.register(Site, SiteWithSettingsAdmin)
