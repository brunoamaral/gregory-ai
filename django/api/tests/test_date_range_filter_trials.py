from datetime import date

from django.test import TestCase, RequestFactory
from rest_framework import status
from rest_framework.test import APIClient

from api.filters import TrialFilter
from gregory.models import Organization, OrganizationApiSettings, Subject, Team, Trials


class TrialDateRangeFilterTests(TestCase):
	"""Tests for date_registration_after / date_registration_before on TrialFilter."""

	def setUp(self):
		self.factory = RequestFactory()

		self.org = Organization.objects.create(
			name="Trial Date Org", slug="trial-date-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Trial Date Team", slug="trial-date-team", organization=self.org
		)
		self.subject = Subject.objects.create(
			subject_name="Trial Date Subject",
			subject_slug="trial-date-subject",
			team=self.team,
		)

		self._link_n = 0

		def make_trial(title, reg_date):
			self._link_n += 1
			t = Trials.objects.create(
				title=title,
				link=f"https://example.com/trial-{self._link_n}",
				date_registration=reg_date,
			)
			t.teams.add(self.team)
			t.subjects.add(self.subject)
			return t

		self.t_2018 = make_trial("Trial 2018-06-15", date(2018, 6, 15))
		self.t_2019 = make_trial("Trial 2019-01-01", date(2019, 1, 1))
		self.t_2021 = make_trial("Trial 2021-07-20", date(2021, 7, 20))
		self.t_2022_end = make_trial("Trial 2022-12-31", date(2022, 12, 31))
		self.t_2023 = make_trial("Trial 2023-03-10", date(2023, 3, 10))
		# Trial with NULL date_registration
		self.t_null = Trials.objects.create(
			title="Trial no date", link="https://example.com/trial-null"
		)
		self.t_null.teams.add(self.team)

	def _filter(self, params):
		request = self.factory.get("/trials/", params)
		qs = Trials.objects.all()
		return TrialFilter(request.GET, queryset=qs, request=request).qs

	# --- after only ---

	def test_after_only(self):
		qs = self._filter({"date_registration_after": "2019-01-01"})
		pks = set(qs.values_list("trial_id", flat=True))
		self.assertIn(self.t_2019.trial_id, pks)
		self.assertIn(self.t_2021.trial_id, pks)
		self.assertIn(self.t_2022_end.trial_id, pks)
		self.assertIn(self.t_2023.trial_id, pks)
		self.assertNotIn(self.t_2018.trial_id, pks)

	def test_after_boundary_inclusive(self):
		qs = self._filter({"date_registration_after": "2018-06-15"})
		pks = set(qs.values_list("trial_id", flat=True))
		self.assertIn(self.t_2018.trial_id, pks)

	# --- before only ---

	def test_before_only(self):
		qs = self._filter({"date_registration_before": "2022-12-31"})
		pks = set(qs.values_list("trial_id", flat=True))
		self.assertIn(self.t_2018.trial_id, pks)
		self.assertIn(self.t_2019.trial_id, pks)
		self.assertIn(self.t_2021.trial_id, pks)
		self.assertIn(self.t_2022_end.trial_id, pks)
		self.assertNotIn(self.t_2023.trial_id, pks)

	def test_before_boundary_inclusive(self):
		qs = self._filter({"date_registration_before": "2022-12-31"})
		pks = set(qs.values_list("trial_id", flat=True))
		self.assertIn(self.t_2022_end.trial_id, pks)

	# --- closed range ---

	def test_closed_range(self):
		qs = self._filter({
			"date_registration_after": "2019-01-01",
			"date_registration_before": "2022-12-31",
		})
		pks = set(qs.values_list("trial_id", flat=True))
		self.assertIn(self.t_2019.trial_id, pks)
		self.assertIn(self.t_2021.trial_id, pks)
		self.assertIn(self.t_2022_end.trial_id, pks)
		self.assertNotIn(self.t_2018.trial_id, pks)
		self.assertNotIn(self.t_2023.trial_id, pks)

	def test_closed_range_count(self):
		qs = self._filter({
			"date_registration_after": "2019-01-01",
			"date_registration_before": "2022-12-31",
		})
		self.assertEqual(qs.count(), 3)

	# --- no params is a no-op ---

	def test_no_date_params_returns_all(self):
		qs = self._filter({})
		self.assertEqual(qs.count(), Trials.objects.count())

	# --- NULL date_registration rows are excluded by date filters ---

	def test_null_date_excluded(self):
		qs = self._filter({"date_registration_after": "2010-01-01"})
		pks = set(qs.values_list("trial_id", flat=True))
		self.assertNotIn(self.t_null.trial_id, pks)

	# --- compose with subjects ---

	def test_compose_with_subjects(self):
		qs = self._filter({
			"date_registration_after": "2019-01-01",
			"date_registration_before": "2022-12-31",
			"subjects": str(self.subject.id),
		})
		self.assertEqual(qs.count(), 3)

	# --- compose with phase ---

	def test_compose_with_phase(self):
		self.t_2021.phase = "PHASE3"
		self.t_2021.save(update_fields=["phase"])
		qs = self._filter({
			"date_registration_after": "2019-01-01",
			"date_registration_before": "2022-12-31",
			"phase": "PHASE3",
		})
		self.assertEqual(qs.count(), 1)
		self.assertEqual(qs.first().trial_id, self.t_2021.trial_id)


class TrialDateRangeAPITests(TestCase):
	"""HTTP-level tests: valid params return 200, invalid dates return 400."""

	def setUp(self):
		self.client = APIClient()

		self.org = Organization.objects.create(
			name="API Trial Date Org", slug="api-trial-date-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="API Trial Date Team",
			slug="api-trial-date-team",
			organization=self.org,
		)

	def test_valid_date_range_returns_200(self):
		response = self.client.get(
			"/trials/",
			{
				"date_registration_after": "2019-01-01",
				"date_registration_before": "2022-12-31",
			},
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_invalid_month_returns_400(self):
		response = self.client.get(
			"/trials/", {"date_registration_after": "2023-13-40"}
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_non_date_string_returns_400(self):
		response = self.client.get(
			"/trials/", {"date_registration_after": "not-a-date"}
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_non_iso_format_returns_400(self):
		response = self.client.get(
			"/trials/", {"date_registration_before": "31/12/2023"}
		)
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
