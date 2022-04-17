from django.contrib import admin
from .models import Subscribers,Lists

# Register your models here.
class SubscriberAdmin(admin.ModelAdmin):
	list_display = ['subscriber_id','first_name','last_name','email']


class ListsAdmin(admin.ModelAdmin):
	list_display = ['list_name','list_description']


admin.site.register(Subscribers,SubscriberAdmin)
admin.site.register(Lists,ListsAdmin)
