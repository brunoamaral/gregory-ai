from django.contrib import admin
from django.utils.html import format_html
from django.urls import path, reverse
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.db.models import Count, Q
from django.utils import timezone
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.contrib import messages
from datetime import timedelta
from .models import Subscribers, Lists, FailedNotification, ListSubscription, SubscriberSiteProfile, Announcement, AnnouncementRecipient
from .forms import ListsAdminForm, AnnouncementAdminForm


class SubscriberSiteProfileInline(admin.TabularInline):
	model = SubscriberSiteProfile
	extra = 0
	fields = ['site', 'profile', 'created_at', 'updated_at']
	readonly_fields = ['created_at', 'updated_at']
	verbose_name = 'Site Profile'
	verbose_name_plural = 'Site Profiles'


class ListSubscriptionInline(admin.TabularInline):
	"""Shows which lists this subscriber belongs to, with consent metadata."""
	model = ListSubscription
	extra = 0
	fields = ['list', 'is_active', 'consent_method', 'consent_source_site', 'consent_ip', 'subscribed_at', 'unsubscribed_at']
	readonly_fields = ['consent_ip', 'consent_source_site', 'consent_method', 'subscribed_at', 'unsubscribed_at']
	verbose_name = 'List Subscription'
	verbose_name_plural = 'List Subscriptions'


class SubscriberAdmin(admin.ModelAdmin):
	list_display = ['subscriber_id', 'first_name', 'last_name', 'email', 'active', 'number_of_subscriptions', 'created_at', 'updated_at']
	list_filter = ['active', 'profile', 'created_at', 'updated_at']
	search_fields = ['first_name', 'last_name', 'email']
	actions = ['make_active', 'make_inactive']
	readonly_fields = ['created_at', 'updated_at', 'unsubscribe_token']
	inlines = [SubscriberSiteProfileInline, ListSubscriptionInline]

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


class ListSubscriberInline(admin.TabularInline):
	"""Shows subscribers for a given list, with consent metadata."""
	model = ListSubscription
	extra = 0
	can_delete = True
	verbose_name = 'Subscriber'
	verbose_name_plural = 'Subscribers'
	fields = ['subscriber_link', 'subscriber_email', 'is_active', 'consent_method', 'subscribed_at']
	readonly_fields = ['subscriber_link', 'subscriber_email', 'consent_method', 'subscribed_at']

	def subscriber_email(self, obj):
		if obj.subscriber_id:
			return obj.subscriber.email
		return ''
	subscriber_email.short_description = 'Email'

	def subscriber_link(self, obj):
		if obj.subscriber_id:
			url = f"/admin/subscriptions/subscribers/{obj.subscriber_id}/change/"
			return format_html('<a href="{}" target="_blank">{} {}</a>', url, obj.subscriber.first_name, obj.subscriber.last_name)
		return ''
	subscriber_link.short_description = 'Subscriber'


class ListsAdmin(admin.ModelAdmin):
	form = ListsAdminForm
	list_display = ['list_name', 'organisation_name', 'team', 'list_description', 'admin_summary','weekly_digest','clinical_trials_notifications', 'has_latest_research', 'subscriber_count']
	inlines = [ListSubscriberInline]
	filter_horizontal = ['subjects', 'latest_research_categories']
	fieldsets = [
		(None, {'fields': ['list_name', 'list_description', 'list_email_subject', 'team', 'allowed_domains']}),
					# allowed_domains: comma-separated domains (e.g. example.com) whose subscription
					# forms are permitted to add subscribers to this list and receive redirects.
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

	def subscriber_count(self, obj):
		return obj.list_subscriptions.filter(is_active=True).count()
	subscriber_count.short_description = 'Subscribers'
	subscriber_count.admin_order_field = 'active_subscriber_count'

	def get_queryset(self, request):
		from django.db.models import Count, Q
		qs = super().get_queryset(request)
		return qs.annotate(active_subscriber_count=Count(
			'list_subscriptions',
			filter=Q(list_subscriptions__is_active=True),
			distinct=True,
		))

	def organisation_name(self, obj):
		if obj.team and obj.team.organization:
			return obj.team.organization.name
		return ''
	organisation_name.short_description = 'Organisation'
	organisation_name.admin_order_field = 'team__organization__name'

class FailedNotificationAdmin(admin.ModelAdmin):
	list_display = ['subscriber','reason','list','created_at']
	list_filter = ['subscriber','list']
	readonly_fields = ['subscriber','reason','list']
admin.site.register(FailedNotification,FailedNotificationAdmin)
admin.site.register(Lists, ListsAdmin)


@admin.register(SubscriberSiteProfile)
class SubscriberSiteProfileAdmin(admin.ModelAdmin):
	list_display = ['subscriber', 'site', 'profile', 'created_at']
	list_filter = ['site', 'profile']
	search_fields = ['subscriber__email', 'subscriber__first_name', 'subscriber__last_name']
	readonly_fields = ['created_at', 'updated_at']


@admin.register(ListSubscription)
class ListSubscriptionAdmin(admin.ModelAdmin):
	list_display = ['subscriber', 'list', 'is_active', 'consent_method', 'subscribed_at', 'unsubscribed_at']
	list_filter = ['is_active', 'consent_method', 'subscribed_at']
	search_fields = ['subscriber__email', 'list__list_name']
	readonly_fields = ['subscribed_at', 'consent_ip', 'consent_source_site', 'consent_method', 'unsubscribed_at']


class AnnouncementRecipientInline(admin.TabularInline):
	model = AnnouncementRecipient
	extra = 0
	readonly_fields = ['subscriber', 'list', 'sent_at', 'success', 'error_message']
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
	form = AnnouncementAdminForm
	list_display = ['subject', 'status_badge', 'created_by', 'created_at', 'sent_at', 'recipients_count', 'failures_count']
	list_filter = ['status', 'created_at']
	search_fields = ['subject']
	readonly_fields = ['status', 'sent_at', 'recipients_count', 'failures_count', 'created_by', 'created_at']
	inlines = [AnnouncementRecipientInline]

	fieldsets = [
		(None, {'fields': ['subject']}),
		('Email Header', {
			'fields': ['header_title', 'header_tagline'],
			'description': 'Optionally override the title (defaults to "Gregory AI") and the tagline shown in the email header.',
		}),
		('Body', {'fields': ['body']}),
		('Destination', {'fields': ['lists']}),

		('Send Status', {
			'fields': ['status', 'created_by', 'created_at', 'sent_at', 'recipients_count', 'failures_count'],
			'classes': ['collapse'],
		}),
	]

	def status_badge(self, obj):
		colors = {
			'draft': '#6b7280',
			'sending': '#f59e0b',
			'sent': '#10b981',
			'failed': '#ef4444',
		}
		color = colors.get(obj.status, '#6b7280')
		return format_html(
			'<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 10px; font-size: 11px;">{}</span>',
			color, obj.get_status_display()
		)
	status_badge.short_description = 'Status'
	status_badge.admin_order_field = 'status'

	def get_form(self, request, obj=None, **kwargs):
		form_class = super().get_form(request, obj, **kwargs)

		class FormWithRequest(form_class):
			def __init__(self, *args, **form_kwargs):
				form_kwargs['request'] = request
				super().__init__(*args, **form_kwargs)

		return FormWithRequest

	def get_queryset(self, request):
		qs = super().get_queryset(request)
		if request.user.is_superuser:
			return qs
		from gregory.admin import get_user_organizations
		user_orgs = get_user_organizations(request.user)
		if user_orgs is not None:
			return qs.filter(lists__team__organization__id__in=user_orgs).distinct()
		return qs

	def get_readonly_fields(self, request, obj=None):
		readonly = list(super().get_readonly_fields(request, obj))
		if obj and obj.status == 'sent':
			readonly.extend(['subject', 'header_title', 'header_tagline', 'body', 'lists'])
		return readonly

	def save_model(self, request, obj, form, change):
		if not change:
			obj.created_by = request.user
		super().save_model(request, obj, form, change)

	def get_urls(self):
		urls = super().get_urls()
		custom_urls = [
			path(
				'<int:announcement_id>/preview/',
				self.admin_site.admin_view(self.preview_view),
				name='subscriptions_announcement_preview',
			),
			path(
				'<int:announcement_id>/send-test/',
				self.admin_site.admin_view(self.send_test_view),
				name='subscriptions_announcement_send_test',
			),
			path(
				'<int:announcement_id>/send/',
				self.admin_site.admin_view(self.send_view),
				name='subscriptions_announcement_send',
			),
		]
		return custom_urls + urls

	def _get_announcement_or_404(self, request, announcement_id):
		"""Get announcement and verify user has access."""
		announcement = get_object_or_404(Announcement, pk=announcement_id)
		if not request.user.is_superuser:
			from gregory.admin import get_user_organizations
			user_orgs = get_user_organizations(request.user)
			if user_orgs is not None:
				if not announcement.lists.filter(team__organization__id__in=user_orgs).exists():
					return None
		return announcement

	def _render_announcement_email(self, announcement, subscriber=None, site=None):
		"""Render announcement as HTML email using the base template."""
		context = {
			'announcement_subject': announcement.subject,
			'announcement_body': announcement.body,
			'email_type': 'announcement',
			'show_date': True,
			'header_title': announcement.header_title,
			'header_tagline': announcement.header_tagline,
		}
		if subscriber:
			context['subscriber'] = subscriber
			context['unsubscribe_base_url'] = f"https://{site.domain}/subscriptions/unsubscribe/{subscriber.unsubscribe_token}" if site else ''
		if site:
			context['site'] = site
		html = render_to_string('emails/announcement.html', context)
		return html

	def _render_announcement_text(self, announcement, subscriber=None):
		"""Render plain-text version of the announcement."""
		lines = []
		if subscriber and subscriber.first_name:
			lines.append(f"Hello {subscriber.first_name},\n")
		lines.append(strip_tags(announcement.body))
		return '\n'.join(lines)

	def preview_view(self, request, announcement_id):
		announcement = self._get_announcement_or_404(request, announcement_id)
		if announcement is None:
			from django.http import HttpResponseForbidden
			return HttpResponseForbidden("Access denied.")
		html = self._render_announcement_email(announcement)
		response = HttpResponse(html)
		response['X-Frame-Options'] = 'SAMEORIGIN'
		return response

	def send_test_view(self, request, announcement_id):
		announcement = self._get_announcement_or_404(request, announcement_id)
		if announcement is None:
			from django.http import HttpResponseForbidden
			return HttpResponseForbidden("Access denied.")

		if request.method == 'POST':
			from subscriptions.management.commands.utils.send_email import send_email
			from subscriptions.management.commands.utils.get_credentials import get_postmark_credentials, get_site_and_settings

			# Use the first list's team for credentials
			first_list = announcement.lists.select_related('team').first()
			if not first_list:
				messages.error(request, "No lists selected. Please select at least one list before sending a test.")
				return redirect(reverse('admin:subscriptions_announcement_change', args=[announcement.pk]))

			try:
				api_token, api_url = get_postmark_credentials(first_list.team)
				site, _ = get_site_and_settings(first_list.team)
			except Exception:
				api_token, api_url, site = None, None, None

			html = self._render_announcement_email(announcement, site=site)
			text = self._render_announcement_text(announcement)

			try:
				response = send_email(
					to=request.user.email,
					subject=f"[TEST] {announcement.subject}",
					html=html,
					text=text,
					site=site,
					api_token=api_token,
					api_url=api_url,
				)
				if response.status_code == 200:
					messages.success(request, f"Test email sent to {request.user.email}.")
				else:
					messages.error(request, f"Failed to send test email: {response.text}")
			except Exception as e:
				messages.error(request, f"Error sending test email: {e}")

			return redirect(reverse('admin:subscriptions_announcement_change', args=[announcement.pk]))

		# GET — show confirmation form
		context = {
			**self.admin_site.each_context(request),
			'announcement': announcement,
			'user_email': request.user.email,
			'title': f'Send Test: {announcement.subject}',
			'opts': self.model._meta,
		}
		return render(request, 'admin/subscriptions/announcement/send_test.html', context)

	def send_view(self, request, announcement_id):
		announcement = self._get_announcement_or_404(request, announcement_id)
		if announcement is None:
			from django.http import HttpResponseForbidden
			return HttpResponseForbidden("Access denied.")

		if announcement.status == 'sent':
			messages.warning(request, "This announcement has already been sent.")
			return redirect(reverse('admin:subscriptions_announcement_change', args=[announcement.pk]))

		target_lists = announcement.lists.select_related('team__organization').prefetch_related(
			'list_subscriptions__subscriber'
		).all()

		# Build subscriber info per list for display and sending
		list_info = []
		all_subscribers = {}  # email -> (subscriber, list) — deduplicate by email
		for lst in target_lists:
			active_subs = Subscribers.objects.filter(
				list_subscriptions__list=lst,
				list_subscriptions__is_active=True,
				active=True,
			).distinct()
			list_info.append({
				'list': lst,
				'subscriber_count': active_subs.count(),
			})
			for sub in active_subs:
				if sub.email not in all_subscribers:
					all_subscribers[sub.email] = (sub, lst)

		if request.method == 'POST':
			from subscriptions.management.commands.utils.send_email import send_email
			from subscriptions.management.commands.utils.get_credentials import get_postmark_credentials, get_site_and_settings

			announcement.status = 'sending'
			announcement.save(update_fields=['status'])

			success_count = 0
			failure_count = 0

			# Group subscribers by the list (for credentials resolution)
			# Use the list from which we first encountered them
			for email, (subscriber, lst) in all_subscribers.items():
				try:
					api_token, api_url = get_postmark_credentials(lst.team)
					site, _ = get_site_and_settings(lst.team)
				except Exception:
					api_token, api_url, site = None, None, None

				html = self._render_announcement_email(announcement, subscriber=subscriber, site=site)
				text = self._render_announcement_text(announcement, subscriber=subscriber)

				error_msg = ''
				success = False
				try:
					response = send_email(
						to=subscriber.email,
						subject=announcement.subject,
						html=html,
						text=text,
						site=site,
						api_token=api_token,
						api_url=api_url,
					)
					if response.status_code == 200:
						success = True
						success_count += 1
					else:
						error_msg = response.text[:500]
						failure_count += 1
				except Exception as e:
					error_msg = str(e)[:500]
					failure_count += 1

				AnnouncementRecipient.objects.create(
					announcement=announcement,
					subscriber=subscriber,
					list=lst,
					success=success,
					error_message=error_msg,
				)

			announcement.status = 'sent' if failure_count == 0 else 'failed'
			announcement.sent_at = timezone.now()
			announcement.recipients_count = success_count
			announcement.failures_count = failure_count
			announcement.save(update_fields=['status', 'sent_at', 'recipients_count', 'failures_count'])

			if failure_count == 0:
				messages.success(request, f"Announcement sent to {success_count} subscriber(s).")
			else:
				messages.warning(
					request,
					f"Announcement sent to {success_count} subscriber(s) with {failure_count} failure(s)."
				)
			return redirect(reverse('admin:subscriptions_announcement_change', args=[announcement.pk]))

		# GET — show confirmation page
		context = {
			**self.admin_site.each_context(request),
			'announcement': announcement,
			'list_info': list_info,
			'total_subscribers': len(all_subscribers),
			'title': f'Confirm Send: {announcement.subject}',
			'opts': self.model._meta,
		}
		return render(request, 'admin/subscriptions/announcement/send_confirm.html', context)

	def change_view(self, request, object_id, form_url='', extra_context=None):
		extra_context = extra_context or {}
		try:
			announcement = Announcement.objects.get(pk=object_id)
			extra_context['show_send_buttons'] = announcement.status == 'draft'
		except Announcement.DoesNotExist:
			pass
		return super().change_view(request, object_id, form_url, extra_context=extra_context)
