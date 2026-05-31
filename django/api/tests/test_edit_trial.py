"""
Tests for POST /trials/edit/

Covers spec §10.2:
  - Successful upsert (no prior TrialOrgContent row)
  - Successful update of existing TrialOrgContent
  - Trial not found by identifiers → 404
  - Multiple trials match identifiers → 409 DuplicateTrialError with ids; no write
  - Trial belongs to different org → 403 CrossOrgPayloadError
  - Missing identifiers field → 400
  - Missing API key → 401
  - API key without org → 403
  - Failed edits create APIAccessSchemeLog row with correct http_code
  - Empty string takeaways clears the field (saved as NULL)

Run with:
    docker exec gregory python manage.py test api.tests.test_edit_trial
"""
import json
from datetime import timedelta

from django.test import TestCase, Client
from django.utils.timezone import now
from organizations.models import Organization

from api.models import APIAccessScheme, APIAccessSchemeLog
from gregory.models import (
	Trials, TrialOrgContent, Team, Subject, OrganizationApiSettings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_org(name, slug=''):
	slug = slug or name.lower().replace(' ', '-')
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=False)
	return org


def _make_team(org, name):
	return Team.objects.create(organization=org, name=name, slug=name.lower().replace(' ', '-'))


def _make_scheme(org, name, ip_addresses=''):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f'{name}@example.com',
		organization=org,
		ip_addresses=ip_addresses,
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


def _make_trial(team, nct, title='Test Trial'):
	trial = Trials.objects.create(
		title=title,
		link=f'https://example.com/{nct}',
		identifiers={'nct': nct},
	)
	trial.teams.add(team)
	return trial


def _edit(client, api_key, payload):
	body = json.dumps(payload).encode()
	headers = {'HTTP_AUTHORIZATION': api_key} if api_key else {}
	return client.post(
		'/trials/edit/',
		data=body,
		content_type='application/json',
		**headers,
	)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class EditTrialOrgContentTest(TestCase):
	"""Per-org fields (takeaways, summary_plain_english)."""

	def setUp(self):
		self.client = Client()
		self.org = _make_org('Trial Org A')
		self.team = _make_team(self.org, 'Trial Team A')
		self.scheme = _make_scheme(self.org, 'trial-key-a')
		self.trial = _make_trial(self.team, 'NCT0000001')

	def test_upsert_creates_new_row(self):
		"""No prior TrialOrgContent → row is created."""
		resp = _edit(self.client, self.scheme.api_key, {
			'identifiers': {'nct': 'NCT0000001'},
			'takeaways': 'Trial takeaway',
		})
		self.assertEqual(resp.status_code, 200)
		data = resp.json()
		self.assertIn('takeaways', data['updated_fields'])
		content = TrialOrgContent.objects.get(trial=self.trial, organization=self.org)
		self.assertEqual(content.takeaways, 'Trial takeaway')

	def test_upsert_updates_existing_row(self):
		"""Existing TrialOrgContent row is updated in place."""
		TrialOrgContent.objects.create(
			trial=self.trial, organization=self.org, takeaways='Old'
		)
		resp = _edit(self.client, self.scheme.api_key, {
			'identifiers': {'nct': 'NCT0000001'},
			'takeaways': 'Updated',
		})
		self.assertEqual(resp.status_code, 200)
		content = TrialOrgContent.objects.get(trial=self.trial, organization=self.org)
		self.assertEqual(content.takeaways, 'Updated')
		self.assertEqual(TrialOrgContent.objects.filter(trial=self.trial, organization=self.org).count(), 1)

	def test_empty_string_clears_takeaways(self):
		"""Empty string is stored as NULL."""
		TrialOrgContent.objects.create(
			trial=self.trial, organization=self.org, takeaways='Something'
		)
		resp = _edit(self.client, self.scheme.api_key, {
			'identifiers': {'nct': 'NCT0000001'},
			'takeaways': '',
		})
		self.assertEqual(resp.status_code, 200)
		content = TrialOrgContent.objects.get(trial=self.trial, organization=self.org)
		self.assertIsNone(content.takeaways)

	def test_summary_plain_english_persists(self):
		resp = _edit(self.client, self.scheme.api_key, {
			'identifiers': {'nct': 'NCT0000001'},
			'summary_plain_english': 'Plain summary',
		})
		self.assertEqual(resp.status_code, 200)
		content = TrialOrgContent.objects.get(trial=self.trial, organization=self.org)
		self.assertEqual(content.summary_plain_english, 'Plain summary')


class EditTrialErrorsTest(TestCase):
	"""Error paths: 401, 403, 404, 409."""

	def setUp(self):
		self.client = Client()
		self.org = _make_org('Trial Org B')
		self.other_org = _make_org('Trial Org C', 'trial-org-c')
		self.team = _make_team(self.org, 'Trial Team B')
		self.other_team = _make_team(self.other_org, 'Trial Team C')
		self.scheme = _make_scheme(self.org, 'trial-key-b')
		self.trial = _make_trial(self.team, 'NCT0000002')

	def test_missing_api_key_returns_401(self):
		resp = _edit(self.client, None, {'identifiers': {'nct': 'NCT0000002'}})
		self.assertEqual(resp.status_code, 401)

	def test_invalid_api_key_returns_401(self):
		resp = _edit(self.client, 'bad-key', {'identifiers': {'nct': 'NCT0000002'}})
		self.assertEqual(resp.status_code, 401)

	def test_missing_identifiers_returns_400(self):
		resp = _edit(self.client, self.scheme.api_key, {'takeaways': 'No id'})
		self.assertEqual(resp.status_code, 400)

	def test_trial_not_found_returns_404(self):
		resp = _edit(self.client, self.scheme.api_key, {
			'identifiers': {'nct': 'NCT9999999'},
		})
		self.assertEqual(resp.status_code, 404)

	def test_duplicate_nct_returns_409_with_ids(self):
		"""Two trials with same NCT id → 409 with both ids."""
		dup = Trials.objects.create(
			title='Duplicate Trial',
			link='https://example.com/dup-trial',
			identifiers={'nct': 'NCT0000002'},
		)
		dup.teams.add(self.team)
		resp = _edit(self.client, self.scheme.api_key, {
			'identifiers': {'nct': 'NCT0000002'},
		})
		self.assertEqual(resp.status_code, 409)
		data = resp.json()
		self.assertIn('trial_ids', data['extra_data'])
		self.assertIn(self.trial.trial_id, data['extra_data']['trial_ids'])
		self.assertIn(dup.trial_id, data['extra_data']['trial_ids'])
		self.assertEqual(TrialOrgContent.objects.count(), 0)

	def test_cross_org_trial_returns_403(self):
		"""Trial belongs to other_org only → 403."""
		other_trial = _make_trial(self.other_team, 'NCT0000099', 'Other Org Trial')
		resp = _edit(self.client, self.scheme.api_key, {
			'identifiers': {'nct': 'NCT0000099'},
			'takeaways': 'Should not write',
		})
		self.assertEqual(resp.status_code, 403)
		self.assertEqual(TrialOrgContent.objects.count(), 0)


class EditTrialAuditLogTest(TestCase):
	"""APIAccessSchemeLog rows created on success and error."""

	def setUp(self):
		self.client = Client()
		self.org = _make_org('Trial Org D')
		self.team = _make_team(self.org, 'Trial Team D')
		self.scheme = _make_scheme(self.org, 'trial-key-d')
		self.trial = _make_trial(self.team, 'NCT0000010')

	def test_success_creates_log_with_200(self):
		_edit(self.client, self.scheme.api_key, {
			'identifiers': {'nct': 'NCT0000010'},
			'takeaways': 'logged',
		})
		log = APIAccessSchemeLog.objects.filter(api_access_scheme=self.scheme).latest('access_date')
		self.assertEqual(log.http_code, 200)

	def test_404_creates_log(self):
		_edit(self.client, self.scheme.api_key, {'identifiers': {'nct': 'NCT9999999'}})
		log = APIAccessSchemeLog.objects.filter(api_access_scheme=self.scheme).latest('access_date')
		self.assertEqual(log.http_code, 404)

	def test_403_cross_org_creates_log(self):
		other_org = _make_org('Trial Org E', 'trial-org-e')
		other_team = _make_team(other_org, 'Trial Team E')
		_make_trial(other_team, 'NCT0000088', 'Other Org Trial E')
		_edit(self.client, self.scheme.api_key, {'identifiers': {'nct': 'NCT0000088'}})
		log = APIAccessSchemeLog.objects.filter(api_access_scheme=self.scheme).latest('access_date')
		self.assertEqual(log.http_code, 403)
