"""One-time (idempotent, re-runnable) backfill that strips HTML markup from WHO
ICTRP trial text fields already in the database.

importWHOXML now cleans every field it reads at ingest time (see
gregory.utils.text_utils.clean_field_html), but rows imported before that change
still hold raw markup (<br>, <b>, <a href="...">, ...) from six different ICTRP
source registries. This command applies the same cleaning to those existing rows.

`summary` is deliberately excluded: the CTIS feedreader composes a labeled HTML
block (`<b>Trial number</b>: ...<br/>...`) and the EUCTR RSS summary embeds
`<a href>` links — both are rendered as HTML by consumers. Cleaning it would
destroy the display summary. See WHO-HTML-CLEANUP-PLAN.md.

Selection is narrowed to rows where some column matches a whitelisted-tag regex
(gregory.utils.text_utils.ALLOWED_TAGS), so the far larger set of rows with no
HTML at all is never touched.

Only the columns that actually changed are written, one bulk_update per column,
never Trials.save() -- save() fans out to sync_trial_countries() and every
registered field normalizer (~3+ queries per row); see backfill_trial_countries.py
/ backfill_trial_sponsors.py for the same reasoning.

inclusion_gender is safe to bulk_update here: this command runs BEFORE
INCLUSION-GENDER-NORMALIZATION-PLAN.md's inclusion_gender_normalized field exists.
If this command is ever re-run after that field lands, follow it with
`backfill_trial_normalized_fields --field inclusion_gender` -- bulk_update bypasses
save(), so it would not recompute that normalizer.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from gregory.models import Trials
from gregory.utils.text_utils import ALLOWED_TAGS, clean_field_html

# summary is intentionally absent -- see module docstring.
COLUMNS = [
	"title",
	"scientific_title",
	"inclusion_criteria",
	"exclusion_criteria",
	"condition",
	"intervention",
	"primary_outcome",
	"secondary_outcome",
	"study_design",
	"inclusion_gender",
	"source_support",
]

# Same whitelist the cleaner itself treats as real markup, so row selection can't
# diverge from what clean_field_html actually strips.
TAG_PATTERN = r"</?(" + "|".join(ALLOWED_TAGS) + r")(\s[^<>]*)?/?>"


class Command(BaseCommand):
	help = "Strip HTML markup from WHO ICTRP trial text fields already in the database (summary excluded by design)."

	def add_arguments(self, parser):
		parser.add_argument(
			"--batch-size",
			type=int,
			default=1000,
			help="Rows per bulk_update batch (default: 1000).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would change without saving.",
		)

	def handle(self, *args, **options):
		batch_size = max(options["batch_size"], 1)
		dry_run = options["dry_run"]

		selection_q = Q()
		for col in COLUMNS:
			selection_q |= Q(**{f"{col}__iregex": TAG_PATTERN})

		queryset = (
			Trials.objects.filter(selection_q)
			.only("trial_id", *COLUMNS)
			.order_by("trial_id")
		)

		scanned = 0
		rows_changed = 0
		column_changed_counts = {col: 0 for col in COLUMNS}
		dirty_by_column = {col: [] for col in COLUMNS}
		samples = []

		def flush(col):
			if dirty_by_column[col]:
				Trials.objects.bulk_update(
					dirty_by_column[col], [col], batch_size=batch_size
				)
				dirty_by_column[col] = []

		for trial in queryset.iterator(chunk_size=2000):
			scanned += 1
			row_changed = False
			for col in COLUMNS:
				raw = getattr(trial, col)
				if not raw:
					continue
				cleaned = clean_field_html(raw)
				if cleaned is None and col == "title":
					# title is NOT NULL; never blank it even if it were all markup.
					continue
				if cleaned == raw:
					continue

				row_changed = True
				column_changed_counts[col] += 1
				if dry_run:
					if len(samples) < 10:
						samples.append((trial.trial_id, col, raw, cleaned))
				else:
					dirty_by_column[col].append(
						Trials(trial_id=trial.trial_id, **{col: cleaned})
					)
					if len(dirty_by_column[col]) >= batch_size:
						flush(col)

			if row_changed:
				rows_changed += 1

		if not dry_run:
			for col in COLUMNS:
				flush(col)

		prefix = "Would change" if dry_run else "Changed"
		self.stdout.write(
			self.style.SUCCESS(
				f"Scanned {scanned} trial(s) matching the HTML-tag selection. "
				f"{prefix} {rows_changed} row(s)."
			)
		)
		for col in COLUMNS:
			count = column_changed_counts[col]
			if count:
				self.stdout.write(f"  {col}: {count}")

		if dry_run and samples:
			self.stdout.write("\nSample changes (up to 10):")
			for trial_id, col, before, after in samples:
				self.stdout.write(f"  trial {trial_id} / {col}:\n    - {before!r}\n    + {after!r}")
