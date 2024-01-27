from django.contrib import admin
# Register your models here.
from .models import Articles, Categories, Trials, Sources, Entities, Authors, Subject

# this class define which department columns will be shown in the department admin web site.
class ArticleAdmin(admin.ModelAdmin):
	fieldsets = (
		('Article Information', {
            'fields': (
                'title', 'link', 'doi', 'summary', 'source',
                'published_date', 'discovery_date', 'authors', 'categories',
                'entities', 'relevant', 'noun_phrases', 'sent_to_admin',
                'sent_to_subscribers', 'kind', 'access', 'publisher',
                'container_title', 'crossref_check', 'takeaways'
            ),
            'description': 'This section contains general information about the article'
        }),
			('Machine Learning Predictions', {  # This is the title of the fieldset
					'fields': ('ml_prediction_gnb', 'ml_prediction_lr', 'ml_prediction_lsvc'),
					'description': 'Grouping machine learning prediction indicators',  # Optional: You can provide a description for the fieldset
			}),
	)
	list_display = ['article_id', 'title','source']
	readonly_fields = ['ml_prediction_gnb','ml_prediction_lr','ml_prediction_lsvc','categories','entities','discovery_date']
	search_fields = ['article_id', 'title','doi' ]
	list_filter = ('relevant',)
	raw_id_fields = ("authors",)  # Add other fields if needed


class TrialAdmin(admin.ModelAdmin):
	# a list of displayed columns name.
	list_display = ['trial_id', 'title', 'last_updated']
	fields = ['discovery_date','last_updated','title','summary','link','published_date','source','relevant','categories','identifiers','export_date','internal_number','last_refreshed_on','scientific_title','primary_sponsor','retrospective_flag','date_registration','source_register','recruitment_status','other_records','inclusion_agemin','inclusion_agemax','inclusion_gender','date_enrollement','target_size','study_type','study_design','phase','countries','contact_firstname','contact_lastname','contact_address','contact_email','contact_tel','contact_affiliation','inclusion_criteria','exclusion_criteria','condition','intervention','primary_outcome','secondary_outcome','secondary_id','source_support','ethics_review_status','ethics_review_approval_date','ethics_review_contact_name','ethics_review_contact_address','ethics_review_contact_phone','ethics_review_contact_email','results_date_completed','results_url_link']
	readonly_fields = ['last_updated']
class SourceAdmin(admin.ModelAdmin):
	# a list of displayed columns name.
	list_display = ['name','source_for','subject','method']

class SubjectAdmin(admin.ModelAdmin):
	list_display = ['subject_name','description']

admin.site.register(Articles,ArticleAdmin)
admin.site.register(Trials, TrialAdmin)
admin.site.register(Sources,SourceAdmin)
admin.site.register(Categories)
admin.site.register(Entities)
admin.site.register(Authors)
admin.site.register(Subject)