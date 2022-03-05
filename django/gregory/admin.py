from django.contrib import admin

# Register your models here.
from .models import Articles, Trials, Sources

admin.site.register(Articles)
admin.site.register(Trials)
admin.site.register(Sources)