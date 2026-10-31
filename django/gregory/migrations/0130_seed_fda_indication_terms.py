from django.db import migrations

# What an EMA therapeutic area (a MeSH heading) is called in a DailyMed label. EMA's
# "Alzheimer Disease" never appears in label text, which says "Alzheimer's disease", so the
# term is the stem that both apostrophes leave whole.
EMA_AREA_TO_FDA_TERM = {
	"multiple sclerosis": "multiple sclerosis",
	"alzheimer disease": "alzheimer",
	"dementia": "dementia",
	"parkinson disease": "parkinson",
}


def seed_terms(apps, schema_editor):
	"""Give every subject that has EMA areas and no FDA terms the FDA terms for those areas.

	Without this, the first pipeline run after the deploy would match FDA candidates under
	these subjects with no disease check and over-link. Areas not in the map are skipped, and
	a subject whose terms are already set is not touched."""
	Subject = apps.get_model("gregory", "Subject")
	for subject in Subject.objects.exclude(ema_therapeutic_areas=[]).filter(fda_indication_terms=[]):
		terms = []
		for area in subject.ema_therapeutic_areas:
			term = EMA_AREA_TO_FDA_TERM.get(" ".join((area or "").lower().split()))
			if term and term not in terms:
				terms.append(term)
		if terms:
			# update(), not save(): a data fix is not an editor's change, so no history row.
			Subject.objects.filter(pk=subject.pk).update(fda_indication_terms=terms)


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0129_subject_fda_indication_terms"),
	]

	operations = [
		migrations.RunPython(seed_terms, migrations.RunPython.noop),
	]
