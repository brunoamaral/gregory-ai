from django import forms
from django.forms import ModelForm

from .models import Subscribers,Lists

class SubscribersForm(ModelForm):
		# marca = forms.CharField(max_length=100  )
		# modelo = forms.CharField(max_length=100  )
		# nro_serie = forms.CharField(max_length=100 )
		# foto = forms.ImageField()
		# processo_crime = forms.FileField()
		# email = forms.EmailField(max_length=100)

	class Meta:
		model = Subscribers
		# fields = [
		# 	'first_name',
		# 	'last_name',
		# 	'email',
		# 	'profile',
		# 	'list_id',
		# ]
		exclude = ['subscriber_id','active','is_admin']

class ListsForm(ModelForm):
	class Meta:
		model = Lists
		fields = ['list_id']


