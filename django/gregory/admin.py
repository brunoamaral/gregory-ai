from django.contrib import admin
from django import forms
# Register your models here.
from .models import Articles, Categories, Trials, Sources, Entities, Authors, Subject

# this class define which department columns will be shown in the department admin web site.
class ArticleAdmin(admin.ModelAdmin):
	# a list of displayed columns name.
	list_display = ['article_id', 'title','source']
	readonly_fields = ['ml_prediction_gnb','ml_prediction_lr','categories','entities']
	search_fields = ['article_id', 'title','doi' ]
	list_filter = ('relevant',)


class TrialAdmin(admin.ModelAdmin):
	# a list of displayed columns name.
	list_display = ['trial_id', 'title']

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