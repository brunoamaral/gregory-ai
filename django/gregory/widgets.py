from django import forms
from django.template.loader import render_to_string

class MLPredictionsWidget(forms.Widget):
	template_name = 'admin/your_app/articles/ml_predictions_table.html'

	def render(self, name, value, attrs=None, renderer=None):
		predictions = []
		if value:
			predictions = value.predictions.all()  # Adjust according to how predictions are related
		return render_to_string(self.template_name, {'predictions': predictions})
