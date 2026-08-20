from django.db import migrations
from django.utils.text import slugify


def backfill(apps, schema_editor):
	Lists = apps.get_model("subscriptions", "Lists")

	count = 0
	qs = Lists.objects.filter(utm_campaign_slug="")
	for lst in qs.iterator():
		# list_name allows up to 150 chars but utm_campaign_slug is capped
		# at 100 — truncate so a long name can't fail this migration.
		lst.utm_campaign_slug = slugify(lst.list_name)[:100]
		lst.save(update_fields=["utm_campaign_slug"])
		count += 1

	if count:
		print(f"Backfilled utm_campaign_slug for {count} list(s)")


def reverse(apps, schema_editor):
	Lists = apps.get_model("subscriptions", "Lists")
	Lists.objects.update(utm_campaign_slug="")


class Migration(migrations.Migration):
	dependencies = [
		("subscriptions", "0037_lists_utm_campaign_slug"),
	]
	operations = [migrations.RunPython(backfill, reverse)]
