from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import path
import csv
from simple_history.admin import SimpleHistoryAdmin  # Import SimpleHistoryAdmin
from .admin_filters import DateRangeFilter

from .models import Articles, Trials, Sources, Entities, Authors, Subject, MLPredictions, ArticleSubjectRelevance, TeamCategory, TeamCredentials, PredictionRunLog, Team
from .widgets import MLPredictionsWidget
from django import forms
from .fields import MLPredictionsField
from django.utils.html import format_html
from django.urls import reverse

class ArticleSubjectRelevanceInline(admin.TabularInline):
	model = ArticleSubjectRelevance
	extra = 1

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
	inlines = [ArticleSubjectRelevanceInline]
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
		}),
	)
	list_display = ['article_id', 'title']
	ordering = ['-discovery_date']
	readonly_fields = ['entities', 'discovery_date']
	search_fields = ['article_id', 'title', 'doi']
	list_filter = ('subjects',)
	raw_id_fields = ("authors",)

class TrialAdmin(SimpleHistoryAdmin):
	list_display = ['trial_id', 'title', 'display_identifiers', 'discovery_date', 'last_updated']
	exclude = ['ml_predictions','relevant']
	readonly_fields = ['last_updated', 'team_categories']
	search_fields = ['trial_id', 'title', 'identifiers']
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
	list_display = ['name', 'source_for', 'subject', 'method']
	list_filter = ['source_for', 'team', 'subject']

class SubjectAdmin(admin.ModelAdmin):
	list_display = ['subject_name','description', 'view_sources','team']  # Display in the list view
	readonly_fields = ['linked_sources']  # Display in the edit form
	list_filter = ['team']  # Add the team filter

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



@admin.register(TeamCredentials)
class TeamCredentialsAdmin(admin.ModelAdmin):
	list_display = ('team', 'created_at', 'updated_at')
	# readonly_fields = ('orcid_client_id', 'orcid_client_secret', 'postmark_api_token')

	def get_readonly_fields(self, request, obj=None):
		return self.readonly_fields
@admin.register(PredictionRunLog)
class PredictionRunLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'team', 'subject', 'run_type', 'model_version', 'run_started', 'run_finished', 'status_label', 'triggered_by']
    list_filter = [DateRangeFilter, 'team', 'subject', 'run_type', 'success', 'model_version']
    search_fields = ['team__organization__name', 'subject__subject_name', 'model_version', 'triggered_by']
    readonly_fields = ['run_started']  # Auto-populated field
    date_hierarchy = 'run_started'
    actions = ['mark_as_failed', 'mark_as_successful', 'export_as_csv']
    
    fieldsets = (
        ('Run Information', {
            'fields': ('team', 'subject', 'run_type', 'model_version', 'triggered_by'),
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
            'id', 'team', 'subject', 'run_type', 'model_version', 
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
admin.site.register(Subject, SubjectAdmin)
admin.site.register(Trials, TrialAdmin)