"""Merge the clear-cut author duplicates: groups of ``Authors`` rows sharing an OpenAlex author
ID, with the same family name and given initial and no conflict (see
``gregory.services.author_merge_candidates``).

Never touches "names differ" or blocked groups, or groups a superuser dismissed. Each group is
merged in its own transaction; ``--limit`` caps the groups per run so the backlog clears over
several pipeline runs.
"""

import fcntl
import os
import tempfile

from django.core.management.base import BaseCommand, CommandError

from gregory.services import author_merge_candidates as svc

LOCK_PATH = os.path.join(tempfile.gettempdir(), "merge_author_candidates.lock")


class Command(BaseCommand):
	help = "Merge clear-cut duplicate author rows (same OpenAlex author ID, same name, no conflicts)."

	def add_arguments(self, parser):
		parser.add_argument(
			"--dry-run", action="store_true", help="Report what would be merged without changing anything."
		)
		parser.add_argument(
			"--limit", type=int, default=500, help="Maximum groups to merge in this run (default 500)."
		)

	def handle(self, *args, **options):
		limit = options["limit"]
		if limit < 1:
			raise CommandError("--limit must be at least 1.")
		dry_run = options["dry_run"]
		with open(LOCK_PATH, "a") as lock:
			try:
				fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
			except BlockingIOError:
				self.stdout.write("Another merge_author_candidates run is in progress; stopping.")
				return
			result = svc.merge_clear_cut_groups(limit=limit, dry_run=dry_run)
		verb = "Would merge" if dry_run else "Merged"
		self.stdout.write(
			f"{verb} {result.groups_merged} group(s), removing {result.rows_removed} author row(s)"
			f"{'' if dry_run else f' and moving {result.articles} article link(s)'}."
		)
		if result.failed:
			self.stdout.write(f"{result.failed} group(s) failed or changed under us; see the log.")
		self.stdout.write(f"{result.remaining} clear-cut group(s) left over the limit.")
		skipped = ", ".join(f"{k}: {v}" for k, v in sorted(result.skipped.items())) or "none"
		self.stdout.write(f"Skipped (not eligible): {skipped}.")
