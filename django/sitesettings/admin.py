from django.contrib import admin

# Register your models here.
from .models import CustomSettings

class CustomSettingsAdmin(admin.ModelAdmin):
	list_display = ['site','title',]


admin.site.register(CustomSettings,CustomSettingsAdmin)
