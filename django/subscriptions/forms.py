from django import forms
from django.forms import ModelForm

from .models import Subscribers, Lists

class ListsAdminForm(ModelForm):
    class Meta:
        model = Lists
        fields = '__all__'
        widgets = {
            'ml_threshold': forms.NumberInput(attrs={
                'step': '0.01',
                'min': '0.0',
                'max': '1.0',
                'style': 'width: 100px;'
            })
        }
        help_texts = {
            'subjects': 'Select subjects for relevant articles and trials in the main content of your emails.',
            'latest_research_categories': 'Select team categories to show the latest research for in a dedicated section.',
            'ml_threshold': 'ML prediction confidence threshold (0.0-1.0). Only articles with ML predictions above this threshold will be considered relevant. Use increments of 0.01.'
        }
        labels = {
            'subjects': 'Subjects for Main Content',
            'latest_research_categories': 'Team Categories for Latest Research'
        }

class SubscribersForm(ModelForm):
	first_name = forms.CharField(max_length=100)
	last_name = forms.CharField(max_length=100)
	email = forms.EmailField(max_length=120)
	# ``list`` was previously an ``IntegerField`` which only captured a
	# single checkbox value.  It has been removed so the view can obtain all
	# selected list IDs using ``request.POST.getlist('list')``.
	class Meta:
		model = Subscribers
		fields = [
			'first_name',
			'last_name',
			'profile',
		]
		exclude = ['subscriber_id','active','is_admin','email']



