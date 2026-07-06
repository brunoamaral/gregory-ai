"""One-time backfill that normalises existing Articles.title values.

The RSS ingester historically stored feed titles verbatim, so titles from
PubMed/Wiley feeds contain inline markup (<scp>, <sub>, <sup>, ...) and
pretty-printed newlines/indentation. feedreader_articles now runs every
incoming title through FeedProcessor.clean_title; this command applies the
exact same cleaning to rows already in the database.

Idempotent and resumable: rerunning only touches rows whose stored title
still differs from its cleaned form. Titles are no longer globally unique
(dedup is DOI/link-based), but unique_article_title_link still applies: if
cleaning would make a row collide on (title, link) the collision is
reported and skipped rather than raising an IntegrityError. Cleaning is
done per row via save() so
django-simple-history records the change and the generated utitle column is
recomputed.
"""

from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from gregory.management.commands.feedreader_articles import FeedProcessor
from gregory.models import Articles

CHANGE_REASON = "Backfilled cleaned title (strip presentational markup / collapse whitespace)"


class Command(BaseCommand):
	help = "Normalise existing Articles.title values using FeedProcessor.clean_title."

	def add_arguments(self, parser):
		parser.add_argument(
			"--limit",
			type=int,
			help="Stop after inspecting this many articles (useful for a smoke test).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would change without saving.",
		)

	def handle(self, *args, **options):
		limit = options.get("limit")
		dry_run = options["dry_run"]
		verbosity = options.get("verbosity", 1)

		queryset = Articles.objects.only("article_id", "title").order_by("article_id")
		if limit:
			queryset = queryset[:limit]

		inspected = would_change = updated = collisions = 0

		for article in queryset.iterator(chunk_size=500):
			inspected += 1
			original = article.title
			cleaned = FeedProcessor.clean_title(original)
			if cleaned == original:
				continue

			if verbosity >= 2:
				self.stdout.write(
					f"{article.article_id}:\n  - {original!r}\n  + {cleaned!r}"
				)

			if dry_run:
				would_change += 1
				continue

			article.title = cleaned
			article._change_reason = CHANGE_REASON
			try:
				# Per-row atomic block so a unique-title collision only rolls
				# back that row, leaving the rest of the run intact.
				with transaction.atomic():
					article.save(update_fields=["title"])
				updated += 1
			except IntegrityError:
				collisions += 1
				self.stderr.write(
					self.style.WARNING(
						f"Skipped article {article.article_id}: cleaned title collides "
						f"with an existing row — {cleaned!r}"
					)
				)

		if dry_run:
			self.stdout.write(
				self.style.SUCCESS(
					f"Inspected {inspected} articles. Would update {would_change}."
				)
			)
		else:
			message = f"Inspected {inspected} articles. Updated {updated}."
			if collisions:
				message += f" Skipped {collisions} title collision(s) — see warnings above."
			self.stdout.write(self.style.SUCCESS(message))
