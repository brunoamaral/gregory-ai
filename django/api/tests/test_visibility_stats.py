"""
Tests for StatsView visibility enforcement (PR 6).

Covers:
  - Anonymous caller: counts reflect public orgs only
  - Authenticated user (member of org): counts reflect own org
  - API key caller: counts reflect key's org
  - ?team=<id> for a hidden team → 404
  - ?team=<id> for a visible team → counts scoped to that team
  - ?include_public=true adds public org data for identified callers

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_stats
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import Articles, OrganizationApiSettings, Subject, Team, Trials

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_org(name, slug, public=False):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=public)
	return org


def _make_team(org, name):
	slug = name.lower().replace(' ', '-')
	return Team.objects.create(organization=org, name=name, slug=slug)


def _make_subject(team, name):
	from django.utils.text import slugify
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def _make_article(title, link, teams=()):
	art = Articles.objects.create(title=title, link=link)
	for t in teams:
		art.teams.add(t)
	return art


def _make_trial(title, link, teams=(), subjects=()):
	from gregory.models import Trials
	trial = Trials.objects.create(title=title, link=link)
	for t in teams:
		trial.teams.add(t)
	for s in subjects:
		trial.subjects.add(s)
	return trial


def _make_api_scheme(org, name):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f'{name}@example.com',
		organization=org,
		ip_addresses='',
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


# ---------------------------------------------------------------------------
# Base setUp
# ---------------------------------------------------------------------------

class StatsVisibilityBase(TestCase):
	def setUp(self):
		self.my_org  = _make_org('My Org',      'my-org-stats',   public=False)
		self.pub_org = _make_org('Public Org',   'pub-org-stats',  public=True)
		self.priv_org = _make_org('Private Org', 'priv-org-stats', public=False)

		self.my_team   = _make_team(self.my_org,   'My Team Stats')
		self.pub_team  = _make_team(self.pub_org,  'Pub Team Stats')
		self.priv_team = _make_team(self.priv_org, 'Priv Team Stats')

		# Articles
		self.art_mine  = _make_article('Mine Art',   'https://st.ex/a1', teams=[self.my_team])
		self.art_pub   = _make_article('Pub Art',    'https://st.ex/a2', teams=[self.pub_team])
		self.art_priv  = _make_article('Priv Art',   'https://st.ex/a3', teams=[self.priv_team])

		# Trials
		self.trial_mine  = _make_trial('Mine Trial',   'https://st.ex/t1', teams=[self.my_team])
		self.trial_pub   = _make_trial('Pub Trial',    'https://st.ex/t2', teams=[self.pub_team])
		self.trial_priv  = _make_trial('Priv Trial',   'https://st.ex/t3', teams=[self.priv_team])

		self.client = APIClient()


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------

class AnonymousStatsVisibilityTest(StatsVisibilityBase):
	"""Anonymous request → only public org data counted."""

	def test_articles_count_only_public(self):
		resp = self.client.get('/stats/')
		self.assertEqual(resp.status_code, 200)
		# Exactly 1 public article (pub_team) visible; mine and priv are hidden
		self.assertEqual(resp.data['articles'], 1)
		self.assertEqual(resp.data['trials'], 1)
		resp_pub = self.client.get('/stats/', {'team': self.pub_team.id})
		resp_priv = self.client.get('/stats/', {'team': self.priv_team.id})
		self.assertEqual(resp_pub.status_code, 200)
		# Hidden team → 404
		self.assertEqual(resp_priv.status_code, 404)

	def test_hidden_team_param_returns_404(self):
		resp = self.client.get('/stats/', {'team': self.priv_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_own_team_param_returns_404_for_anon(self):
		"""my_team belongs to a private org → anonymous can't see it."""
		resp = self.client.get('/stats/', {'team': self.my_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_param_returns_200(self):
		resp = self.client.get('/stats/', {'team': self.pub_team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data['articles'], 1)
		self.assertEqual(resp.data['trials'], 1)

	def test_invalid_team_param_returns_400(self):
		resp = self.client.get('/stats/', {'team': 'abc'})
		self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org)
# ---------------------------------------------------------------------------

class AuthenticatedUserStatsVisibilityTest(StatsVisibilityBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username='stats-member', password='pw')
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_articles_count_only_own_org(self):
		"""Authenticated user without include_public sees only own org."""
		resp = self.client.get('/stats/')
		self.assertEqual(resp.status_code, 200)
		# Scope to own team for a precise count
		resp_mine = self.client.get('/stats/', {'team': self.my_team.id})
		self.assertEqual(resp_mine.status_code, 200)
		self.assertEqual(resp_mine.data['articles'], 1)
		self.assertEqual(resp_mine.data['trials'], 1)

	def test_hidden_team_param_returns_404(self):
		resp = self.client.get('/stats/', {'team': self.priv_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_hidden_without_flag(self):
		"""pub_team is not visible without ?include_public=true."""
		resp = self.client.get('/stats/', {'team': self.pub_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_visible_with_include_public(self):
		resp = self.client.get('/stats/', {'team': self.pub_team.id, 'include_public': 'true'})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data['articles'], 1)

	def test_include_public_expands_global_count(self):
		resp_no_flag = self.client.get('/stats/')
		resp_with_flag = self.client.get('/stats/?include_public=true')
		# Without flag: only own org → 1 article (mine)
		self.assertEqual(resp_no_flag.data['articles'], 1)
		# With flag: own org + public org → 2 articles (mine + pub)
		self.assertEqual(resp_with_flag.data['articles'], 2)
		# Private org article never counted
		self.assertLess(resp_with_flag.data['articles'], 3)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org)
# ---------------------------------------------------------------------------

class APIKeyStatsVisibilityTest(StatsVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, 'stats-key')
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_own_team_visible(self):
		resp = self.client.get('/stats/', {'team': self.my_team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data['articles'], 1)
		self.assertEqual(resp.data['trials'], 1)

	def test_hidden_team_returns_404(self):
		resp = self.client.get('/stats/', {'team': self.priv_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_hidden_without_flag(self):
		resp = self.client.get('/stats/', {'team': self.pub_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_visible_with_include_public(self):
		resp = self.client.get('/stats/', {'team': self.pub_team.id, 'include_public': 'true'})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data['articles'], 1)

	def test_include_public_expands_global_count(self):
		resp_no_flag = self.client.get('/stats/')
		resp_with_flag = self.client.get('/stats/?include_public=true')
		# Without flag: only own org → 1 article (mine)
		self.assertEqual(resp_no_flag.data['articles'], 1)
		# With flag: own org + public org → 2 articles (mine + pub)
		self.assertEqual(resp_with_flag.data['articles'], 2)
		# Private org article never counted
		self.assertLess(resp_with_flag.data['articles'], 3)


# ---------------------------------------------------------------------------
# Null-org API key (anonymous-equivalent)
# ---------------------------------------------------------------------------

class NullOrgAPIKeyStatsVisibilityTest(StatsVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = APIAccessScheme.objects.create(
			client_name='null-org-stats-key',
			client_contacts='null-stats@example.com',
			organization=None,
			ip_addresses='',
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_own_team_not_visible(self):
		resp = self.client.get('/stats/', {'team': self.my_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_visible(self):
		resp = self.client.get('/stats/', {'team': self.pub_team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data['articles'], 1)
