from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import path, reverse
import csv
from simple_history.admin import SimpleHistoryAdmin  # Import SimpleHistoryAdmin
from .admin_filters import DateRangeFilter

from .models import (
    Articles, Trials, Sources, Entities, Authors, Subject, MLPredictions, 
    ArticleSubjectRelevance, TeamCategory, TeamCredentials, PredictionRunLog, Team,
    ArticleTrialReference
)
from .widgets import MLPredictionsWidget
from django import forms
from .fields import MLPredictionsField
from django.utils.html import format_html

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
		super().__init__(*args, **kwargs)
		if self.instance and self.instance.pk:
			self.fields['ml_predictions_display'].initial = self.instance.ml_predictions_detail.all()

class ArticleAdmin(SimpleHistoryAdmin):
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
	list_display = ['article_id', 'title']
	ordering = ['-discovery_date']
	readonly_fields = ['entities', 'discovery_date']
	search_fields = ['article_id', 'title', 'doi']
	list_filter = ('teams', 'subjects','sources', )
	raw_id_fields = ("authors",)
	
	class Media:
		css = {
			'all': ['admin/css/ml_predictions.css'],
		}

class TrialAdmin(SimpleHistoryAdmin):
	list_display = ['trial_id', 'title', 'display_identifiers', 'discovery_date', 'last_updated']
	exclude = ['ml_predictions','relevant']
	readonly_fields = ['last_updated', 'team_categories']
	inlines = [TrialArticleReferenceInline]
	search_fields = [
		'trial_id', 'title', 'summary', 'summary_plain_english', 'scientific_title',
		'primary_sponsor', 'source_register', 'recruitment_status', 'condition',
		'intervention', 'primary_outcome', 'secondary_outcome', 'inclusion_criteria',
		'exclusion_criteria', 'study_type', 'study_design', 'phase', 'countries',
		'contact_firstname', 'contact_lastname', 'contact_affiliation',
		'therapeutic_areas', 'sponsor_type', 'internal_number', 'secondary_id',
		'identifiers'
	]
	list_filter = ['teams', 'subjects', 'sources']

	def display_identifiers(self, obj):
		# Customize this depending on how you want to display the JSON
		if obj.identifiers:
			return ", ".join([f"{k}: {v}" for k, v in obj.identifiers.items()])
		return "No Identifiers"

	display_identifiers.short_description = "Identifiers"
class SourceInline(admin.StackedInline):
	model = Sources
	extra = 1

class SourceAdmin(admin.ModelAdmin):
	list_display = ['name', 'source_for', 'subject', 'method', 'has_keyword_filter']
	list_filter = ['source_for', 'team', 'subject', 'method']
	search_fields = ['name', 'link', 'description', 'keyword_filter']
	fieldsets = (
		('Basic Information', {
			'fields': ('name', 'source_for', 'method', 'active', 'link')
		}),
		('Organization', {
			'fields': ('team', 'subject')
		}),
		('Settings', {
			'fields': ('ignore_ssl', 'language', 'description')
		}),
		('Filtering (bioRxiv and medRxiv)', {
			'fields': ('keyword_filter',),
			'description': 'For bioRxiv and medRxiv sources, specify keywords to filter articles. Use comma-separated values for multiple keywords, or quoted strings for exact phrases (e.g., "multiple sclerosis", alzheimer, parkinson).'
		}),
	)
	
	def has_keyword_filter(self, obj):
		"""Display whether source has keyword filtering enabled."""
		return bool(obj.keyword_filter)
	has_keyword_filter.boolean = True
	has_keyword_filter.short_description = 'Has Filter'

class SubjectAdminForm(forms.ModelForm):
		"""Custom form for Subject admin with superuser-only team access"""
		
		class Meta:
				model = Subject
				fields = '__all__'
		
		def __init__(self, *args, **kwargs):
				# Get the request from kwargs if passed
				self.request = kwargs.pop('request', None)
				super().__init__(*args, **kwargs)
				
				# If user is superuser, show all teams
				# Otherwise, keep the default filtering (user's teams only)
				if self.request and hasattr(self.request, 'user') and self.request.user.is_superuser:
						self.fields['team'].queryset = Team.objects.all()

@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
	list_display = ['subject_name','description', 'view_sources','team']  # Display in the list view
	readonly_fields = ['linked_sources']  # Display in the edit form
	list_filter = ['team']  # Add the team filter
	form = SubjectAdminForm

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
			return format_html('<br>'.join(links))
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
			return format_html('<br>'.join(links))
		return "No sources"

	linked_sources.short_description = "Linked Sources"


class AuthorsAdmin(admin.ModelAdmin):
	search_fields = ['family_name', 'given_name']


@admin.register(TeamCategory)
class TeamCategoryAdmin(admin.ModelAdmin):
	list_display = ('team', 'category_name', 'category_slug')
	search_fields = ('category_name', 'team__name')

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
class TeamAdmin(admin.ModelAdmin):
	form = TeamAdminForm
	list_display = ['id', 'name', 'organization', 'slug', 'subjects_count', 'sources_count']
	list_filter = ['organization']
	search_fields = ['name', 'organization__name', 'slug']
	
	fieldsets = (
		(None, {
			'fields': ('team_name', 'organization', 'slug')
		}),
	)
	
	def subjects_count(self, obj):
		return obj.subjects.count()
	subjects_count.short_description = 'Subjects'
	
	def sources_count(self, obj):
		return obj.sources.count()
	sources_count.short_description = 'Sources'
	
	def get_queryset(self, request):
		return super().get_queryset(request).select_related('organization').prefetch_related('subjects', 'sources')

@admin.register(TeamCredentials)
class TeamCredentialsAdmin(admin.ModelAdmin):
	list_display = ('team', 'created_at', 'updated_at')
	# readonly_fields = ('orcid_client_id', 'orcid_client_secret', 'postmark_api_token')

	def get_readonly_fields(self, request, obj=None):
		return self.readonly_fields
@admin.register(PredictionRunLog)
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