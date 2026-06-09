# Backfill Trials.links from the existing canonical link.
#
# Each trial's current `link` is filed under its registry key (inferred from the
# URL's domain) so the multi-source link merge introduced alongside this
# migration starts from accurate per-registry data instead of an empty map.
# See docs/trials-multi-source-merge.md.

from django.db import migrations

from gregory.utils.trial_utils import registry_from_url

BATCH_SIZE = 2000


def backfill_links(apps, schema_editor):
	Trials = apps.get_model("gregory", "Trials")
	queryset = Trials.objects.filter(links__isnull=True).exclude(link="").only("trial_id", "link")
	batch = []
	for trial in queryset.iterator(chunk_size=BATCH_SIZE):
		key = registry_from_url(trial.link)
		if not key:
			continue
		trial.links = {key: trial.link}
		batch.append(trial)
		if len(batch) >= BATCH_SIZE:
			Trials.objects.bulk_update(batch, ["links"], batch_size=BATCH_SIZE)
			batch = []
	if batch:
		Trials.objects.bulk_update(batch, ["links"], batch_size=BATCH_SIZE)


def noop(apps, schema_editor):
	# Reverse: leave links populated; the schema migration removes the column on
	# full rollback anyway.
	pass


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0056_historicaltrials_links_trials_links"),
	]

	operations = [
		migrations.RunPython(backfill_links, noop),
	]
