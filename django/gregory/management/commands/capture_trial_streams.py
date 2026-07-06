"""
Capture the *raw inbound* clinical-trial stream BEFORE it reaches the database.

Why this exists
---------------
Trial identity is resolved on ingest (identifier match, then a guarded title match).
A residual risk remains until Phase C (corroboration) lands: two *different* trials that
share a title but come from *different* registries can still be merged onto one row. Once a
merge happens it is destructive — the second record's title/link is overwritten — so it is
invisible afterwards.

This command records what each *source* actually sent, as a second stream you can diff
against the merged ``Trials`` rows to detect (and later audit) wrong merges. WHO ICTRP is
already a local XML file you can keep; this captures the two *live* streams:

  * ClinicalTrials.gov API  (Sources.method='ctgov_api')
  * EU CTIS / EU-register RSS (Sources.method='rss', source_for='trials')

How it stays safe on prod
-------------------------
It subclasses the real importer commands and reuses their fetch + parse verbatim, but
overrides the persistence hooks: ``find_existing_trial`` always returns None and both
``create_new_trial`` and ``update_existing_trial`` write a JSON line instead of touching the
DB. The only database access is the read of ``Sources`` needed to know what to fetch. **No
``Trials`` rows are read or written.**

If any feed raises, the command still runs the other feed, then exits non-zero
(``CommandError``) so a scheduler notices the partial failure.

Output
------
JSON Lines (one source record per line):
  {captured_at, feed, source_id, source_name, title, summary, link,
   published_date, identifiers, extra_fields}

Usage (prod)
------------
  docker exec gregory python manage.py capture_trial_streams
  docker exec gregory python manage.py capture_trial_streams --feed ctgov --max-results 1000
  docker exec gregory python manage.py capture_trial_streams --output /code/trial_captures/run.jsonl

Retrieve the file:
  docker cp gregory:/code/trial_captures/<file>.jsonl ./

Run it on the same schedule as (ideally just before) the pipeline so each capture lines up
with what that pipeline run ingests.
"""

import json
import os
from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError

from gregory.classes import ClinicalTrialsGovAPI  # noqa: F401  (parity with importer env)
from gregory.management.commands import feedreader_trials, feedreader_trials_ctgov

DEFAULT_DIR = "/code/trial_captures"


def _serialize(clinical_trial, source, feed):
	"""Flatten an incoming ClinicalTrial into a JSON-able capture record."""
	pub = getattr(clinical_trial, "published_date", None)
	return {
		"captured_at": datetime.now(dt_timezone.utc).isoformat(),
		"feed": feed,
		"source_id": getattr(source, "source_id", None),
		"source_name": getattr(source, "name", None),
		"title": getattr(clinical_trial, "title", None),
		"summary": getattr(clinical_trial, "summary", None),
		"link": getattr(clinical_trial, "link", None),
		"published_date": pub.isoformat() if hasattr(pub, "isoformat") else pub,
		"identifiers": getattr(clinical_trial, "identifiers", None),
		"extra_fields": getattr(clinical_trial, "extra_fields", None),
	}


class _CaptureMixin:
	"""Neutralise every DB-write path; route each parsed record to the capture file."""

	capture_fh = None
	capture_feed = None
	captured = 0

	def find_existing_trial(self, clinical_trial):  # never read/match against the DB
		return None

	def _capture(self, clinical_trial, source):
		self.capture_fh.write(
			json.dumps(
				_serialize(clinical_trial, source, self.capture_feed),
				ensure_ascii=False,
				default=str,
			)
			+ "\n"
		)
		self.captured += 1
		return None

	def create_new_trial(self, clinical_trial, source):
		return self._capture(clinical_trial, source)

	def update_existing_trial(self, existing_trial, clinical_trial, source):
		# Capture on update too. find_existing_trial returns None so this is currently
		# unreachable, but keeping create/update symmetric means a future change to the
		# importer flow can't silently drop records (and matches the module docstring).
		return self._capture(clinical_trial, source)


class _CtgovCapture(_CaptureMixin, feedreader_trials_ctgov.Command):
	capture_feed = "ctgov_api"


class _EuCapture(_CaptureMixin, feedreader_trials.Command):
	capture_feed = "eu_rss"


class Command(BaseCommand):
	help = "Capture the raw inbound trial stream (CTgov API + EU RSS) to a JSONL file without writing to the DB."

	def add_arguments(self, parser):
		parser.add_argument(
			"--feed",
			choices=["ctgov", "eu", "both"],
			default="both",
			help="Which live stream(s) to capture (default: both).",
		)
		parser.add_argument(
			"--output",
			help="Output JSONL path (default: a timestamped file under %s)."
			% DEFAULT_DIR,
		)
		parser.add_argument(
			"--max-results",
			type=int,
			default=1000,
			help="Max results per CTgov source (default: 1000).",
		)
		parser.add_argument(
			"--source-id", type=int, help="Restrict CTgov capture to one source id."
		)

	def handle(self, *args, **options):
		verbosity = options.get("verbosity", 1)
		feed = options["feed"]

		output = options.get("output")
		if not output:
			os.makedirs(DEFAULT_DIR, exist_ok=True)
			stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
			output = os.path.join(DEFAULT_DIR, f"trial_stream_{stamp}.jsonl")
		else:
			os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

		counts = {}
		errors = []
		with open(output, "w", encoding="utf-8") as fh:
			if feed in ("ctgov", "both"):
				cmd = _CtgovCapture()
				cmd.capture_fh, cmd.captured = fh, 0
				try:
					cmd.handle(
						verbosity=verbosity,
						max_results=options["max_results"],
						source_id=options.get("source_id"),
						debug=False,
					)
				except Exception as e:  # keep the EU capture alive if CTgov fails
					errors.append(f"ctgov: {e}")
					self.stderr.write(self.style.ERROR(f"CTgov capture error: {e}"))
				# The feedreaders isolate per-source fetch failures instead of
				# raising; surface them here so the capture still exits non-zero.
				for fetch_error in getattr(cmd, "fetch_errors", []):
					errors.append(f"ctgov: {fetch_error}")
					self.stderr.write(
						self.style.ERROR(f"CTgov capture error: {fetch_error}")
					)
				counts["ctgov_api"] = cmd.captured

			if feed in ("eu", "both"):
				cmd = _EuCapture()
				cmd.capture_fh, cmd.captured = fh, 0
				try:
					cmd.handle(verbosity=verbosity)
				except Exception as e:
					errors.append(f"eu: {e}")
					self.stderr.write(self.style.ERROR(f"EU capture error: {e}"))
				for fetch_error in getattr(cmd, "fetch_errors", []):
					errors.append(f"eu: {fetch_error}")
					self.stderr.write(
						self.style.ERROR(f"EU capture error: {fetch_error}")
					)
				counts["eu_rss"] = cmd.captured

		total = sum(counts.values())
		summary = f"Captured {total} records to {output} ({', '.join(f'{k}={v}' for k, v in counts.items())})"
		if errors:
			# Surface the partial counts, then fail loudly so cron/ops see a non-zero exit.
			self.stdout.write(summary)
			raise CommandError(f"{len(errors)} feed(s) failed: " + "; ".join(errors))
		self.stdout.write(self.style.SUCCESS(summary))
