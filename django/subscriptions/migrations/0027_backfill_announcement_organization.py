from django.db import migrations


def backfill(apps, schema_editor):
	Announcement = apps.get_model('subscriptions', 'Announcement')
	Organization = apps.get_model('organizations', 'Organization')

	step_a_ids = []
	step_b_ids = []
	step_c_ids = []
	unresolved_ids = []

	qs = Announcement.objects.filter(organization__isnull=True)
	for ann in qs.iterator():
		# Step A — from the first attached list's team.organization.
		first_list = ann.lists.select_related('team').order_by('pk').first()
		if first_list is not None and first_list.team is not None:
			ann.organization_id = first_list.team.organization_id
			ann.save(update_fields=['organization'])
			step_a_ids.append(ann.pk)
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
				step_b_ids.append(ann.pk)
				continue

		# Step C — first Organization globally.
		org = Organization.objects.order_by('pk').first()
		if org is None:
			unresolved_ids.append(ann.pk)
			continue
		ann.organization_id = org.pk
		ann.save(update_fields=['organization'])
		step_c_ids.append(ann.pk)

	if unresolved_ids:
		raise RuntimeError(
			"No Organization rows exist; cannot backfill "
			f"Announcements: {unresolved_ids}"
		)

	if step_a_ids:
		print(f"Backfill step A (from lists): {step_a_ids}")
	if step_b_ids:
		print(f"Backfill step B (from created_by): {step_b_ids}")
	if step_c_ids:
		print(f"Backfill step C (global fallback): {step_c_ids}")


def reverse(apps, schema_editor):
	Announcement = apps.get_model('subscriptions', 'Announcement')
	Announcement.objects.update(organization=None)


class Migration(migrations.Migration):
	dependencies = [
		('subscriptions', '0026_announcement_add_organization_nullable'),
		('organizations', '0006_alter_organization_slug'),
	]
	operations = [migrations.RunPython(backfill, reverse)]
