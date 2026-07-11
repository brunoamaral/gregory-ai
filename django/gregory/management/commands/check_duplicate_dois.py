"""
Report any DOI held by more than one article.

Once ``merge_duplicate_articles`` has cleaned the backlog and the
``unique_article_doi`` constraint is in place this should always report zero.
It is a cheap safety net for data that enters outside Django's ORM (raw-SQL
migrations, bulk restores) where the constraint's application-level guard is
bypassed. Suitable for a weekly cron job.

Exits non-zero when duplicates are found so a cron wrapper can alert.

Usage:
  docker exec gregory python manage.py check_duplicate_dois
"""

import sys

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db.models.functions import Lower

from gregory.models import Articles


class Command(BaseCommand):
	help = "Report DOIs shared by more than one article (should be zero)."

	def handle(self, *args, **options):
		dupes = (
			Articles.objects.exclude(doi__isnull=True)
			.exclude(doi="")
			.annotate(ldoi=Lower("doi"))
			.values("ldoi")
			.annotate(n=Count("article_id"))
			.filter(n__gt=1)
			.order_by("-n")
		)

		total = 0
		for row in dupes:
			ldoi = row["ldoi"]
			ids = list(
				Articles.objects.annotate(l=Lower("doi"))
				.filter(l=ldoi)
				.values_list("article_id", flat=True)
			)
			total += 1
			self.stderr.write(
				self.style.ERROR(
					f"DOI {ldoi} is held by {row['n']} articles: {ids}"
				)
			)

		if total:
			self.stderr.write(
				self.style.ERROR(
					f"Found {total} duplicated DOI(s). Resolve with "
					"`manage.py merge_duplicate_articles --scan`."
				)
			)
			sys.exit(1)

		self.stdout.write(self.style.SUCCESS("No duplicate DOIs found."))
