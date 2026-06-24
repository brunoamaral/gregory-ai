"""Backfill access and/or pdf_link from Unpaywall for science paper articles.

Three modes (mutually exclusive):
  --access      Fill access=NULL for all science papers with a DOI, no age limit.
  --pdf-links   Fill pdf_link=NULL for science papers discovered in the last --days days.
  --all         Run both in a single pass (one Unpaywall call per article).
"""

import os
import time

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

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

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		days = options["days"]
		sleep = max(options["sleep"], 0)
		limit = options.get("limit")
		verbosity = options.get("verbosity", 1)

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

		updated_access = updated_pdf = no_data = errors = 0

		for i, article in enumerate(qs, 1):
			try:
				data = unpaywall_utils.getDataByDOI(article.doi, admin_email)
			except Exception as exc:
				if verbosity >= 2:
					self.stderr.write(f"  Error fetching DOI {article.doi}: {exc}")
				errors += 1
				if sleep:
					time.sleep(sleep)
				continue

			if not data:
				no_data += 1
				if verbosity >= 2:
					self.stdout.write(f"  [{i}/{total}] No Unpaywall data: {article.doi}")
				# Mark access as "unknown" so this article isn't re-queried on future runs
				if run_access and article.access is None and not dry_run:
					article.access = "unknown"
					article.save(update_fields=["access"])
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

			if verbosity >= 1 and i % 100 == 0:
				self.stdout.write(f"  Progress: {i}/{total}")

			if sleep and i < total:
				time.sleep(sleep)

		prefix = "Would update" if dry_run else "Updated"
		parts = []
		if run_access:
			parts.append(f"access on {updated_access} articles")
		if run_pdf:
			parts.append(f"pdf_link on {updated_pdf} articles")
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix}: {', '.join(parts)}. "
				f"No Unpaywall data: {no_data}. Errors: {errors}."
			)
		)

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
