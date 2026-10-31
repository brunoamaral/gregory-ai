"""
Fetch the DailyMed labels of the FDA medicines worth checking, and store their indications.

The FDA publishes no disease areas, so the matcher decides whether an FDA medicine belongs
in a category by reading its label: the "Indications and usage" text of each DailyMed label
filed under the application (see gregory/services/medicine_matching.py). Downloading a label
for every application would be 3,000 labels and about 700 MB, so only **candidates** get one:
FDA medicines that pass the pattern, combination and exclusion steps for at least one
automatic category whose subjects have ``fda_indication_terms``. That is about 80
applications today.

For each candidate:

1. Ask DailyMed which labels the application has (one cheap JSON request).
2. Download a label's XML only when its ``setid`` is new or its ``spl_version`` changed;
   keep the stored entry for an unchanged label and drop entries whose label is no longer
   listed.
3. Store them as ``Medicine.label_indications`` (``[{setid, version, title, published,
   indication}]``), set ``label_checked_at``, and set ``indication`` from the main label:
   the one whose title starts with a brand name (the first brand wins), else the most
   recently published.
4. On a CBER row with no ``url`` yet, set it to the main label's DailyMed page: the Purple
   Book has no per-product page.

A medicine that stops being a candidate (a category lost its terms or its pattern) keeps
the labels it has, and the category save hook links against them at once. So every FDA row
that already has stored labels is refreshed too, candidate or not.

A DailyMed error on one application is reported, keeps that application's stored labels and
moves on. So does a 200 with an empty list for an application that has stored labels: it is
more likely a DailyMed hiccup than every label withdrawn, and wiping them would make
match_medicines delete the links. A label that drops out while others stay listed is removed.
The command never fails the run because of DailyMed.

A category edit that creates new candidates is picked up by the next pipeline run; the
category save hook links with the labels already stored and fetches nothing.

Run:
	docker exec gregory python manage.py sync_fda_labels
	docker exec gregory python manage.py sync_fda_labels --dry-run
	docker exec gregory python manage.py sync_fda_labels --application BLA125326
"""

import re
from collections import Counter

from django.core.management.base import CommandError
from django.utils import timezone

from gregory.management.base import GregoryBaseCommand
from gregory.models import CategoryType, Medicine, Regulator, TeamCategory
from gregory.services.medicine_linking import load_medicines
from gregory.services.medicine_matching import category_fda_terms, pattern_match
from gregory.utils.dailymed import (
	DailyMedError,
	fetch_indications,
	label_page_url,
	list_labels,
)

CBER = "CBER"


class Command(GregoryBaseCommand):
	help = (
		"Download the DailyMed labels of the FDA medicines that could belong in a category, "
		"and store their indications."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--application",
			metavar="NUMBER",
			help="Refresh one application (NDA022561, BLA125326), whether or not it is a candidate.",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report the candidates and the labels that would be downloaded; download and write nothing.",
		)

	def handle(self, *args, **options):
		self.dry_run = options["dry_run"]
		prefix = "[dry run] " if self.dry_run else ""
		medicines, current = self._candidates(options["application"])
		counts = Counter()
		counts["candidates"] = len(current)
		counts["stored only"] = len(medicines) - len(current)
		for medicine in medicines:
			self._refresh(medicine, counts)

		self.stdout.write(
			f"{prefix}FDA labels: {counts['candidates']} candidates"
			+ (f" and {counts['stored only']} others with stored labels" if counts["stored only"] else "")
			+ f", {counts['list calls']} list calls, "
			f"{counts['downloaded']} labels {'to download' if self.dry_run else 'downloaded'}, "
			f"{counts['unchanged']} unchanged, {counts['failed']} failed, "
			f"{counts['no label']} candidates with no label, "
			f"{counts['emptied']} emptied (kept)."
		)
		if self.dry_run:
			self.stdout.write("Dry run: nothing was downloaded or written.")

	# --- candidates ----------------------------------------------------

	def _candidates(self, application):
		"""``(medicines, current)``: the FDA medicines whose labels to refresh, as full rows
		(``raw`` deferred), and the set of primary keys among them that are candidates now.

		The others are medicines that stopped being candidates (a category lost its terms or
		its pattern) but still hold labels: the category save hook links against stored
		labels at once, so those must not go stale."""
		stored = Medicine.objects.filter(regulator=Regulator.FDA).defer("raw")
		if application:
			medicine = stored.filter(fda_application_number=application.strip().upper()).first()
			if medicine is None:
				raise CommandError(f"No FDA medicine has application number {application}.")
			return [medicine], {medicine.pk}

		# Only the categories whose subjects name a disease: without terms the matcher
		# never reads a label, so a download would be wasted.
		categories = [
			category
			for category in TeamCategory.objects.filter(
				category_type=CategoryType.AUTOMATIC
			).prefetch_related("subjects")
			if category_fda_terms(category)
		]
		ids = {
			medicine.pk
			for medicine in load_medicines()
			if medicine.regulator == Regulator.FDA
			and any(pattern_match(category, medicine) for category in categories)
		}
		with_labels = set(
			stored.filter(label_checked_at__isnull=False)
			.exclude(label_indications=[])
			.values_list("pk", flat=True)
		)
		medicines = list(stored.filter(pk__in=ids | with_labels).order_by("fda_application_number"))
		return medicines, ids

	# --- one application -----------------------------------------------

	def _refresh(self, medicine, counts):
		number = medicine.fda_application_number
		counts["list calls"] += 1
		try:
			listed = list_labels(number)
		except DailyMedError as exc:
			counts["failed"] += 1
			self.stdout.write(self.style.WARNING(f"{number}: DailyMed list failed ({exc}); kept its stored labels."))
			return

		stored = {
			entry.get("setid"): entry
			for entry in medicine.label_indications or []
			if isinstance(entry, dict) and entry.get("setid")
		}
		if not listed and stored:
			# A 200 with an empty list over labels we hold is more likely a DailyMed hiccup than
			# every label withdrawn: wiping them would make match_medicines delete the links.
			counts["emptied"] += 1
			self.stdout.write(
				self.style.WARNING(
					f"{number}: DailyMed lists no labels, but {len(stored)} are stored; kept them."
				)
			)
			if not self.dry_run:
				Medicine.objects.filter(pk=medicine.pk).update(label_checked_at=timezone.now())
			return
		entries = []
		for label in listed:
			old = stored.get(label.setid)
			if old is not None and str(old.get("version")) == label.version:
				counts["unchanged"] += 1
				entries.append(
					{**old, "version": label.version, "title": label.title, "published": label.published}
				)
				continue
			if self.dry_run:
				counts["downloaded"] += 1
				continue
			try:
				text = fetch_indications(label.setid)
			except DailyMedError as exc:
				counts["failed"] += 1
				self.stdout.write(
					self.style.WARNING(f"{number}: label {label.setid} failed ({exc}); "
					+ ("kept its stored copy." if old is not None else "it will be retried next run."))
				)
				if old is not None:
					entries.append(old)
				continue
			counts["downloaded"] += 1
			entries.append(
				{
					"setid": label.setid,
					"version": label.version,
					"title": label.title,
					"published": label.published,
					"indication": text,
				}
			)
		if not listed:
			counts["no label"] += 1
		if self.dry_run:
			return

		main = self._main_label(entries, medicine.brand_names)
		update = {
			"label_indications": entries,
			"label_checked_at": timezone.now(),
			"indication": main["indication"] if main else "",
		}
		if main and medicine.fda_center == CBER and not medicine.url:
			update["url"] = label_page_url(main["setid"])
		Medicine.objects.filter(pk=medicine.pk).update(**update)

	@staticmethod
	def _main_label(entries, brand_names):
		"""The label whose indication the medicine shows: one whose title starts with a brand
		name (an earlier brand beats a later one), else the most recently published. Labels
		with no indications text only stand in when no label has any."""
		usable = [entry for entry in entries if entry.get("indication")] or entries
		if not usable:
			return None
		brands = [
			re.compile(r"^" + re.escape(brand.strip()) + r"(?!\w)", re.IGNORECASE)
			for brand in brand_names or []
			if brand.strip()
		]

		def brand_rank(entry):
			for rank, pattern in enumerate(brands):
				if pattern.search(entry.get("title") or ""):
					return rank
			return len(brands)

		# Published dates are ISO strings. Newest first, and min() keeps the first of equal
		# ranks, so the best brand wins and, within it, the most recent label.
		newest_first = sorted(usable, key=lambda entry: entry.get("published") or "", reverse=True)
		return min(newest_first, key=brand_rank)
