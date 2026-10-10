"""Import patent families from EPO Open Patent Services (OPS).

One run walks every active ``epo_ops`` Source: it searches the Source's CQL query over a
publication-date window split into monthly slices, groups the results by DOCDB simple
family, and creates one ``Patents`` row per new family (title, abstract, claims, CPC,
inventors and applicants from the representative publication) plus a
``PatentPublication`` for every member of the family.

Incremental window: ``last_successful_fetch_at`` minus 14 days up to today. The anchor
only advances when every slice completed without hitting the 2,000-results-per-query
cap, a quota error, a missing biblio or the --max-families ceiling, so a partial run is
simply repeated. ``--since`` walks a historical backfill with the same slicing.

Credentials come from the Source's team's organisation (OrganizationCredentials); a
Source whose organisation has none is skipped with a warning.

Families grow after first sight (national phases, grants). Both appear as new
publications in later search windows, so a family seen again is re-synced then, subject
to the family_next_check back-off (see gregory.utils.enrichment).
"""

from datetime import date, timedelta
from urllib.parse import quote

from django.db import transaction
from django.utils import timezone

from gregory.management.base import GregoryBaseCommand
from gregory.models import PatentPublication, Patents, Sources
from gregory.utils.enrichment import backoff_delta, clear_marker, record_fruitless_attempt
from gregory.utils.epo_ops import (
	BULK_BIBLIO_SIZE,
	FULLTEXT_COUNTRIES,
	MAX_RESULTS_PER_QUERY,
	PAGE_SIZE,
	FamilyMember,
	OpsClient,
	OpsError,
	OpsQuotaExceeded,
	build_client,
)
from gregory.utils.patent_applicants import save_applicants
from subscriptions.management.commands.utils.get_credentials import get_epo_ops_credentials

OVERLAP_DAYS = 14
DEFAULT_LOOKBACK_DAYS = 90
REFRESH_WINDOW_DAYS = 6 * 365
ABSTRACT_FALLBACK_ATTEMPTS = 3
FALLBACK_COUNTRY_ORDER = ("EP", "WO", "US")


def month_slices(start: date, end: date):
	"""Calendar-month slices covering [start, end], clipped to the bounds."""
	current = start
	while current <= end:
		next_month = (current.replace(day=1) + timedelta(days=32)).replace(day=1)
		yield current, min(next_month - timedelta(days=1), end)
		current = next_month


def choose_representative(members: list) -> FamilyMember:
	"""The member whose text stands for the family: the one on the application OPS flags
	as representative, unless its office has no full text in OPS and an EP or WO member
	exists (then EP, then WO), so that claims can be fetched."""

	def earliest(candidates):
		return min(candidates, key=lambda m: (m.publication_date or date.max, m.publication_number))

	flagged = [m for m in members if m.is_representative] or members
	representative = earliest(flagged)
	if representative.country not in FULLTEXT_COUNTRIES:
		for country in ("EP", "WO"):
			candidates = [m for m in members if m.country == country]
			if candidates:
				return earliest(candidates)
	return representative


def is_grant_kind(country: str, kind: str) -> bool:
	"""Heuristic grant detection from the kind code: B (EP, US, ...), C (CA, CN, DE) and
	T (translations of granted European patents). WO publications are never grants."""
	return country != "WO" and bool(kind) and kind[0] in ("B", "C", "T")


def espacenet_url(member) -> str:
	return (
		"https://worldwide.espacenet.com/publicationDetails/biblio"
		f"?CC={quote(member.country)}&NR={quote(member.doc_number + member.kind)}&KC=&FT=E"
	)


class _Pending:
	def __init__(self, hit, members, representative):
		self.hit = hit
		self.members = members
		self.representative = representative


class Command(GregoryBaseCommand):
	help = "Import patent families from EPO Open Patent Services for every active epo_ops Source."

	def add_arguments(self, parser):
		parser.add_argument("--source-id", type=int, help="Only process this Source.")
		parser.add_argument(
			"--since",
			help="Backfill mode: start the window at this date (YYYY-MM-DD) instead of the incremental anchor.",
		)
		parser.add_argument(
			"--max-families",
			type=int,
			help="Safety ceiling: stop after creating this many new families (the anchor does not advance).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Search and count, but fetch no family data and write nothing.",
		)

	# -- entry point -------------------------------------------------------

	def handle(self, *args, **options):
		self.dry_run = options["dry_run"]
		self.max_families = options.get("max_families")
		self.since = self._parse_since(options.get("since"))
		self.quota_hit = False
		self.stats = {
			"slices": 0,
			"hits": 0,
			"families_created": 0,
			"families_would_create": 0,
			"publications_added": 0,
			"known_skipped": 0,
			"conflicts": 0,
			"claims_fetched": 0,
			"errors": 0,
		}
		self.ops_totals = {"bytes": 0, "requests": 0, "throttle_states": set()}

		sources = Sources.objects.filter(
			active=True, method="epo_ops", source_for="patents"
		).select_related("team", "team__organization", "subject")
		if options.get("source_id"):
			sources = sources.filter(pk=options["source_id"])

		if not sources.exists():
			self.log("No active epo_ops patent Sources found.", level=1, style_func=self.style.WARNING)
			return

		for source in sources:
			if self.quota_hit:
				break
			self.process_source(source)

		self._report()

	def _parse_since(self, value):
		if not value:
			return None
		try:
			return date.fromisoformat(value)
		except ValueError:
			from django.core.management.base import CommandError

			raise CommandError(f"--since must be YYYY-MM-DD, got {value!r}")

	# -- per source ----------------------------------------------------------

	def get_ops_client(self, key, secret) -> OpsClient:
		"""Overridable in tests."""
		return OpsClient(build_client(key, secret))

	def process_source(self, source):
		self.log(f"Processing patent source: {source.name}", level=1)

		if not source.team or not source.team.organization_id:
			self.log(
				f"  Source '{source.name}' has no team/organisation; skipping.",
				level=1,
				style_func=self.style.WARNING,
			)
			return
		key, secret = get_epo_ops_credentials(source.team.organization)
		if not key:
			self.log(
				f"  No EPO OPS credentials for organisation '{source.team.organization}'; skipping '{source.name}'.",
				level=1,
				style_func=self.style.WARNING,
			)
			return
		base_query = (source.ops_cql_query or "").strip()
		if not base_query:
			self.log(
				f"  Source '{source.name}' has no CQL query; skipping.",
				level=1,
				style_func=self.style.WARNING,
			)
			return

		self.ops = self.get_ops_client(key, secret)
		self.source = source
		self.pending = []
		self.seen_publications = set()
		self.seen_families = set()
		self.source_complete = True
		fetch_started = timezone.now()
		today = fetch_started.date()

		if self.since:
			start = self.since
		elif source.last_successful_fetch_at:
			start = (source.last_successful_fetch_at - timedelta(days=OVERLAP_DAYS)).date()
		else:
			start = today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
			self.log(
				f"  No previous successful fetch recorded; using the last {DEFAULT_LOOKBACK_DAYS} days. "
				"Use --since for a longer backfill.",
				level=1,
			)
		self.log(f"  Window: {start} to {today}", level=1)

		try:
			for slice_start, slice_end in month_slices(start, today):
				if self.max_families_reached():
					self.source_complete = False
					break
				self.stats["slices"] += 1
				hits, capped = self.search_slice(base_query, slice_start, slice_end)
				if capped:
					self.source_complete = False
					self.log(
						f"  Slice {slice_start}..{slice_end} hit the {MAX_RESULTS_PER_QUERY}-result cap "
						"at one day; narrow the CQL query. The anchor will not advance.",
						level=1,
						style_func=self.style.WARNING,
					)
				self.log(f"  Slice {slice_start}..{slice_end}: {len(hits)} results", level=2)
				for hit in hits:
					if self.max_families_reached():
						self.source_complete = False
						break
					self.handle_hit(hit)
			self.flush_pending()
		except OpsQuotaExceeded as exc:
			self.quota_hit = True
			self.source_complete = False
			self.stats["errors"] += 1
			self.log(
				f"  OPS quota exhausted ({exc}); stopping. Rerun next week or after the hourly reset.",
				level=1,
				style_func=self.style.ERROR,
			)
		except OpsError as exc:
			self.source_complete = False
			self.stats["errors"] += 1
			self.log(f"  OPS error for '{source.name}': {exc}", level=1, style_func=self.style.ERROR)
		finally:
			self.ops_totals["bytes"] += self.ops.bytes_received
			self.ops_totals["requests"] += self.ops.request_count
			self.ops_totals["throttle_states"] |= self.ops.throttle_states

		if self.source_complete and not self.dry_run:
			source.last_successful_fetch_at = fetch_started
			source.save(update_fields=["last_successful_fetch_at"])
		else:
			self.log(
				f"  Run for '{source.name}' was not complete; not advancing the incremental anchor.",
				level=1,
				style_func=self.style.WARNING,
			)

	def max_families_reached(self) -> bool:
		return bool(
			self.max_families
			and self.stats["families_created"] + len(self.pending) >= self.max_families
		)

	# -- searching -----------------------------------------------------------

	def _cql(self, base_query, start, end):
		return f'({base_query}) and pd within "{start:%Y%m%d} {end:%Y%m%d}"'

	def search_slice(self, base_query, start, end):
		"""All hits for [start, end]; halves the slice when it exceeds the per-query cap.
		Returns (hits, capped) where capped means a single day still exceeded the cap."""
		cql = self._cql(base_query, start, end)
		first = self.ops.search(cql, 1, PAGE_SIZE)
		if first.total == 0:
			return [], False
		if first.total > MAX_RESULTS_PER_QUERY and start < end:
			middle = start + (end - start) // 2
			left, left_capped = self.search_slice(base_query, start, middle)
			right, right_capped = self.search_slice(base_query, middle + timedelta(days=1), end)
			return left + right, left_capped or right_capped
		capped = first.total > MAX_RESULTS_PER_QUERY
		limit = min(first.total, MAX_RESULTS_PER_QUERY)
		hits = list(first.hits)
		begin = PAGE_SIZE + 1
		while begin <= limit:
			page = self.ops.search(cql, begin, min(begin + PAGE_SIZE - 1, MAX_RESULTS_PER_QUERY))
			if not page.hits:
				break
			hits.extend(page.hits)
			begin += PAGE_SIZE
		return hits, capped

	# -- per result ----------------------------------------------------------

	def handle_hit(self, hit):
		number = hit.publication_number
		if number in self.seen_publications:
			return
		self.seen_publications.add(number)
		self.stats["hits"] += 1

		existing_pub = (
			PatentPublication.objects.select_related("patent").filter(publication_number=number).first()
		)
		if existing_pub is not None:
			patent = existing_pub.patent
			if patent.family_id and patent.family_id != hit.family_id:
				self.stats["conflicts"] += 1
				self.log(
					f"  Family conflict: {number} is stored in family {patent.family_id} but OPS now "
					f"reports {hit.family_id}; left unchanged for review.",
					level=1,
					style_func=self.style.WARNING,
				)
				return
			self.stats["known_skipped"] += 1
			if not self.dry_run:
				self.attach(patent)
				self.refresh_if_due(patent, hit)
			return

		patent = Patents.objects.filter(family_id=hit.family_id).first()
		if patent is not None:
			if not self.dry_run:
				self.attach(patent)
				self.sync_family(patent, hit)
			return

		if hit.family_id in self.seen_families:
			return
		self.seen_families.add(hit.family_id)

		if self.dry_run:
			self.stats["families_would_create"] += 1
			return

		members = self.ops.family(hit.country, hit.doc_number, hit.kind, family_id=hit.family_id)
		if not members:
			members = [
				FamilyMember(
					family_id=hit.family_id,
					country=hit.country,
					doc_number=hit.doc_number,
					kind=hit.kind,
					is_representative=True,
				)
			]
		self.pending.append(_Pending(hit, members, choose_representative(members)))
		if len(self.pending) >= BULK_BIBLIO_SIZE:
			self.flush_pending()

	def attach(self, patent):
		"""Add this Source, its team and its subject. Never replaces existing relations."""
		patent.sources.add(self.source)
		if self.source.team_id:
			patent.teams.add(self.source.team)
		if self.source.subject_id:
			patent.subjects.add(self.source.subject)

	# -- new families --------------------------------------------------------

	def flush_pending(self):
		if not self.pending:
			return
		pending, self.pending = self.pending, []
		biblios = {}
		for start in range(0, len(pending), BULK_BIBLIO_SIZE):
			chunk = pending[start : start + BULK_BIBLIO_SIZE]
			request = [(p.representative.country, p.representative.doc_number, p.representative.kind) for p in chunk]
			for biblio in self.ops.biblio_bulk(request):
				biblios[biblio.publication_number] = biblio

		for item in pending:
			biblio = biblios.get(item.representative.publication_number)
			if biblio is None:
				self.source_complete = False
				self.stats["errors"] += 1
				self.log(
					f"  No biblio returned for {item.representative.publication_number} "
					f"(family {item.hit.family_id}); will be retried on the next run.",
					level=1,
					style_func=self.style.WARNING,
				)
				continue
			self.create_family(item, biblio)

	def _fallback_abstract(self, item, biblio):
		"""Look for an English abstract on other members when the representative has none."""
		others = [m for m in item.members if m is not item.representative]
		others.sort(
			key=lambda m: (
				FALLBACK_COUNTRY_ORDER.index(m.country) if m.country in FALLBACK_COUNTRY_ORDER else 99,
				m.publication_date or date.max,
			)
		)
		for member in others[:ABSTRACT_FALLBACK_ATTEMPTS]:
			for alternative in self.ops.biblio_bulk([(member.country, member.doc_number, member.kind)]):
				if alternative.abstract:
					return alternative
		return None

	def create_family(self, item, biblio):
		representative = item.representative
		fallback = None
		if not biblio.abstract:
			fallback = self._fallback_abstract(item, biblio)

		claims_text = None
		if representative.country in FULLTEXT_COUNTRIES:
			claims = self.ops.claims(representative.country, representative.doc_number, representative.kind)
			if claims:
				claims_text = claims.text
				self.stats["claims_fetched"] += 1

		priority_dates = [d for m in item.members for d in m.priority_dates] + list(biblio.priority_dates)
		if not priority_dates:
			priority_dates = [m.application_date for m in item.members if m.application_date]
		publication_dates = [m.publication_date for m in item.members if m.publication_date]
		link = espacenet_url(representative)
		title = biblio.title or (fallback.title if fallback else None) or f"Patent family {item.hit.family_id}"

		with transaction.atomic():
			patent = Patents(
				family_id=item.hit.family_id,
				title=title,
				summary=biblio.abstract or (fallback.abstract if fallback else None),
				claims=claims_text,
				representative_publication=representative.publication_number,
				link=link,
				links={"espacenet": link},
				earliest_priority_date=min(priority_dates) if priority_dates else None,
				earliest_publication_date=min(publication_dates) if publication_dates else None,
				has_grant=any(is_grant_kind(m.country, m.kind) for m in item.members),
				cpc_classes=biblio.cpc,
				ipc_classes=biblio.ipc,
				inventors=biblio.inventors,
				family_next_check=timezone.now() + backoff_delta(1),
			)
			patent._change_reason = f"Created from EPO OPS Source: {self.source.name}"[:100]
			patent.save()
			self.add_publications(patent, item.members)
			save_applicants(patent, biblio)
			self.attach(patent)
		self.stats["families_created"] += 1

	def add_publications(self, patent, members) -> int:
		"""Create PatentPublication rows for members not stored yet. A publication number
		already stored under another family is a conflict: logged, never moved."""
		created = 0
		for member in members:
			number = member.publication_number
			existing = PatentPublication.objects.filter(publication_number=number).first()
			if existing is not None:
				if existing.patent_id != patent.pk:
					self.stats["conflicts"] += 1
					self.log(
						f"  Family conflict: {number} belongs to patent {existing.patent_id}, "
						f"not {patent.pk} (family {patent.family_id}); left unchanged.",
						level=1,
						style_func=self.style.WARNING,
					)
				continue
			PatentPublication.objects.create(
				patent=patent,
				publication_number=number,
				country=member.country,
				doc_number=member.doc_number,
				kind=member.kind,
				publication_date=member.publication_date,
				application_number=member.application_number,
				application_date=member.application_date,
				sources=["epo_ops"],
			)
			created += 1
		self.stats["publications_added"] += created
		return created

	# -- known families --------------------------------------------------------

	def sync_family(self, patent, hit) -> int:
		"""Fetch the family and add any member publications not stored yet. Returns the
		number added and updates the derived family fields."""
		members = self.ops.family(hit.country, hit.doc_number, hit.kind, family_id=hit.family_id)
		if not members:
			members = [
				FamilyMember(
					family_id=hit.family_id,
					country=hit.country,
					doc_number=hit.doc_number,
					kind=hit.kind,
				)
			]
		with transaction.atomic():
			added = self.add_publications(patent, members)
			if added:
				dates = [m.publication_date for m in members if m.publication_date]
				if dates and (
					patent.earliest_publication_date is None
					or min(dates) < patent.earliest_publication_date
				):
					patent.earliest_publication_date = min(dates)
				if any(is_grant_kind(m.country, m.kind) for m in members):
					patent.has_grant = True
				patent._change_reason = f"New family members from EPO OPS Source: {self.source.name}"[:100]
				patent.save()
		return added

	def refresh_if_due(self, patent, hit):
		"""Re-sync a recent family when its back-off marker is due."""
		if patent.family_next_check and patent.family_next_check > timezone.now():
			return
		cutoff = timezone.now().date() - timedelta(days=REFRESH_WINDOW_DAYS)
		if patent.earliest_priority_date and patent.earliest_priority_date < cutoff:
			return
		if self.sync_family(patent, hit):
			clear_marker(patent, "family")
			patent.family_next_check = timezone.now() + backoff_delta(1)
			patent.save(update_fields=["family_next_check"])
		else:
			record_fruitless_attempt(patent, "family")

	# -- summary ---------------------------------------------------------------

	def _report(self):
		s = self.stats
		verb = "Would create" if self.dry_run else "Created"
		created = s["families_would_create"] if self.dry_run else s["families_created"]
		self.log(
			f"{verb} {created} patent famil(ies); {s['publications_added']} publication(s) added, "
			f"{s['known_skipped']} known publication(s) skipped, {s['claims_fetched']} claims fetched, "
			f"{s['conflicts']} family conflict(s), {s['errors']} error(s) across {s['slices']} slice(s).",
			level=1,
			style_func=self.style.SUCCESS,
		)
		self.log(
			f"OPS: {self.ops_totals['requests']} request(s), "
			f"{self.ops_totals['bytes'] / 1_000_000:.2f} MB received.",
			level=1,
		)
		for state in sorted(self.ops_totals["throttle_states"]):
			self.log(f"  throttling seen: {state}", level=3)
