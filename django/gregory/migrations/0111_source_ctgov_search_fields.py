from django.db import migrations, models


PREFIXES = (
	("INTERVENTION:", "ctgov_search_intervention"),
	("TERM:", "ctgov_search_term"),
)


def move_description_queries(apps, schema_editor):
	"""Copy "INTERVENTION:…" / "TERM:…" out of ClinicalTrials.gov sources' description.

	feedreader_trials_ctgov used to build part of its search from a description
	starting with one of those prefixes. Description is notes for people, so the
	importer now reads two dedicated fields instead. Copying the values keeps
	each source's search exactly as it was; the description is left untouched
	because it documents the query. A field that already has a value is never
	overwritten, which also makes a re-run harmless.
	"""
	Sources = apps.get_model("gregory", "Sources")
	for source in Sources.objects.filter(method="ctgov_api").exclude(description__isnull=True):
		description = source.description.strip()
		for prefix, field in PREFIXES:
			if description.upper().startswith(prefix) and not getattr(source, field):
				setattr(source, field, description[len(prefix):].strip())
				source.save(update_fields=[field])
				break


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0110_patent_trial_links"),
	]

	operations = [
		migrations.AddField(
			model_name="sources",
			name="ctgov_search_intervention",
			field=models.TextField(
				blank=True,
				help_text='Intervention or treatment searched on ClinicalTrials.gov (query.intr), e.g. "dihydroartemisinin". Only used when method is "ClinicalTrials.gov API".',
				null=True,
				verbose_name="ClinicalTrials.gov intervention",
			),
		),
		migrations.AddField(
			model_name="sources",
			name="ctgov_search_term",
			field=models.TextField(
				blank=True,
				help_text='Terms searched anywhere in a ClinicalTrials.gov record (query.term), e.g. "cell reprogramming". Only used when method is "ClinicalTrials.gov API".',
				null=True,
				verbose_name="ClinicalTrials.gov other terms",
			),
		),
		migrations.RunPython(move_description_queries, migrations.RunPython.noop),
	]
