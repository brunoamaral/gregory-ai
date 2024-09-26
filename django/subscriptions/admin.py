from django.contrib import admin
from .models import Subscribers, Lists

# Register your models here.
class SubscriberAdmin(admin.ModelAdmin):
	list_display = ['subscriber_id', 'first_name', 'last_name', 'email', 'active', 'number_of_subscriptions']
	list_filter = ['active', 'profile']
	search_fields = ['first_name', 'last_name', 'email']
	actions = ['make_active', 'make_inactive']

	def number_of_subscriptions(self, obj):
		return obj.subscriptions.count()
	number_of_subscriptions.short_description = 'Number of Subscriptions'

	# Define the action to set 'active' to True
	def make_active(self, request, queryset):
		updated_count = queryset.update(active=True)
		self.message_user(request, f"{updated_count} subscriber(s) marked as active.")
	make_active.short_description = "Mark selected subscribers as active"

	# Define the action to set 'active' to False
	def make_inactive(self, request, queryset):
		updated_count = queryset.update(active=False)
		self.message_user(request, f"{updated_count} subscriber(s) marked as inactive.")
	make_inactive.short_description = "Mark selected subscribers as inactive"

class ListsAdmin(admin.ModelAdmin):
		list_display = ['list_name', 'list_description']

admin.site.register(Subscribers, SubscriberAdmin)
admin.site.register(Lists, ListsAdmin)