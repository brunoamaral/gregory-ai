from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import path, reverse
import csv
from simple_history.admin import SimpleHistoryAdmin  # Import SimpleHistoryAdmin
from .admin_filters import DateRangeFilter, SourceHealthFilter
from django.db import models  # Add this import for models.Count
from django.utils import timezone
from django import forms
from django.utils.html import format_html, mark_safe

from .models import (
    Articles, Trials, Sources, Entities, Authors, Subject, MLPredictions, 
    ArticleSubjectRelevance, TeamCategory, TeamCredentials, PredictionRunLog, Team,
    ArticleTrialReference
)
from .widgets import MLPredictionsWidget
from django import forms
from .fields import MLPredictionsField
from django.utils.html import format_html


def get_user_organizations(user):
	"""Get the list of organization IDs for a user."""
	if user.is_superuser:
		return None  # None means all organizations
	return user.organizations_organizationuser.values_list('organization__id', flat=True)


class OrganizationRestrictedFieldListFilter(admin.RelatedFieldListFilter):
	"""Custom filter that restricts related field choices to user's organization."""
	
	def field_choices(self, field, request, model_admin):
		"""Override to filter choices based on user's organization."""
		choices = super().field_choices(field, request, model_admin)
		
		if request.user.is_superuser:
			return choices
		
		user_orgs = get_user_organizations(request.user)
		
		# Filter the choices based on organization
		filtered_choices = []
		for choice_value, choice_label in choices:
			if choice_value is None:
				# Include the empty choice
				filtered_choices.append((choice_value, choice_label))
				continue
			
			try:
				# Get the object for this choice
				obj = field.related_model.objects.get(pk=choice_value)
				
				# Check if it belongs to user's organization
				if hasattr(obj, 'organization'):
					if obj.organization_id in user_orgs:
						filtered_choices.append((choice_value, choice_label))
				elif hasattr(obj, 'team'):
					if obj.team.organization_id in user_orgs:
						filtered_choices.append((choice_value, choice_label))
				else:
					# If no organization field, include it
					filtered_choices.append((choice_value, choice_label))
			except:
				pass
		
		return filtered_choices


class OrganizationFilterMixin:
	"""
	Mixin to restrict admin queryset visibility based on user's organization.
	Superusers see everything; staff users only see objects from their organization.
	"""
	
	def get_queryset(self, request):
		"""Filter queryset by organization for non-superusers."""
		qs = super().get_queryset(request)
		
		# Superusers see everything
		if request.user.is_superuser:
			return qs
		
		# Staff users only see objects from their organization
		user_orgs = get_user_organizations(request.user)
		
		# Try to filter by organization directly
		if hasattr(qs.model, 'organization'):
			return qs.filter(organization__id__in=user_orgs)
		
		# If the model has a team relationship, filter by team's organization
		if hasattr(qs.model, 'team'):
			return qs.filter(team__organization__id__in=user_orgs)
		
		# If the model has multiple teams (M2M), filter by any team's organization
		try:
			# Check if 'teams' is a M2M field
			qs.model._meta.get_field('teams')
			return qs.filter(teams__organization__id__in=user_orgs).distinct()
		except:
			pass
		
		return qs


# @admin.register(PredictionRunLog)
class PredictionRunLogAdmin(admin.ModelAdmin):
		list_display = ['id', 'team', 'subject', 'run_type', 'algorithm', 'model_version', 'run_started', 'run_finished', 'status_label', 'triggered_by']
		list_filter = [DateRangeFilter, 'team', 'subject', 'run_type', 'algorithm', 'success', 'model_version']
		search_fields = ['team__organization__name', 'subject__subject_name', 'model_version', 'triggered_by', 'algorithm']
		readonly_fields = ['run_started']  # Auto-populated field
		date_hierarchy = 'run_started'
		actions = ['mark_as_failed', 'mark_as_successful', 'export_as_csv']
		
		fieldsets = (
				('Run Information', {
						'fields': ('team', 'subject', 'run_type', 'algorithm', 'model_version', 'triggered_by'),
				}),
				('Status', {
						'fields': ('run_started', 'run_finished', 'success', 'error_message'),
				}),
		)


class ArticleTrialReferenceInline(admin.TabularInline):
    model = ArticleTrialReference
    extra = 0
    readonly_fields = ['trial', 'identifier_type', 'identifier_value', 'discovered_date']
    can_delete = False
    verbose_name_plural = "Referenced Trials"
    verbose_name = "Trial Reference"
    classes = ['collapse']

    def has_add_permission(self, request, obj=None):
        return False  # Prevent manual adding, should be done by the detect_trial_references command

class TrialArticleReferenceInline(admin.TabularInline):
    model = ArticleTrialReference
    extra = 0
    readonly_fields = ['article', 'identifier_type', 'identifier_value', 'discovered_date']
    can_delete = False
    verbose_name_plural = "Referencing Articles"
    verbose_name = "Article Reference"
    classes = ['collapse']
    
    def has_add_permission(self, request, obj=None):
        return False  # Prevent manual adding, should be done by the detect_trial_references command

class RelevanceRadioWidget(forms.RadioSelect):
	"""Custom radio widget with horizontal layout and emojis"""
	
	def render(self, name, value, attrs=None, renderer=None):
		from django.utils.safestring import mark_safe
		
		if attrs is None:
			attrs = {}
		
		choices_html = []
		for choice_value, choice_label in self.choices:
			# Handle None, True, False comparison properly
			if choice_value is None and value is None:
				checked = 'checked'
			elif choice_value == value:
				checked = 'checked'
			elif str(choice_value) == str(value):
				checked = 'checked'
			else:
				checked = ''
			
			choice_id = f"{attrs.get('id', name)}_{choice_value or 'none'}"
			
			choice_html = f'''
				<label style="margin-right: 15px; white-space: nowrap; cursor: pointer;">
					<input type="radio" id="{choice_id}" name="{name}" value="{choice_value if choice_value is not None else ''}" {checked} style="margin-right: 5px;">
					{choice_label}
				</label>
			'''
			choices_html.append(choice_html)
		
		final_html = f'<div style="display: flex; align-items: center; gap: 10px;">{"".join(choices_html)}</div>'
		return mark_safe(final_html)

class ArticleSubjectRelevanceForm(forms.ModelForm):
	RELEVANCE_CHOICES = [
		(None, '⚪ Not Reviewed'),
		(True, '✅ Relevant'),
		(False, '❌ Not Relevant'),
	]
	
	is_relevant = forms.ChoiceField(
		choices=RELEVANCE_CHOICES,
		widget=RelevanceRadioWidget,
		required=False,
		initial=None,
		label='Relevance'
	)
	
	class Meta:
		model = ArticleSubjectRelevance
		fields = ['subject', 'is_relevant']
		widgets = {
			'subject': forms.HiddenInput(),  # Hide the subject field since it's readonly
		}
	
	def clean_is_relevant(self):
		"""Convert string choice to boolean/None value"""
		value = self.cleaned_data.get('is_relevant')
		# Convert string representations to actual values
		if value == 'True' or value is True:
			return True
		elif value == 'False' or value is False:
			return False
		elif value == 'None' or value == '' or value is None:
			return None
		else:
			return None  # Default to "Not Reviewed"

class ArticleSubjectRelevanceInline(admin.TabularInline):
	model = ArticleSubjectRelevance
	form = ArticleSubjectRelevanceForm
	extra = 0  # Don't show extra empty forms
	can_delete = False  # Prevent deletion since we want all subjects visible
	fields = ['subject_name', 'is_relevant']  # Show subject name as readonly, then relevance
	readonly_fields = ['subject_name']
	
	def subject_name(self, obj):
		"""Display the subject name as read-only"""
		return str(obj.subject) if obj.subject else ''
	subject_name.short_description = 'Subject'
	
	def get_formset(self, request, obj=None, **kwargs):
		"""Pre-populate with all subjects for the article's teams"""
		if obj and obj.pk:  # If editing existing article
			# Get subjects for the teams this article belongs to that
			# have auto_predict enabled
			team_subjects = Subject.objects.filter(
				team__in=obj.teams.all(),
				auto_predict=True
			).distinct().order_by('subject_name')

			# Create ArticleSubjectRelevance instances for any missing subjects
			for subject in team_subjects:
				ArticleSubjectRelevance.objects.get_or_create(
					article=obj,
					subject=subject,
					defaults={'is_relevant': None}
				)
		return super().get_formset(request, obj, **kwargs)

	def get_queryset(self, request):
		"""Order by subject name for consistency and filter auto_predict subjects"""
		return super().get_queryset(request).select_related('subject').filter(
			subject__auto_predict=True
		).order_by('subject__subject_name')

class ArticleAdminForm(forms.ModelForm):
	ml_predictions_display = MLPredictionsField(required=False)

	class Meta:
		model = Articles
		fields = '__all__'
		widgets = {
			'ml_predictions': MLPredictionsWidget(),
		}

	def __init__(self, *args, **kwargs):
		self.request = kwargs.pop('request', None)
		super().__init__(*args, **kwargs)
		
		if self.instance and self.instance.pk:
			self.fields['ml_predictions_display'].initial = self.instance.ml_predictions_detail.all()
		
		# Filter team and subject choices based on user's organization
		if self.request:
			if self.request.user.is_superuser:
				self.fields['teams'].queryset = Team.objects.all()
				self.fields['subjects'].queryset = Subject.objects.all()
			else:
				user_orgs = get_user_organizations(self.request.user)
				self.fields['teams'].queryset = Team.objects.filter(organization__id__in=user_orgs)
				self.fields['subjects'].queryset = Subject.objects.filter(team__organization__id__in=user_orgs)

class ArticleAdmin(OrganizationFilterMixin, SimpleHistoryAdmin):
	form = ArticleAdminForm
	inlines = [ArticleSubjectRelevanceInline, ArticleTrialReferenceInline]
	fieldsets = (
		('Article Information', {
			'fields': (
				'title', 'link', 'doi', 'summary','summary_plain_english', 'teams', 'subjects', 'sources',
				'published_date', 'discovery_date', 'authors', 'team_categories',
				'entities', 'kind', 'access',
				'publisher', 'container_title', 'crossref_check', 'takeaways',
			),
			'description': 'This section contains general information about the article'
		}),
		('Machine Learning Relevancy Predictions per Subject', {
			'fields': ('ml_predictions_display',),
			'description': 'Grouping machine learning prediction indicators',
			'classes': ('ml-predictions-section',),
		}),
	)
	list_display = ['article_id', 'title', 'discovery_date', 'display_sources']
	ordering = ['-discovery_date']
	
	@admin.display(description='Sources')
	def display_sources(self, obj):
		"""Display sources as comma-separated list."""
		return ', '.join([source.name for source in obj.sources.all()])
	
	def get_queryset(self, request):
		"""Optimize queryset with prefetch for sources."""
		qs = super().get_queryset(request)
		return qs.prefetch_related('sources')
	
	readonly_fields = ['entities', 'discovery_date']
	search_fields = ['article_id', 'title', 'doi']
	list_filter = [
		('teams', OrganizationRestrictedFieldListFilter),
		('subjects', OrganizationRestrictedFieldListFilter),
		('sources', OrganizationRestrictedFieldListFilter),
	]
	raw_id_fields = ("authors",)
	
	def get_form(self, request, obj=None, **kwargs):
		"""Pass the request to the form so it can filter field choices."""
		form_class = super().get_form(request, obj, **kwargs)
		
		class FormWithRequest(form_class):
			def __init__(self, *args, **form_kwargs):
				form_kwargs['request'] = request
				super().__init__(*args, **form_kwargs)
		
		return FormWithRequest
	
	def get_urls(self):
		from django.urls import path
		from .admin_views import article_review_status_view, update_article_relevance_ajax
		
		urls = super().get_urls()
		custom_urls = [
			path('review-status/', self.admin_site.admin_view(article_review_status_view), name='article_review_status'),
			path('update-article-relevance/', self.admin_site.admin_view(update_article_relevance_ajax), name='update_article_relevance'),
		]
		return custom_urls + urls
	
	def changelist_view(self, request, extra_context=None):
		"""Override changelist view to add a button to access review status page"""
		extra_context = extra_context or {}
		extra_context['review_status_url'] = reverse('admin:article_review_status')
		return super().changelist_view(request, extra_context=extra_context)
	
	class Media:
		css = {
			'all': ['admin/css/ml_predictions.css'],
		}

class TrialAdmin(OrganizationFilterMixin, SimpleHistoryAdmin):
	list_display = ['trial_id', 'title', 'display_identifiers', 'discovery_date', 'last_updated']
	exclude = ['ml_predictions']
	readonly_fields = ['last_updated', 'team_categories']
	inlines = [TrialArticleReferenceInline]
	search_fields = [
		'trial_id', 'title', 'summary', 'summary_plain_english', 'scientific_title',
		'primary_sponsor', 'source_register', 'recruitment_status', 'condition',
		'intervention', 'primary_outcome', 'secondary_outcome', 'inclusion_criteria',
		'exclusion_criteria', 'study_type', 'study_design', 'phase', 'countries',
		'contact_firstname', 'contact_lastname', 'contact_affiliation',
		'therapeutic_areas', 'sponsor_type', 'internal_number', 'secondary_id',
		'identifiers', 'ctg_detailed_description'
	]
	list_filter = [
		('teams', OrganizationRestrictedFieldListFilter),
		('subjects', OrganizationRestrictedFieldListFilter),
		('sources', OrganizationRestrictedFieldListFilter),
	]
	fieldsets = (
		(None, {
			'fields': ('title', 'scientific_title', 'link', 'identifiers', 'discovery_date', 'published_date', 'last_updated')
		}),
		('Description', {
			'fields': ('summary', 'summary_plain_english', 'ctg_detailed_description'),
			'classes': ('collapse',),
		}),
		('Study Details', {
			'fields': ('study_type', 'study_design', 'phase', 'recruitment_status', 'target_size', 'date_registration'),
			'classes': ('collapse',),
		}),
		('Conditions & Interventions', {
			'fields': ('condition', 'intervention', 'primary_outcome', 'secondary_outcome'),
			'classes': ('collapse',),
		}),
		('Eligibility', {
			'fields': ('inclusion_criteria', 'exclusion_criteria', 'inclusion_agemin', 'inclusion_agemax', 'inclusion_gender'),
			'classes': ('collapse',),
		}),
		('Sponsors & Contacts', {
			'fields': ('primary_sponsor', 'sponsor_type', 'contact_firstname', 'contact_lastname', 'contact_email', 'contact_tel', 'contact_affiliation'),
			'classes': ('collapse',),
		}),
		('Location & Registry', {
			'fields': ('countries', 'source_register', 'secondary_id', 'internal_number', 'other_records'),
			'classes': ('collapse',),
		}),
		('Relationships', {
			'fields': ('sources', 'teams', 'subjects', 'team_categories'),
		}),
		('EU Clinical Trials', {
			'fields': ('therapeutic_areas', 'country_status', 'trial_region', 'results_posted', 'overall_decision_date', 'countries_decision_date'),
			'classes': ('collapse',),
		}),
		('Ethics Review', {
			'fields': ('ethics_review_status', 'ethics_review_approval_date', 'ethics_review_contact_name', 'ethics_review_contact_address', 'ethics_review_contact_phone', 'ethics_review_contact_email'),
			'classes': ('collapse',),
		}),
		('Results', {
			'fields': ('results_date_completed', 'results_url_link'),
			'classes': ('collapse',),
		}),
	)

	def display_identifiers(self, obj):
		# Customize this depending on how you want to display the JSON
		if obj.identifiers:
			return ", ".join([f"{k}: {v}" for k, v in obj.identifiers.items()])
		return "No Identifiers"

	display_identifiers.short_description = "Identifiers"
class SourceInline(admin.StackedInline):
	model = Sources
	extra = 1


class SourceAdminForm(forms.ModelForm):
	"""Custom form for Source admin with organization-based field access"""
	
	class Meta:
		model = Sources
		fields = '__all__'
	
	def __init__(self, *args, **kwargs):
		self.request = kwargs.pop('request', None)
		super().__init__(*args, **kwargs)
		
		# Filter team and subject choices based on user's organization
		if self.request:
			if self.request.user.is_superuser:
				self.fields['team'].queryset = Team.objects.all()
				self.fields['subject'].queryset = Subject.objects.all()
			else:
				user_orgs = get_user_organizations(self.request.user)
				self.fields['team'].queryset = Team.objects.filter(organization__id__in=user_orgs)
				self.fields['subject'].queryset = Subject.objects.filter(team__organization__id__in=user_orgs)


class SourceAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	form = SourceAdminForm
	list_display = ['name', 'active', 'source_for', 'subject', 'last_article_date', 'article_count', 'health_status_indicator', 'has_keyword_filter']
	list_filter = [
		'active', 'source_for', 'method', SourceHealthFilter,
		('team', OrganizationRestrictedFieldListFilter),
		('subject', OrganizationRestrictedFieldListFilter),
	]
	search_fields = ['name', 'link', 'description', 'keyword_filter']
	actions = ['activate_sources', 'deactivate_sources']
	fieldsets = (
		('Basic Information', {
			'fields': ('name', 'source_for', 'method', 'active', 'link')
		}),
		('Organization', {
			'fields': ('team', 'subject')
		}),
		('Settings', {
			'fields': ('ignore_ssl', 'description')
		}),
		('Filtering (bioRxiv and medRxiv)', {
			'fields': ('keyword_filter',),
			'description': 'For bioRxiv and medRxiv sources, specify keywords to filter articles. Use comma-separated values for multiple keywords, or quoted strings for exact phrases (e.g., "multiple sclerosis", alzheimer, parkinson).'
		}),
		('ClinicalTrials.gov API Settings', {
			'fields': ('ctgov_search_condition',),
			'classes': ('ctgov-settings',),
			'description': 'Settings for ClinicalTrials.gov API sources. Enter the condition/disease to search for clinical trials.'
		}),
	)
	
	class Media:
		js = ('admin/js/source_method_toggle.js',)
	
	def get_form(self, request, obj=None, **kwargs):
		"""Pass the request to the form so it can filter field choices."""
		form_class = super().get_form(request, obj, **kwargs)
		
		class FormWithRequest(form_class):
			def __init__(self, *args, **form_kwargs):
				form_kwargs['request'] = request
				super().__init__(*args, **form_kwargs)
		
		return FormWithRequest
	
	def has_keyword_filter(self, obj):
		"""Display whether source has keyword filtering enabled."""
		return bool(obj.keyword_filter)
	has_keyword_filter.boolean = True
	has_keyword_filter.short_description = 'Has Filter'
	
	def last_article_date(self, obj):
		"""Display the date of the latest article or trial from this source."""
		if obj.source_for == 'trials':
			latest_date = obj.get_latest_trial_date()
			if latest_date:
				days_since = (timezone.now() - latest_date).days
				return f"{latest_date.strftime('%Y-%m-%d')} ({days_since} days ago)"
			return "No trials"
		else:
			latest_date = obj.get_latest_article_date()
			if latest_date:
				days_since = (timezone.now() - latest_date).days
				return f"{latest_date.strftime('%Y-%m-%d')} ({days_since} days ago)"
			return "No articles"
	last_article_date.short_description = 'Last Content'
	last_article_date.short_description = 'Last Article'
	
	def article_count(self, obj):
		"""Display the count of articles or trials from this source."""
		if obj.source_for == 'trials':
			return obj.get_trial_count()
		else:
			return obj.get_article_count()
	article_count.short_description = 'Content Count'
	
	def health_status_indicator(self, obj):
		"""Display a visual indicator of the source's health status."""
		status = obj.get_health_status()
		
		if status == "healthy":
			return format_html('<span style="color: green; font-size: 14px;">{}</span>', '●')
		elif status == "warning":
			return format_html('<span style="color: orange; font-size: 14px;">{}</span>', '●')
		elif status == "error":
			return format_html('<span style="color: red; font-size: 14px;">{}</span>', '●')
		elif status == "inactive":
			return format_html('<span style="color: gray; font-size: 14px;">{}</span>', '●')
		else:  # no_content
			return format_html('<span style="color: blue; font-size: 14px;">{}</span>', '●')
	health_status_indicator.short_description = 'Status'
	
	def activate_sources(self, request, queryset):
		"""Admin action to activate selected sources."""
		updated_count = queryset.update(active=True)
		self.message_user(
			request,
			f'Successfully activated {updated_count} source(s).'
		)
	activate_sources.short_description = "Activate selected sources"
	
	def deactivate_sources(self, request, queryset):
		"""Admin action to deactivate selected sources."""
		updated_count = queryset.update(active=False)
		self.message_user(
			request,
			f'Successfully deactivated {updated_count} source(s).'
		)
	deactivate_sources.short_description = "Deactivate selected sources"

class SourcesInline(admin.StackedInline):
	"""Inline admin for managing sources within a subject"""
	model = Sources
	extra = 1  # Show 1 empty form by default for adding new sources
	fields = ['name', 'link', 'source_for', 'method', 'active', 'keyword_filter', 'description']
	verbose_name = "New Source"
	verbose_name_plural = "Add New Source"
	
	def get_queryset(self, request):
		"""Return empty queryset to hide existing sources from inline forms"""
		return super().get_queryset(request).none()
	
	def save_formset(self, request, form, formset, change):
		"""Automatically set subject and team when saving sources"""
		instances = formset.save(commit=False)
		for instance in instances:
			# Auto-populate subject and team from the parent Subject object
			instance.subject = form.instance
			instance.team = form.instance.team
			instance.save()
		formset.save_m2m()


class SubjectAdminForm(forms.ModelForm):
	"""Custom form for Subject admin with organization-based team access"""
	
	class Meta:
		model = Subject
		fields = '__all__'
	
	def __init__(self, *args, **kwargs):
		# Get the request from kwargs if passed
		self.request = kwargs.pop('request', None)
		super().__init__(*args, **kwargs)
		
		# Filter teams based on user's organization
		if self.request:
			if self.request.user.is_superuser:
				self.fields['team'].queryset = Team.objects.all()
			else:
				user_orgs = get_user_organizations(self.request.user)
				self.fields['team'].queryset = Team.objects.filter(organization__id__in=user_orgs)

@admin.register(Subject)
class SubjectAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	list_display = ['formatted_subject_name', 'description', 'view_sources', 'team']  # Updated list display
	readonly_fields = ['linked_sources']  # Display in the edit form
	list_filter = [('team', OrganizationRestrictedFieldListFilter)]  # Add the team filter
	form = SubjectAdminForm
	inlines = [SourcesInline]  # Add the inline for managing sources
	
	def formatted_subject_name(self, obj):
		"""Display subject name with emphasis"""
		return format_html('<strong>{}</strong>', obj.subject_name)
	formatted_subject_name.short_description = 'Subject'
	formatted_subject_name.admin_order_field = 'subject_name'

	def get_form(self, request, obj=None, **kwargs):
		"""Pass the request to the form so it can check user permissions"""
		form_class = super().get_form(request, obj, **kwargs)
		
		# Create a wrapper that passes the request to the form
		class FormWithRequest(form_class):
				def __init__(self, *args, **form_kwargs):
						form_kwargs['request'] = request
						super().__init__(*args, **form_kwargs)
		
		return FormWithRequest

	def view_sources(self, obj):
		"""Display sources as clickable links in the list view."""
		sources = obj.sources_set.all()
		if sources.exists():
			links = [
				format_html(
					'<a href="{}">{}</a>',
					reverse('admin:gregory_sources_change', args=[source.source_id]),
					source.name
				)
				for source in sources
			]
			return mark_safe('<br>'.join(links))
		return "No sources"

	view_sources.short_description = "Linked Sources"

	def linked_sources(self, obj):
		"""Display sources as clickable links in the form view."""
		sources = obj.sources_set.all()
		if sources.exists():
			links = [
				format_html(
					'<a href="{}">{}</a>',
					reverse('admin:gregory_sources_change', args=[source.source_id]),
					source.name
				)
				for source in sources
			]
			return mark_safe('<br>'.join(links))
		return "No sources"

	linked_sources.short_description = "Linked Sources"


class AuthorArticlesInline(admin.TabularInline):
	model = Articles.authors.through
	verbose_name = "Article"
	verbose_name_plural = "Author's Articles"
	extra = 0
	can_delete = False
	fields = ['article_info', 'article_summary', 'article_doi_link', 'admin_link']
	readonly_fields = ['article_info', 'article_summary', 'article_doi_link', 'admin_link']
	ordering = ['-articles__published_date']  # Order by most recent articles first
	
	def article_info(self, obj):
		try:
			article = obj.articles
			title = article.title
			published_date = article.published_date
			if published_date:
				return format_html('{}<br/><span style="color: #666; font-size: 0.8em;">Published: {}</span>', 
						title, published_date.strftime('%Y-%m-%d'))
			return title
		except Exception as e:
			return f"Error accessing article: {str(e)}"
	article_info.short_description = 'Title'
	
	def article_summary(self, obj):
		try:
			article = obj.articles
			summary = article.summary
			if summary:
				return summary[:200] + '...' if len(summary) > 200 else summary
			return '-'
		except Exception as e:
			return f"Error accessing article summary: {str(e)}"
	article_summary.short_description = 'Summary'
	
	def article_doi_link(self, obj):
		try:
			article = obj.articles
			if article.doi:
				doi_link = f"https://doi.org/{article.doi}"
				return format_html('<a href="{}" target="_blank">{}</a>', doi_link, article.doi)
			elif article.link:
				return format_html('<a href="{}" target="_blank">Link to article</a>', article.link)
			return '-'
		except Exception as e:
			return f"Error accessing article link: {str(e)}"
	article_doi_link.short_description = 'External Link'
	
	def admin_link(self, obj):
		try:
			article = obj.articles
			url = reverse('admin:gregory_articles_change', args=[article.pk])
			return format_html('<a href="{}" target="_blank">View Article</a>', url)
		except Exception as e:
			return f"Error generating admin link: {str(e)}"
	admin_link.short_description = 'Admin Link'
	
	def has_add_permission(self, request, obj=None):
		return False

class ArticleCountFilter(admin.SimpleListFilter):
	title = 'Number of Articles'
	parameter_name = 'article_count'

	def lookups(self, request, model_admin):
		return (
			('0', 'No articles'),
			('1-5', '1 to 5 articles'),
			('6-10', '6 to 10 articles'),
			('11+', 'More than 10 articles'),
		)

	def queryset(self, request, queryset):
		if self.value() == '0':
			# Authors with no articles
			return queryset.annotate(count=models.Count('articles_set')).filter(count=0)
		elif self.value() == '1-5':
			# Authors with 1-5 articles
			return queryset.annotate(count=models.Count('articles_set')).filter(count__gte=1, count__lte=5)
		elif self.value() == '6-10':
			# Authors with 6-10 articles
			return queryset.annotate(count=models.Count('articles_set')).filter(count__gte=6, count__lte=10)
		elif self.value() == '11+':
			# Authors with more than 10 articles
			return queryset.annotate(count=models.Count('articles_set')).filter(count__gt=10)
		return queryset

class AuthorsAdmin(admin.ModelAdmin):
	search_fields = ['family_name', 'given_name', 'ORCID']
	list_display = ['given_name', 'family_name', 'display_orcid', 'country', 'article_count']
	list_filter = ['country', ArticleCountFilter]
	inlines = [AuthorArticlesInline]
	
	def display_orcid(self, obj):
		if obj.ORCID:
			orcid_url = f"https://orcid.org/{obj.ORCID}"
			return format_html('<a href="{}" target="_blank">{}</a>', orcid_url, obj.ORCID)
		return "-"
	display_orcid.short_description = 'ORCID'
	display_orcid.admin_order_field = 'ORCID'
	
	def article_count(self, obj):
		return obj.articles_count
	article_count.short_description = 'Number of Articles'
	article_count.admin_order_field = 'articles_count'
	
	def get_queryset(self, request):
		queryset = super().get_queryset(request)
		queryset = queryset.annotate(articles_count=models.Count('articles'))
		return queryset
	
	def get_inline_instances(self, request, obj=None):
		if not obj:  # If we're adding a new object, don't display inlines
			return []
		return super().get_inline_instances(request, obj)
	
@admin.register(TeamCategory)
class TeamCategoryAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	list_display = ('category_name', 'team', 'article_count', 'display_subjects')
	search_fields = ('category_name', 'team__name', 'subjects__subject_name')
	list_filter = [
		('team', OrganizationRestrictedFieldListFilter),
		('subjects', OrganizationRestrictedFieldListFilter),
	]
	filter_horizontal = ('subjects',)  # For better subject selection in the edit form
	
	def get_queryset(self, request):
		"""Add prefetch_related to avoid multiple DB queries"""
		return super().get_queryset(request).prefetch_related('subjects', 'articles')
	
	def article_count(self, obj):
		"""Display number of articles in this category"""
		return obj.articles.count()
	article_count.short_description = "Articles"
	
	def display_subjects(self, obj):
		"""Display subjects as a comma-separated list"""
		subjects = list(obj.subjects.all())
		if len(subjects) == 0:
			return "-"
		elif len(subjects) <= 3:
			return ", ".join(str(subject) for subject in subjects)
		else:
			return f"{', '.join(str(subject) for subject in subjects[:3])} (+{len(subjects) - 3})"
	display_subjects.short_description = "Subjects"

class TeamAdminForm(forms.ModelForm):
	"""Custom form for Team admin that allows creating organization and team together"""
	team_name = forms.CharField(
		max_length=200, 
		required=False,
		help_text="Enter team name, or select an existing organization below."
	)
	
	class Meta:
		model = Team
		fields = ['organization', 'slug']  # Exclude 'name' since we use 'team_name' instead
	
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		
		# If editing an existing team, populate the team_name field with the actual team name
		if self.instance and self.instance.pk:
			self.fields['team_name'].initial = self.instance.name
			# For existing teams, team_name updates the actual team name
			self.fields['team_name'].help_text = "Team name within the organization."
		
		# Make organization field optional for new teams
		if not self.instance.pk:
			self.fields['organization'].required = False
			self.fields['organization'].help_text = "Select an existing organization, or leave blank to create a new one with the team name above."
	
	def clean(self):
		cleaned_data = super().clean()
		team_name = cleaned_data.get('team_name')
		organization = cleaned_data.get('organization')
		
		# For new teams, require either team_name or organization
		if not self.instance.pk:
			if not team_name and not organization:
				raise forms.ValidationError("Please provide either a team name or select an existing organization.")
		else:
			# For existing teams, always require a team name
			if not team_name:
				raise forms.ValidationError("Team name is required.")
		
		# Check for unique team name within organization
		if team_name and organization:
			existing_team = Team.objects.filter(
				organization=organization, 
				name=team_name
			).exclude(pk=self.instance.pk if self.instance.pk else None)
			
			if existing_team.exists():
				raise forms.ValidationError(f"A team named '{team_name}' already exists in organization '{organization.name}'.")
		
		return cleaned_data
	
	def save(self, commit=True):
		team_name = self.cleaned_data.get('team_name')
		organization = self.cleaned_data.get('organization')
		
		# For new teams, create organization if team_name is provided and no organization selected
		if not self.instance.pk:
			if organization:
				# If organization is selected, use it
				self.instance.organization = organization
				self.instance.name = team_name or organization.name
				# Auto-generate slug if not provided
				if not self.instance.slug:
					from django.utils.text import slugify
					self.instance.slug = slugify(f"{organization.name}-{team_name or 'team'}")
			elif team_name:
				# If only team_name is provided, create new organization
				from organizations.models import Organization
				from django.utils.text import slugify
				
				# Create the organization
				organization = Organization.objects.create(name=team_name.strip())
				self.instance.organization = organization
				self.instance.name = team_name.strip()
				
				# Auto-generate slug if not provided
				if not self.instance.slug:
					self.instance.slug = slugify(team_name)
		else:
			# For existing teams, update the name
			if team_name:
				self.instance.name = team_name.strip()
		
		return super().save(commit)

@admin.register(Team)
class TeamAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	form = TeamAdminForm
	list_display = ['id', 'formatted_team_name', 'organization', 'slug', 'subjects_count', 'sources_count']
	list_filter = ['organization']
	search_fields = ['name', 'organization__name', 'slug']
	
	fieldsets = (
		(None, {
			'fields': ('team_name', 'organization', 'slug')
		}),
	)
	
	def formatted_team_name(self, obj):
		"""Display team name with formatting"""
		return format_html('<strong>{}</strong>', obj.name)
	formatted_team_name.short_description = 'Team Name'
	formatted_team_name.admin_order_field = 'name'
	
	def subjects_count(self, obj):
		return obj.subjects.count()
	subjects_count.short_description = 'Subjects'
	
	def sources_count(self, obj):
		return obj.sources.count()
	sources_count.short_description = 'Sources'
	
	def get_queryset(self, request):
		return super().get_queryset(request).select_related('organization').prefetch_related('subjects', 'sources')

@admin.register(TeamCredentials)
class TeamCredentialsAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	list_display = ('team', 'created_at', 'updated_at')
	# readonly_fields = ('orcid_client_id', 'orcid_client_secret', 'postmark_api_token')

	def get_readonly_fields(self, request, obj=None):
		return self.readonly_fields

@admin.register(PredictionRunLog)
class PredictionRunLogAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	list_display = ['id', 'team', 'subject', 'run_type', 'algorithm', 'model_version', 'run_started', 'run_finished', 'status_label', 'triggered_by']
	list_filter = [DateRangeFilter, 'team', 'subject', 'run_type', 'algorithm', 'success', 'model_version']
	search_fields = ['team__organization__name', 'subject__subject_name', 'model_version', 'triggered_by', 'algorithm']
	readonly_fields = ['run_started']  # Auto-populated field
	date_hierarchy = 'run_started'
	actions = ['mark_as_failed', 'mark_as_successful', 'export_as_csv']
	
	fieldsets = (
		('Run Information', {
			'fields': ('team', 'subject', 'run_type', 'algorithm', 'model_version', 'triggered_by'),
		}),
		('Status', {
			'fields': ('run_started', 'run_finished', 'success', 'error_message'),
		}),
	)
	
	def status_label(self, obj):
		if obj.success is True:
			return format_html('<span style="color: green; font-weight: bold;">Success</span>')
		elif obj.success is False:
			return format_html('<span style="color: red; font-weight: bold;">Failed</span>')
		else:
			return format_html('<span style="color: orange; font-weight: bold;">Running</span>')
	status_label.short_description = "Status"
	
	def get_queryset(self, request):
		# Order by most recent runs first
		return super().get_queryset(request).order_by('-run_started')
	
	def has_change_permission(self, request, obj=None):
		# Logs should generally not be modified after creation
		# But admins might need to update status or error messages
		return True
	
	def has_delete_permission(self, request, obj=None):
		# Allow deletion for admins
		return True
	
	def mark_as_failed(self, request, queryset):
		"""Mark selected unfinished runs as failed"""
		from django.utils import timezone
		
		# Only update runs that are still in progress (success is None)
		updated = queryset.filter(success__isnull=True).update(
			success=False,
			run_finished=timezone.now(),
			error_message="Manually marked as failed by admin."
		)
		
		if updated == 0:
			self.message_user(request, "No unfinished runs were selected.")
		else:
			self.message_user(request, f"Successfully marked {updated} run(s) as failed.")
	mark_as_failed.short_description = "Mark selected unfinished runs as failed"
	
	def mark_as_successful(self, request, queryset):
		"""Mark selected unfinished runs as successful"""
		from django.utils import timezone
		
		# Only update runs that are still in progress (success is None)
		updated = queryset.filter(success__isnull=True).update(
			success=True,
			run_finished=timezone.now()
		)
		
		if updated == 0:
			self.message_user(request, "No unfinished runs were selected.")
		else:
			self.message_user(request, f"Successfully marked {updated} run(s) as successful.")
	mark_as_successful.short_description = "Mark selected unfinished runs as successful"
	
	def export_as_csv(self, request, queryset):
		"""Export selected logs to CSV file"""
		meta = self.model._meta
		field_names = [
			'id', 'team', 'subject', 'run_type', 'algorithm', 'model_version', 
			'run_started', 'run_finished', 'success', 'triggered_by', 'error_message'
		]
		
		response = HttpResponse(content_type='text/csv')
		response['Content-Disposition'] = f'attachment; filename={meta.verbose_name_plural}.csv'
		
		writer = csv.writer(response)
		writer.writerow([field for field in field_names])
		
		for obj in queryset:
			row = []
			for field in field_names:
				if field == 'team':
					value = str(getattr(obj, field))
				elif field == 'subject':
					value = str(getattr(obj, field))
				elif field == 'run_type':
					value = obj.get_run_type_display()
				else:
					value = getattr(obj, field)
				row.append(value)
			writer.writerow(row)
		
		return response
	export_as_csv.short_description = "Export selected logs to CSV"
	
	def changelist_view(self, request, extra_context=None):
		# Add dashboard statistics to the context
		extra_context = extra_context or {}
		
		# Training runs statistics
		extra_context['training_success_count'] = PredictionRunLog.objects.filter(run_type='train', success=True).count()
		extra_context['training_failed_count'] = PredictionRunLog.objects.filter(run_type='train', success=False).count()
		extra_context['training_running_count'] = PredictionRunLog.objects.filter(run_type='train', success__isnull=True).count()
		
		# Prediction runs statistics
		extra_context['prediction_success_count'] = PredictionRunLog.objects.filter(run_type='predict', success=True).count()
		extra_context['prediction_failed_count'] = PredictionRunLog.objects.filter(run_type='predict', success=False).count()
		extra_context['prediction_running_count'] = PredictionRunLog.objects.filter(run_type='predict', success__isnull=True).count()
		
		# Recent runs (last 10)
		extra_context['recent_runs'] = PredictionRunLog.objects.all().order_by('-run_started')[:10]
		
		return super().changelist_view(request, extra_context=extra_context)
	
	def get_urls(self):
		urls = super().get_urls()
		custom_urls = [
			path(
				'ml-coverage/',
				self.admin_site.admin_view(self.ml_coverage_view),
				name='predictionrunlog_ml_coverage',
			),
		]
		return custom_urls + urls
	
	def ml_coverage_view(self, request):
		"""View to show ML coverage across teams and subjects"""
		# Get all teams
		teams = Team.objects.prefetch_related('subjects').all()
		
		# Create dictionaries to store the latest runs per subject
		training_data = {}
		prediction_data = {}
		
		# Get all subjects
		all_subjects = Subject.objects.all()
		
		# For each subject, get its latest training and prediction runs
		for subject in all_subjects:
			latest_training = PredictionRunLog.get_latest_run(subject.team, subject, run_type='train')
			latest_prediction = PredictionRunLog.get_latest_run(subject.team, subject, run_type='predict')
			
			if latest_training:
				training_data[subject.id] = latest_training
			
			if latest_prediction:
				prediction_data[subject.id] = latest_prediction
		
		context = {
			'title': 'ML Coverage Report',
			'teams': teams,
			'training_data': training_data,
			'prediction_data': prediction_data,
		}
		
		# Render the template
		return render(request, 'admin/gregory/predictionrunlog/ml_coverage.html', context)

admin.site.register(Articles, ArticleAdmin)
admin.site.register(Authors, AuthorsAdmin)
admin.site.register(Entities)
admin.site.register(Sources, SourceAdmin)
admin.site.register(Trials, TrialAdmin)

# Register ArticleTrialReference model with default admin
# @admin.register(ArticleTrialReference)
# class ArticleTrialReferenceAdmin(admin.ModelAdmin):
#     list_display = ['article', 'trial', 'identifier_type', 'identifier_value', 'discovered_date']
#     list_filter = ['identifier_type', 'discovered_date']
#     search_fields = ['article__title', 'trial__title', 'identifier_value']
#     date_hierarchy = 'discovered_date'
#     readonly_fields = ['discovered_date']