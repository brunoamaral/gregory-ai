from django import forms
from django.template.loader import render_to_string

class MLPredictionsField(forms.Field):
	def __init__(self, *args, **kwargs):
		kwargs['widget'] = MLPredictionsWidget()
		super().__init__(*args, **kwargs)

class MLPredictionsWidget(forms.Widget):
	template_name = 'admin/gregory/ml_predictions_table.html'

	def render(self, name, value, attrs=None, renderer=None):
		if value is None:
				value = []
		return render_to_string(self.template_name, {'predictions': value})
