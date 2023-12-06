from django.contrib import admin
from .models import Subscribers, Lists

# Register your models here.
class SubscriberAdmin(admin.ModelAdmin):
	list_display = ['subscriber_id', 'first_name', 'last_name', 'email', 'number_of_subscriptions']

	def number_of_subscriptions(self, obj):
			return obj.subscriptions.count()
	number_of_subscriptions.short_description = 'Number of Subscriptions'

class ListsAdmin(admin.ModelAdmin):
		list_display = ['list_name', 'list_description']

admin.site.register(Subscribers, SubscriberAdmin)
admin.site.register(Lists, ListsAdmin)