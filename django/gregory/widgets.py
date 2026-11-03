from django import forms
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe


class MLPredictionsWidget(forms.Widget):
	template_name = "admin/your_app/articles/ml_predictions_table.html"

	class Media:
		css = {
			"all": ["admin/css/ml_predictions.css"],
		}

	def render(self, name, value, attrs=None, renderer=None):
		predictions = []
		if value:
			predictions = (
				value.predictions.all()
			)  # Adjust according to how predictions are related
		context = {
			"predictions": predictions,
			"name": name,
		}
		return mark_safe(render_to_string(self.template_name, context))


class GroupedSourceCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
	"""Checkboxes grouped by team and subject, with a summary of what's linked.

	Takes grouped choices: [(group_label, [(pk, label), ...]), ...]. A group
	holding a linked source renders open; the others render closed. Closed
	groups still submit their ticked boxes.
	"""

	template_name = "admin/gregory/widgets/grouped_source_checkboxes.html"

	class Media:
		css = {"all": ["admin/css/grouped_sources.css"]}

	def get_context(self, name, value, attrs):
		context = super().get_context(name, value, attrs)
		groups, linked = [], []
		for label, options, _index in context["widget"]["optgroups"]:
			selected = [option for option in options if option["selected"]]
			linked.extend(option["label"] for option in selected)
			groups.append({
				"label": label,
				"options": options,
				"linked": len(selected),
				"total": len(options),
			})
		context["widget"]["groups"] = groups
		context["widget"]["linked_labels"] = linked
		return context
