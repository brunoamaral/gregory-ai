from django.db import migrations


def normalise_trial_reference_types(apps, schema_editor):
	"""Relabel auto-detected article-trial links whose identifier_type is one the
	current identifier patterns never produce (dev carries `irctn` and `euct`).

	A full detection scan would otherwise add a second row for the same pair
	under the current label. Manual rows are an editor's work and are not
	touched. A row whose value does not resolve to exactly one canonical
	identifier is left alone.
	"""
	from gregory.utils.trial_identifiers import PATTERNS, extract_identifiers

	Reference = apps.get_model("gregory", "ArticleTrialReference")
	known_types = {name for name, _ in PATTERNS}

	old_rows = (
		Reference.objects.filter(source="auto")
		.exclude(identifier_type__in=known_types)
		.order_by("id")
	)
	for row in old_rows:
		found = extract_identifiers(row.identifier_value)
		if len(found) != 1:
			continue
		new_type, new_value = next(iter(found))
		current = Reference.objects.filter(
			article_id=row.article_id,
			trial_id=row.trial_id,
			identifier_type=new_type,
		).exists()
		if not current:
			row.identifier_type = new_type
			row.identifier_value = new_value
			row.save(update_fields=["identifier_type", "identifier_value"])
		elif not row.suppressed:
			row.delete()
		# else: a suppressed old-label row stays; the pair is skipped by detection.


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0133_patent_trial_link_manual"),
	]

	operations = [
		migrations.RunPython(normalise_trial_reference_types, migrations.RunPython.noop),
	]
