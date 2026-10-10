"""Patent matching in rebuild_categories (--patents-only, patent hash isolation)."""

import hashlib
import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from organizations.models import Organization

from gregory.management.commands.rebuild_categories import Command
from gregory.models import (
	CategoryAssignmentSource,
	PatentCategoryAssignment,
	Patents,
	Subject,
	Team,
	TeamCategory,
)


class PatentCategoryTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org", slug="cat-org")
		self.team = Team.objects.create(organization=self.org, name="Research", slug="research-p")
		self.subject = Subject.objects.create(subject_name="MS", subject_slug="ms-p", team=self.team)
		self.other_subject = Subject.objects.create(subject_name="PD", subject_slug="pd-p", team=self.team)
		self.category = TeamCategory.objects.create(
			team=self.team, category_name="Ocrelizumab", category_terms=["ocrelizumab"]
		)
		self.category.subjects.add(self.subject)
		self._n = 0

	def make_patent(self, title, summary="", claims="", subject=None):
		self._n += 1
		patent = Patents.objects.create(
			family_id=str(self._n), title=title, summary=summary or None, claims=claims or None
		)
		patent.subjects.add(subject or self.subject)
		return patent

	def run_rebuild(self, **kwargs):
		call_command("rebuild_categories", stdout=StringIO(), **kwargs)

	def assignment(self, patent):
		return PatentCategoryAssignment.objects.filter(patents=patent, teamcategory=self.category).first()

	def test_matches_on_title_and_summary(self):
		by_title = self.make_patent("Ocrelizumab dosing")
		by_summary = self.make_patent("Antibody method", summary="Uses ocrelizumab for MS")
		unrelated = self.make_patent("Battery electrode")

		self.run_rebuild(patents_only=True)

		self.assertEqual(self.assignment(by_title).source, CategoryAssignmentSource.AUTOMATIC)
		self.assertIsNotNone(self.assignment(by_summary))
		self.assertIsNone(self.assignment(unrelated))
		self.assertEqual(self.category.patents_count(), 2)

	def test_claims_alone_are_below_the_default_threshold(self):
		# claims weight 1 + 2 bonus for one matched term = 3 = the default minimum
		patent = self.make_patent("Antibody method", claims="1. A method using ocrelizumab")
		self.run_rebuild(patents_only=True)
		self.assertIsNotNone(self.assignment(patent))

	def test_patent_outside_the_category_subjects_is_not_matched(self):
		patent = self.make_patent("Ocrelizumab dosing", subject=self.other_subject)
		self.run_rebuild(patents_only=True)
		self.assertIsNone(self.assignment(patent))

	def test_stale_automatic_assignment_is_removed(self):
		patent = self.make_patent("Ocrelizumab dosing")
		self.run_rebuild(patents_only=True)
		Patents.objects.filter(pk=patent.pk).update(title="Battery electrode")
		self.run_rebuild(patents_only=True)
		self.assertIsNone(self.assignment(patent))

	def test_manual_assignment_is_never_removed(self):
		patent = self.make_patent("Battery electrode")
		PatentCategoryAssignment.objects.create(
			patents=patent, teamcategory=self.category, source=CategoryAssignmentSource.MANUAL
		)
		self.run_rebuild(patents_only=True)
		self.assertEqual(self.assignment(patent).source, CategoryAssignmentSource.MANUAL)

	def test_dry_run_changes_nothing(self):
		self.make_patent("Ocrelizumab dosing")
		self.run_rebuild(patents_only=True, dry_run=True)
		self.assertEqual(PatentCategoryAssignment.objects.count(), 0)
		self.category.refresh_from_db()
		self.assertIsNone(self.category.patent_match_config_hash)

	def test_full_run_includes_patents(self):
		patent = self.make_patent("Ocrelizumab dosing")
		self.run_rebuild()
		self.assertIsNotNone(self.assignment(patent))

	def test_articles_only_and_trials_only_skip_patents(self):
		patent = self.make_patent("Ocrelizumab dosing")
		self.run_rebuild(articles_only=True)
		self.run_rebuild(trials_only=True)
		self.assertIsNone(self.assignment(patent))

	def test_only_flags_are_mutually_exclusive(self):
		with self.assertRaises(CommandError):
			self.run_rebuild(articles_only=True, patents_only=True)


class PatentHashIsolationTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org", slug="hash-org")
		self.team = Team.objects.create(organization=self.org, name="Research", slug="research-h")
		self.subject = Subject.objects.create(subject_name="MS", subject_slug="ms-h", team=self.team)
		self.category = TeamCategory.objects.create(
			team=self.team, category_name="Ocrelizumab", category_terms=["ocrelizumab"]
		)
		self.category.subjects.add(self.subject)

	def run_rebuild(self, **kwargs):
		call_command("rebuild_categories", stdout=StringIO(), **kwargs)

	def reload(self):
		return TeamCategory.objects.get(pk=self.category.pk)

	def test_article_trial_hash_payload_is_unchanged(self):
		"""The existing hash must keep its exact payload, or every category would be
		fully re-matched against all articles and trials on the next run."""
		command = Command()
		category = TeamCategory.objects.prefetch_related("subjects").get(pk=self.category.pk)
		expected_payload = json.dumps(
			{
				"terms": ["ocrelizumab"],
				"subjects": [self.subject.id],
				"min_score_articles": 3,
				"min_score_trials": 3,
				"scope": category.match_scope,
				"weights": {
					"article": {"title": 3, "summary": 1},
					"trial": {
						"title": 3,
						"summary": 2,
						"scientific_title": 2,
						"intervention": 2,
						"primary_outcome": 1,
						"secondary_outcome": 1,
						"therapeutic_areas": 1,
					},
				},
			},
			sort_keys=True,
		)
		self.assertEqual(
			command.category_config_hash(category),
			hashlib.sha256(expected_payload.encode()).hexdigest(),
		)

	def test_patent_settings_do_not_change_the_article_trial_hash(self):
		command = Command()
		before = command.category_config_hash(
			TeamCategory.objects.prefetch_related("subjects").get(pk=self.category.pk)
		)
		self.category.match_min_score_patents = 9
		self.category.match_weights = {"patent": {"title": 9, "summary": 9, "claims": 9}}
		self.category.save()
		after = command.category_config_hash(
			TeamCategory.objects.prefetch_related("subjects").get(pk=self.category.pk)
		)
		self.assertEqual(before, after)

	def test_full_run_records_both_hashes(self):
		self.run_rebuild()
		category = self.reload()
		self.assertIsNotNone(category.match_config_hash)
		self.assertIsNotNone(category.patent_match_config_hash)

	def test_patents_only_records_only_the_patent_hash(self):
		self.run_rebuild(patents_only=True)
		category = self.reload()
		self.assertIsNotNone(category.patent_match_config_hash)
		self.assertIsNone(category.match_config_hash)

	def test_articles_only_leaves_the_patent_hash_alone(self):
		self.run_rebuild(articles_only=True)
		self.assertIsNone(self.reload().patent_match_config_hash)

	def test_changing_patent_settings_forces_only_a_patent_rematch(self):
		self.run_rebuild()
		old = self.reload()

		# A patent too old for the incremental window
		patent = Patents.objects.create(family_id="1", title="Ocrelizumab dosing")
		patent.subjects.add(self.subject)
		Patents.objects.filter(pk=patent.pk).update(
			discovery_date=timezone.now() - timedelta(days=400),
			last_updated=timezone.now() - timedelta(days=400),
		)
		self.run_rebuild(days=7)
		self.assertEqual(PatentCategoryAssignment.objects.count(), 0)

		# Changing a patent setting changes only the patent hash, which forces a full
		# patent re-match that now reaches the old patent
		self.category.match_min_score_patents = 4
		self.category.save()
		self.run_rebuild(days=7)
		self.assertEqual(PatentCategoryAssignment.objects.count(), 1)

		new = self.reload()
		self.assertEqual(new.match_config_hash, old.match_config_hash)
		self.assertNotEqual(new.patent_match_config_hash, old.patent_match_config_hash)
