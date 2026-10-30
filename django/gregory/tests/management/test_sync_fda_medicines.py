"""Tests for sync_fda_medicines. The FDA is never called: files are read with --file and
--purplebook-file, and the download functions are mocked.

fixtures/fda/drugsfda-base.json holds 13 real applications cut from the Drugs@FDA bulk file
of 2026-10-23 (one of them an ANDA, which the sync skips), and fixtures/fda/purplebook-base.csv
real rows from the Purple Book file for September 2026. fixtures/fda/drugsfda-changed.json and
purplebook-changed.csv are the same files with SYNTHETIC edits made up for these tests: a new
efficacy supplement for Ocrevus (dated 2026-12-01), every Synjardy product discontinued,
Tysabri missing, and Kymriah's licence voluntarily revoked. None of those changes happened at
the FDA.
"""

import io
import json
import tempfile
import zipfile
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from gregory.models import FdaSyncState, Medicine, MedicineEvent, Regulator

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "fda"
DRUGS_BASE = FIXTURES / "drugsfda-base.json"
DRUGS_CHANGED = FIXTURES / "drugsfda-changed.json"
PB_BASE = FIXTURES / "purplebook-base.csv"
PB_CHANGED = FIXTURES / "purplebook-changed.csv"
MODULE = "gregory.management.commands.sync_fda_medicines"
DOWNLOAD_DRUGS = f"{MODULE}.download_drugsfda"
DOWNLOAD_PB = f"{MODULE}.download_purplebook"
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

	def write_drugs(self, payload, name="drugs.json"):
		path = self.tmp / name
		path.write_text(json.dumps(payload))
		return path

	def sync(self, drugs=DRUGS_BASE, purplebook=PB_BASE, **options):
		out = StringIO()
		kwargs = {}
		if drugs is not None:
			kwargs["file"] = str(drugs)
		if purplebook is not None:
			kwargs["purplebook_file"] = str(purplebook)
		call_command("sync_fda_medicines", stdout=out, **kwargs, **options)
		return out.getvalue()

	def medicine(self, key):
		return Medicine.objects.get(fda_application_number=key)

	def events(self, key):
		return {
			(e.kind, e.event_date): e
			for e in MedicineEvent.objects.filter(medicine__fda_application_number=key)
		}

	def base_record(self, number):
		return next(r for r in load(DRUGS_BASE)["results"] if r["application_number"] == number)

	def mark_tiny_fixture(self):
		# The changed fixture drops one of 12 applications, which is 8% and trips the 5%
		# guard. That guard is meant for the real file of ~6,400, not a 12-row fixture.
		FdaSyncState.objects.filter(source="drugsfda").update(last_count=11)


class FirstSyncTests(SyncTestCase):
	def setUp(self):
		super().setUp()
		self.output = self.sync()

	def test_creates_the_applications_and_skips_the_anda(self):
		# 12 NDA and BLA applications from Drugs@FDA, 5 CBER BLAs from the Purple Book.
		self.assertEqual(Medicine.objects.filter(regulator=Regulator.FDA).count(), 17)
		self.assertFalse(Medicine.objects.filter(fda_application_number="ANDA076192").exists())
		self.assertIn("12 new", self.output)
		self.assertIn("1 ANDAs", self.output)
		self.assertIn("5 new", self.output)

	def test_every_row_is_an_fda_row_with_no_ema_key(self):
		self.assertFalse(Medicine.objects.filter(regulator=Regulator.EMA).exists())
		self.assertFalse(Medicine.objects.exclude(ema_product_number__isnull=True).exists())

	def test_stores_the_parsed_fields(self):
		ocrevus = self.medicine("BLA761053")
		self.assertEqual(ocrevus.name, "OCREVUS")
		self.assertEqual(ocrevus.substances, ["ocrelizumab"])
		self.assertEqual(ocrevus.holder, "GENENTECH INC")
		self.assertEqual(ocrevus.status, "Approved")
		self.assertEqual(ocrevus.authorisation_date, date(2017, 3, 28))
		self.assertEqual((ocrevus.fda_application_type, ocrevus.fda_center), ("BLA", "CDER"))
		self.assertEqual(ocrevus.fda_original_class, "TYPE 1")
		self.assertIs(ocrevus.fda_priority_review, True)
		self.assertEqual(ocrevus.fda_pharm_classes, ["CD20-directed Cytolytic Antibody [EPC]"])
		self.assertEqual(ocrevus.raw["application_number"], "BLA761053")
		self.assertTrue(ocrevus.in_latest_export)
		self.assertEqual(ocrevus.indication, "")
		self.assertEqual(str(ocrevus), "OCREVUS (BLA761053)")

	def test_kesimpta_and_arzerra_are_one_row_with_both_brands(self):
		kesimpta = self.medicine("BLA125326")
		self.assertEqual(kesimpta.brand_names, ["KESIMPTA", "ARZERRA"])
		self.assertEqual(kesimpta.name, "KESIMPTA / ARZERRA")
		self.assertEqual(Medicine.objects.filter(name__icontains="arzerra").count(), 1)

	def test_the_purple_book_flags_tyruko_as_a_biosimilar_and_leaves_ocrevus_alone(self):
		self.assertTrue(self.medicine("BLA761322").biosimilar)
		self.assertFalse(self.medicine("BLA761322").interchangeable)
		self.assertFalse(self.medicine("BLA761053").biosimilar)
		self.assertIn("1 with biosimilar or interchangeable flags changed", self.output)

	def test_kymriah_is_a_cber_row(self):
		kymriah = self.medicine("BLA125646")
		self.assertEqual((kymriah.fda_center, kymriah.fda_application_type), ("CBER", "BLA"))
		self.assertEqual(kymriah.name, "Kymriah")
		self.assertEqual(kymriah.authorisation_date, date(2017, 8, 30))
		self.assertEqual(kymriah.url, "")
		self.assertEqual(kymriah.raw["rows"][0]["Proper Name"], "tisagenlecleucel")

	def test_only_the_full_list_of_the_purple_book_is_read(self):
		# Tudriqev and Fayuvi are change rows above the second header.
		self.assertFalse(Medicine.objects.filter(brand_names__contains=["Fayuvi"]).exists())
		self.assertEqual(Medicine.objects.filter(fda_center="CBER").count(), 5)

	def test_a_revoked_cber_licence_gives_the_revoked_status(self):
		self.assertEqual(self.medicine("BLA125586").status, "Revoked")

	def test_ocrevus_gets_reconstructed_events(self):
		events = self.events("BLA761053")
		self.assertEqual(
			set(events),
			{
				(Kind.FDA_APPROVED, date(2017, 3, 28)),
				(Kind.FDA_EFFICACY_SUPPLEMENT, date(2020, 12, 14)),
				(Kind.FDA_EFFICACY_SUPPLEMENT, date(2026, 5, 8)),
			},
		)
		self.assertTrue(all(e.basis == Basis.RECONSTRUCTED for e in events.values()))
		self.assertEqual(events[(Kind.FDA_EFFICACY_SUPPLEMENT, date(2026, 5, 8))].to_value, "S-38")

	def test_two_same_day_efficacy_approvals_are_stored_as_one_event(self):
		efficacy = MedicineEvent.objects.filter(
			medicine__fda_application_number="NDA017581", kind=Kind.FDA_EFFICACY_SUPPLEMENT
		)
		self.assertEqual(efficacy.count(), 1)
		self.assertEqual(efficacy.get().event_date, date(1987, 3, 23))

	def test_a_tentative_approval_has_its_own_event_and_no_authorisation_date(self):
		tentative = self.medicine("NDA202172")
		self.assertEqual(tentative.status, "Tentatively approved")
		self.assertIsNone(tentative.authorisation_date)
		self.assertEqual(set(self.events("NDA202172")), {(Kind.FDA_TENTATIVE_APPROVAL, date(2016, 2, 12))})

	def test_a_cber_row_gets_one_approval_dated_with_its_authorisation_date(self):
		self.assertEqual(set(self.events("BLA125789")), {(Kind.FDA_APPROVED, date(2024, 8, 1))})

	def test_the_report_lists_events_by_kind_and_basis(self):
		self.assertIn("fda_approved (reconstructed)", self.output)
		self.assertIn("fda_efficacy_supplement (reconstructed)", self.output)
		self.assertIn("unparseable dates: 0", self.output)

	def test_the_state_of_each_file_is_recorded(self):
		drugs = FdaSyncState.objects.get(source="drugsfda")
		purple = FdaSyncState.objects.get(source="purplebook")
		self.assertEqual(drugs.last_count, 12)
		self.assertEqual(purple.last_count, 5)
		self.assertIsNotNone(drugs.last_run_at)
		# A local file has no Last-Modified header, so nothing is stored for the next download.
		self.assertEqual((drugs.last_modified, drugs.etag), ("", ""))


class RepeatSyncTests(SyncTestCase):
	def test_second_sync_of_the_same_files_changes_nothing(self):
		self.sync()
		events_before = list(MedicineEvent.objects.order_by("id").values_list("id", flat=True))
		synced_before = dict(Medicine.objects.values_list("fda_application_number", "last_synced_at"))
		output = self.sync()
		self.assertIn("0 new, 0 changed", output)
		self.assertIn("12 unchanged", output)
		self.assertIn("5 unchanged", output)
		self.assertEqual(
			list(MedicineEvent.objects.order_by("id").values_list("id", flat=True)), events_before
		)
		self.assertEqual(
			dict(Medicine.objects.values_list("fda_application_number", "last_synced_at")),
			synced_before,
		)

	def test_the_flags_the_purple_book_set_survive_a_drugsfda_only_sync(self):
		self.sync()
		self.assertTrue(self.medicine("BLA761322").biosimilar)
		self.sync(purplebook=None)
		self.assertTrue(self.medicine("BLA761322").biosimilar)

	def test_label_fields_are_not_overwritten(self):
		self.sync()
		Medicine.objects.filter(fda_application_number="BLA125646").update(
			url="https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=abc"
		)
		Medicine.objects.filter(fda_application_number="BLA761053").update(
			indication="Ocrevus is indicated for MS.", label_indications=[{"setid": "x"}]
		)
		output = self.sync()
		self.assertIn("0 new, 0 changed", output)
		self.assertTrue(self.medicine("BLA125646").url.startswith("https://dailymed"))
		self.assertEqual(self.medicine("BLA761053").indication, "Ocrevus is indicated for MS.")
		self.assertEqual(self.medicine("BLA761053").label_indications, [{"setid": "x"}])


class ChangedSyncTests(SyncTestCase):
	def setUp(self):
		super().setUp()
		self.sync()
		self.mark_tiny_fixture()
		with patch(f"{MODULE}.timezone.localdate", return_value=TODAY):
			self.output = self.sync(DRUGS_CHANGED, PB_CHANGED)

	def test_summary(self):
		self.assertIn("0 new, 2 changed", self.output)  # Ocrevus and Synjardy
		self.assertIn("1 no longer in the file", self.output)  # Tysabri
		self.assertIn("0 new, 1 changed", self.output)  # Kymriah

	def test_ocrevus_gets_the_new_efficacy_supplement_as_observed(self):
		event = self.events("BLA761053")[(Kind.FDA_EFFICACY_SUPPLEMENT, date(2026, 12, 1))]
		self.assertEqual(event.basis, Basis.OBSERVED)
		self.assertEqual(event.to_value, "S-39")
		# The old events are untouched.
		self.assertEqual(
			self.events("BLA761053")[(Kind.FDA_APPROVED, date(2017, 3, 28))].basis, Basis.RECONSTRUCTED
		)

	def test_synjardy_is_discontinued_today(self):
		self.assertEqual(self.medicine("NDA206111").status, "Discontinued")
		event = self.events("NDA206111")[(Kind.FDA_DISCONTINUED, TODAY)]
		self.assertEqual((event.basis, event.from_value, event.to_value), (Basis.OBSERVED, "Approved", "Discontinued"))

	def test_tysabri_is_flagged_not_deleted(self):
		tysabri = self.medicine("BLA125104")
		self.assertFalse(tysabri.in_latest_export)
		self.assertEqual(tysabri.status, "Approved")
		self.assertTrue(self.events("BLA125104"))

	def test_kymriah_licence_revoked(self):
		self.assertEqual(self.medicine("BLA125646").status, "Revoked")
		event = self.events("BLA125646")[(Kind.FDA_REVOKED, TODAY)]
		self.assertEqual((event.basis, event.from_value, event.to_value), (Basis.OBSERVED, "Approved", "Revoked"))

	def test_a_returning_application_is_in_the_file_again(self):
		with patch(f"{MODULE}.timezone.localdate", return_value=TODAY):
			output = self.sync()
		self.assertTrue(self.medicine("BLA125104").in_latest_export)
		self.assertIn("1 back in the file", output)

	def test_a_row_that_is_not_in_the_file_gets_no_new_events_on_the_next_run(self):
		count = MedicineEvent.objects.count()
		with patch(f"{MODULE}.timezone.localdate", return_value=TODAY):
			self.sync(DRUGS_CHANGED, PB_CHANGED)
		self.assertEqual(MedicineEvent.objects.count(), count)


class OldSubmissionTests(SyncTestCase):
	def test_an_old_submission_that_appears_late_is_reconstructed(self):
		self.sync()
		payload = load(DRUGS_BASE)
		ocrevus = next(r for r in payload["results"] if r["application_number"] == "BLA761053")
		ocrevus["submissions"].append(
			{
				"submission_type": "SUPPL",
				"submission_number": "40",
				"submission_status": "AP",
				"submission_status_date": "20190501",
				"review_priority": "STANDARD",
				"submission_class_code": "EFFICACY",
			}
		)
		ocrevus["submissions"].append(
			{
				"submission_type": "SUPPL",
				"submission_number": "41",
				"submission_status": "AP",
				"submission_status_date": "20261101",
				"review_priority": "STANDARD",
				"submission_class_code": "EFFICACY",
			}
		)
		path = self.write_drugs(payload)
		with patch(f"{MODULE}.timezone.localdate", return_value=TODAY):
			self.sync(path)
		events = self.events("BLA761053")
		self.assertEqual(events[(Kind.FDA_EFFICACY_SUPPLEMENT, date(2019, 5, 1))].basis, Basis.RECONSTRUCTED)
		self.assertEqual(events[(Kind.FDA_EFFICACY_SUPPLEMENT, date(2026, 11, 1))].basis, Basis.OBSERVED)

	def test_an_application_new_to_us_is_reconstructed_even_when_recent(self):
		payload = load(DRUGS_BASE)
		payload["results"][0]["submissions"][-1]["submission_status_date"] = "20261201"
		with patch(f"{MODULE}.timezone.localdate", return_value=TODAY):
			self.sync(self.write_drugs(payload))
		self.assertTrue(all(e.basis == Basis.RECONSTRUCTED for e in MedicineEvent.objects.all()))


class StatusChangeTests(SyncTestCase):
	def test_tentative_to_approved_is_explained_by_the_approval_event(self):
		self.sync()
		payload = load(DRUGS_BASE)
		record = next(r for r in payload["results"] if r["application_number"] == "NDA202172")
		for product in record["products"]:
			product["marketing_status"] = "Prescription"
		record["submissions"].append(
			{
				"submission_type": "ORIG",
				"submission_number": "2",
				"submission_status": "AP",
				"submission_status_date": "20261201",
				"review_priority": "STANDARD",
				"submission_class_code": "TYPE 2",
			}
		)
		with patch(f"{MODULE}.timezone.localdate", return_value=TODAY):
			self.sync(self.write_drugs(payload))
		events = self.events("NDA202172")
		self.assertIn((Kind.FDA_APPROVED, date(2026, 12, 1)), events)
		self.assertNotIn(Kind.STATUS_CHANGED, {kind for kind, _ in events})
		self.assertEqual(self.medicine("NDA202172").authorisation_date, date(2026, 12, 1))

	def test_any_other_status_change_is_a_status_changed_event(self):
		self.sync()
		payload = load(DRUGS_BASE)
		record = next(r for r in payload["results"] if r["application_number"] == "NDA020229")
		for product in record["products"]:
			product["marketing_status"] = "Prescription"
		with patch(f"{MODULE}.timezone.localdate", return_value=TODAY):
			self.sync(self.write_drugs(payload))
		event = self.events("NDA020229")[(Kind.STATUS_CHANGED, TODAY)]
		self.assertEqual((event.from_value, event.to_value), ("Discontinued", "Approved"))
		self.assertEqual(event.in_words, "Status changed: Discontinued → Approved")


class GuardTests(SyncTestCase):
	def test_a_drugsfda_count_drop_stops_that_half_and_lets_the_purple_book_run(self):
		FdaSyncState.objects.create(source="drugsfda", last_count=100)
		with self.assertRaises(CommandError):
			self.sync()
		self.assertFalse(Medicine.objects.filter(fda_center="CDER").exists())
		self.assertEqual(Medicine.objects.filter(fda_center="CBER").count(), 5)
		self.assertEqual(FdaSyncState.objects.get(source="drugsfda").last_count, 100)

	def test_a_purple_book_count_drop_stops_that_half_and_lets_drugsfda_run(self):
		FdaSyncState.objects.create(source="purplebook", last_count=100)
		with self.assertRaises(CommandError):
			self.sync()
		self.assertEqual(Medicine.objects.filter(fda_center="CDER").count(), 12)
		self.assertFalse(Medicine.objects.filter(fda_center="CBER").exists())

	def test_the_failure_is_reported(self):
		FdaSyncState.objects.create(source="drugsfda", last_count=100)
		err = StringIO()
		with self.assertRaises(CommandError):
			call_command(
				"sync_fda_medicines", file=str(DRUGS_BASE), purplebook_file=str(PB_BASE), stdout=StringIO(), stderr=err
			)
		self.assertIn("down from 100", err.getvalue())

	def test_force_accepts_the_drop(self):
		FdaSyncState.objects.create(source="drugsfda", last_count=100)
		FdaSyncState.objects.create(source="purplebook", last_count=100)
		self.sync(force=True)
		self.assertEqual(Medicine.objects.count(), 17)

	def test_a_truncated_drugsfda_file_writes_nothing(self):
		payload = load(DRUGS_BASE)
		payload["meta"]["results"]["total"] = 99
		with self.assertRaisesRegex(CommandError, "truncated|At least one"):
			self.sync(self.write_drugs(payload), purplebook=None)
		self.assertEqual(Medicine.objects.count(), 0)

	def test_a_file_that_is_not_json_writes_nothing(self):
		path = self.tmp / "bad.json"
		path.write_text('{"meta": {"results": {"total": 1}}, "results": [{"application_number": "NDA1"')
		with self.assertRaises(CommandError):
			self.sync(path, purplebook=None)
		self.assertEqual(Medicine.objects.count(), 0)

	def test_a_purple_book_with_other_columns_writes_nothing(self):
		path = self.tmp / "pb.csv"
		path.write_text(PB_BASE.read_text().replace("Proper Name", "Proper Title"))
		with self.assertRaises(CommandError):
			self.sync(drugs=None, purplebook=path)
		self.assertEqual(Medicine.objects.count(), 0)

	def test_an_unreadable_file_is_a_command_error(self):
		with self.assertRaises(CommandError):
			self.sync(self.tmp / "missing.json", purplebook=None)

	def test_a_zip_works(self):
		path = self.tmp / "drugs.json.zip"
		with zipfile.ZipFile(path, "w") as archive:
			archive.write(DRUGS_BASE, "drug-drugsfda-0001-of-0001.json")
		self.sync(path, purplebook=None)
		self.assertEqual(Medicine.objects.filter(fda_center="CDER").count(), 12)


class DryRunTests(SyncTestCase):
	def test_dry_run_writes_nothing_and_does_not_match(self):
		with patch(f"{MODULE}.call_command") as matcher:
			output = self.sync(dry_run=True)
		self.assertEqual(Medicine.objects.count(), 0)
		self.assertEqual(MedicineEvent.objects.count(), 0)
		self.assertEqual(FdaSyncState.objects.count(), 0)
		matcher.assert_not_called()
		self.assertIn("[dry run] Drugs@FDA: 12 new", output)
		self.assertIn("[dry run] Purple Book: 5 new", output)
		# The flags are counted against the applications a real run would have created.
		self.assertIn("3 with a Drugs@FDA row", output)
		self.assertIn("nothing was written", output)

	def test_dry_run_against_stored_rows_reports_changes_only(self):
		self.sync()
		self.mark_tiny_fixture()
		self.sync(DRUGS_CHANGED, PB_CHANGED, dry_run=True)
		self.assertEqual(self.medicine("NDA206111").status, "Approved")
		self.assertTrue(self.medicine("BLA125104").in_latest_export)


class HalvesTests(SyncTestCase):
	def test_source_runs_one_half(self):
		self.sync(source="purplebook")
		self.assertEqual(Medicine.objects.filter(fda_center="CDER").count(), 0)
		self.assertEqual(Medicine.objects.filter(fda_center="CBER").count(), 5)
		self.assertEqual(FdaSyncState.objects.count(), 1)

	def test_a_single_local_file_runs_only_its_half(self):
		self.sync(purplebook=None)
		self.assertEqual(Medicine.objects.filter(fda_center="CBER").count(), 0)
		self.assertEqual(Medicine.objects.filter(fda_center="CDER").count(), 12)


class DownloadTests(SyncTestCase):
	def zip_bytes(self):
		buffer = io.BytesIO()
		with zipfile.ZipFile(buffer, "w") as archive:
			archive.write(DRUGS_BASE, "drug-drugsfda-0001-of-0001.json")
		return buffer.getvalue()

	def drugs_headers(self):
		return {
			"Last-Modified": "Fri, 23 Oct 2026 12:00:00 GMT",
			"ETag": '"abc"',
			"url": "https://download.open.fda.gov/drug/drugsfda/x.json.zip",
		}

	def pb_headers(self):
		return {
			"Last-Modified": "Wed, 28 Oct 2026 12:00:00 GMT",
			"ETag": '"def"',
			"url": "https://www.accessdata.fda.gov/drugsatfda_docs/PurpleBook/2026/purplebook-search-September-data-download.csv",
		}

	def test_a_download_records_the_validators_and_the_next_run_sends_them(self):
		sent = []

		def drugs_download(state):
			sent.append(state.last_modified)  # the state object is saved after the call
			return 200, self.zip_bytes(), self.drugs_headers()

		with patch(DOWNLOAD_DRUGS, side_effect=drugs_download), patch(
			DOWNLOAD_PB, return_value=(200, PB_BASE.read_bytes(), self.pb_headers())
		):
			out = StringIO()
			call_command("sync_fda_medicines", stdout=out)
		self.assertEqual(Medicine.objects.count(), 17)
		state = FdaSyncState.objects.get(source="drugsfda")
		self.assertEqual((state.last_modified, state.etag), ("Fri, 23 Oct 2026 12:00:00 GMT", '"abc"'))
		self.assertTrue(state.source_url.endswith("x.json.zip"))
		purple_state = FdaSyncState.objects.get(source="purplebook")
		self.assertTrue(purple_state.source_url.endswith("September-data-download.csv"))
		self.assertEqual(sent, [""])  # no state yet on the first run

		with patch(DOWNLOAD_DRUGS, return_value=(304, b"", {})) as drugs, patch(
			DOWNLOAD_PB, return_value=(304, b"", {})
		) as purple, patch(f"{MODULE}.call_command") as matcher:
			out = StringIO()
			call_command("sync_fda_medicines", stdout=out)
		self.assertEqual(drugs.call_args.args[0].etag, '"abc"')
		self.assertEqual(purple.call_args.args[0].etag, '"def"')
		self.assertIn("Drugs@FDA file unchanged (HTTP 304)", out.getvalue())
		self.assertIn("Purple Book file unchanged (HTTP 304)", out.getvalue())
		matcher.assert_not_called()
		self.assertEqual(Medicine.objects.count(), 17)

	def test_a_304_on_both_exits_cleanly_with_an_empty_database(self):
		with patch(DOWNLOAD_DRUGS, return_value=(304, b"", {})), patch(
			DOWNLOAD_PB, return_value=(304, b"", {})
		):
			call_command("sync_fda_medicines", stdout=StringIO())
		self.assertEqual(Medicine.objects.count(), 0)

	def test_force_downloads_without_the_validators(self):
		FdaSyncState.objects.create(source="drugsfda", last_modified="x", etag='"y"')
		with patch(DOWNLOAD_DRUGS, return_value=(304, b"", {})) as drugs, patch(
			DOWNLOAD_PB, return_value=(304, b"", {})
		):
			call_command("sync_fda_medicines", force=True, stdout=StringIO())
		self.assertIsNone(drugs.call_args.args[0])

	def test_the_purplebook_url_option_is_passed_on(self):
		with patch(DOWNLOAD_PB, return_value=(304, b"", {})) as purple:
			call_command(
				"sync_fda_medicines",
				source="purplebook",
				purplebook_url="https://example.test/pb.csv",
				stdout=StringIO(),
			)
		self.assertEqual(purple.call_args.kwargs["url"], "https://example.test/pb.csv")

	def test_an_http_error_is_a_failure_for_that_half_only(self):
		with patch(DOWNLOAD_DRUGS, return_value=(404, b"", {})), patch(
			DOWNLOAD_PB, return_value=(200, PB_BASE.read_bytes(), self.pb_headers())
		):
			with self.assertRaises(CommandError):
				call_command("sync_fda_medicines", stdout=StringIO(), stderr=StringIO())
		self.assertEqual(Medicine.objects.filter(fda_center="CBER").count(), 5)

	def test_a_failed_download_is_a_failure_for_that_half_only(self):
		with patch(DOWNLOAD_DRUGS, side_effect=OSError("down")), patch(
			DOWNLOAD_PB, return_value=(200, PB_BASE.read_bytes(), self.pb_headers())
		):
			with self.assertRaises(CommandError):
				call_command("sync_fda_medicines", stdout=StringIO(), stderr=StringIO())
		self.assertEqual(Medicine.objects.filter(fda_center="CBER").count(), 5)


class CheckConstraintTests(TestCase):
	def create(self, **kwargs):
		with transaction.atomic():
			return Medicine.objects.create(name="Test", **kwargs)

	def test_an_fda_row_needs_an_application_number(self):
		with self.assertRaises(IntegrityError):
			self.create(regulator=Regulator.FDA)
		with self.assertRaises(IntegrityError):
			self.create(regulator=Regulator.FDA, ema_product_number="EMEA/H/C/000001")

	def test_an_ema_row_needs_a_product_number(self):
		with self.assertRaises(IntegrityError):
			self.create(regulator=Regulator.EMA)
		with self.assertRaises(IntegrityError):
			self.create(regulator=Regulator.EMA, fda_application_number="NDA000001")

	def test_valid_rows_of_each_regulator(self):
		self.create(regulator=Regulator.EMA, ema_product_number="EMEA/H/C/000001")
		self.create(regulator=Regulator.FDA, fda_application_number="NDA000001")
		self.assertEqual(Medicine.objects.count(), 2)

	def test_the_default_regulator_is_ema(self):
		self.assertEqual(self.create(ema_product_number="EMEA/H/C/000002").regulator, Regulator.EMA)

	def test_two_rows_may_leave_the_other_regulators_key_empty(self):
		self.create(ema_product_number="EMEA/H/C/000003")
		self.create(ema_product_number="EMEA/H/C/000004")
		self.create(regulator=Regulator.FDA, fda_application_number="NDA000002")
		self.create(regulator=Regulator.FDA, fda_application_number="NDA000003")


class FdaRowsStayUnlinkedTests(SyncTestCase):
	"""PR 1 imports FDA rows but links none of them: the FDA matching rules come later."""

	def test_the_matcher_links_nothing_to_fda_rows(self):
		from organizations.models import Organization

		from gregory.models import (
			CategoryModality,
			MedicineCategoryAssignment,
			MedicineSubjectLink,
			Subject,
			Team,
			TeamCategory,
		)

		org = Organization.objects.create(name="Fda Org", slug="fda-org")
		team = Team.objects.create(organization=org, name="Fda Team", slug="fda-team")
		subject = Subject.objects.create(
			subject_name="MS", subject_slug="ms", team=team, ema_therapeutic_areas=["Multiple Sclerosis"]
		)
		bare = TeamCategory.objects.create(
			team=team,
			category_name="Anti-CD20",
			category_terms=["ocrelizumab", "natalizumab", "ofatumumab", "cladribine", "sildenafil"],
			modality=CategoryModality.BIOLOGIC_ANTIBODY,
		)
		with_subject = TeamCategory.objects.create(
			team=team,
			category_name="With subject",
			category_terms=["ocrelizumab"],
			modality=CategoryModality.BIOLOGIC_ANTIBODY,
		)
		with_subject.subjects.add(subject)
		# `bare` has no subjects, so it skips the disease check: it must still link no FDA row.
		self.sync()
		call_command("match_medicines", stdout=StringIO())
		self.assertEqual(MedicineCategoryAssignment.objects.count(), 0)
		self.assertEqual(MedicineSubjectLink.objects.count(), 0)
		self.assertFalse(Medicine.objects.filter(grouped_under__isnull=False).exists())
		self.assertEqual(bare.medicine_assignments.count(), 0)


class EmaSyncIgnoresFdaRowsTests(SyncTestCase):
	def test_the_ema_sync_does_not_flag_fda_rows_as_gone(self):
		self.sync()
		ema_fixture = Path(__file__).resolve().parent.parent / "fixtures" / "ema" / "medicines-base.json"
		call_command("sync_ema_medicines", file=str(ema_fixture), stdout=StringIO())
		self.assertEqual(Medicine.objects.filter(regulator=Regulator.FDA, in_latest_export=False).count(), 0)
		self.assertEqual(Medicine.objects.filter(regulator=Regulator.EMA).count(), 16)
