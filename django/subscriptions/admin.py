from django.contrib import admin
from django.utils.html import format_html
from .models import Subscribers, Lists, FailedNotification
from .forms import ListsAdminForm

class SubscriberAdmin(admin.ModelAdmin):
	list_display = ['subscriber_id', 'first_name', 'last_name', 'email', 'active', 'number_of_subscriptions']
	list_filter = ['active', 'profile']
	search_fields = ['first_name', 'last_name', 'email']
	actions = ['make_active', 'make_inactive']

	def number_of_subscriptions(self, obj):
		return obj.subscriptions.count()
	number_of_subscriptions.short_description = 'Number of Subscriptions'

	def make_active(self, request, queryset):
		updated_count = queryset.update(active=True)
		self.message_user(request, f"{updated_count} subscriber(s) marked as active.")
	make_active.short_description = "Mark selected subscribers as active"

	def make_inactive(self, request, queryset):
		updated_count = queryset.update(active=False)
		self.message_user(request, f"{updated_count} subscriber(s) marked as inactive.")
	make_inactive.short_description = "Mark selected subscribers as inactive"

admin.site.register(Subscribers, SubscriberAdmin)

class SubscriberInline(admin.TabularInline):
	model = Subscribers.subscriptions.through  # Through model for M2M
	extra = 1
	can_delete = True
	verbose_name = "Subscriber"
	verbose_name_plural = "Subscribers"
	fields = ['subscriber_link', 'subscriber_email']
	readonly_fields = ['subscriber_link', 'subscriber_email']

	def subscriber_email(self, obj):
		if obj.subscribers_id:
			return obj.subscribers.email
		return ""
	subscriber_email.short_description = "Subscriber Email"

	def subscriber_link(self, obj):
		if obj.subscribers_id:
			url = f"/admin/subscriptions/subscribers/{obj.subscribers_id}/change/"
			return format_html('<a href="{}" target="_blank">{}</a>', url, obj.subscribers.email)
		return ""
	subscriber_link.short_description = "Edit Subscriber"

class ListsAdmin(admin.ModelAdmin):
	form = ListsAdminForm
	list_display = ['list_name', 'list_description', 'admin_summary','weekly_digest','clinical_trials_notifications', 'has_latest_research']
	inlines = [SubscriberInline]
	filter_horizontal = ['subjects', 'latest_research_categories']
	fieldsets = [
		(None, {'fields': ['list_name', 'list_description', 'list_email_subject', 'team']}),
		('Email Types', {'fields': ['admin_summary', 'weekly_digest', 'clinical_trials_notifications']}),
		('Main Content', {
			'fields': ['subjects'],
			'description': 'Select subjects for which relevant articles and trials will be included in the main content of emails.'
		}),
		('Latest Research Section', {
			'fields': ['latest_research_categories'],
			'description': 'Select team categories to include in the "Latest Research" section of weekly digest emails. '
						'This section will display the latest articles for each selected category.'
		}),
	]
	
	def has_latest_research(self, obj):
		return obj.latest_research_categories.exists()
	has_latest_research.boolean = True
	has_latest_research.short_description = 'Latest Research'

class FailedNotificationAdmin(admin.ModelAdmin):
	list_display = ['subscriber','reason','list','created_at']
	list_filter = ['subscriber','list']
	readonly_fields = ['subscriber','reason','list']
admin.site.register(FailedNotification,FailedNotificationAdmin)
admin.site.register(Lists, ListsAdmin)