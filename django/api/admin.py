from django.contrib import admin
from .models import APIAccessScheme, APIAccessSchemeLog

class APIAcccessSchemeAdmin(admin.ModelAdmin):
	list_display = ['api_key','client_name','client_contacts','ip_addresses','begin_date','end_date','max_calls_minute','max_calls_hour','max_calls_day']
	# a list of displayed columns name.

class APILogAdmin(admin.ModelAdmin):
	list_display = ['call_type','ip_addr','api_access_scheme','access_date','http_code','error_message']

admin.site.register(APIAccessScheme,APIAcccessSchemeAdmin)
admin.site.register(APIAccessSchemeLog,APILogAdmin)
# Register your models here.
