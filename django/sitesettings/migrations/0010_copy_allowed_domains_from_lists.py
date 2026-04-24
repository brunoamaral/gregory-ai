# Generated manually on 2026-04-24
#
# Copies allowed_domains values from subscriptions.Lists to the site-level
# sitesettings.CustomSetting model. Domains from all Lists sharing the same
# Site are merged and deduplicated into a single comma-separated string on
# the first CustomSetting for that Site. If a Site has no CustomSetting, a
# minimal one is created so the values are not lost.
#
# Existing CustomSetting.allowed_domains values are preserved: per-list
# domains are merged into the existing set rather than overwriting it.

from django.db import migrations


def _split_domains(value):
	return {d.strip().lower() for d in (value or '').split(',') if d.strip()}


def _join_domains(domains):
	return ', '.join(sorted(domains))


def copy_allowed_domains_forward(apps, schema_editor):
	Lists = apps.get_model('subscriptions', 'Lists')
	CustomSetting = apps.get_model('sitesettings', 'CustomSetting')
	Site = apps.get_model('sites', 'Site')

	domains_by_site = {}
	for lst in Lists.objects.exclude(site_id=None).exclude(allowed_domains='').only('site_id', 'allowed_domains'):
		domains_by_site.setdefault(lst.site_id, set()).update(_split_domains(lst.allowed_domains))

	for site_id, new_domains in domains_by_site.items():
		if not new_domains:
			continue
		custom = CustomSetting.objects.filter(site_id=site_id).order_by('pk').first()
		if custom is None:
			try:
				site = Site.objects.get(pk=site_id)
			except Site.DoesNotExist:
				continue
			custom = CustomSetting.objects.create(
				site_id=site_id,
				title=f'Settings for {site.domain}',
				allowed_domains=_join_domains(new_domains),
			)
			continue
		merged = _split_domains(custom.allowed_domains) | new_domains
		custom.allowed_domains = _join_domains(merged)
		custom.save(update_fields=['allowed_domains'])


class Migration(migrations.Migration):

	dependencies = [
		('sitesettings', '0009_customsetting_allowed_domains'),
		('subscriptions', '0019_announcement_show_tagline_preheader_text'),
	]

	operations = [
		migrations.RunPython(copy_allowed_domains_forward, migrations.RunPython.noop),
	]
