from django import forms
from django.forms import ModelForm

from .models import Subscribers

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



