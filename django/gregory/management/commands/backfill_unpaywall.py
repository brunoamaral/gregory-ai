"""Backfill access and/or pdf_link from Unpaywall for science paper articles.

Three modes (mutually exclusive):
  --access      Fill access=NULL for all science papers with a DOI, no age limit.
  --pdf-links   Fill pdf_link=NULL for science papers discovered in the last --days days.
  --all         Run both in a single pass (one Unpaywall call per article).
"""

import csv
import os
import time
from contextlib import contextmanager
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from gregory.models import Articles
from gregory.unpaywall import unpaywall_utils
from sitesettings.models import CustomSetting

_CSV_FIELDS = [
	"article_id", "doi", "title", "status", "fields_updated",
	"access_before", "access_after", "pdf_link_before", "pdf_link_after",
]


class Command(BaseCommand):
	help = "Backfill access and/or pdf_link on science papers using the Unpaywall API."

	def add_arguments(self, parser):
		mode = parser.add_mutually_exclusive_group(required=True)
		mode.add_argument(
			"--access",
			action="store_true",
			help="Populate access for all science papers where it is NULL.",
		)
		mode.add_argument(
			"--pdf-links",
			action="store_true",
			help="Populate pdf_link for science papers from the last --days days where it is NULL.",
		)
		mode.add_argument(
			"--all",
			action="store_true",
			help="Run both --access and --pdf-links in one pass.",
		)
		parser.add_argument(
			"--days",
			type=int,
			default=30,
			help="How many days back to look for --pdf-links / --all (default: 30).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would be updated without writing to the database.",
		)
		parser.add_argument(
			"--sleep",
			type=float,
			default=1.0,
			help="Seconds to wait between Unpaywall API calls (default: 1.0).",
		)
		parser.add_argument(
			"--limit",
			type=int,
			help="Stop after processing this many articles (useful for smoke-testing).",
		)
		parser.add_argument(
			"--csv",
			metavar="PATH",
			dest="csv_path",
			help="Stream a per-article result report to this CSV file.",
		)
		parser.add_argument(
			"--log-file",
			default="backfill_unpaywall.log",
			metavar="PATH",
			help="File that records processed article_ids so resumed runs skip them "
			     "(default: backfill_unpaywall.log). Pass an empty string to disable.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		days = options["days"]
		sleep = max(options["sleep"], 0)
		limit = options.get("limit")
		verbosity = options.get("verbosity", 1)
		csv_path = options.get("csv_path")
		log_path = options.get("log_file") or ""

		run_access = options["access"] or options["all"]
		run_pdf = options["pdf_links"] or options["all"]

		try:
			site = CustomSetting.objects.get(site__domain=os.environ.get("DOMAIN_NAME"))
			admin_email = site.admin_email
		except Exception:
			self.stderr.write(self.style.ERROR("Could not load site settings."))
			return

		if not admin_email:
			self.stderr.write(self.style.ERROR("No admin email in site settings — required by Unpaywall."))
			return

		seen_ids = self._load_log(log_path)
		if seen_ids:
			self.stdout.write(
				f"Log loaded: {len(seen_ids)} already-processed article_ids will be skipped."
			)

		qs = self._build_queryset(run_access, run_pdf, days)
		if limit:
			qs = qs[:limit]

		total = qs.count()
		mode_label = self._mode_label(run_access, run_pdf, days)
		self.stdout.write(f"{mode_label}: {total} articles to process.")
		if not total:
			return

		updated_articles = updated_access = updated_pdf = no_data = 0

		with self._open_csv(csv_path) as csv_writer:
			for i, article in enumerate(qs, 1):
				if article.article_id in seen_ids:
					continue
				access_before = article.access
				pdf_link_before = article.pdf_link

				# getDataByDOI returns {} for both "not in Unpaywall" and internal
				# API errors; no distinction is possible with errors="ignore".
				data = unpaywall_utils.getDataByDOI(article.doi, admin_email)

				if not data:
					no_data += 1
					if verbosity >= 2:
						self.stdout.write(f"  [{i}/{total}] No Unpaywall data: {article.doi}")
					if run_access and article.access is None and not dry_run:
						article.access = "unknown"
						article.save(update_fields=["access"])
					if csv_writer:
						csv_writer.writerow(self._csv_row(
							article, "no_data", [], access_before, pdf_link_before,
						))
					if not dry_run:
						self._append_log(log_path, article.article_id)
					if sleep:
						time.sleep(sleep)
					continue

				# Determine what would change for this article.
				access_new = None
				pdf_new = None

				if run_access and article.access is None:
					access_new = "open" if data.get("is_oa") else "restricted"
					updated_access += 1
					if verbosity >= 2:
						self.stdout.write(
							f"  [{i}/{total}] access={access_new!r}  {article.doi}"
						)

				if run_pdf and article.pdf_link is None:
					oa_loc = data.get("best_oa_location") or {}
					candidate = oa_loc.get("url_for_pdf") or oa_loc.get("url")
					if candidate:
						pdf_new = candidate
						updated_pdf += 1
						if verbosity >= 2:
							self.stdout.write(
								f"  [{i}/{total}] pdf_link set  {article.doi}"
							)

				will_change = [f for f, v in [("access", access_new), ("pdf_link", pdf_new)] if v]
				if will_change:
					updated_articles += 1

				# Apply changes only when not a dry run.
				if not dry_run and will_change:
					if access_new is not None:
						article.access = access_new
					if pdf_new is not None:
						article.pdf_link = pdf_new
					article.save(update_fields=will_change)

				if csv_writer:
					status = ("would_update" if dry_run else "updated") if will_change else "no_change"
					csv_writer.writerow(self._csv_row(
						article, status, will_change, access_before, pdf_link_before,
					))

				if not dry_run:
					self._append_log(log_path, article.article_id)

				if verbosity >= 1 and i % 100 == 0:
					self.stdout.write(f"  Progress: {i}/{total}")

				if sleep and i < total:
					time.sleep(sleep)

		self._print_summary(
			dry_run, run_access, run_pdf, total,
			updated_articles, updated_access, updated_pdf, no_data,
		)
		if csv_path:
			self.stdout.write(f"Report written to {csv_path}")

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------

	@staticmethod
	@contextmanager
	def _open_csv(path):
		"""Context manager that yields a DictWriter streaming to path, or None."""
		if not path:
			yield None
			return
		with open(path, "w", newline="", encoding="utf-8") as fh:
			writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
			writer.writeheader()
			yield writer

	def _build_queryset(self, run_access, run_pdf, days):
		base = (
			Articles.objects.filter(kind="science paper")
			.exclude(doi__isnull=True)
			.exclude(doi="")
		)

		conditions = Q()
		if run_access:
			conditions |= Q(access__isnull=True)
		if run_pdf:
			cutoff = timezone.now() - timedelta(days=days)
			conditions |= Q(pdf_link__isnull=True, discovery_date__gte=cutoff)

		return base.filter(conditions).order_by("article_id").distinct()

	def _mode_label(self, run_access, run_pdf, days):
		if run_access and run_pdf:
			return f"Access (all time) + pdf_link (last {days} days)"
		if run_access:
			return "Access (all time)"
		return f"PDF links (last {days} days)"

	def _print_summary(
		self, dry_run, run_access, run_pdf, total,
		updated_articles, updated_access, updated_pdf, no_data,
	):
		prefix = "[dry run] " if dry_run else ""
		w = len(str(total))
		lines = [
			f"{prefix}Backfill complete. {total} articles queried.",
			f"  {'Updated articles:':<22} {updated_articles:{w}}",
		]
		if run_access:
			lines.append(f"  {'  access:':<22} {updated_access:{w}}")
		if run_pdf:
			lines.append(f"  {'  pdf_link:':<22} {updated_pdf:{w}}")
		lines.append(f"  {'No Unpaywall data:':<22} {no_data:{w}}")
		self.stdout.write(self.style.SUCCESS("\n".join(lines)))

	@staticmethod
	def _csv_row(article, status, will_change, access_before, pdf_link_before):
		return {
			"article_id": article.article_id,
			"doi": article.doi,
			"title": article.title,
			"status": status,
			"fields_updated": ",".join(will_change),
			"access_before": access_before or "",
			"access_after": article.access or "",
			"pdf_link_before": pdf_link_before or "",
			"pdf_link_after": article.pdf_link or "",
		}

	@staticmethod
	def _load_log(path):
		if not path or not os.path.exists(path):
			return set()
		ids = set()
		with open(path) as fh:
			for line in fh:
				line = line.strip()
				if line.isdigit():
					ids.add(int(line))
		return ids

	@staticmethod
	def _append_log(path, article_id):
		if not path:
			return
		with open(path, "a") as fh:
			fh.write(f"{article_id}\n")
