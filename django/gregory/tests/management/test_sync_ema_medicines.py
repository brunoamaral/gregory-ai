"""Tests for sync_ema_medicines. EMA is never called: files are read with --file, and the
download function is mocked.

fixtures/ema/medicines-base.json holds 17 real rows cut from EMA's export of 2026-10-25
(one of them veterinary). fixtures/ema/medicines-changed.json is the same file with
SYNTHETIC edits made up for these tests: Fovlussea authorised, a new EC decision and a
longer indication for Ocrevus, Tysabri withdrawn, and Arzerra gone. None of those
changes happened at EMA.
"""

import json
import tempfile
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase

from gregory.models import EmaSyncState, Medicine, MedicineEvent
from gregory.services.ema_events import observed_events, opinion_kind, reconstructed_events
from gregory.utils.ema import parse_row

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ema"
BASE = FIXTURES / "medicines-base.json"
CHANGED = FIXTURES / "medicines-changed.json"
DOWNLOAD = "gregory.management.commands.sync_ema_medicines.download_export"
TODAY = date(2026, 12, 11)

Kind = MedicineEvent.Kind
Basis = MedicineEvent.Basis


def load(path):
	with open(path) as handle:
		return json.load(handle)


class SyncTestCase(TestCase):
	def setUp(self):
		tmp = tempfile.TemporaryDirectory()
		self.addCleanup(tmp.cleanup)
		self.tmp = Path(tmp.name)

	def write_export(self, rows, name="export.json", timestamp="2026-10-25T06:02:02Z"):
		path = self.tmp / name
		path.write_text(
			json.dumps({"meta": {"total_records": len(rows), "timestamp": timestamp}, "data": rows})
		)
		return path

	def sync(self, path=BASE, **options):
		out = StringIO()
		call_command("sync_ema_medicines", file=str(path), stdout=out, **options)
		return out.getvalue()

	def medicine(self, name):
		return Medicine.objects.get(name=name)

	def events(self, name):
		return {
			(e.kind, e.event_date): e for e in MedicineEvent.objects.filter(medicine__name=name)
		}

	def base_row(self, name):
		return next(r for r in load(BASE)["data"] if r["name_of_medicine"] == name)

	def mark_tiny_fixture(self):
		# The changed fixture drops one of 16 rows, which is 6% and trips the 5% guard.
		# That guard is meant for the real export of ~2,400 rows, not a 16-row fixture.
		EmaSyncState.objects.filter(pk=1).update(last_human_count=15)


class FirstSyncTests(SyncTestCase):
	def test_creates_human_medicines_and_skips_veterinary(self):
		output = self.sync()
		self.assertEqual(Medicine.objects.count(), 16)
		self.assertFalse(Medicine.objects.filter(name="Stemyca").exists())
		self.assertIn("16 new", output)
		self.assertIn("1 veterinary", output)

	def test_stores_the_parsed_fields(self):
		self.sync()
		ocrevus = self.medicine("Ocrevus")
		self.assertEqual(ocrevus.substances, ["ocrelizumab"])
		self.assertEqual(ocrevus.therapeutic_areas, ["Multiple Sclerosis"])
		self.assertEqual(ocrevus.authorisation_date, date(2018, 1, 8))
		self.assertEqual(ocrevus.latest_ec_decision_date, date(2026, 10, 15))
		self.assertTrue(ocrevus.in_latest_export)
		self.assertEqual(ocrevus.raw["name_of_medicine"], "Ocrevus")
		self.assertTrue(self.medicine("Synjardy").is_combination)
		self.assertEqual(self.medicine("Hydrocortisone Aguettant").substances, ["hydrocortisone"])
		self.assertNotIn("&nbsp;", self.medicine("Kesimpta").indication)

	def test_ocrevus_gets_reconstructed_events(self):
		self.sync()
		events = self.events("Ocrevus")
		self.assertEqual(
			set(events),
			{
				(Kind.OPINION_POSITIVE, date(2017, 11, 9)),
				(Kind.AUTHORISED, date(2018, 1, 8)),
				(Kind.EC_DECISION, date(2026, 10, 15)),
			},
		)
		self.assertTrue(all(e.basis == Basis.RECONSTRUCTED for e in events.values()))

	def test_opinion_outcome_follows_the_rule(self):
		self.sync()
		# opinion_status is empty on these; the outcome comes from the product's fate.
		self.assertIn((Kind.OPINION_POSITIVE, date(2023, 7, 20)), self.events("Tyruko"))
		self.assertIn((Kind.OPINION_NEGATIVE, date(2007, 11, 15)), self.events("Natalizumab Elan Pharma"))
		# opinion_status is set on these.
		self.assertIn((Kind.OPINION_POSITIVE, date(2026, 10, 15)), self.events("Fovlussea"))
		self.assertIn((Kind.OPINION_NEGATIVE, date(2024, 6, 27)), self.events("Masitinib AB Science"))
		self.assertFalse(MedicineEvent.objects.filter(kind=Kind.OPINION_ADOPTED).exists())

	def test_other_event_kinds(self):
		self.sync()
		self.assertIn((Kind.REFUSED, date(2008, 1, 11)), self.events("Natalizumab Elan Pharma"))
		self.assertIn((Kind.APPLICATION_WITHDRAWN, date(2025, 9, 18)), self.events("Hydrocortisone Aguettant"))
		ended = self.events("Arzerra")[(Kind.ENDED, date(2019, 2, 28))]
		self.assertEqual(ended.to_value, "Withdrawn")

	def test_an_opinion_with_no_outcome_is_opinion_adopted(self):
		row = dict(
			self.base_row("Fovlussea"),
			opinion_status="",
			ema_product_number="EMEA/H/C/999999",
			name_of_medicine="Pending",
		)
		self.sync(self.write_export([row]))
		self.assertEqual(
			set(self.events("Pending")), {(Kind.OPINION_ADOPTED, date(2026, 10, 15))}
		)

	def test_ec_decision_dated_like_the_authorisation_is_not_an_event(self):
		row = dict(
			self.base_row("Ocrevus"),
			european_commission_decision_date="08/01/2018",
		)
		self.sync(self.write_export([row]))
		self.assertNotIn(Kind.EC_DECISION, {k for k, _ in self.events("Ocrevus")})

	def test_state_is_recorded(self):
		self.sync()
		state = EmaSyncState.objects.get(pk=1)
		self.assertEqual(state.last_human_count, 16)
		self.assertEqual(state.last_export_timestamp, "2026-10-25T06:02:02Z")
		self.assertIsNotNone(state.last_run_at)
		# A file has no Last-Modified header, so nothing is stored for the next download.
		self.assertEqual(state.last_modified, "")

	def test_start_of_evaluation_and_rolling_review_are_handled(self):
		row = dict(
			self.base_row("Fovlussea"),
			start_of_evaluation_date="01/03/2026",
			start_of_rolling_review_date="01/02/2026",
		)
		self.sync(self.write_export([row]))
		events = self.events("Fovlussea")
		self.assertIn((Kind.EVALUATION_STARTED, date(2026, 3, 1)), events)
		self.assertIn((Kind.ROLLING_REVIEW_STARTED, date(2026, 2, 1)), events)

	def test_a_bad_date_is_stored_as_null_and_does_not_stop_the_run(self):
		row = dict(self.base_row("Ocrevus"), marketing_authorisation_date="not a date")
		with self.assertLogs("gregory.utils.ema", level="WARNING"):
			output = self.sync(self.write_export([row]))
		self.assertIsNone(self.medicine("Ocrevus").authorisation_date)
		self.assertIn("Unparseable dates: 1", output)


class RepeatSyncTests(SyncTestCase):
	def test_second_sync_of_the_same_file_changes_nothing(self):
		self.sync()
		events_before = list(MedicineEvent.objects.order_by("id").values_list("id", flat=True))
		synced_before = dict(Medicine.objects.values_list("name", "last_synced_at"))
		output = self.sync()
		self.assertIn("0 new, 0 changed", output)
		self.assertIn("16 unchanged", output)
		self.assertEqual(
			list(MedicineEvent.objects.order_by("id").values_list("id", flat=True)), events_before
		)
		self.assertEqual(dict(Medicine.objects.values_list("name", "last_synced_at")), synced_before)


class ChangedSyncTests(SyncTestCase):
	def setUp(self):
		super().setUp()
		self.sync()
		self.mark_tiny_fixture()
		with patch("gregory.management.commands.sync_ema_medicines.timezone.localdate", return_value=TODAY):
			self.output = self.sync(CHANGED)

	def test_summary(self):
		self.assertIn("0 new, 3 changed", self.output)
		self.assertIn("1 no longer in the export", self.output)

	def test_fovlussea_is_authorised(self):
		events = self.events("Fovlussea")
		authorised = events[(Kind.AUTHORISED, date(2026, 12, 10))]
		self.assertEqual(authorised.basis, Basis.OBSERVED)
		self.assertEqual(self.medicine("Fovlussea").status, "Authorised")
		self.assertEqual(self.medicine("Fovlussea").opinion_status, "")

	def test_clearing_opinion_status_is_not_an_event(self):
		# The positive opinion stays as it was; the EC decision on the authorisation
		# date is the authorisation, not a second event.
		self.assertEqual(
			set(self.events("Fovlussea")),
			{
				(Kind.OPINION_POSITIVE, date(2026, 10, 15)),
				(Kind.AUTHORISED, date(2026, 12, 10)),
			},
		)

	def test_ocrevus_gets_an_ec_decision_and_an_indication_change(self):
		events = self.events("Ocrevus")
		decision = events[(Kind.EC_DECISION, date(2026, 11, 20))]
		self.assertEqual(decision.basis, Basis.OBSERVED)
		changed = events[(Kind.INDICATION_CHANGED, TODAY)]
		self.assertEqual(changed.basis, Basis.OBSERVED)
		self.assertTrue(changed.to_value.startswith(changed.from_value))
		self.assertGreater(len(changed.to_value), len(changed.from_value))
		# The earlier reconstructed decision is still there.
		self.assertIn((Kind.EC_DECISION, date(2026, 10, 15)), events)
		self.assertEqual(self.medicine("Ocrevus").latest_ec_decision_date, date(2026, 11, 20))

	def test_tysabri_ended_with_the_new_status(self):
		ended = self.events("Tysabri")[(Kind.ENDED, date(2026, 12, 1))]
		self.assertEqual(ended.to_value, "Withdrawn")
		self.assertEqual(ended.basis, Basis.OBSERVED)
		self.assertEqual(self.medicine("Tysabri").status, "Withdrawn")
		# The date explains the status change, so there is no status_changed too.
		self.assertFalse(MedicineEvent.objects.filter(kind=Kind.STATUS_CHANGED).exists())

	def test_arzerra_is_flagged_not_deleted(self):
		arzerra = self.medicine("Arzerra")
		self.assertFalse(arzerra.in_latest_export)
		self.assertEqual(arzerra.status, "Withdrawn")
		self.assertTrue(self.medicine("Kesimpta").in_latest_export)

	def test_a_row_that_comes_back_is_flagged_present_again(self):
		output = self.sync(BASE)
		self.assertTrue(self.medicine("Arzerra").in_latest_export)
		self.assertIn("1 back in the export", output)
		self.assertEqual(
			MedicineEvent.objects.filter(medicine__name="Arzerra").count(),
			len(self.events("Arzerra")),
		)

	def test_state_follows_the_latest_sync(self):
		state = EmaSyncState.objects.get(pk=1)
		self.assertEqual(state.last_human_count, 15)
		self.assertEqual(state.last_export_timestamp, "2026-12-10T06:02:02Z")


class CorrectedDateTests(SyncTestCase):
	"""A one-off milestone that EMA moves is a correction; recurring kinds are not."""

	def sync_with(self, name, **changes):
		rows = load(BASE)["data"]
		for row in rows:
			if row["name_of_medicine"] == name:
				row.update(changes)
		self.sync(self.write_export(rows))

	def kinds(self, name, kind):
		return sorted(
			MedicineEvent.objects.filter(medicine__name=name, kind=kind).values_list("event_date", "basis")
		)

	def test_a_corrected_authorisation_date_leaves_one_event_on_the_new_date(self):
		self.sync()
		self.assertEqual(self.kinds("Ocrevus", Kind.AUTHORISED), [(date(2018, 1, 8), Basis.RECONSTRUCTED)])
		self.sync_with("Ocrevus", marketing_authorisation_date="09/01/2018")
		self.assertEqual(self.kinds("Ocrevus", Kind.AUTHORISED), [(date(2018, 1, 9), Basis.OBSERVED)])
		self.assertEqual(self.medicine("Ocrevus").authorisation_date, date(2018, 1, 9))

	def test_every_one_off_kind_is_replaced(self):
		self.sync()
		self.sync_with(
			"Natalizumab Elan Pharma",
			refusal_of_marketing_authorisation_date="12/01/2008",
		)
		self.assertEqual(
			self.kinds("Natalizumab Elan Pharma", Kind.REFUSED), [(date(2008, 1, 12), Basis.OBSERVED)]
		)
		self.sync_with(
			"Hydrocortisone Aguettant", withdrawal_of_application_date="19/09/2025"
		)
		self.assertEqual(
			self.kinds("Hydrocortisone Aguettant", Kind.APPLICATION_WITHDRAWN),
			[(date(2025, 9, 19), Basis.OBSERVED)],
		)
		self.sync_with(
			"Arzerra",
			withdrawal_expiry_revocation_lapse_of_marketing_authorisation_date="01/03/2019",
		)
		self.assertEqual(self.kinds("Arzerra", Kind.ENDED), [(date(2019, 3, 1), Basis.OBSERVED)])

	def test_a_moved_ec_decision_adds_a_second_event(self):
		self.sync()
		self.sync_with("Ocrevus", european_commission_decision_date="20/11/2026")
		self.assertEqual(
			self.kinds("Ocrevus", Kind.EC_DECISION),
			[(date(2026, 10, 15), Basis.RECONSTRUCTED), (date(2026, 11, 20), Basis.OBSERVED)],
		)

	def test_a_moved_suspension_date_adds_a_second_event(self):
		self.sync_with("Ocrevus", suspension_of_marketing_authorisation_date="01/02/2020")
		self.sync_with("Ocrevus", suspension_of_marketing_authorisation_date="01/03/2021")
		self.assertEqual(
			[d for d, _ in self.kinds("Ocrevus", Kind.SUSPENDED)], [date(2020, 2, 1), date(2021, 3, 1)]
		)

	def test_a_date_that_appears_adds_one_event_and_deletes_nothing(self):
		self.sync()
		before = MedicineEvent.objects.filter(medicine__name="Tysabri").count()
		self.sync_with(
			"Tysabri",
			withdrawal_expiry_revocation_lapse_of_marketing_authorisation_date="01/12/2026",
		)
		self.assertEqual(MedicineEvent.objects.filter(medicine__name="Tysabri").count(), before + 1)
		self.assertEqual(self.kinds("Tysabri", Kind.ENDED), [(date(2026, 12, 1), Basis.OBSERVED)])
		self.assertEqual(self.kinds("Tysabri", Kind.AUTHORISED), [(date(2006, 6, 27), Basis.RECONSTRUCTED)])

	def test_the_replaces_marker_is_set_only_for_a_move_between_two_dates(self):
		self.sync()
		stored = self.medicine("Ocrevus")
		moved = observed_events(
			stored, parse_row(dict(self.base_row("Ocrevus"), marketing_authorisation_date="09/01/2018")), TODAY
		)
		(authorised,) = [e for e in moved if e["kind"] == Kind.AUTHORISED]
		self.assertEqual(authorised["replaces"], date(2018, 1, 8))
		stored = self.medicine("Tysabri")
		appeared = observed_events(
			stored,
			parse_row(
				dict(
					self.base_row("Tysabri"),
					withdrawal_expiry_revocation_lapse_of_marketing_authorisation_date="01/12/2026",
				)
			),
			TODAY,
		)
		(ended,) = [e for e in appeared if e["kind"] == Kind.ENDED]
		self.assertNotIn("replaces", ended)


class StatusChangedTests(SyncTestCase):
	def test_a_status_change_nothing_else_explains(self):
		self.sync()
		rows = load(BASE)["data"]
		for row in rows:
			if row["name_of_medicine"] == "Fovlussea":
				row["medicine_status"] = "Opinion under re-examination"
		with patch("gregory.management.commands.sync_ema_medicines.timezone.localdate", return_value=TODAY):
			self.sync(self.write_export(rows))
		changed = self.events("Fovlussea")[(Kind.STATUS_CHANGED, TODAY)]
		self.assertEqual(changed.basis, Basis.OBSERVED)
		self.assertEqual(changed.from_value, "Opinion")
		self.assertEqual(changed.to_value, "Opinion under re-examination")


class ObservedEventUnitTests(SyncTestCase):
	"""observed_events on a stored Medicine, without a sync around it."""

	def stored(self, name):
		self.sync()
		return self.medicine(name)

	def test_nothing_changed_gives_nothing(self):
		medicine = self.stored("Ocrevus")
		parsed = parse_row(self.base_row("Ocrevus"))
		self.assertEqual(observed_events(medicine, parsed, TODAY), [])

	def test_opinion_status_going_to_empty_is_not_an_event(self):
		medicine = self.stored("Fovlussea")
		row = dict(self.base_row("Fovlussea"), opinion_status="")
		self.assertEqual(observed_events(medicine, parse_row(row), TODAY), [])

	def test_an_opinion_that_firms_up_is_an_event(self):
		self.sync(self.write_export([dict(self.base_row("Fovlussea"), opinion_status="")]))
		medicine = self.medicine("Fovlussea")
		self.assertEqual(set(self.events("Fovlussea")), {(Kind.OPINION_ADOPTED, date(2026, 10, 15))})
		parsed = parse_row(self.base_row("Fovlussea"))
		events = observed_events(medicine, parsed, TODAY)
		self.assertEqual([(e["kind"], e["event_date"]) for e in events], [(Kind.OPINION_POSITIVE, date(2026, 10, 15))])

	def test_a_new_opinion_date_is_an_event(self):
		medicine = self.stored("Fovlussea")
		row = dict(self.base_row("Fovlussea"), opinion_adopted_date="20/11/2026")
		events = observed_events(medicine, parse_row(row), TODAY)
		self.assertEqual([(e["kind"], e["event_date"]) for e in events], [(Kind.OPINION_POSITIVE, date(2026, 11, 20))])


class OpinionKindTests(SyncTestCase):
	def test_rule(self):
		self.assertEqual(opinion_kind("Positive", None, "Opinion", None), Kind.OPINION_POSITIVE)
		self.assertEqual(opinion_kind("negative", None, "Opinion", None), Kind.OPINION_NEGATIVE)
		self.assertEqual(opinion_kind("", date(2020, 1, 1), "Withdrawn", None), Kind.OPINION_POSITIVE)
		self.assertEqual(opinion_kind("", None, "Refused", None), Kind.OPINION_NEGATIVE)
		self.assertEqual(opinion_kind("", None, "Application withdrawn", None), Kind.OPINION_ADOPTED)
		# An explicit outcome beats what the authorisation date implies.
		self.assertEqual(opinion_kind("Negative", date(2020, 1, 1), "Authorised", None), Kind.OPINION_NEGATIVE)

	def test_reconstructed_events_have_no_duplicate_keys(self):
		for row in load(BASE)["data"]:
			events = reconstructed_events(parse_row(row))
			keys = [(e["kind"], e["event_date"]) for e in events]
			self.assertEqual(len(keys), len(set(keys)), row["name_of_medicine"])


class GuardTests(SyncTestCase):
	def test_a_big_drop_in_human_rows_is_refused_and_writes_nothing(self):
		self.sync()
		medicines = list(Medicine.objects.order_by("id").values_list("id", "status", "in_latest_export"))
		events = MedicineEvent.objects.count()
		rows = [r for r in load(BASE)["data"] if r["category"] == "Human"][:3]
		with self.assertRaisesMessage(CommandError, "looks partial"):
			self.sync(self.write_export(rows))
		self.assertEqual(
			list(Medicine.objects.order_by("id").values_list("id", "status", "in_latest_export")), medicines
		)
		self.assertEqual(MedicineEvent.objects.count(), events)
		self.assertEqual(EmaSyncState.objects.get(pk=1).last_human_count, 16)

	def test_a_small_drop_is_accepted(self):
		def clone(number):
			return dict(self.base_row("Ocrevus"), ema_product_number=f"EMEA/H/C/9{number:05d}")

		self.sync(self.write_export([clone(n) for n in range(20)]))
		# 19 of 20 is a 5% drop: within what the guard allows.
		self.sync(self.write_export([clone(n) for n in range(19)], name="second.json"))
		self.assertEqual(Medicine.objects.filter(in_latest_export=False).count(), 1)

	def test_total_records_must_match_the_rows(self):
		path = self.tmp / "truncated.json"
		path.write_text(json.dumps({"meta": {"total_records": 99}, "data": load(BASE)["data"]}))
		with self.assertRaisesMessage(CommandError, "truncated"):
			self.sync(path)
		self.assertEqual(Medicine.objects.count(), 0)

	def test_invalid_json_is_refused(self):
		path = self.tmp / "broken.json"
		path.write_text('{"meta": {"total_rec')
		with self.assertRaisesMessage(CommandError, "not valid JSON"):
			self.sync(path)
		self.assertEqual(Medicine.objects.count(), 0)

	def test_a_missing_data_list_is_refused(self):
		path = self.tmp / "nodata.json"
		path.write_text(json.dumps({"meta": {"total_records": 0}}))
		with self.assertRaisesMessage(CommandError, "no 'data' list"):
			self.sync(path)

	def test_a_missing_file_is_refused(self):
		with self.assertRaisesMessage(CommandError, "Cannot read"):
			self.sync(self.tmp / "nope.json")

	def test_an_export_with_no_human_rows_is_refused(self):
		rows = [r for r in load(BASE)["data"] if r["category"] != "Human"]
		with self.assertRaisesMessage(CommandError, "no human medicines"):
			self.sync(self.write_export(rows))

	def test_a_failure_while_writing_leaves_no_state_or_partial_rows(self):
		with patch(
			"gregory.management.commands.sync_ema_medicines.MedicineEvent.objects.bulk_create",
			side_effect=RuntimeError("boom"),
		):
			with self.assertRaises(RuntimeError):
				self.sync()
		self.assertEqual(Medicine.objects.count(), 0)
		self.assertFalse(EmaSyncState.objects.exists())


class DryRunTests(SyncTestCase):
	def test_dry_run_writes_nothing_but_reports(self):
		output = self.sync(dry_run=True)
		self.assertEqual(Medicine.objects.count(), 0)
		self.assertEqual(MedicineEvent.objects.count(), 0)
		self.assertFalse(EmaSyncState.objects.exists())
		self.assertIn("16 new", output)
		self.assertIn("opinion_positive (reconstructed)", output)
		self.assertIn("Dry run", output)

	def test_dry_run_after_a_sync_changes_no_rows(self):
		self.sync()
		self.mark_tiny_fixture()
		before = list(Medicine.objects.order_by("id").values_list("status", "in_latest_export"))
		events = MedicineEvent.objects.count()
		output = self.sync(CHANGED, dry_run=True)
		self.assertIn("3 changed", output)
		self.assertEqual(list(Medicine.objects.order_by("id").values_list("status", "in_latest_export")), before)
		self.assertEqual(MedicineEvent.objects.count(), events)


class DownloadTests(SyncTestCase):
	def run_download(self, **options):
		out = StringIO()
		call_command("sync_ema_medicines", stdout=out, **options)
		return out.getvalue()

	def body(self):
		return BASE.read_bytes()

	def test_downloads_and_remembers_last_modified(self):
		stamp = "Sun, 25 Oct 2026 06:00:00 GMT"
		with patch(DOWNLOAD, return_value=(200, self.body(), stamp)) as download:
			self.run_download()
		download.assert_called_once_with("")
		self.assertEqual(Medicine.objects.count(), 16)
		self.assertEqual(EmaSyncState.objects.get(pk=1).last_modified, stamp)

	def test_a_304_exits_cleanly_and_writes_nothing(self):
		stamp = "Sun, 25 Oct 2026 06:00:00 GMT"
		EmaSyncState.objects.create(pk=1, last_modified=stamp, last_human_count=16)
		with patch(DOWNLOAD, return_value=(304, b"", stamp)) as download:
			output = self.run_download()
		download.assert_called_once_with(stamp)
		self.assertIn("unchanged", output)
		self.assertEqual(Medicine.objects.count(), 0)

	def test_force_ignores_last_modified(self):
		EmaSyncState.objects.create(pk=1, last_modified="Sun, 25 Oct 2026 06:00:00 GMT")
		with patch(DOWNLOAD, return_value=(200, self.body(), "newer")) as download:
			self.run_download(force=True)
		download.assert_called_once_with("")
		self.assertEqual(EmaSyncState.objects.get(pk=1).last_modified, "newer")

	def test_file_skips_the_download(self):
		EmaSyncState.objects.create(pk=1, last_modified="Sun, 25 Oct 2026 06:00:00 GMT")
		with patch(DOWNLOAD) as download:
			self.sync()
		download.assert_not_called()
		self.assertEqual(Medicine.objects.count(), 16)
		# The stored header is kept: the file had none of its own.
		self.assertEqual(
			EmaSyncState.objects.get(pk=1).last_modified, "Sun, 25 Oct 2026 06:00:00 GMT"
		)

	def test_an_http_error_is_a_command_error(self):
		with patch(DOWNLOAD, return_value=(404, b"", "")):
			with self.assertRaisesMessage(CommandError, "HTTP 404"):
				self.run_download()

	def test_a_download_failure_is_a_command_error(self):
		with patch(DOWNLOAD, side_effect=RuntimeError("down")):
			with self.assertRaisesMessage(CommandError, "Could not download"):
				self.run_download()
