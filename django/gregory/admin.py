from django.contrib import admin

# Register your models here.
from .models import Articles, Trials, Sources

# this class define which department columns will be shown in the department admin web site.
class ArticleAdmin(admin.ModelAdmin):
    # a list of displayed columns name.
    list_display = ['article_id', 'title']

admin.site.register(Articles,ArticleAdmin)
admin.site.register(Trials)
admin.site.register(Sources)