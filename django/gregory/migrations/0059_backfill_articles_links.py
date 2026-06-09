# Backfill Articles.links from the existing canonical link.
#
# Each article's current `link` is filed under a key derived from the URL's
# domain, so the multi-source link merge introduced alongside this migration
# starts from accurate per-source data instead of an empty map.
#
# NOTE: bulk_update deliberately bypasses django-simple-history. Writing a
# historical row per article would duplicate every column of every article in
# the history table for a one-time metadata derivation that adds no new
# information (links is computed from the already-recorded link). Importer
# writes after this migration are fully history-tracked as usual.
#
# NOTE: The URL→key mapping is intentionally frozen inline here rather than
# imported from gregory.utils.trial_utils. Migrations must be self-contained:
# importing runtime helpers means future changes to that module silently alter
# historical migration behaviour and can break fresh installs or rollbacks.

from urllib.parse import urlparse

from django.db import migrations

# Frozen snapshot of REGISTRY_DOMAINS from gregory.utils.trial_utils at the
# time this migration was created (2026-06-09). Do NOT update this dict when
# the live helper changes — create a new migration instead.
_REGISTRY_DOMAINS = {
	'clinicaltrials.gov': 'ctgov',
	'euclinicaltrials.eu': 'ctis',
	'clinicaltrialsregister.eu': 'euctr',
	'trialsearch.who.int': 'ictrp',
	'isrctn.com': 'isrctn',
	'drks.de': 'drks',
	'anzctr.org.au': 'anzctr',
}

BATCH_SIZE = 2000


def _key_from_url(url):
	"""Return a stable key for *url*: a known registry slug or the hostname."""
	if not url:
		return None
	hostname = (urlparse(url).hostname or '').lower()
	if not hostname:
		return None
	if hostname.startswith('www.'):
		hostname = hostname[4:]
	for domain, key in _REGISTRY_DOMAINS.items():
		if hostname == domain or hostname.endswith('.' + domain):
			return key
	return hostname


def backfill_links(apps, schema_editor):
	Articles = apps.get_model("gregory", "Articles")
	queryset = Articles.objects.filter(links__isnull=True).exclude(link="").only("article_id", "link")
	batch = []
	for article in queryset.iterator(chunk_size=BATCH_SIZE):
		key = _key_from_url(article.link)
		if not key:
			continue
		article.links = {key: article.link}
		batch.append(article)
		if len(batch) >= BATCH_SIZE:
			Articles.objects.bulk_update(batch, ["links"], batch_size=BATCH_SIZE)
			batch = []
	if batch:
		Articles.objects.bulk_update(batch, ["links"], batch_size=BATCH_SIZE)


def noop(apps, schema_editor):
	# Reverse: leave links populated; the schema migration removes the column on
	# full rollback anyway.
	pass


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0058_historicalarticles_links_articles_links"),
	]

	operations = [
		migrations.RunPython(backfill_links, noop),
	]
