"""
Link labs to the subjects and categories their heads and members publish in, with the counts
that rank them. A link needs 3 or more papers, unless a superuser added it.

Never changes a superuser's added or hidden links, and never writes to an article. Writes only
what changed, so a second run with the same data writes nothing. Refuses to delete over half of
the computed links (empty or half-loaded memberships or categories) unless --force is given.

Run:
	docker exec gregory python manage.py rebuild_lab_topics --dry-run
	docker exec gregory python manage.py rebuild_lab_topics
"""

import fcntl
import os
import tempfile

from django.core.management.base import CommandError

from gregory.management.base import GregoryBaseCommand
from gregory.services.lab_topics import (
	LabTopicRebuildAborted,
	apply_topic_links,
	compute_topic_links,
)

LOCK_PATH = os.path.join(tempfile.gettempdir(), "rebuild_lab_topics.lock")


class Command(GregoryBaseCommand):
	help = "Link labs to the subjects and categories of their papers."

	def add_arguments(self, parser):
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Print the counts of what would change and write nothing.",
		)
		parser.add_argument(
			"--force",
			action="store_true",
			help="Go ahead even when the run would delete more than half of the computed links.",
		)

	def handle(self, *args, **options):
		with open(LOCK_PATH, "a") as lock:
			try:
				fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
			except BlockingIOError:
				self.log("Another rebuild_lab_topics run is in progress; stopping.", level=1)
				return
			self._rebuild(options["dry_run"], options["force"])

	def _rebuild(self, dry_run, force):
		result = compute_topic_links()
		self.log(
			f"Counted {len(result.subjects)} lab-subject and {len(result.categories)} "
			"lab-category pairs.",
			level=1,
		)
		try:
			stats = apply_topic_links(result, dry_run=dry_run, force=force)
		except LabTopicRebuildAborted as exc:
			raise CommandError(str(exc))
		prefix = "Would change" if dry_run else "Changed"
		for label, counts in (("subject", stats.subjects), ("category", stats.categories)):
			self.log(
				f"{prefix}: {label} links {counts.created} created, {counts.updated} updated, "
				f"{counts.deleted} deleted. Kept below the threshold: {counts.manual_kept} "
				f"added, {counts.hidden_kept} hidden.",
				level=1,
			)
