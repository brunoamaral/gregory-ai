from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
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
from organizations.models import Organization, OrganizationUser
from organizations.admin import OrganizationAdmin as BaseOrganizationAdmin

from .models import (
    Articles, Trials, Sources, Entities, Authors, Subject, MLPredictions, 
    ArticleSubjectRelevance, TeamCategory, TeamCredentials, PredictionRunLog, Team,
    ArticleTrialReference, OrganizationCredentials, OrganizationSite
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


class ArticleOrganizationFilter(admin.SimpleListFilter):
	"""Filter articles by organisation (via teams__organization)."""
	title = 'organisation'
	parameter_name = 'organisation'

	def lookups(self, request, model_admin):
		if request.user.is_superuser:
			orgs = Organization.objects.all().order_by('name')
		else:
			user_org_ids = get_user_organizations(request.user)
			orgs = Organization.objects.filter(id__in=user_org_ids).order_by('name')
		return [(org.pk, org.name) for org in orgs]

	def queryset(self, request, queryset):
		if self.value():
			return queryset.filter(teams__organization__id=self.value()).distinct()
		return queryset


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

class SubjectDisplayWidget(forms.Widget):
	"""Shows subject name as bold text (with hidden pk) for existing rows."""

	def render(self, name, value, attrs=None, renderer=None):
		if value:
			try:
				subject = Subject.objects.get(pk=value)
				return mark_safe(
					f'<strong>{subject}</strong>'
					f'<input type="hidden" name="{name}" value="{value}">'
				)
			except Subject.DoesNotExist:
				pass
		return mark_safe(f'<input type="hidden" name="{name}" value="">')


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
			'subject': SubjectDisplayWidget(),
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

class ArticleSubjectRelevanceFormSet(forms.BaseInlineFormSet):
	"""Custom formset that shows the subject dropdown only on new (unsaved) rows."""

	def add_fields(self, form, index):
		super().add_fields(form, index)
		if not form.instance.pk:
			# New row: show a subject select dropdown
			form.fields['subject'].widget = forms.Select()
			form.fields['subject'].queryset = Subject.objects.all().order_by('subject_name')
		else:
			# Existing row: show subject name as text with embedded hidden pk
			form.fields['subject'].widget = SubjectDisplayWidget()


class ArticleSubjectRelevanceInline(admin.TabularInline):
	model = ArticleSubjectRelevance
	form = ArticleSubjectRelevanceForm
	formset = ArticleSubjectRelevanceFormSet
	can_delete = True
	fields = ['subject', 'is_relevant']

	def get_extra(self, request, obj=None, **kwargs):
		"""Superusers get one extra empty form to add new subject relevances"""
		if request.user.is_superuser:
			return 1
		return 0

	def get_formset(self, request, obj=None, **kwargs):
		"""Pre-populate with subjects for the article's teams.
		Superusers get all subjects; other users only get auto_predict subjects.
		"""
		if obj and obj.pk:  # If editing existing article
			if request.user.is_superuser:
				team_subjects = Subject.objects.filter(
					team__in=obj.teams.all()
				).distinct().order_by('subject_name')
			else:
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
		"""Superusers see all subjects; other users see only auto_predict subjects"""
		qs = super().get_queryset(request).select_related('subject')
		if not request.user.is_superuser:
			qs = qs.filter(subject__auto_predict=True)
		return qs.order_by('subject__subject_name')

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
		ArticleOrganizationFilter,
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


class ReassignToTeamForm(forms.Form):
	"""Simple form for granular per-object 'reassign to team' actions."""
	target_team = forms.ModelChoiceField(
		queryset=Team.objects.none(),
		label="Target team",
		help_text="All selected objects will be moved to this team.",
	)


class ReassignToTeamMixin:
	"""
	Admin mixin that adds a 'Reassign to team' bulk action.

	Subclasses must ensure their model has a ``team`` ForeignKey field.
	"""

	def _get_reassign_template(self):
		return 'admin/gregory/reassign_to_team_intermediate.html'

	@admin.action(description="Reassign selected to another team…")
	def reassign_to_team_action(self, request, queryset):
		# Safety: all selected objects must belong to the same organisation.
		team_ids = queryset.values_list('team_id', flat=True).distinct()
		org_ids = list(
			Team.all_objects.filter(pk__in=team_ids)
			.values_list('organization_id', flat=True)
			.distinct()
		)
		if len(org_ids) != 1:
			self.message_user(
				request,
				"Selected objects span multiple organisations. "
				"Please select only objects from a single organisation.",
				level=messages.ERROR,
			)
			return

		target_qs = Team.objects.filter(organization_id=org_ids[0])

		if 'apply' not in request.POST:
			form = ReassignToTeamForm()
			form.fields['target_team'].queryset = target_qs
			return render(
				request,
				self._get_reassign_template(),
				{
					'title': 'Reassign to team',
					'objects': queryset,
					'form': form,
					'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
					'model_name': queryset.model._meta.verbose_name_plural,
				},
			)

		form = ReassignToTeamForm(request.POST)
		form.fields['target_team'].queryset = target_qs

		if not form.is_valid():
			self.message_user(request, "Invalid form — please try again.", level=messages.ERROR)
			return

		to_team = form.cleaned_data['target_team']
		# Final guard: target team must belong to the same organisation.
		if to_team.organization_id != org_ids[0]:
			self.message_user(
				request,
				"Target team does not belong to the same organisation as the selected objects.",
				level=messages.ERROR,
			)
			return

		count = queryset.count()
		queryset.update(team=to_team)
		self.message_user(request, f"{count} object(s) reassigned to '{to_team}'.")


class SourceAdmin(OrganizationFilterMixin, ReassignToTeamMixin, admin.ModelAdmin):
	form = SourceAdminForm
	list_display = ['name', 'active', 'source_for', 'subject', 'last_article_date', 'article_count', 'health_status_indicator', 'has_keyword_filter']
	list_filter = [
		'active', 'source_for', 'method', SourceHealthFilter,
		('team', OrganizationRestrictedFieldListFilter),
		('subject', OrganizationRestrictedFieldListFilter),
	]
	search_fields = ['name', 'link', 'description', 'keyword_filter']
	actions = ['activate_sources', 'deactivate_sources', 'reassign_to_team_action']
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

	def save_model(self, request, obj, form, change):
		"""Warn admin if source has no team assigned."""
		super().save_model(request, obj, form, change)
		if not obj.team:
			messages.warning(
				request,
				f"Source '{obj.name}' has no team assigned. "
				f"Feedreaders will skip team association for content from this source."
			)
	
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
class SubjectAdmin(OrganizationFilterMixin, ReassignToTeamMixin, admin.ModelAdmin):
	list_display = ['formatted_subject_name', 'description', 'view_sources', 'team']  # Updated list display
	readonly_fields = ['linked_sources']  # Display in the edit form
	list_filter = [('team', OrganizationRestrictedFieldListFilter)]  # Add the team filter
	form = SubjectAdminForm
	inlines = [SourcesInline]  # Add the inline for managing sources
	actions = ['reassign_to_team_action']
	
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
class TeamCategoryAdmin(OrganizationFilterMixin, ReassignToTeamMixin, admin.ModelAdmin):
	list_display = ('category_name', 'team', 'article_count', 'display_subjects')
	search_fields = ('category_name', 'team__name', 'subjects__subject_name')
	list_filter = [
		('team', OrganizationRestrictedFieldListFilter),
		('subjects', OrganizationRestrictedFieldListFilter),
	]
	filter_horizontal = ('subjects',)
	actions = ['reassign_to_team_action']
	
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

class TeamSubjectInline(admin.TabularInline):
	model = Subject
	extra = 0
	fields = ('subject_name', 'subject_slug', 'auto_predict', 'ml_consensus_type')
	readonly_fields = ('subject_name', 'subject_slug', 'auto_predict', 'ml_consensus_type')
	show_change_link = True
	verbose_name = 'Subject'
	verbose_name_plural = 'Subjects'
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


class OrganizationSiteInline(admin.TabularInline):
	"""Inline to manage sites associated with an organization."""
	model = OrganizationSite
	extra = 1
	fields = ('site', 'is_default')
	verbose_name = 'Site'
	verbose_name_plural = 'Sites'


class OrganizationCredentialsInline(admin.StackedInline):
	"""Inline to manage Postmark/ORCID credentials for an organization."""
	model = OrganizationCredentials
	extra = 0
	max_num = 1
	fields = ('postmark_api_token', 'postmark_api_url', 'orcid_client_id', 'orcid_client_secret')
	verbose_name = 'Credentials'
	verbose_name_plural = 'Credentials'


class OrganizationTeamInline(admin.TabularInline):
	"""Inline to display teams belonging to an organization."""
	model = Team
	extra = 0
	fields = ('name', 'slug')
	readonly_fields = ('name', 'slug')
	show_change_link = True
	verbose_name = 'Team'
	verbose_name_plural = 'Teams'
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


admin.site.unregister(Organization)


@admin.register(Organization)
class OrganizationAdmin(BaseOrganizationAdmin):
	"""Custom Organization admin that shows associated teams."""
	list_display = ['name', 'slug', 'teams_count']

	def get_inline_instances(self, request, obj=None):
		inlines = super().get_inline_instances(request, obj)
		if obj is not None:
			inlines.append(OrganizationTeamInline(self.model, self.admin_site))
			inlines.append(OrganizationSiteInline(self.model, self.admin_site))
			inlines.append(OrganizationCredentialsInline(self.model, self.admin_site))
		return inlines

	def teams_count(self, obj):
		return obj.teams.count()
	teams_count.short_description = 'Teams'


class TeamSourceInline(admin.TabularInline):
	model = Sources
	extra = 0
	fields = ('name', 'source_for', 'method', 'subject', 'active')
	readonly_fields = ('name', 'source_for', 'method', 'subject', 'active')
	show_change_link = True
	verbose_name = 'Source'
	verbose_name_plural = 'Sources'
	can_delete = False

	def has_add_permission(self, request, obj=None):
		return False


class TeamCredentialsInline(admin.StackedInline):
	"""Inline to manage Postmark/ORCID credentials for a team."""
	model = TeamCredentials
	extra = 0
	max_num = 1
	fields = ('postmark_api_token', 'postmark_api_url', 'orcid_client_id', 'orcid_client_secret')
	verbose_name = 'Credentials'
	verbose_name_plural = 'Credentials'


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
		from django.utils.text import slugify
		from organizations.models import Organization
		from gregory.models import OrganizationSite

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
					self.instance.slug = slugify(f"{organization.name}-{team_name or 'team'}")
			elif team_name:
				# If only team_name is provided, create new organization
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

		# Default site to the organisation's default site when not explicitly set
		if not self.instance.site_id and self.instance.organization_id:
			default_org_site = OrganizationSite.objects.filter(
				organization_id=self.instance.organization_id,
				is_default=True,
			).select_related('site').first()
			if default_org_site:
				self.instance.site = default_org_site.site

		return super().save(commit)

class TeamMembersInline(admin.TabularInline):
	model = Team.members.through
	extra = 1
	verbose_name = 'Member'
	verbose_name_plural = 'Members'
	autocomplete_fields = ['user']


def _ensure_user_in_organization(user, organization):
	"""Add user to organization if not already a member."""
	if not OrganizationUser.objects.filter(user=user, organization=organization).exists():
		OrganizationUser.objects.create(user=user, organization=organization)


class ReassignTeamForm(forms.Form):
	"""Intermediate form for the 'Reassign to team' admin action."""
	target_team = forms.ModelChoiceField(
		queryset=Team.objects.none(),  # filled dynamically
		label="Target team",
		help_text="All objects will be moved to this team. Must be in the same organisation.",
	)
	conflict = forms.ChoiceField(
		choices=[
			('skip',   'Skip — leave conflicting subjects on the old team'),
			('rename', 'Rename — append a suffix to conflicting subject slugs'),
			('merge',  'Merge — fold conflicting subjects into the existing one'),
		],
		initial='skip',
		label="Subject slug conflict handling",
	)


@admin.register(Team)
class TeamAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	form = TeamAdminForm
	inlines = [TeamMembersInline, TeamSubjectInline, TeamSourceInline, TeamCredentialsInline]
	list_display = ['id', 'formatted_team_name', 'organization_link', 'slug', 'subjects_count', 'sources_count', 'active_badge']
	list_display_links = ['id', 'formatted_team_name']
	list_filter = ['organization', 'is_active']
	search_fields = ['name', 'organization__name', 'slug']
	actions = ['soft_delete_teams', 'reassign_to_team', 'hard_delete_empty_inactive_teams']

	fieldsets = (
		(None, {
			'fields': ('team_name', 'organization', 'slug', 'is_active')
		}),
	)
	readonly_fields = ('organization_link',)

	def get_queryset(self, request):
		# Show all teams (active and inactive) in the admin.
		qs = Team.all_objects.select_related('organization').prefetch_related('subjects', 'sources', 'members')
		# Apply organisation-based filtering from OrganizationFilterMixin if needed.
		if not request.user.is_superuser:
			user_orgs = get_user_organizations(request.user)
			if user_orgs is not None:
				qs = qs.filter(organization__in=user_orgs)
		return qs

	# ------------------------------------------------------------------ #
	# Display helpers                                                      #
	# ------------------------------------------------------------------ #

	def organization_link(self, obj):
		if obj.organization_id:
			url = reverse('admin:organizations_organization_change', args=[obj.organization_id])
			return format_html('<a href="{}">{}</a>', url, obj.organization.name)
		return '-'
	organization_link.short_description = 'Organisation'
	organization_link.admin_order_field = 'organization__name'

	def formatted_team_name(self, obj):
		if obj.is_active:
			return format_html('<strong>{}</strong>', obj.name)
		return format_html(
			'<strong style="color:#999;text-decoration:line-through;">{}</strong> '
			'<span style="color:#c0392b;font-size:0.85em;">[inactive]</span>',
			obj.name,
		)
	formatted_team_name.short_description = 'Team Name'
	formatted_team_name.admin_order_field = 'name'

	def active_badge(self, obj):
		if obj.is_active:
			return format_html('<span style="color:green;font-weight:bold;">{}</span>', '✓ Active')
		return format_html('<span style="color:#c0392b;font-weight:bold;">{}</span>', '✗ Inactive')
	active_badge.short_description = 'Status'
	active_badge.admin_order_field = 'is_active'

	def subjects_count(self, obj):
		return obj.subjects.count()
	subjects_count.short_description = 'Subjects'

	def sources_count(self, obj):
		return obj.sources.count()
	sources_count.short_description = 'Sources'

	# ------------------------------------------------------------------ #
	# Soft-delete: override delete_model and delete_queryset               #
	# ------------------------------------------------------------------ #

	def delete_model(self, request, obj):
		"""Soft-delete a single team from the change view."""
		obj.delete()  # calls Team.delete() which sets is_active=False

	def delete_queryset(self, request, queryset):
		"""Soft-delete all selected teams from the list view."""
		for team in queryset:
			team.delete()

	# ------------------------------------------------------------------ #
	# Admin actions                                                        #
	# ------------------------------------------------------------------ #

	@admin.action(description="Deactivate selected teams (soft delete)")
	def soft_delete_teams(self, request, queryset):
		count = 0
		for team in queryset:
			if team.is_active:
				team.delete()
				count += 1
		self.message_user(request, f"{count} team(s) deactivated.")

	@admin.action(description="Reassign all objects to another team…")
	def reassign_to_team(self, request, queryset):
		from gregory.services.team_reassignment import reassign_team

		# Step 1 — show the intermediate form.
		if 'apply' not in request.POST:
			# Build queryset of valid target teams: active, not in the selection,
			# but sharing the same organisation as the selected teams.
			org_ids = queryset.values_list('organization_id', flat=True).distinct()
			target_qs = Team.objects.filter(organization_id__in=org_ids).exclude(
				pk__in=queryset.values_list('pk', flat=True)
			)
			form = ReassignTeamForm()
			form.fields['target_team'].queryset = target_qs
			return render(
				request,
				'admin/gregory/team/reassign_intermediate.html',
				{
					'title': 'Reassign team objects',
					'teams': queryset,
					'form': form,
					'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
				},
			)

		# Step 2 — process the form.
		org_ids = queryset.values_list('organization_id', flat=True).distinct()
		target_qs = Team.objects.filter(organization_id__in=org_ids).exclude(
			pk__in=queryset.values_list('pk', flat=True)
		)
		form = ReassignTeamForm(request.POST)
		form.fields['target_team'].queryset = target_qs

		if not form.is_valid():
			self.message_user(request, "Invalid form — please try again.", level=messages.ERROR)
			return

		to_team = form.cleaned_data['target_team']
		conflict = form.cleaned_data['conflict']

		errors = []
		for from_team in queryset:
			try:
				report = reassign_team(from_team=from_team, to_team=to_team, conflict=conflict)
				self.message_user(
					request,
					f"Reassigned '{from_team.name}' → '{to_team.name}': "
					f"{len(report.subjects_moved)} subjects, {report.sources_moved} sources, "
					f"{report.lists_moved} lists moved.",
				)
			except ValueError as exc:
				errors.append(str(exc))

		if errors:
			self.message_user(request, "Errors: " + "; ".join(errors), level=messages.ERROR)

	@admin.action(description="Hard-delete selected inactive teams (only if empty)")
	def hard_delete_empty_inactive_teams(self, request, queryset):
		deleted = 0
		skipped = []
		for team in queryset:
			if team.is_active:
				skipped.append(f"'{team.name}' is still active")
				continue
			has_objects = (
				team.subjects.exists()
				or team.sources.exists()
				or team.team_categories.exists()
				or team.lists.exists()
				or team.prediction_run_logs.exists()
			)
			if has_objects:
				skipped.append(f"'{team.name}' still has related objects — reassign first")
				continue
			team.hard_delete()
			deleted += 1

		if deleted:
			self.message_user(request, f"{deleted} team(s) permanently deleted.")
		if skipped:
			self.message_user(request, "Skipped: " + "; ".join(skipped), level=messages.WARNING)

	# ------------------------------------------------------------------ #
	# URLs for custom views                                                #
	# ------------------------------------------------------------------ #

	def get_urls(self):
		from django.urls import path as url_path
		urls = super().get_urls()
		custom = [
			url_path(
				'<int:team_id>/reassign/',
				self.admin_site.admin_view(self.reassign_view),
				name='gregory_team_reassign',
			),
		]
		return custom + urls

	def reassign_view(self, request, team_id):
		"""Detail-level reassign view (accessible from the change form)."""
		from gregory.services.team_reassignment import reassign_team

		from_team = Team.all_objects.get(pk=team_id)
		org_id = from_team.organization_id
		target_qs = Team.objects.filter(organization_id=org_id).exclude(pk=team_id)

		if request.method == 'POST':
			form = ReassignTeamForm(request.POST)
			form.fields['target_team'].queryset = target_qs
			if form.is_valid():
				to_team = form.cleaned_data['target_team']
				conflict = form.cleaned_data['conflict']
				try:
					report = reassign_team(from_team=from_team, to_team=to_team, conflict=conflict)
					self.message_user(request, f"Reassignment complete.\n{report.summary()}")
				except ValueError as exc:
					self.message_user(request, str(exc), level=messages.ERROR)
				return self._response_post_save(request, from_team)
		else:
			form = ReassignTeamForm()
			form.fields['target_team'].queryset = target_qs

		return render(
			request,
			'admin/gregory/team/reassign_intermediate.html',
			{
				'title': f'Reassign objects from "{from_team}"',
				'teams': [from_team],
				'form': form,
				'action_checkbox_name': admin.helpers.ACTION_CHECKBOX_NAME,
				'original': from_team,
				'opts': self.model._meta,
			},
		)

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		# Auto-add new members to the team's organization
		team = form.instance
		if team.organization_id:
			for user in team.members.all():
				_ensure_user_in_organization(user, team.organization)

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


# --- Custom UserAdmin with Team membership inline ---

class UserTeamInline(admin.TabularInline):
	model = Team.members.through
	extra = 1
	verbose_name = 'Team membership'
	verbose_name_plural = 'Team memberships'
	autocomplete_fields = ['team']


class CustomUserAdmin(BaseUserAdmin):
	inlines = list(BaseUserAdmin.inlines) + [UserTeamInline]

	def save_related(self, request, form, formsets, change):
		super().save_related(request, form, formsets, change)
		# Auto-add user to each team's organization
		user = form.instance
		for team in Team.objects.filter(members=user):
			if team.organization_id:
				_ensure_user_in_organization(user, team.organization)


try:
	admin.site.unregister(User)
except admin.sites.NotRegistered:
	pass
admin.site.register(User, CustomUserAdmin)

# Register ArticleTrialReference model with default admin
# @admin.register(ArticleTrialReference)
# class ArticleTrialReferenceAdmin(admin.ModelAdmin):
#     list_display = ['article', 'trial', 'identifier_type', 'identifier_value', 'discovered_date']
#     list_filter = ['identifier_type', 'discovered_date']
#     search_fields = ['article__title', 'trial__title', 'identifier_value']
#     date_hierarchy = 'discovered_date'
#     readonly_fields = ['discovered_date']