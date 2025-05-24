from django import forms
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

class MLPredictionsWidget(forms.Widget):
	template_name = 'admin/your_app/articles/ml_predictions_table.html'
	
	class Media:
		css = {
			'all': ['admin/css/ml_predictions.css'],
		}

	def render(self, name, value, attrs=None, renderer=None):
		predictions = []
		if value:
			predictions = value.predictions.all()  # Adjust according to how predictions are related
		context = {
			'predictions': predictions,
			'name': name,
		}
		return mark_safe(render_to_string(self.template_name, context))
