from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone

from gregory.models import Trials, Team, Subject, Organization, OrganizationApiSettings


class TrialIdentifierFilterTests(TestCase):
	"""Tests for the per-registry-key and acronym filters on /trials/.

	Exercises ?nct=, ?eudract=, ?euct=, ?ctis= (matched against the
	``identifiers`` JSON) and ?acronym= (matched against the ``acronym``
	column). Each accepts a comma-separated list and matches any value
	case-insensitively.
	"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(name="Identifier Org", slug="identifier-filter-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(make_api_public=True)
		self.team = Team.objects.create(name="Identifier Team", slug="identifier-filter-team", organization=self.org)
		self.subject = Subject.objects.create(
			subject_name="Identifier Subject",
			subject_slug="identifier-subject",
			team=self.team,
		)

		self.trial_nct = self._make_trial("Trial NCT one", identifiers={'nct': 'NCT02521311'}, acronym='ReCOVER')
		self.trial_nct2 = self._make_trial("Trial NCT two", identifiers={'nct': 'NCT06065670'}, acronym='ReINFORCE')
		# Stored lower-case NCT — matching must be case-insensitive on the stored side too.
		self.trial_nct_lower = self._make_trial("Trial NCT lower", identifiers={'nct': 'nct05359653'})
		self.trial_eudract = self._make_trial("Trial EudraCT", identifiers={'eudract': '2020-001234-12'})
		self.trial_euct = self._make_trial("Trial EUCT", identifiers={'euct': '2022-500111-22-00'})
		# euctr is an alternate key for the same EU register; the ?euct= param matches both.
		self.trial_euctr = self._make_trial("Trial EUCTR", identifiers={'euctr': '2018-000999-10'})
		self.trial_ctis = self._make_trial("Trial CTIS", identifiers={'ctis': '2023-505555-33-00'})
		# Acronym-only trial (e.g. an early-phase study with no registry id yet).
		self.trial_acronym = self._make_trial("Trial acronym only", identifiers={}, acronym='MODIF-MS')

	def _make_trial(self, title, identifiers=None, acronym=None):
		trial = Trials.objects.create(
			title=title,
			link=f"https://example.com/{title.replace(' ', '-').lower()}",
			published_date=timezone.now(),
			identifiers=identifiers,
			acronym=acronym,
		)
		trial.teams.add(self.team)
		trial.subjects.add(self.subject)
		return trial

	def _ids(self, response):
		return {r['trial_id'] for r in response.data['results']}

	# ------------------------------------------------------------------
	# NCT
	# ------------------------------------------------------------------

	def test_nct_single(self):
		response = self.client.get('/trials/?nct=NCT02521311')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	def test_nct_multiple(self):
		response = self.client.get('/trials/?nct=NCT02521311,NCT06065670')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id, self.trial_nct2.trial_id})

	def test_nct_query_case_insensitive(self):
		"""A lower-case query still matches an upper-case stored value."""
		response = self.client.get('/trials/?nct=nct02521311')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	def test_nct_stored_value_case_insensitive(self):
		"""An upper-case query matches a lower-case stored value."""
		response = self.client.get('/trials/?nct=NCT05359653')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct_lower.trial_id})

	def test_nct_no_match(self):
		response = self.client.get('/trials/?nct=NCT00000000')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['count'], 0)

	def test_nct_does_not_match_acronym(self):
		"""An acronym value passed to ?nct= must not match via the acronym column."""
		response = self.client.get('/trials/?nct=MODIF-MS')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['count'], 0)

	# ------------------------------------------------------------------
	# EU registries and CTIS
	# ------------------------------------------------------------------

	def test_eudract(self):
		response = self.client.get('/trials/?eudract=2020-001234-12')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_eudract.trial_id})

	def test_euct_matches_euct_key(self):
		response = self.client.get('/trials/?euct=2022-500111-22-00')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_euct.trial_id})

	def test_euct_matches_euctr_key(self):
		"""?euct= also matches trials stored under the alternate ``euctr`` key."""
		response = self.client.get('/trials/?euct=2018-000999-10')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_euctr.trial_id})

	def test_ctis(self):
		response = self.client.get('/trials/?ctis=2023-505555-33-00')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_ctis.trial_id})

	# ------------------------------------------------------------------
	# Acronym
	# ------------------------------------------------------------------

	def test_acronym_exact(self):
		response = self.client.get('/trials/?acronym=MODIF-MS')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_acronym.trial_id})

	def test_acronym_case_insensitive(self):
		response = self.client.get('/trials/?acronym=modif-ms')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_acronym.trial_id})

	def test_acronym_multiple(self):
		response = self.client.get('/trials/?acronym=ReCOVER,MODIF-MS')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id, self.trial_acronym.trial_id})

	def test_acronym_does_not_match_nct(self):
		"""An NCT value passed to ?acronym= must not match via the identifiers JSON."""
		response = self.client.get('/trials/?acronym=NCT02521311')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['count'], 0)

	# ------------------------------------------------------------------
	# Composition — separate params combine with AND
	# ------------------------------------------------------------------

	def test_nct_and_acronym_combine_with_and(self):
		"""?nct=A,B&acronym=ReCOVER narrows to the trial that satisfies both."""
		response = self.client.get('/trials/?nct=NCT02521311,NCT06065670&acronym=ReCOVER')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	# ------------------------------------------------------------------
	# identifiers — umbrella param across registry keys
	# ------------------------------------------------------------------

	def test_identifiers_mixed_list(self):
		"""A mixed list matches across NCT, EudraCT and CTIS keys at once."""
		response = self.client.get('/trials/?identifiers=NCT02521311,2020-001234-12,2023-505555-33-00')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response),
			{self.trial_nct.trial_id, self.trial_eudract.trial_id, self.trial_ctis.trial_id},
		)

	def test_identifiers_single_nct(self):
		response = self.client.get('/trials/?identifiers=NCT06065670')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct2.trial_id})

	def test_identifiers_matches_euctr_key(self):
		"""The umbrella param also reaches the alternate euctr key."""
		response = self.client.get('/trials/?identifiers=2018-000999-10')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_euctr.trial_id})

	def test_identifiers_case_insensitive(self):
		response = self.client.get('/trials/?identifiers=nct02521311')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	def test_identifiers_excludes_acronym(self):
		"""An acronym token must NOT match via the umbrella param (registry keys only)."""
		response = self.client.get('/trials/?identifiers=MODIF-MS')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['count'], 0)

	def test_identifiers_no_match(self):
		response = self.client.get('/trials/?identifiers=NCT00000000,9999-000000-00')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['count'], 0)

	def test_identifiers_composes_with_typed_param(self):
		"""?identifiers=… AND ?acronym=… narrows to trials satisfying both."""
		response = self.client.get('/trials/?identifiers=NCT02521311,NCT06065670&acronym=ReCOVER')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	def test_blank_value_is_noop(self):
		"""An empty ?nct= leaves the result set unfiltered (all visible trials)."""
		response = self.client.get('/trials/?nct=')
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data['count'], Trials.objects.count())
