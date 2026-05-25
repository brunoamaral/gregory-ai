from django.db import migrations


def backfill(apps, schema_editor):
	Announcement = apps.get_model('subscriptions', 'Announcement')
	Organization = apps.get_model('organizations', 'Organization')

	# Track counts rather than accumulating PK lists to avoid unbounded
	# memory usage on large databases.
	step_a_count = 0
	step_b_count = 0
	step_c_count = 0
	step_c_ids_sample = []  # keep first 10 for operator review
	unresolved_ids = []

	qs = Announcement.objects.filter(organization__isnull=True)
	for ann in qs.iterator():
		# Step A — from the first attached list's team.organization.
		first_list = ann.lists.select_related('team').order_by('pk').first()
		if first_list is not None and first_list.team is not None:
			ann.organization_id = first_list.team.organization_id
			ann.save(update_fields=['organization'])
			step_a_count += 1
			continue

		# Step B — from created_by's first OrganizationUser.
		if ann.created_by_id is not None:
			ou = (
				ann.created_by.organizations_organizationuser
				.order_by('pk')
				.first()
			)
			if ou is not None:
				ann.organization_id = ou.organization_id
				ann.save(update_fields=['organization'])
				step_b_count += 1
				continue

		# Step C — first Organization globally.
		org = Organization.objects.order_by('pk').first()
		if org is None:
			unresolved_ids.append(ann.pk)
			continue
		ann.organization_id = org.pk
		ann.save(update_fields=['organization'])
		step_c_count += 1
		if len(step_c_ids_sample) < 10:
			step_c_ids_sample.append(ann.pk)

	if unresolved_ids:
		raise RuntimeError(
			"No Organization rows exist; cannot backfill "
			f"Announcements: {unresolved_ids}"
		)

	if step_a_count:
		print(f"Backfill step A (from lists): {step_a_count} row(s)")
	if step_b_count:
		print(f"Backfill step B (from created_by): {step_b_count} row(s)")
	if step_c_count:
		sample_str = f", first 10 PKs: {step_c_ids_sample}" if step_c_ids_sample else ""
		print(f"Backfill step C (global fallback): {step_c_count} row(s){sample_str}")


def reverse(apps, schema_editor):
	Announcement = apps.get_model('subscriptions', 'Announcement')
	Announcement.objects.update(organization=None)


class Migration(migrations.Migration):
	dependencies = [
		('subscriptions', '0026_announcement_add_organization_nullable'),
		('organizations', '0006_alter_organization_slug'),
	]
	operations = [migrations.RunPython(backfill, reverse)]
