"""Tests for sync_fda_labels. DailyMed is never called: ``list_labels`` and
``fetch_indications`` are replaced with a fake that answers from the real responses in
fixtures/fda/dailymed/ (fetched on 2026-10-30; the labels are trimmed to their indications).
The medicines are the real applications in fixtures/fda/drugsfda-base.json and the Purple
Book rows in purplebook-base.csv, loaded with sync_fda_medicines --file. Where a test needs
a label DailyMed has no fixture for (Kymriah's, a label published in 2027) the response is
SYNTHETIC and made up here."""

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase
from organizations.models import Organization

from gregory.management.commands.sync_fda_labels import Command
from gregory.models import (
	CategoryModality,
	CategoryType,
	Medicine,
	Subject,
	Team,
	TeamCategory,
)
from gregory.utils.dailymed import (
	DailyMedError,
	DailyMedLabel,
	label_page_url,
	parse_indications,
	parse_label_list,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "fda"
DAILYMED = FIXTURES / "dailymed"
MODULE = "gregory.management.commands.sync_fda_labels"

OCREVUS_SETID = "9da42362-3bb5-4b83-b4bb-b59fd4e55f0d"
KESIMPTA_SETID = "6a8a3f53-2062-48ff-9dbe-b939df133ca3"
VIAGRA_SETID = "0b0be196-0c62-461c-94f4-9a35339b4501"
MYLAN_SETID = "98a9897b-0cbc-4ea6-9ac2-4dd719a2ede0"
KYMRIAH_SETID = "kymriah-synthetic-setid"

LISTS = {
	"BLA761053": "list-ocrevus.json",
	"BLA125326": "list-kesimpta.json",
	"NDA020895": "list-viagra.json",
}


def fixture_list(name):
	return parse_label_list(json.loads((DAILYMED / name).read_text()))


class FakeDailyMed:
	"""Answers ``list_labels`` and ``fetch_indications`` from the fixtures and records the
	calls. ``lists`` maps an application number to its labels; any other application has none."""

	def __init__(self):
		self.lists = {number: fixture_list(name) for number, name in LISTS.items()}
		self.texts = {}
		self.list_calls = []
		self.fetch_calls = []
		self.list_error = set()
		self.fetch_error = set()

	def list_labels(self, number):
		self.list_calls.append(number)
		if number in self.list_error:
			raise DailyMedError("down")
		return self.lists.get(number, [])

	def fetch_indications(self, setid):
		self.fetch_calls.append(setid)
		if setid in self.fetch_error:
			raise DailyMedError("down")
		if setid in self.texts:
			return self.texts[setid]
		return parse_indications((DAILYMED / f"{setid}.xml").read_bytes())

	def patch(self):
		patches = [
			patch(f"{MODULE}.list_labels", self.list_labels),
			patch(f"{MODULE}.fetch_indications", self.fetch_indications),
		]
		for started in patches:
			started.start()
		return patches


class SyncFdaLabelsTestCase(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Labels Org", slug="labels-org")
		cls.team = Team.objects.create(organization=cls.org, name="Labels Team", slug="labels-team")
		cls.ms = Subject.objects.create(
			subject_name="MS", subject_slug="ms", team=cls.team, fda_indication_terms=["multiple sclerosis"]
		)
		cls.unmapped = Subject.objects.create(subject_name="Other", subject_slug="other", team=cls.team)
		call_command(
			"sync_fda_medicines",
			file=str(FIXTURES / "drugsfda-base.json"),
			purplebook_file=str(FIXTURES / "purplebook-base.csv"),
			stdout=StringIO(),
		)

	def setUp(self):
		self.dailymed = FakeDailyMed()
		for started in self.dailymed.patch():
			self.addCleanup(started.stop)

	def category(self, name, terms, subject=None, modality=CategoryModality.BIOLOGIC_ANTIBODY, **kwargs):
		cat = TeamCategory.objects.create(
			team=self.team, category_name=name, category_terms=terms, modality=modality, **kwargs
		)
		cat.subjects.add(subject or self.ms)
		return cat

	def run_labels(self, **options):
		out = StringIO()
		call_command("sync_fda_labels", stdout=out, **options)
		return out.getvalue()

	def medicine(self, number):
		return Medicine.objects.get(fda_application_number=number)


class CandidateTests(SyncFdaLabelsTestCase):
	def test_only_candidates_are_asked_about(self):
		self.category("Ocrelizumab", ["ocrelizumab"])
		self.category("Ofatumumab", ["ofatumumab"])
		self.category("Sildenafil", ["sildenafil"], modality=CategoryModality.SMALL_MOLECULE)
		self.category("Natalizumab", ["natalizumab"])
		self.category("Cladribine", ["cladribine"], modality=CategoryModality.SMALL_MOLECULE)
		out = self.run_labels()
		self.assertEqual(
			sorted(self.dailymed.list_calls),
			[
				"BLA125104",  # Tysabri
				"BLA125326",  # Kesimpta / Arzerra
				"BLA761053",  # Ocrevus
				"BLA761322",  # Tyruko
				"NDA020229",  # Leustatin
				"NDA020895",  # Viagra
				"NDA022561",  # Mavenclad
			],
		)
		# Ocrevus Zunovo's substance is not the pattern plus one word, so it is no candidate.
		self.assertNotIn("BLA761371", self.dailymed.list_calls)
		self.assertIn("7 candidates, 7 list calls", out)

	def test_a_category_whose_subjects_have_no_terms_makes_no_candidates(self):
		self.category("Ocrelizumab", ["ocrelizumab"], subject=self.unmapped)
		out = self.run_labels()
		self.assertEqual(self.dailymed.list_calls, [])
		self.assertIn("0 candidates", out)

	def test_a_manual_category_makes_no_candidates(self):
		self.category("Ocrelizumab", ["ocrelizumab"], category_type=CategoryType.MANUAL)
		self.run_labels()
		self.assertEqual(self.dailymed.list_calls, [])

	def test_a_research_topic_without_medicine_terms_makes_no_candidates(self):
		self.category("Anti-CD20", ["ocrelizumab"], modality=CategoryModality.RESEARCH_TOPIC)
		self.run_labels()
		self.assertEqual(self.dailymed.list_calls, [])

	def test_medicine_terms_make_candidates_by_brand_name(self):
		self.category("Arzerra", [], modality=CategoryModality.RESEARCH_TOPIC, medicine_terms=["arzerra"])
		self.run_labels()
		self.assertEqual(self.dailymed.list_calls, ["BLA125326"])

	def test_a_combination_is_no_candidate_unless_the_category_opts_in(self):
		self.category("Metformin", ["metformin"], modality=CategoryModality.SMALL_MOLECULE)
		self.run_labels()
		self.assertEqual(self.dailymed.list_calls, [])
		self.category(
			"Metformin too",
			["metformin"],
			modality=CategoryModality.SMALL_MOLECULE,
			medicine_include_combinations=True,
		)
		self.run_labels()
		self.assertEqual(self.dailymed.list_calls, ["NDA206111"])

	def test_a_contrast_agent_is_no_candidate(self):
		self.category("CO2", ["carbon dioxide"], modality=CategoryModality.SMALL_MOLECULE)
		self.run_labels()
		self.assertEqual(self.dailymed.list_calls, [])

	def test_an_ema_row_is_no_candidate(self):
		call_command(
			"sync_ema_medicines",
			file=str(FIXTURES.parent / "ema" / "medicines-base.json"),
			stdout=StringIO(),
		)
		self.category("Ocrelizumab", ["ocrelizumab"])
		self.run_labels()
		self.assertEqual(self.dailymed.list_calls, ["BLA761053"])


class StoreTests(SyncFdaLabelsTestCase):
	def setUp(self):
		super().setUp()
		self.category("Ocrelizumab", ["ocrelizumab"])
		self.category("Sildenafil", ["sildenafil"], modality=CategoryModality.SMALL_MOLECULE)

	def test_labels_indication_and_check_time_are_stored(self):
		out = self.run_labels()
		ocrevus = self.medicine("BLA761053")
		self.assertEqual(len(ocrevus.label_indications), 1)
		entry = ocrevus.label_indications[0]
		self.assertEqual(
			(entry["setid"], entry["version"], entry["title"], entry["published"]),
			(OCREVUS_SETID, "24", "OCREVUS (OCRELIZUMAB) INJECTION [GENENTECH, INC.]", "2026-05-20"),
		)
		self.assertIn("multiple sclerosis", entry["indication"])
		self.assertEqual(ocrevus.indication, entry["indication"])
		self.assertIsNotNone(ocrevus.label_checked_at)
		self.assertIn("3 labels downloaded", out)

	def test_a_label_is_downloaded_once_and_not_again(self):
		self.run_labels()
		self.assertEqual(sorted(self.dailymed.fetch_calls), sorted([OCREVUS_SETID, VIAGRA_SETID, MYLAN_SETID]))
		self.dailymed.fetch_calls.clear()
		first_check = self.medicine("BLA761053").label_checked_at
		out = self.run_labels()
		self.assertEqual(self.dailymed.fetch_calls, [])
		self.assertIn("0 labels downloaded, 3 unchanged", out)
		# The application is still asked about, and the check time moves.
		self.assertGreater(self.medicine("BLA761053").label_checked_at, first_check)

	def test_a_new_version_is_downloaded_again(self):
		self.run_labels()
		self.dailymed.fetch_calls.clear()
		self.dailymed.lists["BLA761053"] = fixture_list("list-ocrevus-v25.json")
		self.dailymed.texts[OCREVUS_SETID] = "OCREVUS is indicated for multiple sclerosis, version 25."
		self.run_labels()
		self.assertEqual(self.dailymed.fetch_calls, [OCREVUS_SETID])
		ocrevus = self.medicine("BLA761053")
		self.assertEqual(ocrevus.label_indications[0]["version"], "25")
		self.assertEqual(ocrevus.label_indications[0]["published"], "2026-10-30")
		self.assertEqual(ocrevus.indication, "OCREVUS is indicated for multiple sclerosis, version 25.")

	def test_a_label_that_is_no_longer_listed_is_dropped(self):
		self.run_labels()
		self.assertEqual({e["setid"] for e in self.medicine("NDA020895").label_indications}, {VIAGRA_SETID, MYLAN_SETID})
		self.dailymed.lists["NDA020895"] = [
			label for label in self.dailymed.lists["NDA020895"] if label.setid == VIAGRA_SETID
		]
		self.run_labels()
		self.assertEqual({e["setid"] for e in self.medicine("NDA020895").label_indications}, {VIAGRA_SETID})

	def test_an_application_that_loses_all_its_labels_is_emptied(self):
		self.run_labels()
		self.dailymed.lists["BLA761053"] = []
		out = self.run_labels()
		ocrevus = self.medicine("BLA761053")
		self.assertEqual(ocrevus.label_indications, [])
		self.assertEqual(ocrevus.indication, "")
		self.assertIn("1 candidates with no label", out)

	def test_a_candidate_with_no_label_is_counted(self):
		self.dailymed.lists["BLA761053"] = []
		out = self.run_labels()
		self.assertIn("1 candidates with no label", out)
		ocrevus = self.medicine("BLA761053")
		self.assertEqual(ocrevus.label_indications, [])
		self.assertIsNotNone(ocrevus.label_checked_at)

	def test_a_dailymed_list_failure_keeps_the_stored_labels(self):
		self.run_labels()
		stored = self.medicine("BLA761053")
		self.dailymed.list_error.add("BLA761053")
		out = self.run_labels()
		after = self.medicine("BLA761053")
		self.assertEqual(after.label_indications, stored.label_indications)
		self.assertEqual(after.indication, stored.indication)
		self.assertEqual(after.label_checked_at, stored.label_checked_at)
		self.assertIn("BLA761053: DailyMed list failed", out)
		self.assertIn("1 failed", out)
		# The other applications still ran.
		self.assertEqual(len(self.medicine("NDA020895").label_indications), 2)

	def test_a_failed_label_download_keeps_the_stored_copy_of_that_label(self):
		self.run_labels()
		stored = self.medicine("BLA761053").label_indications
		self.dailymed.lists["BLA761053"] = fixture_list("list-ocrevus-v25.json")
		self.dailymed.fetch_error.add(OCREVUS_SETID)
		out = self.run_labels()
		self.assertEqual(self.medicine("BLA761053").label_indications, stored)
		self.assertIn("kept its stored copy", out)

	def test_a_new_label_that_fails_to_download_is_retried_next_run(self):
		self.dailymed.fetch_error.add(OCREVUS_SETID)
		out = self.run_labels()
		self.assertEqual(self.medicine("BLA761053").label_indications, [])
		self.assertIn("it will be retried next run", out)
		self.dailymed.fetch_error.clear()
		self.run_labels()
		self.assertEqual(len(self.medicine("BLA761053").label_indications), 1)

	def test_the_run_never_fails_because_of_dailymed(self):
		for number in LISTS:
			self.dailymed.list_error.add(number)
		self.dailymed.fetch_error.add(OCREVUS_SETID)
		self.run_labels()  # no CommandError

	def test_the_summary_counts(self):
		out = self.run_labels()
		self.assertIn(
			"FDA labels: 2 candidates, 2 list calls, 3 labels downloaded, 0 unchanged, 0 failed, "
			"0 candidates with no label.",
			out,
		)

	def test_the_indication_comes_from_the_label_titled_with_a_brand_name(self):
		# Viagra has Pfizer's VIAGRA label and Mylan's generic-titled one. Make the generic one
		# the newest: the brand rule must still win.
		self.dailymed.lists["NDA020895"] = [
			DailyMedLabel(label.setid, label.version, label.title, "2027-01-01" if label.setid == MYLAN_SETID else label.published)
			for label in self.dailymed.lists["NDA020895"]
		]
		self.dailymed.texts[MYLAN_SETID] = "Sildenafil citrate tablets are indicated for erectile dysfunction (Mylan)."
		self.run_labels()
		viagra = self.medicine("NDA020895")
		self.assertTrue(viagra.indication.startswith("1 INDICATIONS AND USAGE VIAGRA is indicated"))
		self.assertEqual(len(viagra.label_indications), 2)

	def test_without_a_brand_label_the_most_recent_one_is_used(self):
		self.dailymed.lists["NDA020895"] = [
			label for label in self.dailymed.lists["NDA020895"] if label.setid == MYLAN_SETID
		]
		self.run_labels()
		self.assertTrue(self.medicine("NDA020895").indication.startswith("1 INDICATIONS AND USAGE Sildenafil citrate"))

	def test_a_cder_rows_url_is_left_alone(self):
		before = self.medicine("BLA761053").url
		self.assertIn("accessdata.fda.gov", before)
		self.run_labels()
		self.assertEqual(self.medicine("BLA761053").url, before)

	def test_the_sync_of_the_main_files_does_not_overwrite_the_label_columns(self):
		self.run_labels()
		stored = self.medicine("BLA761053")
		with patch("gregory.management.commands.sync_fda_medicines.call_command"):
			call_command(
				"sync_fda_medicines",
				file=str(FIXTURES / "drugsfda-changed.json"),
				purplebook_file=str(FIXTURES / "purplebook-changed.csv"),
				force=True,  # the changed file has one application fewer
				stdout=StringIO(),
			)
		after = self.medicine("BLA761053")
		self.assertEqual(after.label_indications, stored.label_indications)
		self.assertEqual(after.indication, stored.indication)
		self.assertEqual(after.label_checked_at, stored.label_checked_at)
		self.assertGreater(after.last_synced_at, stored.last_synced_at)  # the sync did change the row


class CberUrlTests(SyncFdaLabelsTestCase):
	def setUp(self):
		super().setUp()
		self.category("Tisagenlecleucel", ["tisagenlecleucel"], modality=CategoryModality.CELL_GENE_THERAPY)
		# SYNTHETIC: DailyMed has a Kymriah label, but no fixture was fetched for it.
		self.dailymed.lists["BLA125646"] = [
			DailyMedLabel(KYMRIAH_SETID, "3", "KYMRIAH (TISAGENLECLEUCEL) SUSPENSION", "2026-02-01")
		]
		self.dailymed.texts[KYMRIAH_SETID] = "KYMRIAH is indicated for B-cell acute lymphoblastic leukemia."

	def test_a_cber_row_gets_its_dailymed_page(self):
		self.assertEqual(self.medicine("BLA125646").url, "")
		self.run_labels()
		kymriah = self.medicine("BLA125646")
		self.assertEqual(kymriah.url, label_page_url(KYMRIAH_SETID))
		self.assertEqual(kymriah.fda_center, "CBER")

	def test_a_cber_url_that_is_already_set_is_kept(self):
		Medicine.objects.filter(fda_application_number="BLA125646").update(url="https://example.org/kept")
		self.run_labels()
		self.assertEqual(self.medicine("BLA125646").url, "https://example.org/kept")

	def test_a_cber_row_with_no_label_keeps_a_blank_url(self):
		self.dailymed.lists["BLA125646"] = []
		self.run_labels()
		self.assertEqual(self.medicine("BLA125646").url, "")

	def test_the_purple_book_sync_leaves_the_url_alone(self):
		self.run_labels()
		with patch("gregory.management.commands.sync_fda_medicines.call_command"):
			call_command(
				"sync_fda_medicines",
				purplebook_file=str(FIXTURES / "purplebook-changed.csv"),
				stdout=StringIO(),
			)
		self.assertEqual(self.medicine("BLA125646").url, label_page_url(KYMRIAH_SETID))


class OptionTests(SyncFdaLabelsTestCase):
	def test_application_refreshes_one_application_whether_or_not_it_is_a_candidate(self):
		out = self.run_labels(application="BLA125326")
		self.assertEqual(self.dailymed.list_calls, ["BLA125326"])
		self.assertEqual(len(self.medicine("BLA125326").label_indications), 1)
		self.assertIn("1 candidates, 1 list calls, 1 labels downloaded", out)

	def test_application_is_case_insensitive(self):
		self.run_labels(application=" bla125326 ")
		self.assertEqual(self.dailymed.list_calls, ["BLA125326"])

	def test_an_unknown_application_is_an_error(self):
		with self.assertRaises(CommandError):
			self.run_labels(application="NDA999999")

	def test_an_ema_application_is_not_found(self):
		with self.assertRaises(CommandError):
			self.run_labels(application="EMEA/H/C/000001")

	def test_dry_run_lists_but_downloads_and_writes_nothing(self):
		TeamCategory.objects.create(
			team=self.team,
			category_name="Ocrelizumab",
			category_terms=["ocrelizumab"],
			modality=CategoryModality.BIOLOGIC_ANTIBODY,
		).subjects.add(self.ms)
		out = self.run_labels(dry_run=True)
		self.assertEqual(self.dailymed.list_calls, ["BLA761053"])
		self.assertEqual(self.dailymed.fetch_calls, [])
		ocrevus = self.medicine("BLA761053")
		self.assertEqual(ocrevus.label_indications, [])
		self.assertIsNone(ocrevus.label_checked_at)
		self.assertEqual(ocrevus.indication, "")
		self.assertIn("[dry run] FDA labels: 1 candidates, 1 list calls, 1 labels to download", out)
		self.assertIn("Dry run: nothing was downloaded or written.", out)

	def test_dry_run_with_stored_labels_reports_them_unchanged(self):
		self.run_labels(application="BLA761053")
		self.dailymed.list_calls.clear()
		out = self.run_labels(application="BLA761053", dry_run=True)
		self.assertIn("0 labels to download, 1 unchanged", out)


class WiringTests(TestCase):
	"""sync_fda_medicines runs the labels, then the matcher."""

	def sync(self, **options):
		out = StringIO()
		with patch("gregory.management.commands.sync_fda_medicines.call_command") as mock:
			call_command(
				"sync_fda_medicines",
				file=str(FIXTURES / "drugsfda-base.json"),
				purplebook_file=str(FIXTURES / "purplebook-base.csv"),
				stdout=out,
				**options,
			)
		return [call[0][0] for call in mock.call_args_list]

	def test_labels_come_before_the_matcher(self):
		self.assertEqual(self.sync(), ["sync_fda_labels", "match_medicines"])

	def test_a_dry_run_runs_neither(self):
		self.assertEqual(self.sync(dry_run=True), [])

	def test_a_failure_in_the_labels_does_not_stop_the_matcher(self):
		calls = []

		def fake(name, *args, **kwargs):
			calls.append(name)
			if name == "sync_fda_labels":
				raise RuntimeError("boom")

		with patch("gregory.management.commands.sync_fda_medicines.call_command", fake):
			call_command(
				"sync_fda_medicines",
				file=str(FIXTURES / "drugsfda-base.json"),
				purplebook_file=str(FIXTURES / "purplebook-base.csv"),
				stdout=StringIO(),
				stderr=StringIO(),
			)
		self.assertEqual(calls, ["sync_fda_labels", "match_medicines"])


class MainLabelTests(TestCase):
	"""The brand-name rule on its own."""

	def entry(self, setid, title, published):
		return {"setid": setid, "title": title, "published": published, "indication": "text " + setid}

	def test_the_first_brand_beats_a_later_one(self):
		entries = [
			self.entry("a", "ARZERRA (OFATUMUMAB) INJECTION", "2026-06-01"),
			self.entry("k", "KESIMPTA (OFATUMUMAB) INJECTION", "2026-01-01"),
		]
		self.assertEqual(Command._main_label(entries, ["KESIMPTA", "ARZERRA"])["setid"], "k")
		self.assertEqual(Command._main_label(entries, ["ARZERRA", "KESIMPTA"])["setid"], "a")

	def test_among_equal_brand_matches_the_newest_wins(self):
		entries = [
			self.entry("old", "VIAGRA (SILDENAFIL) [A]", "2019-12-20"),
			self.entry("new", "VIAGRA (SILDENAFIL) [B]", "2026-02-13"),
		]
		self.assertEqual(Command._main_label(entries, ["VIAGRA"])["setid"], "new")

	def test_a_brand_is_matched_at_the_start_and_as_a_whole_word_ignoring_case(self):
		entries = [
			self.entry("contains", "GENERIC OCREVUS INJECTION", "2026-06-01"),
			self.entry("longer", "OCREVUSX INJECTION", "2026-05-01"),
			self.entry("real", "Ocrevus (ocrelizumab)", "2026-01-01"),
		]
		self.assertEqual(Command._main_label(entries, ["OCREVUS"])["setid"], "real")

	def test_no_brand_match_falls_back_to_the_newest(self):
		entries = [self.entry("a", "X", "2020-01-01"), self.entry("b", "Y", "2026-01-01")]
		self.assertEqual(Command._main_label(entries, ["ZZZ"])["setid"], "b")
		self.assertEqual(Command._main_label(entries, [])["setid"], "b")

	def test_a_label_with_no_indications_text_only_stands_in_when_none_has_any(self):
		empty = {"setid": "e", "title": "OCREVUS", "published": "2026-06-01", "indication": ""}
		full = self.entry("f", "OTHER", "2020-01-01")
		self.assertEqual(Command._main_label([empty, full], ["OCREVUS"])["setid"], "f")
		self.assertEqual(Command._main_label([empty], ["OCREVUS"])["setid"], "e")

	def test_no_labels_no_main_label(self):
		self.assertIsNone(Command._main_label([], ["OCREVUS"]))
