"""
Seed and refresh the PublicationType table from NLM's MeSH publication types.

ESearch on db=mesh with ``"Publication Type"[Record Type]`` lists every
publication type record (194 on 2026-10-17), then ESummary gives each one's
MeSH ID, name and scope note. Rows are upserted by MeSH ID (``D016428``), so
NLM's renames flow through. ESearch returns the UID as ``68016428``; the key is
``ds_meshui``, never the UID. Safe to re-run.

Run:
	docker exec gregory python manage.py sync_publication_types
"""

from django.core.management.base import BaseCommand, CommandError
from organizations.models import Organization

from gregory.models import PublicationType
from gregory.utils.ncbi import NcbiClient

SEARCH_TERM = '"Publication Type"[Record Type]'
SUMMARY_BATCH = 200


class Command(BaseCommand):
	help = "Upsert PublicationType rows from NLM's MeSH publication types."

	def add_arguments(self, parser):
		parser.add_argument(
			"--organization",
			type=int,
			help="Organization ID whose NCBI credentials to use. Default: keyless.",
		)

	def handle(self, *args, **options):
		org_id = options.get("organization")
		if org_id:
			try:
				organization = Organization.objects.get(pk=org_id)
			except Organization.DoesNotExist:
				raise CommandError(f"No organization with id {org_id}.")
			client = NcbiClient.for_organization(organization)
		else:
			client = NcbiClient()
		counts = sync_publication_types(client)
		self.stdout.write(
			"Publication types: {created} created, {renamed} renamed, "
			"{rescoped} scope notes updated, {unchanged} unchanged, "
			"{skipped} skipped (no MeSH ID).".format(**counts)
		)


def fetch_publication_types(client):
	"""Return ``([(ui, name, scope_note)], skipped)`` for every MeSH publication type."""
	uids = client.esearch(SEARCH_TERM, db="mesh", retmax=1000).ids
	records = []
	skipped = 0
	for start in range(0, len(uids), SUMMARY_BATCH):
		batch = uids[start : start + SUMMARY_BATCH]
		result = client.esummary("mesh", batch).get("result", {})
		for uid in batch:
			entry = result.get(uid) or {}
			terms = entry.get("ds_meshterms") or []
			ui = entry.get("ds_meshui")
			if not ui and uid.startswith("68"):
				# ESearch UIDs for descriptors are "68" + the digits of the MeSH ID.
				ui = "D" + uid[2:]
			if ui and terms:
				records.append((ui, terms[0], entry.get("ds_scopenote") or ""))
			else:
				skipped += 1
	return records, skipped


def sync_publication_types(client):
	records, skipped = fetch_publication_types(client)
	existing = {t.ui: t for t in PublicationType.objects.filter(ui__in=[r[0] for r in records])}
	counts = {
		"created": 0,
		"renamed": 0,
		"rescoped": 0,
		"unchanged": 0,
		"skipped": skipped,
	}
	to_create, to_update = [], []
	for ui, name, scope_note in records:
		row = existing.get(ui)
		if row is None:
			to_create.append(PublicationType(ui=ui, name=name, scope_note=scope_note))
			counts["created"] += 1
			continue
		changed = False
		if row.name != name:
			row.name = name
			counts["renamed"] += 1
			changed = True
		if row.scope_note != scope_note:
			row.scope_note = scope_note
			counts["rescoped"] += 1
			changed = True
		if changed:
			to_update.append(row)
		else:
			counts["unchanged"] += 1
	# ignore_conflicts: an ingest that met the same type first must not crash the sync.
	PublicationType.objects.bulk_create(to_create, ignore_conflicts=True)
	PublicationType.objects.bulk_update(to_update, ["name", "scope_note"])
	return counts
