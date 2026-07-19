"""
Archive raw CTIS /retrieve JSON dossiers to disk, deduplicated by content.

Used by feedreader_trials_ctis to keep a versioned history of each trial's deep
record (docs/ctis-public-api-schema.md) — one file per distinct content snapshot,
not one per run, so a daily cron doesn't fill the directory with identical copies.
"""

import glob
import json
import os
from datetime import date


def save_retrieve_backup(
	backup_dir: str, ct_number: str, payload: dict, run_date: date | None = None
) -> str | None:
	"""Write *payload* to ``backup_dir/{ct_number}-{run_date}.json``.

	If any existing backup file for this ct_number already holds byte-identical
	content (compared as canonical JSON — sorted keys, so key-order differences
	across requests don't count as a change), nothing is written and that file's
	path is returned instead — this is what keeps the oldest copy of a given
	snapshot rather than piling up daily duplicates.

	If today's file already exists for this ct_number but holds *different*
	content (the trial changed more than once in a day), it is overwritten —
	the filename has day granularity by design, so same-day is last-write-wins.

	Returns the path written to (or the pre-existing duplicate's path), or None
	if ct_number is falsy.
	"""
	if not ct_number:
		return None

	os.makedirs(backup_dir, exist_ok=True)
	run_date = run_date or date.today()
	serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)

	existing_paths = sorted(glob.glob(os.path.join(backup_dir, f"{ct_number}-*.json")))
	for path in existing_paths:
		with open(path, "r", encoding="utf-8") as f:
			if f.read() == serialized:
				return path

	new_path = os.path.join(backup_dir, f"{ct_number}-{run_date.isoformat()}.json")
	with open(new_path, "w", encoding="utf-8") as f:
		f.write(serialized)
	return new_path
