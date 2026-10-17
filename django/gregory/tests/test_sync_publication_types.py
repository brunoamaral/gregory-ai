"""Tests for sync_publication_types against canned ESearch/ESummary JSON (no NCBI calls)."""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from gregory.models import PublicationType
from gregory.utils.ncbi import ESearchResult


def summary(**entries):
	return {"result": {"uids": list(entries), **entries}}


ENTRY_JOURNAL = {
	"ds_meshui": "D016428",
	"ds_meshterms": ["Journal Article", "Article, Journal"],
	"ds_scopenote": "Scholarly cited works.",
}
ENTRY_REVIEW = {
	"ds_meshui": "D016454",
	"ds_meshterms": ["Review"],
	"ds_scopenote": "Article that summarizes.",
}


def fake_client(entries):
	"""A client whose ESearch lists the UIDs and whose ESummary returns the entries."""
	client = MagicMock()
	client.esearch.return_value = ESearchResult(count=len(entries), ids=list(entries))
	client.esummary.side_effect = lambda db, ids: summary(**{i: entries[i] for i in ids})
	return client


def run(client):
	out = StringIO()
	with patch(
		"gregory.management.commands.sync_publication_types.NcbiClient",
		return_value=client,
	):
		call_command("sync_publication_types", stdout=out)
	return out.getvalue()


class SyncPublicationTypesTests(TestCase):
	def test_creates_rows_keyed_by_mesh_ui_not_uid(self):
		client = fake_client({"68016428": ENTRY_JOURNAL, "68016454": ENTRY_REVIEW})
		output = run(client)
		journal = PublicationType.objects.get(ui="D016428")
		self.assertEqual(journal.name, "Journal Article")
		self.assertEqual(journal.scope_note, "Scholarly cited works.")
		self.assertEqual(PublicationType.objects.count(), 2)
		self.assertIn("2 created", output)
		client.esearch.assert_called_once()
		self.assertEqual(client.esearch.call_args.kwargs["db"], "mesh")

	def test_is_idempotent(self):
		entries = {"68016428": ENTRY_JOURNAL}
		run(fake_client(entries))
		output = run(fake_client(entries))
		self.assertEqual(PublicationType.objects.count(), 1)
		self.assertIn("0 created", output)
		self.assertIn("1 unchanged", output)

	def test_renames_flow_through_by_id(self):
		PublicationType.objects.create(ui="D016428", name="Old Name", scope_note="Scholarly cited works.")
		output = run(fake_client({"68016428": ENTRY_JOURNAL}))
		self.assertEqual(PublicationType.objects.get(ui="D016428").name, "Journal Article")
		self.assertEqual(PublicationType.objects.count(), 1)
		self.assertIn("1 renamed", output)

	def test_scope_note_updates_are_counted_separately(self):
		PublicationType.objects.create(ui="D016428", name="Journal Article", scope_note="")
		output = run(fake_client({"68016428": ENTRY_JOURNAL}))
		self.assertEqual(
			PublicationType.objects.get(ui="D016428").scope_note, "Scholarly cited works."
		)
		self.assertIn("0 renamed", output)
		self.assertIn("1 scope notes updated", output)

	def test_entries_without_a_mesh_ui_are_skipped(self):
		bad = {"ds_meshterms": ["No UI"], "ds_scopenote": ""}
		run(fake_client({"1": bad, "68016428": ENTRY_JOURNAL}))
		self.assertEqual(PublicationType.objects.count(), 1)
