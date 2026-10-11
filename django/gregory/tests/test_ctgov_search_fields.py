"""
ClinicalTrials.gov sources search with three dedicated fields, never with `description`.

The importer used to read "INTERVENTION:…" / "TERM:…" from a source's description.
Description is notes for people, so migration 0111 copied those values into
ctgov_search_intervention / ctgov_search_term and the importer now reads only the
three search fields. A source with none of them set is skipped: an empty search
would return every trial in the registry.

Run:
  docker exec gregory pytest gregory/tests/test_ctgov_search_fields.py
"""

import importlib
from unittest.mock import MagicMock

from django.apps import apps
from django.test import TestCase

from gregory.management.commands.feedreader_trials_ctgov import Command
from gregory.models import Sources

migration_0111 = importlib.import_module("gregory.migrations.0111_source_ctgov_search_fields")


def make_command(api=None):
	cmd = Command()
	cmd.debug = False
	cmd.api = api if api is not None else MagicMock()
	return cmd


def ctgov_source(**fields):
	return Sources.objects.create(
		name="CTGov | Test", method="ctgov_api", source_for="trials", active=True, **fields
	)


class SearchParamsTests(TestCase):
	def test_each_field_maps_to_its_query_parameter(self):
		source = ctgov_source(
			ctgov_search_condition="multiple sclerosis",
			ctgov_search_intervention="ocrelizumab",
			ctgov_search_term="remyelination",
		)
		params = make_command()._build_search_params(source)
		self.assertEqual(params["query_cond"], "multiple sclerosis")
		self.assertEqual(params["query_intr"], "ocrelizumab")
		self.assertEqual(params["query_term"], "remyelination")

	def test_description_never_builds_the_search(self):
		source = ctgov_source(
			ctgov_search_condition="multiple sclerosis",
			description="INTERVENTION:rituximab",
		)
		params = make_command()._build_search_params(source)
		self.assertNotIn("query_intr", params)
		self.assertNotIn("query_term", params)

	def test_blank_fields_are_left_out(self):
		source = ctgov_source(ctgov_search_condition="  ", ctgov_search_term="neuroimmune")
		params = make_command()._build_search_params(source)
		self.assertNotIn("query_cond", params)
		self.assertEqual(params["query_term"], "neuroimmune")


class EmptySearchIsSkippedTests(TestCase):
	def test_source_without_any_search_field_is_never_fetched(self):
		source = ctgov_source(description="TERM:cell reprogramming")
		api = MagicMock()
		make_command(api).process_sources(max_results=2000)
		api.search_all.assert_not_called()
		source.refresh_from_db()
		self.assertIsNone(source.last_successful_fetch_at)

	def test_other_sources_still_run_when_one_is_skipped(self):
		ctgov_source()
		ctgov_source(ctgov_search_condition="multiple sclerosis")
		api = MagicMock()
		api.search_all.return_value = iter([])
		make_command(api).process_sources(max_results=2000)
		self.assertEqual(api.search_all.call_count, 1)


class MoveDescriptionQueriesMigrationTests(TestCase):
	def run_migration(self):
		migration_0111.move_description_queries(apps, None)

	def test_prefixes_move_into_their_fields_and_description_stays(self):
		term = ctgov_source(description="TERM:cell reprogramming")
		intervention = ctgov_source(description="intervention: Dihydroartemisinin ")
		self.run_migration()
		term.refresh_from_db()
		intervention.refresh_from_db()
		self.assertEqual(term.ctgov_search_term, "cell reprogramming")
		self.assertEqual(intervention.ctgov_search_intervention, "Dihydroartemisinin")
		self.assertEqual(term.description, "TERM:cell reprogramming")

	def test_existing_values_and_plain_notes_are_left_alone(self):
		kept = ctgov_source(description="TERM:new", ctgov_search_term="already set")
		notes = ctgov_source(description="Search for trials where the condition is MS.")
		self.run_migration()
		kept.refresh_from_db()
		notes.refresh_from_db()
		self.assertEqual(kept.ctgov_search_term, "already set")
		self.assertIsNone(notes.ctgov_search_term)
		self.assertIsNone(notes.ctgov_search_intervention)

	def test_other_methods_are_not_touched(self):
		rss = Sources.objects.create(
			name="RSS", method="rss", source_for="trials", description="TERM:not a search"
		)
		self.run_migration()
		rss.refresh_from_db()
		self.assertIsNone(rss.ctgov_search_term)
