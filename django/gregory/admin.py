from django.contrib import admin
# Register your models here.
from .models import Articles, Trials, Sources, Entities, Authors, Subject, MLPredictions,ArticleSubjectRelevance,TeamCategory
from .widgets import MLPredictionsWidget
from django import forms
from .fields import MLPredictionsField

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

class ArticleAdmin(admin.ModelAdmin):
	form = ArticleAdminForm  # Use the custom form here
	inlines = [ArticleSubjectRelevanceInline]
	fieldsets = (
		('Article Information', {
				'fields': (
					'title', 'link', 'doi', 'summary', 'teams', 'subjects', 'sources',
					'published_date', 'discovery_date', 'authors','team_categories',
					'entities', 'relevant', 'noun_phrases', 'sent_to_teams',
					'sent_to_subscribers', 'kind', 'access', 'publisher',
					'container_title', 'crossref_check', 'takeaways',
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
	list_filter = ('relevant',)
	raw_id_fields = ("authors",)
class TrialAdmin(admin.ModelAdmin):
	# a list of displayed columns name.
	list_display = ['trial_id', 'title', 'last_updated']
	fields = ['discovery_date','last_updated','title','summary','link','published_date','sources','teams','subjects','relevant','team_categories','identifiers','export_date','internal_number','last_refreshed_on','scientific_title','primary_sponsor','retrospective_flag','date_registration','source_register','recruitment_status','other_records','inclusion_agemin','inclusion_agemax','inclusion_gender','date_enrollement','target_size','study_type','study_design','phase','countries','contact_firstname','contact_lastname','contact_address','contact_email','contact_tel','contact_affiliation','inclusion_criteria','exclusion_criteria','condition','intervention','primary_outcome','secondary_outcome','secondary_id','source_support','ethics_review_status','ethics_review_approval_date','ethics_review_contact_name','ethics_review_contact_address','ethics_review_contact_phone','ethics_review_contact_email','results_date_completed','results_url_link']
	readonly_fields = ['last_updated', 'team_categories']
class SourceAdmin(admin.ModelAdmin):
	# a list of displayed columns name.
	list_display = ['name','source_for','subject','method']

class SubjectAdmin(admin.ModelAdmin):
	list_display = ['subject_name','description']

class AuthorsAdmin(admin.ModelAdmin):
	search_fields = ['family_name', 'given_name' ]

@admin.register(TeamCategory)
class TeamCategoryAdmin(admin.ModelAdmin):
	list_display = ('team', 'category_name', 'category_slug')
	search_fields = ('category_name', 'team__name')
	
admin.site.register(Articles,ArticleAdmin)
admin.site.register(Authors,AuthorsAdmin)
admin.site.register(Entities)
admin.site.register(Sources,SourceAdmin)
admin.site.register(Subject)
admin.site.register(Trials, TrialAdmin)