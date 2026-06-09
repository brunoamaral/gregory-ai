"""
Tests for multi-source trial link handling.

Covers:
  - registry_from_url / merge_trial_links / canonical_link helpers (no DB)
  - importer integration: for a trial cross-registered in ClinicalTrials.gov
    and EU CTIS, the FIRST registry URL stored stays the canonical link (the
    trial team's primary registration choice — registries are not ranked),
    later importers never replace it, and every registry URL is preserved in
    Trials.links (see docs/trials-multi-source-merge.md)

Run:
  docker exec gregory python manage.py test gregory.tests.test_trial_links
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import SimpleTestCase, TestCase
from organizations.models import Organization

from gregory.models import Trials, Sources, Subject, Team
from gregory.classes import ClinicalTrial
from gregory.management.commands.feedreader_trials import Command as EUCommand
from gregory.management.commands.feedreader_trials_ctgov import Command as CTGovCommand
from gregory.management.commands.importWHOXML import Command as WHOCommand
from gregory.utils.trial_utils import registry_from_url, merge_trial_links, canonical_link


CTGOV_LINK = 'https://clinicaltrials.gov/study/NCT00000001'
CTIS_LINK = 'https://euclinicaltrials.eu/ctis-public/view/2024-000001-11-00'
ICTRP_LINK = 'https://trialsearch.who.int/Trial2.aspx?TrialID=NCT00000001'


class RegistryFromUrlTest(SimpleTestCase):
	def test_known_registries(self):
		self.assertEqual(registry_from_url(CTGOV_LINK), 'ctgov')
		self.assertEqual(registry_from_url(CTIS_LINK), 'ctis')
		self.assertEqual(registry_from_url(ICTRP_LINK), 'ictrp')
		self.assertEqual(registry_from_url('https://www.clinicaltrialsregister.eu/ctr-search/trial/2024-000001-11/GB'), 'euctr')
		self.assertEqual(registry_from_url('https://www.isrctn.com/ISRCTN12345678'), 'isrctn')

	def test_www_prefix_and_subdomains(self):
		self.assertEqual(registry_from_url('https://www.clinicaltrials.gov/study/NCT1'), 'ctgov')
		self.assertEqual(registry_from_url('https://beta.clinicaltrials.gov/study/NCT1'), 'ctgov')

	def test_unknown_domain_falls_back_to_hostname(self):
		self.assertEqual(registry_from_url('https://www.example-registry.org/trial/1'), 'example-registry.org')

	def test_empty_or_invalid(self):
		self.assertIsNone(registry_from_url(None))
		self.assertIsNone(registry_from_url(''))
		self.assertIsNone(registry_from_url('not a url'))


class MergeTrialLinksTest(SimpleTestCase):
	def test_adds_new_registry_key(self):
		self.assertEqual(merge_trial_links(None, CTGOV_LINK), {'ctgov': CTGOV_LINK})
		self.assertEqual(
			merge_trial_links({'ctgov': CTGOV_LINK}, CTIS_LINK),
			{'ctgov': CTGOV_LINK, 'ctis': CTIS_LINK},
		)

	def test_never_overwrites_existing_entry(self):
		existing = {'ctgov': CTGOV_LINK}
		merged = merge_trial_links(existing, 'https://clinicaltrials.gov/ct2/show/NCT00000001')
		self.assertEqual(merged, existing)

	def test_fills_empty_entry(self):
		merged = merge_trial_links({'ctgov': None}, CTGOV_LINK)
		self.assertEqual(merged, {'ctgov': CTGOV_LINK})

	def test_ignores_empty_url(self):
		self.assertEqual(merge_trial_links({'ctgov': CTGOV_LINK}, None), {'ctgov': CTGOV_LINK})

	def test_does_not_mutate_input(self):
		existing = {'ctgov': CTGOV_LINK}
		merge_trial_links(existing, CTIS_LINK)
		self.assertEqual(existing, {'ctgov': CTGOV_LINK})


class CanonicalLinkTest(SimpleTestCase):
	def test_first_registry_link_is_kept(self):
		"""Registries are not ranked: whichever registry URL was stored first stays
		canonical, even when other registry URLs arrive later."""
		links = {'ctis': CTIS_LINK, 'ctgov': CTGOV_LINK}
		self.assertEqual(canonical_link(links, CTIS_LINK), CTIS_LINK)
		self.assertEqual(canonical_link(links, CTGOV_LINK), CTGOV_LINK)

	def test_aggregator_link_upgraded_to_registry(self):
		"""A WHO ICTRP (aggregator) URL is replaced once a registry URL exists —
		a search portal is not a registry the trial team registered with."""
		links = {'ictrp': ICTRP_LINK, 'ctis': CTIS_LINK}
		self.assertEqual(canonical_link(links, ICTRP_LINK), CTIS_LINK)

	def test_aggregator_link_kept_when_no_registry_link(self):
		links = {'ictrp': ICTRP_LINK}
		self.assertEqual(canonical_link(links, ICTRP_LINK), ICTRP_LINK)

	def test_empty_current_link_picks_a_registry_link(self):
		links = {'ctgov': CTGOV_LINK, 'ictrp': ICTRP_LINK}
		self.assertEqual(canonical_link(links, None), CTGOV_LINK)

	def test_empty_current_link_falls_back_to_aggregator(self):
		self.assertEqual(canonical_link({'ictrp': ICTRP_LINK}, None), ICTRP_LINK)

	def test_all_empty(self):
		self.assertIsNone(canonical_link(None, None))
		self.assertIsNone(canonical_link({}, ''))


# ---------------------------------------------------------------------------
# Importer integration: order independence
# ---------------------------------------------------------------------------

TITLE = 'A Cross-registered Study of Order Independence'


def _make_source(org, method='rss', name='Test Source'):
	team = Team.objects.create(organization=org, name='Test Team %s' % name, slug='test-team-%s' % method)
	subject = Subject.objects.create(subject_name='MS %s' % name, subject_slug='ms-%s' % method)
	return Sources.objects.create(
		name=name,
		source_for='trials',
		method=method,
		subject=subject,
		team=team,
	)


def _eu_cmd():
	cmd = EUCommand()
	cmd.verbosity = 0
	cmd.setup()
	return cmd


def _ctgov_cmd():
	cmd = CTGovCommand()
	cmd.verbosity = 0
	return cmd


def _ctgov_trial():
	return ClinicalTrial(
		title=TITLE,
		summary='Summary from ClinicalTrials.gov',
		link=CTGOV_LINK,
		published_date=None,
		identifiers={'nct': 'NCT00000001'},
	)


def _eu_trial():
	return ClinicalTrial(
		title=TITLE,
		summary='Summary from EU CTIS',
		link=CTIS_LINK,
		published_date=None,
		identifiers={'euct': '2024-000001-11-00'},
	)


class ImporterFirstSeenWinsTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.eu_source = _make_source(self.org, method='rss', name='EU CTIS')
		self.ctgov_source = _make_source(self.org, method='ctgov_api', name='CTGov API')

	def _run_ctgov(self):
		cmd = _ctgov_cmd()
		incoming = _ctgov_trial()
		existing = cmd.find_existing_trial(incoming)
		if existing:
			cmd.update_existing_trial(existing, incoming, self.ctgov_source)
		else:
			cmd.create_new_trial(incoming, self.ctgov_source)

	def _run_eu(self):
		cmd = _eu_cmd()
		incoming = _eu_trial()
		existing = cmd.find_existing_trial(incoming)
		if existing:
			cmd.update_existing_trial(existing, incoming, self.eu_source)
		else:
			cmd.create_new_trial(incoming, self.eu_source)

	def _assert_merged(self, expected_link):
		trial = Trials.objects.get(title=TITLE)
		# Both registry URLs preserved
		self.assertEqual(trial.links, {'ctgov': CTGOV_LINK, 'ctis': CTIS_LINK})
		# Canonical link is whichever registry URL arrived FIRST — the trial
		# team's primary registration — and later importers must not replace it.
		self.assertEqual(trial.link, expected_link)
		# Identifiers merged from both registries
		self.assertEqual(trial.identifiers.get('nct'), 'NCT00000001')
		self.assertEqual(trial.identifiers.get('euct'), '2024-000001-11-00')

	def test_ctgov_then_eu_keeps_ctgov_link(self):
		self._run_ctgov()
		self._run_eu()
		self.assertEqual(Trials.objects.filter(title=TITLE).count(), 1)
		self._assert_merged(CTGOV_LINK)

	def test_eu_then_ctgov_keeps_ctis_link(self):
		self._run_eu()
		self._run_ctgov()
		self.assertEqual(Trials.objects.filter(title=TITLE).count(), 1)
		self._assert_merged(CTIS_LINK)

	def test_reimport_is_idempotent(self):
		"""Re-running both importers must not change the row again (no churn)."""
		self._run_eu()
		self._run_ctgov()
		trial = Trials.objects.get(title=TITLE)
		link, links = trial.link, trial.links
		self._run_eu()
		self._run_ctgov()
		trial.refresh_from_db()
		self.assertEqual(trial.link, link)
		self.assertEqual(trial.links, links)


class WHOImporterLinkTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.source = _make_source(self.org, method='rss', name='WHO ICTRP')
		self.subject = self.source.subject

	def test_who_does_not_overwrite_registry_link(self):
		"""WHO exports the registry URL (older format); it must not clobber the
		link already stored by the registry's own importer."""
		trial = Trials.objects.create(
			title=TITLE,
			link=CTGOV_LINK,
			links={'ctgov': CTGOV_LINK},
			identifiers={'nct': 'NCT00000001'},
		)
		trial_data = {
			'identifiers': {'nct': 'NCT00000001'},
			'title': TITLE,
			'link': 'https://clinicaltrials.gov/ct2/show/NCT00000001',
		}
		WHOCommand().update_existing_trial(trial, trial_data, self.source, self.subject)
		trial.refresh_from_db()
		self.assertEqual(trial.link, CTGOV_LINK)
		self.assertEqual(trial.links, {'ctgov': CTGOV_LINK})

	def test_aggregator_link_upgraded_when_registry_link_arrives(self):
		"""A trial first discovered via WHO ICTRP carries an aggregator URL; the
		first real registry URL upgrades it (a search portal is not a registry
		the trial team registered with)."""
		trial = Trials.objects.create(
			title=TITLE,
			link=ICTRP_LINK,
			links={'ictrp': ICTRP_LINK},
			identifiers={'nct': 'NCT00000001'},
		)
		trial_data = {
			'identifiers': {'nct': 'NCT00000001'},
			'title': TITLE,
			'link': CTGOV_LINK,
		}
		WHOCommand().update_existing_trial(trial, trial_data, self.source, self.subject)
		trial.refresh_from_db()
		self.assertEqual(trial.link, CTGOV_LINK)
		self.assertEqual(trial.links, {'ictrp': ICTRP_LINK, 'ctgov': CTGOV_LINK})
