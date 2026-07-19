"""Tests for gregory.utils.sponsor_merge.merge_sponsors — the shared routine extracted
in PR D1 and used by the merge_sponsors command, sync_sponsor_seeds' fold path, and
recompute_sponsor_alias_keys."""

from django.test import TestCase

from gregory.models import Sponsor, SponsorAlias, Trials
from gregory.utils.sponsor_merge import merge_sponsors


class MergeSponsorsTests(TestCase):
	def _sponsor(self, name, slug, **extra):
		return Sponsor.objects.create(name=name, slug=slug, **extra)

	def test_repoints_trials_and_aliases_and_deletes_source(self):
		target = self._sponsor("Target Corp", "target-corp")
		source = self._sponsor("Source Corp", "source-corp")
		alias = SponsorAlias.objects.create(
			sponsor=source, key="source corp", raw_sample="Source Corp"
		)
		trial = Trials.objects.create(
			title="T1", link="https://example.com/merge-1", primary_sponsor=None
		)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=source)

		trials_repointed, aliases_moved = merge_sponsors(target, [source])

		self.assertEqual(trials_repointed, 1)
		self.assertEqual(aliases_moved, 1)
		trial.refresh_from_db()
		alias.refresh_from_db()
		self.assertEqual(trial.primary_sponsor_normalized_id, target.pk)
		self.assertEqual(alias.sponsor_id, target.pk)
		self.assertFalse(Sponsor.objects.filter(pk=source.pk).exists())

	def test_sponsor_type_carried_over_only_when_target_has_none(self):
		target = self._sponsor("Target Corp", "target-corp-2")
		source = self._sponsor(
			"Source Corp", "source-corp-2", sponsor_type="industry", sponsor_type_source="rules"
		)

		merge_sponsors(target, [source])

		target.refresh_from_db()
		self.assertEqual(target.sponsor_type, "industry")
		self.assertEqual(target.sponsor_type_source, "rules")

	def test_target_sponsor_type_never_overwritten(self):
		target = self._sponsor(
			"Target Corp", "target-corp-3", sponsor_type="nonprofit", sponsor_type_source="curated"
		)
		source = self._sponsor(
			"Source Corp", "source-corp-3", sponsor_type="industry", sponsor_type_source="rules"
		)

		merge_sponsors(target, [source])

		target.refresh_from_db()
		self.assertEqual(target.sponsor_type, "nonprofit")
		self.assertEqual(target.sponsor_type_source, "curated")

	def test_merges_multiple_sources_in_one_call(self):
		target = self._sponsor("Target Corp", "target-corp-4")
		s1 = self._sponsor("Source One", "source-one-4")
		s2 = self._sponsor("Source Two", "source-two-4")

		trials_repointed, aliases_moved = merge_sponsors(target, [s1, s2])

		self.assertEqual(trials_repointed, 0)
		self.assertEqual(aliases_moved, 0)
		self.assertFalse(Sponsor.objects.filter(pk__in=[s1.pk, s2.pk]).exists())
