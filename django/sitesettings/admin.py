from django.contrib import admin
from django.contrib.sites.admin import SiteAdmin
from django.contrib.sites.models import Site

from .models import CustomSetting


class CustomSettingInline(admin.StackedInline):
	model = CustomSetting
	extra = 1
	max_num = 1
	fields = ['title', 'sender_email_prefix', 'email_footer', 'admin_email']


class SiteWithSettingsAdmin(SiteAdmin):
	inlines = [CustomSettingInline]


admin.site.unregister(Site)
admin.site.register(Site, SiteWithSettingsAdmin)
