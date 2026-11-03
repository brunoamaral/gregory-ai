"""The admin's autocomplete endpoint, with a readable label for trials and patents.

``Trials.__str__`` is the database id (it is used in history, logs and other inlines, so
it stays). The admin's autocomplete shows ``str(obj)``, so a trial picker would offer bare
numbers, and a patent picker only the title (``patent_label`` adds the publication number). ``AutocompleteJsonView.serialize_result`` is Django's hook for the label; the
admin site has no way to name a different view, so ``admin/urls.py`` mounts this one in
front of the admin's own ``autocomplete/`` route.
"""

from django.contrib.admin.views.autocomplete import AutocompleteJsonView

from gregory.models import Patents, Trials
from gregory.services.patent_trial_links import patent_label
from gregory.services.trial_links import trial_label


class GregoryAutocompleteJsonView(AutocompleteJsonView):
	def serialize_result(self, obj, to_field_name):
		result = super().serialize_result(obj, to_field_name)
		if isinstance(obj, Trials):
			result["text"] = trial_label(obj)
		elif isinstance(obj, Patents):
			result["text"] = patent_label(obj)
		return result
