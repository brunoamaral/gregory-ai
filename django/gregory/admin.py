from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin  # Import SimpleHistoryAdmin

from .models import Articles, Trials, Sources, Entities, Authors, Subject, MLPredictions, ArticleSubjectRelevance, TeamCategory, TeamCredentials
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
			self.fields['ml_predictions_display'].initial = self.instance.ml_predictions.all()

class ArticleAdmin(SimpleHistoryAdmin):
	form = ArticleAdminForm
	inlines = [ArticleSubjectRelevanceInline]
	fieldsets = (
		('Article Information', {
			'fields': (
				'title', 'link', 'doi', 'summary', 'teams', 'subjects', 'sources',
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
admin.site.register(Articles, ArticleAdmin)
admin.site.register(Authors, AuthorsAdmin)
admin.site.register(Entities)
admin.site.register(Sources, SourceAdmin)
admin.site.register(Subject, SubjectAdmin)
admin.site.register(Trials, TrialAdmin)