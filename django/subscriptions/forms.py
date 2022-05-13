from django import forms
from django.forms import ModelForm

from .models import Subscribers

class SubscribersForm(ModelForm):
		# marca = forms.CharField(max_length=100  )
		# modelo = forms.CharField(max_length=100  )
		# nro_serie = forms.CharField(max_length=100 )
		# foto = forms.ImageField()
		# processo_crime = forms.FileField()
		# email = forms.EmailField(max_length=100)

	class Meta:
		model = Subscribers
		fields = [
			'first_name',
			'last_name',
			'email',
			'profile',
		]




