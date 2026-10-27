from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone

from gregory.models import Trials, Team, Subject, Organization, OrganizationApiSettings
from api.tests.visibility_helpers import publish_subjects


class TrialIdentifierFilterTests(TestCase):
	"""Tests for the per-registry-key and acronym filters on /trials/.

	Exercises ?nct=, ?eudract=, ?euct=, ?ctis= (matched against the
	``identifiers`` JSON) and ?acronym= (matched against the ``acronym``
	column). Each accepts a comma-separated list and matches any value
	case-insensitively. ?acronym_contains= is the exception: one value, matched
	as a case-insensitive substring of the acronym.
	"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(
			name="Identifier Org", slug="identifier-filter-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Identifier Team", slug="identifier-filter-team", organization=self.org
		)
		self.subject = Subject.objects.create(
			subject_name="Identifier Subject",
			subject_slug="identifier-subject",
			team=self.team,
		)
		self.site = publish_subjects(self.subject, organization=self.org)

		self.trial_nct = self._make_trial(
			"Trial NCT one", identifiers={"nct": "NCT02521311"}, acronym="ReCOVER"
		)
		self.trial_nct2 = self._make_trial(
			"Trial NCT two", identifiers={"nct": "NCT06065670"}, acronym="ReINFORCE"
		)
		# Stored lower-case NCT — matching must be case-insensitive on the stored side too.
		self.trial_nct_lower = self._make_trial(
			"Trial NCT lower", identifiers={"nct": "nct05359653"}
		)
		self.trial_eudract = self._make_trial(
			"Trial EudraCT", identifiers={"eudract": "2020-001234-12"}
		)
		self.trial_euct = self._make_trial(
			"Trial EUCT", identifiers={"euct": "2022-500111-22-00"}
		)
		# euctr is an alternate key for the same EU register; the ?euct= param matches both.
		self.trial_euctr = self._make_trial(
			"Trial EUCTR", identifiers={"euctr": "2018-000999-10"}
		)
		self.trial_ctis = self._make_trial(
			"Trial CTIS", identifiers={"ctis": "2023-505555-33-00"}
		)
		# Acronym-only trial (e.g. an early-phase study with no registry id yet).
		self.trial_acronym = self._make_trial(
			"Trial acronym only", identifiers={}, acronym="MODIF-MS"
		)

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
		return {r["trial_id"] for r in response.data["results"]}

	# ------------------------------------------------------------------
	# NCT
	# ------------------------------------------------------------------

	def test_nct_single(self):
		response = self.client.get("/trials/?nct=NCT02521311")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	def test_nct_multiple(self):
		response = self.client.get("/trials/?nct=NCT02521311,NCT06065670")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response), {self.trial_nct.trial_id, self.trial_nct2.trial_id}
		)

	def test_nct_query_case_insensitive(self):
		"""A lower-case query still matches an upper-case stored value."""
		response = self.client.get("/trials/?nct=nct02521311")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	def test_nct_stored_value_case_insensitive(self):
		"""An upper-case query matches a lower-case stored value."""
		response = self.client.get("/trials/?nct=NCT05359653")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct_lower.trial_id})

	def test_nct_no_match(self):
		response = self.client.get("/trials/?nct=NCT00000000")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	def test_nct_does_not_match_acronym(self):
		"""An acronym value passed to ?nct= must not match via the acronym column."""
		response = self.client.get("/trials/?nct=MODIF-MS")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	# ------------------------------------------------------------------
	# EU registries and CTIS
	# ------------------------------------------------------------------

	def test_eudract(self):
		response = self.client.get("/trials/?eudract=2020-001234-12")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_eudract.trial_id})

	def test_euct_matches_euct_key(self):
		response = self.client.get("/trials/?euct=2022-500111-22-00")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_euct.trial_id})

	def test_euct_matches_euctr_key(self):
		"""?euct= also matches trials stored under the alternate ``euctr`` key."""
		response = self.client.get("/trials/?euct=2018-000999-10")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_euctr.trial_id})

	def test_ctis(self):
		response = self.client.get("/trials/?ctis=2023-505555-33-00")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_ctis.trial_id})

	# ------------------------------------------------------------------
	# Acronym
	# ------------------------------------------------------------------

	def test_acronym_exact(self):
		response = self.client.get("/trials/?acronym=MODIF-MS")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_acronym.trial_id})

	def test_acronym_case_insensitive(self):
		response = self.client.get("/trials/?acronym=modif-ms")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_acronym.trial_id})

	def test_acronym_multiple(self):
		response = self.client.get("/trials/?acronym=ReCOVER,MODIF-MS")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response), {self.trial_nct.trial_id, self.trial_acronym.trial_id}
		)

	def test_acronym_does_not_match_nct(self):
		"""An NCT value passed to ?acronym= must not match via the identifiers JSON."""
		response = self.client.get("/trials/?acronym=NCT02521311")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	# ------------------------------------------------------------------
	# acronym_contains
	# ------------------------------------------------------------------

	def _make_prom_trials(self):
		self.trial_promise = self._make_trial("Trial PROMISE", acronym="PROMISE")
		self.trial_promote = self._make_trial("Trial PROMOTE", acronym="PROMOTE")
		self.trial_empty_acronym = self._make_trial("Trial empty acronym", acronym="")

	def test_acronym_contains_partial_lowercase(self):
		self._make_prom_trials()
		response = self.client.get("/trials/?acronym_contains=prom")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response),
			{self.trial_promise.trial_id, self.trial_promote.trial_id},
		)

	def test_acronym_contains_full_value(self):
		self._make_prom_trials()
		response = self.client.get("/trials/?acronym_contains=PROMISE")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_promise.trial_id})

	def test_acronym_contains_mid_string(self):
		response = self.client.get("/trials/?acronym_contains=dif")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_acronym.trial_id})

	def test_acronym_exact_unchanged(self):
		"""Regression guard: ?acronym= stays an exact match."""
		self._make_prom_trials()
		response = self.client.get("/trials/?acronym=PROM")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)
		response = self.client.get("/trials/?acronym=PROMISE")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_promise.trial_id})

	def test_acronym_contains_excludes_null_and_empty(self):
		self._make_prom_trials()
		response = self.client.get("/trials/?acronym_contains=e")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = self._ids(response)
		self.assertNotIn(self.trial_empty_acronym.trial_id, ids)
		self.assertNotIn(self.trial_nct_lower.trial_id, ids)  # acronym is NULL
		self.assertNotIn(self.trial_eudract.trial_id, ids)  # acronym is NULL
		self.assertIn(self.trial_promise.trial_id, ids)

	def test_acronym_contains_blank_is_noop(self):
		baseline = self.client.get("/trials/").data["count"]
		for query in ("/trials/?acronym_contains=", "/trials/?acronym_contains=%20"):
			response = self.client.get(query)
			self.assertEqual(response.status_code, status.HTTP_200_OK)
			self.assertEqual(response.data["count"], baseline)

	def test_acronym_contains_composes_with_subject_and_team(self):
		self._make_prom_trials()
		other_team = Team.objects.create(
			name="Other Team", slug="other-identifier-team", organization=self.org
		)
		other_subject = Subject.objects.create(
			subject_name="Other Subject",
			subject_slug="other-identifier-subject",
			team=other_team,
		)
		# Add the second subject to the existing site's scope; a second
		# publish_subjects call would create a second site and make the
		# request's site ambiguous.
		self.site.customsetting_set.get().scope_subjects.add(other_subject)
		other = Trials.objects.create(
			title="Trial PROMETHEUS",
			link="https://example.com/trial-prometheus",
			published_date=timezone.now(),
			acronym="PROMETHEUS",
		)
		other.teams.add(other_team)
		other.subjects.add(other_subject)

		response = self.client.get("/trials/?acronym_contains=prom")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response),
			{
				self.trial_promise.trial_id,
				self.trial_promote.trial_id,
				other.trial_id,
			},
		)

		response = self.client.get(
			f"/trials/?acronym_contains=prom&subject_id={other_subject.pk}"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {other.trial_id})

		response = self.client.get(
			f"/trials/?acronym_contains=prom&subject_id={self.subject.pk}"
		)
		self.assertEqual(
			self._ids(response),
			{self.trial_promise.trial_id, self.trial_promote.trial_id},
		)

		response = self.client.get(
			f"/trials/?acronym_contains=prom&team_id={other_team.pk}"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {other.trial_id})

		response = self.client.get(
			f"/trials/?acronym_contains=prom&team_id={self.team.pk}"
		)
		self.assertEqual(
			self._ids(response),
			{self.trial_promise.trial_id, self.trial_promote.trial_id},
		)

	def test_acronym_contains_count_and_pagination(self):
		"""count equals the matches and pages are disjoint (no join, no duplicates)."""
		made = {
			self._make_trial(f"Trial BULK {i}", acronym=f"BULK-{i}").trial_id
			for i in range(12)
		}
		seen = []
		count = None
		for page in (1, 2, 3):
			response = self.client.get(
				f"/trials/?acronym_contains=bulk&page_size=5&page={page}"
			)
			self.assertEqual(response.status_code, status.HTTP_200_OK)
			count = response.data["count"]
			seen.extend(r["trial_id"] for r in response.data["results"])
		self.assertEqual(count, 12)
		self.assertEqual(len(seen), 12)
		self.assertEqual(len(set(seen)), 12)
		self.assertEqual(set(seen), made)

	def test_acronym_contains_no_match_via_identifiers(self):
		"""An NCT fragment must not match via the identifiers JSON."""
		response = self.client.get("/trials/?acronym_contains=NCT0252")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	# ------------------------------------------------------------------
	# Composition — separate params combine with AND
	# ------------------------------------------------------------------

	def test_nct_and_acronym_combine_with_and(self):
		"""?nct=A,B&acronym=ReCOVER narrows to the trial that satisfies both."""
		response = self.client.get(
			"/trials/?nct=NCT02521311,NCT06065670&acronym=ReCOVER"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	# ------------------------------------------------------------------
	# identifiers — umbrella param across registry keys
	# ------------------------------------------------------------------

	def test_identifiers_mixed_list(self):
		"""A mixed list matches across NCT, EudraCT and CTIS keys at once."""
		response = self.client.get(
			"/trials/?identifiers=NCT02521311,2020-001234-12,2023-505555-33-00"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response),
			{
				self.trial_nct.trial_id,
				self.trial_eudract.trial_id,
				self.trial_ctis.trial_id,
			},
		)

	def test_identifiers_single_nct(self):
		response = self.client.get("/trials/?identifiers=NCT06065670")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct2.trial_id})

	def test_identifiers_matches_euctr_key(self):
		"""The umbrella param also reaches the alternate euctr key."""
		response = self.client.get("/trials/?identifiers=2018-000999-10")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_euctr.trial_id})

	def test_identifiers_case_insensitive(self):
		response = self.client.get("/trials/?identifiers=nct02521311")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	def test_identifiers_excludes_acronym(self):
		"""An acronym token must NOT match via the umbrella param (registry keys only)."""
		response = self.client.get("/trials/?identifiers=MODIF-MS")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	def test_identifiers_no_match(self):
		response = self.client.get("/trials/?identifiers=NCT00000000,9999-000000-00")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], 0)

	def test_identifiers_composes_with_typed_param(self):
		"""?identifiers=… AND ?acronym=… narrows to trials satisfying both."""
		response = self.client.get(
			"/trials/?identifiers=NCT02521311,NCT06065670&acronym=ReCOVER"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_nct.trial_id})

	def test_blank_value_is_noop(self):
		"""An empty ?nct= leaves the result set unfiltered (all visible trials)."""
		response = self.client.get("/trials/?nct=")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["count"], Trials.objects.count())


class TrialRegistryIdFormatDecidesFilterTests(TestCase):
	"""An id's own format now
	decides what it matches — the identifiers_normalized__overlap branch OR'd
	onto the legacy exact-key match every test above already covers (the
	superset guarantee: every test in TrialIdentifierFilterTests above still
	passes unchanged). These cover the NEW reach only.
	"""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(
			name="Format Decides Org", slug="format-decides-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Format Decides Team", slug="format-decides-team", organization=self.org
		)
		self.subject = Subject.objects.create(
			subject_name="Format Decides Subject",
			subject_slug="format-decides-subject",
			team=self.team,
		)
		publish_subjects(self.subject, organization=self.org)

		# OCTOPUS-shaped: EudraCT sits only in secondary_id, free text —
		# exactly the regression case this PR exists to fix (plan intro).
		self.trial_secondary_id_eudract = self._make_trial(
			"OCTOPUS-shaped trial",
			identifiers={"isrctn": "ISRCTN14048364"},
			secondary_id="2021-003034-37;CPMS: 54274, ND001",
		)
		# STHENOS-shaped duplicate pair: the same real-world EudraCT number,
		# stored under two different raw keys/formats by two importers.
		self.trial_eudract_key = self._make_trial(
			"STHENOS eudract-keyed", identifiers={"eudract": "2020-004505-32"}
		)
		self.trial_euctr_key = self._make_trial(
			"STHENOS euctr-keyed", identifiers={"euctr": "EUCTR2020-004505-32-DE"}
		)
		self.trial_ctis_prefixed = self._make_trial(
			"CTIS-prefixed trial", identifiers={"ctis": "CTIS2023-507431-37-00"}
		)
		self.trial_drks = self._make_trial(
			"DRKS trial", identifiers={"drks": "DRKS00041145"}
		)
		# WHO Universal Trial Number: never its own identifiers key in
		# practice — it shows up in free-text secondary_id.
		self.trial_utn = self._make_trial(
			"UTN trial",
			identifiers={"nct": "NCT00099999"},
			secondary_id="U1111-1299-8084",
		)
		# Truncated at source (54 such rows on dev): one digit
		# short of a parseable EudraCT number, so extract_identifiers finds
		# nothing in it; only the legacy exact-match branch can still find it.
		self.trial_truncated_eudract = self._make_trial(
			"Truncated EudraCT trial", identifiers={"eudract": "2019-004822-1"}
		)
		# Simulates a pre-existing row from before identifiers_normalized
		# existed / before the backfill reached it: .update() bypasses
		# save(), forcing the derived column back to NULL without touching
		# the raw identifiers key a lookup should still find it through.
		self.trial_pending_backfill = self._make_trial(
			"Pending backfill trial", identifiers={"nct": "NCT00012345"}
		)
		Trials.objects.filter(pk=self.trial_pending_backfill.pk).update(
			identifiers_normalized=None
		)

	def _make_trial(self, title, identifiers=None, secondary_id=None):
		trial = Trials.objects.create(
			title=title,
			link=f"https://example.com/{title.replace(' ', '-').lower()}",
			published_date=timezone.now(),
			identifiers=identifiers,
			secondary_id=secondary_id,
		)
		trial.teams.add(self.team)
		trial.subjects.add(self.subject)
		return trial

	def _ids(self, response):
		return {r["trial_id"] for r in response.data["results"]}

	def test_eudract_finds_id_sitting_only_in_secondary_id(self):
		response = self.client.get("/trials/?eudract=2021-003034-37")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response), {self.trial_secondary_id_eudract.trial_id}
		)

	def test_eudract_finds_both_eudract_and_euctr_keyed_rows(self):
		"""The STHENOS example: one bare number, two rows, two
		different raw keys/formats."""
		response = self.client.get("/trials/?eudract=2020-004505-32")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response),
			{self.trial_eudract_key.trial_id, self.trial_euctr_key.trial_id},
		)

	def test_ctis_finds_ctis_prefixed_row(self):
		response = self.client.get("/trials/?ctis=2023-507431-37-00")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_ctis_prefixed.trial_id})

	def test_query_format_tolerance_eudract_prefixed(self):
		"""EUDRACT-prefixed input normalizes to the same bare number as the
		euctr-keyed row's own stored value, so — like the bare-number query in
		test_eudract_finds_both_eudract_and_euctr_keyed_rows above — this finds
		both rows, not only the one stored under the "eudract" key."""
		response = self.client.get("/trials/?eudract=EUDRACT2020-004505-32")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response),
			{self.trial_eudract_key.trial_id, self.trial_euctr_key.trial_id},
		)

	def test_query_format_tolerance_euctr_prefixed_with_suffix(self):
		response = self.client.get("/trials/?eudract=EUCTR2020-004505-32-DE")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response),
			{self.trial_eudract_key.trial_id, self.trial_euctr_key.trial_id},
		)

	def test_format_decides_eudract_param_finds_ctis_number(self):
		"""Design decision: the format decides what matches,
		whichever typed param the id arrived in."""
		response = self.client.get("/trials/?eudract=2023-507431-37-00")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_ctis_prefixed.trial_id})

	def test_format_decides_ctis_param_finds_eudract_number(self):
		response = self.client.get("/trials/?ctis=2021-003034-37")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response), {self.trial_secondary_id_eudract.trial_id}
		)

	def test_identifiers_umbrella_matches_isrctn(self):
		"""Nothing today matches this; identifiers_normalized makes the
		umbrella param reach every registry extract_identifiers knows."""
		response = self.client.get("/trials/?identifiers=ISRCTN14048364")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response), {self.trial_secondary_id_eudract.trial_id}
		)

	def test_identifiers_umbrella_matches_drks(self):
		response = self.client.get("/trials/?identifiers=DRKS00041145")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_drks.trial_id})

	def test_identifiers_umbrella_matches_who_utn(self):
		response = self.client.get("/trials/?identifiers=U1111-1299-8084")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_utn.trial_id})

	def test_unparseable_input_falls_back_to_legacy_exact_match(self):
		"""A truncated EudraCT number extract_identifiers can't parse is still
		found — the legacy exact-match branch never goes away."""
		response = self.client.get("/trials/?eudract=2019-004822-1")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(
			self._ids(response), {self.trial_truncated_eudract.trial_id}
		)

	def test_null_identifiers_normalized_still_matches_via_legacy_branch(self):
		"""A row identifiers_normalized hasn't been (re)computed for yet — e.g.
		pre-migration, pre-backfill — must still be reachable by its raw key."""
		self.assertIsNone(
			Trials.objects.get(pk=self.trial_pending_backfill.pk).identifiers_normalized
		)
		response = self.client.get("/trials/?nct=NCT00012345")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._ids(response), {self.trial_pending_backfill.trial_id})
