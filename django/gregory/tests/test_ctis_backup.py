"""
Tests for gregory.utils.ctis_backup.save_retrieve_backup.

Run:
  docker exec gregory python -m pytest gregory/tests/test_ctis_backup.py
"""

import json
import os
from datetime import date

from gregory.utils.ctis_backup import save_retrieve_backup


def test_writes_file_named_by_ct_number_and_date(tmp_path):
	path = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"a": 1}, run_date=date(2026, 7, 19)
	)
	assert path == str(tmp_path / "2025-523726-40-00-2026-07-19.json")
	assert os.path.exists(path)
	with open(path) as f:
		assert json.load(f) == {"a": 1}


def test_creates_backup_dir_if_missing(tmp_path):
	backup_dir = tmp_path / "nested" / "backups"
	path = save_retrieve_backup(
		str(backup_dir), "2025-523726-40-00", {"a": 1}, run_date=date(2026, 7, 19)
	)
	assert os.path.exists(path)


def test_identical_content_on_a_later_date_is_not_duplicated(tmp_path):
	first = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"a": 1}, run_date=date(2026, 7, 19)
	)
	second = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"a": 1}, run_date=date(2026, 7, 20)
	)
	# The oldest file is returned/kept; no new file is written for identical content.
	assert second == first
	assert not os.path.exists(str(tmp_path / "2025-523726-40-00-2026-07-20.json"))
	assert len(os.listdir(tmp_path)) == 1


def test_different_content_on_a_later_date_writes_a_new_file(tmp_path):
	first = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"a": 1}, run_date=date(2026, 7, 19)
	)
	second = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"a": 2}, run_date=date(2026, 7, 20)
	)
	assert first != second
	assert os.path.exists(first)
	assert os.path.exists(second)
	assert len(os.listdir(tmp_path)) == 2


def test_key_order_differences_do_not_count_as_a_change(tmp_path):
	"""Canonical (sort_keys) serialization means a re-ordered-but-equal payload is
	recognised as the same snapshot, not a new one."""
	first = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"a": 1, "b": 2}, run_date=date(2026, 7, 19)
	)
	second = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"b": 2, "a": 1}, run_date=date(2026, 7, 20)
	)
	assert second == first
	assert len(os.listdir(tmp_path)) == 1


def test_same_day_rerun_with_different_content_overwrites(tmp_path):
	"""Filename has day granularity by design; two different snapshots on the same
	day collapse into one file, last write wins."""
	path1 = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"a": 1}, run_date=date(2026, 7, 19)
	)
	path2 = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"a": 2}, run_date=date(2026, 7, 19)
	)
	assert path1 == path2
	with open(path2) as f:
		assert json.load(f) == {"a": 2}
	assert len(os.listdir(tmp_path)) == 1


def test_different_trials_never_collide(tmp_path):
	path1 = save_retrieve_backup(
		str(tmp_path), "2025-523726-40-00", {"a": 1}, run_date=date(2026, 7, 19)
	)
	path2 = save_retrieve_backup(
		str(tmp_path), "2026-000000-00-00", {"a": 1}, run_date=date(2026, 7, 19)
	)
	assert path1 != path2
	assert len(os.listdir(tmp_path)) == 2


def test_falsy_ct_number_is_a_noop(tmp_path):
	assert save_retrieve_backup(str(tmp_path), "", {"a": 1}) is None
	assert save_retrieve_backup(str(tmp_path), None, {"a": 1}) is None
	assert os.listdir(tmp_path) == []


def test_defaults_to_todays_date(tmp_path):
	path = save_retrieve_backup(str(tmp_path), "2025-523726-40-00", {"a": 1})
	assert path == str(tmp_path / f"2025-523726-40-00-{date.today().isoformat()}.json")
