"""
Tests for PR 7 — post_article cross-org enforcement.

Covers:
  - Valid payload: source_id belongs to key's org → 201 (new article) or 200 (duplicate)
  - Cross-org payload: source_id belongs to a different org → 400
  - Key with organization=None → 403
  - Null key (no Authorization header) → 401

Run with:
    docker exec gregory python manage.py test api.tests.test_post_article_org_scoping
"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.utils.timezone import now
from organizations.models import Organization

from api.models import APIAccessScheme
from gregory.models import Articles, Sources, Team, Subject
from gregory.models import OrganizationApiSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_org(name, slug):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=False)
	return org


def _make_team(org, name):
	slug = name.lower().replace(' ', '-')
	return Team.objects.create(organization=org, name=name, slug=slug)


def _make_subject(team, name):
	from django.utils.text import slugify
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def _make_source(team, subject, name, source_for='science paper'):
	return Sources.objects.create(
		name=name,
		link=f'https://src.example.com/{name}',
		team=team,
		subject=subject,
		source_for=source_for,
	)


def _make_scheme(org, name):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f'{name}@example.com',
		organization=org,
		ip_addresses='',
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


def _post(client, api_key, payload, path='/articles/post/'):
	body = json.dumps(payload).encode()
	headers = {}
	if api_key:
		headers['HTTP_AUTHORIZATION'] = api_key
	return client.post(
		path,
		data=body,
		content_type='application/json',
		**headers,
	)


# ---------------------------------------------------------------------------
# Base fixture
# ---------------------------------------------------------------------------

class PostArticleOrgScopingBase(TestCase):
	def setUp(self):
		self.my_org    = _make_org('My Org',    'pa-my-org')
		self.other_org = _make_org('Other Org', 'pa-other-org')

		self.my_team    = _make_team(self.my_org,    'PA My Team')
		self.other_team = _make_team(self.other_org, 'PA Other Team')

		self.my_subj    = _make_subject(self.my_team,    'PA My Subject')
		self.other_subj = _make_subject(self.other_team, 'PA Other Subject')

		self.my_source    = _make_source(self.my_team,    self.my_subj,    'pa-my-source')
		self.other_source = _make_source(self.other_team, self.other_subj, 'pa-other-source')

		self.scheme       = _make_scheme(self.my_org, 'pa-my-key')
		self.null_scheme  = APIAccessScheme.objects.create(
			client_name='pa-null-key',
			client_contacts='null@example.com',
			organization=None,
			ip_addresses='',
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)
		self.client = Client()

	def _valid_payload(self, source_id):
		"""Minimal payload that references the given source.
		Uses a DOI so SciencePaper skips the CrossRef title-lookup
		(which requires a CustomSetting row not present in tests).
		"""
		return {
			'kind': 'science paper',
			'source_id': source_id,
			'title': f'Test article for source {source_id}',
			'link': f'https://example.com/article-src-{source_id}',
			'doi': f'10.9999/test-source-{source_id}',
		}


# ---------------------------------------------------------------------------
# Null-org key → 403
# ---------------------------------------------------------------------------

class NullOrgKeyPostArticleTest(PostArticleOrgScopingBase):

	def test_null_org_key_rejected_403(self):
		payload = self._valid_payload(self.my_source.pk)
		resp = _post(self.client, self.null_scheme.api_key, payload)
		self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Cross-org payload → 400
# ---------------------------------------------------------------------------

class CrossOrgPayloadTest(PostArticleOrgScopingBase):

	def test_cross_org_source_returns_400(self):
		"""Key for my_org, source belongs to other_org → 400."""
		payload = self._valid_payload(self.other_source.pk)
		resp = _post(self.client, self.scheme.api_key, payload)
		self.assertEqual(resp.status_code, 400)
		data = resp.json()
		self.assertEqual(data['code'], 10)  # CROSS_ORG_PAYLOAD

	def test_cross_org_error_message_present(self):
		payload = self._valid_payload(self.other_source.pk)
		resp = _post(self.client, self.scheme.api_key, payload)
		data = resp.json()
		self.assertIn('organisation', data['error_msg'].lower())

	def test_kind_mismatch_returns_400(self):
		"""Payload kind differs from source.source_for → 400 (FieldNotFoundError)."""
		# Create a trials source within my_org, then submit with kind='science paper'
		trials_source = _make_source(self.my_team, self.my_subj, 'pa-trials-source', source_for='trials')
		payload = {
			'kind': 'science paper',   # mismatches source_for='trials'
			'source_id': trials_source.pk,
			'title': 'Mismatch test article',
			'doi': '10.9999/mismatch-test',
		}
		resp = _post(self.client, self.scheme.api_key, payload)
		self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# No API key → 401
# ---------------------------------------------------------------------------

class NoKeyPostArticleTest(PostArticleOrgScopingBase):

	def test_no_key_returns_401(self):
		payload = self._valid_payload(self.my_source.pk)
		resp = _post(self.client, None, payload)
		self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# Unknown source_id → 404
# ---------------------------------------------------------------------------

class SourceNotFoundTest(PostArticleOrgScopingBase):

	def test_source_not_found_returns_404(self):
		"""Requesting a source_id that does not exist → 404."""
		payload = {
			'kind': 'science paper',
			'source_id': 999999,
			'title': 'Source not found test',
			'doi': '10.9999/source-not-found',
		}
		resp = _post(self.client, self.scheme.api_key, payload)
		self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Valid payload (own org) → 200/201
# ---------------------------------------------------------------------------

class ValidOrgPostArticleTest(PostArticleOrgScopingBase):

	def test_own_org_source_accepted(self):
		"""Key for my_org, source belongs to my_org → article created (201) or duplicate (200).

		SciencePaper.refresh() is mocked because the test environment does not
		have a CustomSetting / CrossRef configuration.
		"""
		payload = self._valid_payload(self.my_source.pk)
		with patch('gregory.classes.SciencePaper.refresh', return_value=None):
			resp = _post(self.client, self.scheme.api_key, payload)
		# 201 new article, or 200 duplicate — both acceptable; must NOT be 400/403/500
		self.assertIn(resp.status_code, [200, 201])

	def test_own_org_source_not_cross_org_error(self):
		"""Response code must not be CROSS_ORG_PAYLOAD (10) for valid source."""
		payload = self._valid_payload(self.my_source.pk)
		with patch('gregory.classes.SciencePaper.refresh', return_value=None):
			resp = _post(self.client, self.scheme.api_key, payload)
		data = resp.json()
		self.assertNotEqual(data.get('code'), 10)
