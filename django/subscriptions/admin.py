from django.contrib import admin
from django.utils.html import format_html
from django.urls import path, reverse
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
import csv
from django.db.models import Count, Q
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth
from django.utils import timezone
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.contrib import messages
from datetime import timedelta
from django import forms
import bleach
from .models import Subscribers, Lists, FailedNotification, ListSubscription, SubscriberSiteProfile, Announcement, AnnouncementRecipient
from .forms import ListsAdminForm, AnnouncementAdminForm
from gregory.models import Team

# Allowlist for announcement body HTML (used by bleach sanitization)
_ANNOUNCEMENT_ALLOWED_TAGS = [
	'p', 'strong', 'em', 'u', 's', 'ul', 'ol', 'li',
	'a', 'h2', 'h3', 'h4', 'blockquote', 'br', 'hr',
]
_ANNOUNCEMENT_ALLOWED_ATTRS = {
	'a': ['href', 'target', 'rel'],
}


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


class SubscriptionListFilter(admin.SimpleListFilter):
	"""Filter subscribers by the list they are subscribed to."""
	title = 'list'
	parameter_name = 'list'

	def lookups(self, request, model_admin):
		from gregory.admin import get_user_organizations
		user_orgs = get_user_organizations(request.user)
		if user_orgs is not None:
			qs = Lists.objects.filter(team__organization__id__in=user_orgs)
		else:
			qs = Lists.objects.all()
		return qs.values_list('list_id', 'list_name').order_by('list_name')

	def queryset(self, request, queryset):
		if self.value():
			return queryset.filter(list_subscriptions__list_id=self.value())
		return queryset


class SubscriberAdmin(admin.ModelAdmin):
	list_display = ['first_name', 'last_name', 'email', 'active', 'list_names', 'number_of_subscriptions', 'created_at']
	list_filter = ['active', SubscriptionListFilter, 'created_at']
	search_fields = ['first_name', 'last_name', 'email']
	actions = ['make_active', 'make_inactive', 'export_csv', 'add_to_list']
	readonly_fields = ['created_at', 'updated_at', 'unsubscribe_token']
	inlines = [SubscriberSiteProfileInline, ListSubscriptionInline]

	def get_queryset(self, request):
		from gregory.admin import get_user_organizations
		qs = super().get_queryset(request)
		qs = qs.annotate(
			active_subscription_count=Count(
				'list_subscriptions',
				filter=Q(list_subscriptions__is_active=True),
				distinct=True,
			)
		).prefetch_related('list_subscriptions__list')
		user_orgs = get_user_organizations(request.user)
		if user_orgs is not None:
			qs = qs.filter(list_subscriptions__list__team__organization__id__in=user_orgs).distinct()
		return qs

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
		# Determine range and granularity from query param
		range_param = request.GET.get('range', '30d')
		RANGES = {
			'7d':   (7,   TruncDate,  '%Y-%m-%d'),
			'30d':  (30,  TruncDate,  '%Y-%m-%d'),
			'90d':  (90,  TruncWeek,  '%Y-%m-%d'),
			'365d': (365, TruncMonth, '%Y-%m'),
		}
		if range_param not in RANGES:
			range_param = '30d'
		days, trunc_fn, date_fmt = RANGES[range_param]

		end_date = timezone.now().date()
		start_date = end_date - timedelta(days=days - 1)

		# Build full period sequence for zero-filling
		if trunc_fn is TruncDate:
			period_range = []
			d = start_date
			while d <= end_date:
				period_range.append(d)
				d += timedelta(days=1)
		elif trunc_fn is TruncWeek:
			# ISO week starts on Monday
			from datetime import date
			period_range = []
			d = start_date - timedelta(days=start_date.weekday())
			while d <= end_date:
				period_range.append(d)
				d += timedelta(weeks=1)
		else:  # TruncMonth
			from datetime import date
			period_range = []
			d = start_date.replace(day=1)
			while d <= end_date:
				period_range.append(d)
				# advance to first day of next month
				if d.month == 12:
					d = date(d.year + 1, 1, 1)
				else:
					d = date(d.year, d.month + 1, 1)

		def _build_series(qs, date_field):
			counts = (
				qs
				.annotate(period=trunc_fn(date_field))
				.values('period')
				.annotate(count=Count('id'))
				.order_by('period')
			)
			lookup = {item['period']: item['count'] for item in counts if item['period']}
			return [lookup.get(p, 0) for p in period_range]

		# New subscribers
		new_subscribers_qs = Subscribers.objects.filter(
			created_at__date__gte=start_date,
			created_at__date__lte=end_date,
		)
		new_subscribers_data = _build_series(new_subscribers_qs, 'created_at')

		# New subscriptions (list joins)
		new_subs_qs = ListSubscription.objects.filter(
			subscribed_at__date__gte=start_date,
			subscribed_at__date__lte=end_date,
		)
		new_subscriptions_data = _build_series(new_subs_qs, 'subscribed_at')

		# Unsubscriptions
		unsubs_qs = ListSubscription.objects.filter(
			unsubscribed_at__date__gte=start_date,
			unsubscribed_at__date__lte=end_date,
		)
		unsubscriptions_data = _build_series(unsubs_qs, 'unsubscribed_at')

		labels = [p.strftime(date_fmt) for p in period_range]

		return JsonResponse({
			'labels': labels,
			'new_subscribers': new_subscribers_data,
			'new_subscriptions': new_subscriptions_data,
			'unsubscriptions': unsubscriptions_data,
			'totals': {
				'new_subscribers': sum(new_subscribers_data),
				'new_subscriptions': sum(new_subscriptions_data),
				'unsubscriptions': sum(unsubscriptions_data),
			},
		})

	def changelist_view(self, request, extra_context=None):
		extra_context = extra_context or {}
		extra_context['analytics_url'] = reverse('admin:subscriptions_subscribers_analytics')
		return super().changelist_view(request, extra_context)

	def list_names(self, obj):
		names = [
			ls.list.list_name
			for ls in obj.list_subscriptions.all()
			if ls.is_active and ls.list_id
		]
		return ', '.join(names) if names else '—'
	list_names.short_description = 'Lists'

	def number_of_subscriptions(self, obj):
		return obj.active_subscription_count
	number_of_subscriptions.short_description = 'Subscriptions'
	number_of_subscriptions.admin_order_field = 'active_subscription_count'

	def make_active(self, request, queryset):
		updated_count = queryset.update(active=True)
		self.message_user(request, f"{updated_count} subscriber(s) marked as active.")
	make_active.short_description = "Mark selected subscribers as active"

	def make_inactive(self, request, queryset):
		updated_count = queryset.update(active=False)
		self.message_user(request, f"{updated_count} subscriber(s) marked as inactive.")
	make_inactive.short_description = "Mark selected subscribers as inactive"

	@admin.action(description="Export selected subscribers as CSV")
	def export_csv(self, request, queryset):
		response = HttpResponse(content_type='text/csv')
		response['Content-Disposition'] = 'attachment; filename="subscribers.csv"'
		writer = csv.writer(response)
		writer.writerow(['email', 'first_name', 'last_name', 'active', 'lists', 'created_at'])
		qs = queryset.prefetch_related('list_subscriptions__list')
		for sub in qs:
			active_lists = ', '.join(
				ls.list.list_name
				for ls in sub.list_subscriptions.all()
				if ls.is_active and ls.list_id
			)
			writer.writerow([
				sub.email,
				sub.first_name,
				sub.last_name or '',
				sub.active,
				active_lists,
				sub.created_at.strftime('%Y-%m-%d %H:%M'),
			])
		return response

	@admin.action(description="Add selected subscribers to a list…")
	def add_to_list(self, request, queryset):
		from gregory.admin import get_user_organizations

		class AddToListForm(forms.Form):
			target_list = forms.ModelChoiceField(
				queryset=Lists.objects.none(),
				label='Target list',
				help_text='Selected subscribers will be added to this list.',
			)

		user_orgs = get_user_organizations(request.user)
		if user_orgs is not None:
			available_lists = Lists.objects.filter(team__organization__id__in=user_orgs)
		else:
			available_lists = Lists.objects.all()

		if 'apply' not in request.POST:
			form = AddToListForm()
			form.fields['target_list'].queryset = available_lists
			return render(
				request,
				'admin/subscriptions/subscribers/add_to_list_intermediate.html',
				{
					'title': 'Add subscribers to list',
					'objects': queryset,
					'form': form,
					'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
				},
			)

		form = AddToListForm(request.POST)
		form.fields['target_list'].queryset = available_lists
		if not form.is_valid():
			self.message_user(request, 'Invalid form — please try again.', level=messages.ERROR)
			return

		target_list = form.cleaned_data['target_list']
		added = 0
		for sub in queryset:
			_, created = ListSubscription.objects.get_or_create(
				subscriber=sub,
				list=target_list,
				defaults={
					'consent_method': 'admin',
					'is_active': True,
				},
			)
			if created:
				added += 1
			else:
				# Re-activate if they previously unsubscribed
				ListSubscription.objects.filter(
					subscriber=sub, list=target_list, is_active=False
				).update(is_active=True, unsubscribed_at=None)
		self.message_user(
			request,
			f"{added} subscriber(s) added to '{target_list}' ({queryset.count() - added} already subscribed).",
		)

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
			url = reverse('admin:subscriptions_subscribers_change', args=[obj.subscriber_id])
			return format_html('<a href="{}" target="_blank">{} {}</a>', url, obj.subscriber.first_name, obj.subscriber.last_name)
		return ''
	subscriber_link.short_description = 'Subscriber'


class ListsAdmin(admin.ModelAdmin):
	form = ListsAdminForm
	list_display = ['list_name', 'organisation_name', 'team', 'list_description', 'admin_summary','weekly_digest','clinical_trials_notifications', 'has_latest_research', 'subscriber_count']
	inlines = [ListSubscriberInline]
	filter_horizontal = ['subjects', 'latest_research_categories']
	actions = ['reassign_to_team_action']
	fieldsets = [
		(None, {'fields': ['list_name', 'list_description', 'list_email_subject', 'team', 'site', 'allowed_domains']}),
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

	@admin.action(description="Reassign selected lists to another team…")
	def reassign_to_team_action(self, request, queryset):
		class ReassignListsForm(forms.Form):
			target_team = forms.ModelChoiceField(
				queryset=Team.objects.none(),
				label="Target team",
				help_text="Selected lists will be moved to this team.",
			)

		# Safety: lists without a team have no organisation and cannot be reassigned.
		if queryset.filter(team__isnull=True).exists():
			self.message_user(
				request,
				"Some selected lists have no team assigned and cannot be reassigned.",
				level=messages.ERROR,
			)
			return

		# Safety: all selected lists must belong to the same organisation.
		org_ids = list(
			queryset.values_list('team__organization_id', flat=True).distinct()
		)
		if len(org_ids) != 1:
			self.message_user(
				request,
				"Selected lists span multiple organisations. "
				"Please select only lists from a single organisation.",
				level=messages.ERROR,
			)
			return

		target_qs = Team.objects.filter(organization_id=org_ids[0])

		if 'apply' not in request.POST:
			form = ReassignListsForm()
			form.fields['target_team'].queryset = target_qs
			return render(
				request,
				'admin/gregory/reassign_to_team_intermediate.html',
				{
					'title': 'Reassign lists to team',
					'objects': queryset,
					'form': form,
					'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
					'model_name': 'lists',
				},
			)

		form = ReassignListsForm(request.POST)
		form.fields['target_team'].queryset = target_qs
		if not form.is_valid():
			self.message_user(request, "Invalid form — please try again.", level=messages.ERROR)
			return
		to_team = form.cleaned_data['target_team']
		# Final guard: target team must belong to the same organisation.
		if to_team.organization_id != org_ids[0]:
			self.message_user(
				request,
				"Target team does not belong to the same organisation as the selected lists.",
				level=messages.ERROR,
			)
			return
		count = queryset.count()
		queryset.update(team=to_team)
		self.message_user(request, f"{count} list(s) reassigned to '{to_team}'.")

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

	def _render_announcement_email(self, announcement, subscriber=None, site=None, list_id=None, custom_settings=None):
		"""Render announcement as HTML email using the base template."""
		safe_body = bleach.clean(
			announcement.body,
			tags=_ANNOUNCEMENT_ALLOWED_TAGS,
			attributes=_ANNOUNCEMENT_ALLOWED_ATTRS,
			strip=True,
		)
		context = {
			'announcement_subject': announcement.subject,
			'announcement_body': safe_body,
			'email_type': 'announcement',
			'show_date': True,
			'current_date': timezone.now(),
			'header_title': announcement.header_title,
			'header_tagline': announcement.header_tagline,
			'title': getattr(custom_settings, 'title', 'Gregory AI') if custom_settings else 'Gregory AI',
			'email_footer': getattr(custom_settings, 'email_footer', '') if custom_settings else '',
			'website_url': getattr(custom_settings, 'website_url', '') if custom_settings else '',
			'support_url': getattr(custom_settings, 'support_url', '') if custom_settings else '',
			'about_url': getattr(custom_settings, 'about_url', '') if custom_settings else '',
			'contact_url': getattr(custom_settings, 'contact_url', '') if custom_settings else '',
			'bluesky_url': getattr(custom_settings, 'bluesky_url', '') if custom_settings else '',
			'github_url': getattr(custom_settings, 'github_url', '') if custom_settings else '',
			'mastodon_url': getattr(custom_settings, 'mastodon_url', '') if custom_settings else '',
			'privacy_policy_url': '',
			'terms_url': '',
		}
		if subscriber:
			context['subscriber'] = subscriber
		if site:
			_api_domain = getattr(custom_settings, 'api_domain', '') if custom_settings else ''
			_api_domain = _api_domain or site.domain
			_scheme = 'https' if _api_domain not in ('localhost', '127.0.0.1') else 'http'
			context['unsubscribe_base_url'] = f"{_scheme}://{_api_domain}"
			context['site'] = site
		if list_id:
			context['list_id'] = list_id
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
				site, custom_settings = get_site_and_settings(first_list.team, list_obj=first_list)
				api_token, api_url = get_postmark_credentials(custom_settings=custom_settings, organization=first_list.team.organization)
			except Exception:
				api_token, api_url, site, custom_settings = None, None, None, None

			html = self._render_announcement_email(announcement, site=site, custom_settings=custom_settings)
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

		if announcement.status in ('sent', 'sending'):
			messages.warning(request, "This announcement has already been sent.")
			return redirect(reverse('admin:subscriptions_announcement_change', args=[announcement.pk]))

		target_lists = announcement.lists.select_related('team__organization').all()

		# Build subscriber info per list for display and sending
		list_info = []
		all_subscribers = {}  # email -> (subscriber, list) — deduplicate by email
		for lst in target_lists:
			active_subs = list(Subscribers.objects.filter(
				list_subscriptions__list=lst,
				list_subscriptions__is_active=True,
				active=True,
			).distinct())
			list_info.append({
				'list': lst,
				'subscriber_count': len(active_subs),
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

			# Pre-compute credentials per list to avoid re-fetching for every subscriber
			list_credentials = {}
			for _email, (_sub, _lst) in all_subscribers.items():
				pk = _lst.list_id
				if pk not in list_credentials:
					try:
						_site, _cs = get_site_and_settings(_lst.team, list_obj=_lst)
						_api_token, _api_url = get_postmark_credentials(custom_settings=_cs, organization=_lst.team.organization)
					except Exception:
						_api_token, _api_url, _site, _cs = None, None, None, None
					list_credentials[pk] = (_api_token, _api_url, _site, _cs)

			# Group subscribers by the list (for credentials resolution)
			# Use the list from which we first encountered them
			for email, (subscriber, lst) in all_subscribers.items():
				api_token, api_url, site, custom_settings = list_credentials.get(lst.list_id, (None, None, None, None))

				html = self._render_announcement_email(announcement, subscriber=subscriber, site=site, list_id=lst.list_id, custom_settings=custom_settings)
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

				AnnouncementRecipient.objects.update_or_create(
					announcement=announcement,
					subscriber=subscriber,
					defaults={
						'list': lst,
						'success': success,
						'error_message': error_msg,
					},
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
