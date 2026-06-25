"""Backfill access and/or pdf_link from Unpaywall for science paper articles.

Three modes (mutually exclusive):
  --access      Fill access=NULL for all science papers with a DOI, no age limit.
  --pdf-links   Fill pdf_link=NULL for science papers discovered in the last --days days.
  --all         Run both in a single pass (one Unpaywall call per article).
"""

import csv
import os
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from gregory.models import Articles
from gregory.unpaywall import unpaywall_utils
from sitesettings.models import CustomSetting


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
			help="Write a per-article result report to this CSV file.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		days = options["days"]
		sleep = max(options["sleep"], 0)
		limit = options.get("limit")
		verbosity = options.get("verbosity", 1)
		csv_path = options.get("csv_path")

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

		qs = self._build_queryset(run_access, run_pdf, days)
		if limit:
			qs = qs[:limit]

		total = qs.count()
		mode_label = self._mode_label(run_access, run_pdf, days)
		self.stdout.write(f"{mode_label}: {total} articles to process.")
		if not total:
			return

		updated_articles = updated_access = updated_pdf = no_data = errors = 0
		csv_rows = [] if csv_path else None

		for i, article in enumerate(qs, 1):
			access_before = article.access
			pdf_link_before = article.pdf_link

			try:
				data = unpaywall_utils.getDataByDOI(article.doi, admin_email)
			except Exception as exc:
				if verbosity >= 2:
					self.stderr.write(f"  Error fetching DOI {article.doi}: {exc}")
				errors += 1
				if csv_rows is not None:
					csv_rows.append(self._csv_row(
						article, "error", [], access_before, pdf_link_before,
					))
				if sleep:
					time.sleep(sleep)
				continue

			if not data:
				no_data += 1
				if verbosity >= 2:
					self.stdout.write(f"  [{i}/{total}] No Unpaywall data: {article.doi}")
				if run_access and article.access is None and not dry_run:
					article.access = "unknown"
					article.save(update_fields=["access"])
				if csv_rows is not None:
					csv_rows.append(self._csv_row(
						article, "no_data", [], access_before, pdf_link_before,
					))
				if sleep:
					time.sleep(sleep)
				continue

			changed_fields = []

			if run_access and article.access is None:
				new_access = "open" if data.get("is_oa") else "restricted"
				if not dry_run:
					article.access = new_access
					changed_fields.append("access")
				updated_access += 1
				if verbosity >= 2:
					self.stdout.write(
						f"  [{i}/{total}] access={new_access!r}  {article.doi}"
					)

			if run_pdf and article.pdf_link is None:
				oa_loc = data.get("best_oa_location") or {}
				pdf_url = oa_loc.get("url_for_pdf") or oa_loc.get("url")
				if pdf_url:
					if not dry_run:
						article.pdf_link = pdf_url
						changed_fields.append("pdf_link")
					updated_pdf += 1
					if verbosity >= 2:
						self.stdout.write(
							f"  [{i}/{total}] pdf_link set  {article.doi}"
						)

			if changed_fields:
				article.save(update_fields=changed_fields)
				updated_articles += 1

			if csv_rows is not None:
				status = "updated" if changed_fields else "no_change"
				csv_rows.append(self._csv_row(
					article, status, changed_fields, access_before, pdf_link_before,
				))

			if verbosity >= 1 and i % 100 == 0:
				self.stdout.write(f"  Progress: {i}/{total}")

			if sleep and i < total:
				time.sleep(sleep)

		self._print_summary(
			dry_run, run_access, run_pdf, total,
			updated_articles, updated_access, updated_pdf, no_data, errors,
		)

		if csv_path and csv_rows:
			self._write_csv(csv_path, csv_rows)
			self.stdout.write(f"Report written to {csv_path}")

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------

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
		updated_articles, updated_access, updated_pdf, no_data, errors,
	):
		prefix = "[dry run] " if dry_run else ""
		w = len(str(total))  # column width for right-aligning counts
		lines = [
			f"{prefix}Backfill complete. {total} articles queried.",
			f"  {'Updated articles:':<22} {updated_articles:{w}}",
		]
		if run_access:
			lines.append(f"  {'  access:':<22} {updated_access:{w}}")
		if run_pdf:
			lines.append(f"  {'  pdf_link:':<22} {updated_pdf:{w}}")
		lines.append(f"  {'No Unpaywall data:':<22} {no_data:{w}}")
		lines.append(f"  {'Errors:':<22} {errors:{w}}")
		self.stdout.write(self.style.SUCCESS("\n".join(lines)))

	@staticmethod
	def _csv_row(article, status, changed_fields, access_before, pdf_link_before):
		return {
			"article_id": article.article_id,
			"doi": article.doi,
			"title": article.title,
			"status": status,
			"fields_updated": ",".join(changed_fields),
			"access_before": access_before or "",
			"access_after": article.access or "",
			"pdf_link_before": pdf_link_before or "",
			"pdf_link_after": article.pdf_link or "",
		}

	@staticmethod
	def _write_csv(path, rows):
		fieldnames = [
			"article_id", "doi", "title", "status", "fields_updated",
			"access_before", "access_after", "pdf_link_before", "pdf_link_after",
		]
		with open(path, "w", newline="", encoding="utf-8") as fh:
			writer = csv.DictWriter(fh, fieldnames=fieldnames)
			writer.writeheader()
			writer.writerows(rows)
