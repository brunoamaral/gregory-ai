"""
Tests for viewset lockdown — all write methods must return 405.

Verify that ArticleViewSet and the other API viewsets only allow
GET/HEAD/OPTIONS now that they subclass ReadOnlyModelViewSet.

Covers spec §10.4:
  - PATCH /articles/<id>/ → 405
  - PUT /articles/<id>/ → 405
  - DELETE /articles/<id>/ → 405
  - POST /articles/ → 405
  - Same matrix for /trials/, /authors/, /sources/, /subjects/, /categories/, /teams/

Run with:
    docker exec gregory python manage.py test api.tests.test_viewset_lockdown
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization
from rest_framework.test import APIClient

User = get_user_model()

from gregory.models import (
	Articles, Trials, Authors, Sources, Team, Subject,
	OrganizationApiSettings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_org(name, slug=''):
	slug = slug or name.lower().replace(' ', '-')
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=True)
	return org


def _make_team(org, name):
	return Team.objects.create(organization=org, name=name, slug=name.lower().replace(' ', '-'))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class ArticleViewSetLockdownTest(TestCase):
	"""
	PATCH/PUT/DELETE/POST on /articles/ must return 405 Method Not Allowed
	after Phase 5 switches ArticleViewSet to ReadOnlyModelViewSet.
	"""

	def setUp(self):
		self.client = APIClient()
		self.org = _make_org('Lockdown Org')
		self.team = _make_team(self.org, 'Lockdown Team')
		self.article = Articles.objects.create(
			title='Lockdown Article',
			link='https://example.com/lock',
		)
		self.article.teams.add(self.team)
		self.user = User.objects.create_user(username='lockdown-user', password='pw')
		self.client.force_login(self.user)

	def _auth_headers(self):
		return {}

	def test_patch_returns_405(self):
		resp = self.client.patch(
			f'/articles/{self.article.article_id}/',
			data={'title': 'Patched'},
			format='json',
			**self._auth_headers(),
		)
		self.assertEqual(resp.status_code, 405)

	def test_put_returns_405(self):
		resp = self.client.put(
			f'/articles/{self.article.article_id}/',
			data={'title': 'Put', 'link': 'https://example.com/put'},
			format='json',
			**self._auth_headers(),
		)
		self.assertEqual(resp.status_code, 405)

	def test_delete_returns_405(self):
		resp = self.client.delete(
			f'/articles/{self.article.article_id}/',
			**self._auth_headers(),
		)
		self.assertEqual(resp.status_code, 405)

	def test_post_returns_405(self):
		resp = self.client.post(
			'/articles/',
			data={'title': 'New', 'link': 'https://example.com/new'},
			format='json',
			**self._auth_headers(),
		)
		self.assertEqual(resp.status_code, 405)


class TrialViewSetLockdownTest(TestCase):
	"""PATCH /trials/<id>/ must return 405 after Phase 5."""

	def setUp(self):
		self.client = APIClient()
		self.org = _make_org('Trial Lockdown Org')
		self.team = _make_team(self.org, 'Trial Lockdown Team')
		self.trial = Trials.objects.create(
			title='Lockdown Trial',
			link='https://example.com/trial-lock',
			identifiers={'nct': 'NCT9999998'},
		)
		self.trial.teams.add(self.team)
		self.user = User.objects.create_user(username='trial-lockdown-user', password='pw')
		self.client.force_login(self.user)

	def test_patch_returns_405(self):
		resp = self.client.patch(
			f'/trials/{self.trial.trial_id}/',
			data={'title': 'Patched'},
			format='json',
		)
		self.assertEqual(resp.status_code, 405)

	def test_delete_returns_405(self):
		resp = self.client.delete(
			f'/trials/{self.trial.trial_id}/',
		)
		self.assertEqual(resp.status_code, 405)


class OtherViewSetsLockdownTest(TestCase):
	"""
	Smoke-tests: PATCH on /authors/, /sources/, /subjects/, /teams/, /categories/
	must all return 405 after Phase 5.
	"""

	def setUp(self):
		self.client = APIClient()
		self.org = _make_org('Other Lockdown Org')
		self.team = _make_team(self.org, 'Other Lockdown Team')
		from django.utils.text import slugify
		self.subject = Subject.objects.create(
			team=self.team,
			subject_name='Lockdown Subject',
			subject_slug='lockdown-subject',
		)
		self.source = Sources.objects.create(
			name='Lockdown Source',
			link='https://src.example.com/lock',
			team=self.team,
			subject=self.subject,
		)
		self.author = Authors.objects.create(
			given_name='Test',
			family_name='Author',
		)
		self.user = User.objects.create_user(username='other-lockdown-user', password='pw')
		self.client.force_login(self.user)

	def test_patch_authors_returns_405(self):
		resp = self.client.patch(
			f'/authors/{self.author.author_id}/',
			data={'given_name': 'Changed'},
			format='json',
		)
		self.assertEqual(resp.status_code, 405)

	def test_patch_sources_returns_405(self):
		resp = self.client.patch(
			f'/sources/{self.source.source_id}/',
			data={'name': 'Changed'},
			format='json',
		)
		self.assertEqual(resp.status_code, 405)

	def test_patch_subjects_returns_405(self):
		resp = self.client.patch(
			f'/subjects/{self.subject.id}/',
			data={'subject_name': 'Changed'},
			format='json',
		)
		self.assertEqual(resp.status_code, 405)

	def test_patch_teams_returns_405(self):
		resp = self.client.patch(
			f'/teams/{self.team.id}/',
			data={'name': 'Changed'},
			format='json',
		)
		self.assertEqual(resp.status_code, 405)
