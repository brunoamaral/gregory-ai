from django.contrib import admin
from django.utils.html import format_html
from django.urls import path, reverse
from django.shortcuts import render
from django.http import JsonResponse
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta
from .models import Subscribers, Lists, FailedNotification
from .forms import ListsAdminForm

class SubscriberAdmin(admin.ModelAdmin):
	list_display = ['subscriber_id', 'first_name', 'last_name', 'email', 'active', 'number_of_subscriptions', 'created_at', 'updated_at']
	list_filter = ['active', 'profile', 'created_at', 'updated_at']
	search_fields = ['first_name', 'last_name', 'email']
	actions = ['make_active', 'make_inactive']
	readonly_fields = ['created_at', 'updated_at']

	def get_urls(self):
		urls = super().get_urls()
		custom_urls = [
			path('analytics/', self.admin_site.admin_view(self.analytics_view), name='subscriptions_subscribers_analytics'),
			path('analytics/data/', self.admin_site.admin_view(self.analytics_data), name='subscriptions_subscribers_analytics_data'),
		]
		return custom_urls + urls

	def analytics_view(self, request):
		context = {
			'title': 'Subscriber Analytics',
			'opts': self.model._meta,
			'has_view_permission': self.has_view_permission(request),
		}
		return render(request, 'admin/subscriptions/subscribers/analytics.html', context)

	def analytics_data(self, request):
		# Get data for the last 30 days
		end_date = timezone.now().date()
		start_date = end_date - timedelta(days=29)  # 30 days including today
		
		# Create a list of all dates in the range
		date_range = []
		current_date = start_date
		while current_date <= end_date:
			date_range.append(current_date)
			current_date += timedelta(days=1)
		
		# Get subscriber counts by date
		subscriber_counts = (
			Subscribers.objects
			.filter(created_at__date__gte=start_date, created_at__date__lte=end_date)
			.extra(select={'date': 'DATE(created_at)'})
			.values('date')
			.annotate(count=Count('subscriber_id'))
			.order_by('date')
		)
		
		# Create a dictionary for easy lookup
		counts_dict = {item['date']: item['count'] for item in subscriber_counts}
		
		# Prepare data for the chart
		labels = [date.strftime('%Y-%m-%d') for date in date_range]
		data = [counts_dict.get(date, 0) for date in date_range]
		
		return JsonResponse({
			'labels': labels,
			'data': data,
			'total_new_subscribers': sum(data),
		})

	def changelist_view(self, request, extra_context=None):
		extra_context = extra_context or {}
		extra_context['analytics_url'] = reverse('admin:subscriptions_subscribers_analytics')
		return super().changelist_view(request, extra_context)

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
	list_display = ['list_name', 'team', 'list_description', 'admin_summary','weekly_digest','clinical_trials_notifications', 'has_latest_research']
	inlines = [SubscriberInline]
	filter_horizontal = ['subjects', 'latest_research_categories']
	fieldsets = [
		(None, {'fields': ['list_name', 'list_description', 'list_email_subject', 'team']}),
		('Email Types', {'fields': ['admin_summary', 'weekly_digest', 'clinical_trials_notifications']}),
		('Content Settings', {
			'fields': ['article_limit', 'ml_threshold'],
			'description': 'Configure content limits and ML prediction thresholds for weekly digest emails. '
						'The ML threshold determines the minimum confidence level required for ML predictions to be considered relevant.'
		}),
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