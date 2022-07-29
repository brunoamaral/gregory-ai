from django.contrib import admin

# Register your models here.
from .models import CustomSetting

class CustomSettingAdmin(admin.ModelAdmin):
	list_display = ['site','title',]


admin.site.register(CustomSetting,CustomSettingAdmin)
