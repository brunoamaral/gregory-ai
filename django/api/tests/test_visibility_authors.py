"""
Tests for author visibility enforcement (PR 5).

Covers:
  - Authors list: only authors with at least one article in a visible org
  - articles_count reflects only visible articles
  - Detail endpoint 404s when author has no visible articles
  - Author search: team_id of hidden team returns 404
  - Four caller archetypes × the standard test matrix

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_authors
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import Articles, Authors, OrganizationApiSettings, Subject, Team

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


def _make_author(given, family):
	full = f'{given} {family}'
	return Authors.objects.create(
		given_name=given,
		family_name=family,
		full_name=full,
	)


def _make_article(title, link, teams=(), subjects=(), authors=()):
	art = Articles.objects.create(title=title, link=link)
	for t in teams:
		art.teams.add(t)
	for s in subjects:
		art.subjects.add(s)
	for a in authors:
		art.authors.add(a)
	return art


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

class AuthorVisibilityBase(TestCase):
	def setUp(self):
		self.my_org = _make_org('My Org', 'my-org-auth', public=False)
		self.pub_org = _make_org('Public Org', 'pub-org-auth', public=True)
		self.priv_org = _make_org('Private Org', 'priv-org-auth', public=False)

		self.my_team = _make_team(self.my_org, 'My Team Auth')
		self.pub_team = _make_team(self.pub_org, 'Pub Team Auth')
		self.priv_team = _make_team(self.priv_org, 'Priv Team Auth')

		self.my_subj = _make_subject(self.my_team, 'My Subject Auth')
		self.pub_subj = _make_subject(self.pub_team, 'Pub Subject Auth')

		# Authors
		self.author_mine = _make_author('Alice', 'Mine')
		self.author_pub = _make_author('Bob', 'Public')
		self.author_priv = _make_author('Carol', 'Private')
		self.author_cross = _make_author('Dave', 'Cross')  # articles in my + priv org

		# Articles
		_make_article('Mine Art', 'https://ex.com/a1', teams=[self.my_team], authors=[self.author_mine])
		_make_article('Pub Art', 'https://ex.com/a2', teams=[self.pub_team], authors=[self.author_pub])
		_make_article('Priv Art', 'https://ex.com/a3', teams=[self.priv_team], authors=[self.author_priv])
		# author_cross has articles in both my_team and priv_team
		_make_article('Cross Mine Art', 'https://ex.com/a4', teams=[self.my_team], authors=[self.author_cross])
		_make_article('Cross Priv Art', 'https://ex.com/a5', teams=[self.priv_team], authors=[self.author_cross])

		self.client = APIClient()


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------

class AnonymousAuthorVisibilityTest(AuthorVisibilityBase):
	"""Anonymous → only public org data visible."""

	def test_list_includes_public_author(self):
		resp = self.client.get('/authors/')
		self.assertEqual(resp.status_code, 200)
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertIn(self.author_pub.author_id, ids)

	def test_list_excludes_private_authors(self):
		resp = self.client.get('/authors/')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertNotIn(self.author_mine.author_id, ids)
		self.assertNotIn(self.author_priv.author_id, ids)

	def test_list_excludes_cross_org_author_with_no_public_article(self):
		"""author_cross has mine+priv articles, neither is public → not listed."""
		resp = self.client.get('/authors/')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertNotIn(self.author_cross.author_id, ids)

	def test_detail_of_hidden_author_returns_404(self):
		resp = self.client.get(f'/authors/{self.author_mine.author_id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_of_public_author_returns_200(self):
		resp = self.client.get(f'/authors/{self.author_pub.author_id}/')
		self.assertEqual(resp.status_code, 200)

	def test_author_search_hidden_team_returns_404(self):
		resp = self.client.get('/authors/search/', {
			'team_id': self.my_team.id,
			'subject_id': self.my_subj.id,
		})
		self.assertEqual(resp.status_code, 404)

	def test_author_search_public_team_returns_200(self):
		resp = self.client.get('/authors/search/', {
			'team_id': self.pub_team.id,
			'subject_id': self.pub_subj.id,
		})
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org)
# ---------------------------------------------------------------------------

class AuthenticatedUserAuthorVisibilityTest(AuthorVisibilityBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username='author-member', password='pw')
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_list_shows_own_org_author(self):
		resp = self.client.get('/authors/')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertIn(self.author_mine.author_id, ids)

	def test_list_excludes_unrelated_private_author(self):
		resp = self.client.get('/authors/')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertNotIn(self.author_priv.author_id, ids)

	def test_list_excludes_public_author_without_flag(self):
		resp = self.client.get('/authors/')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertNotIn(self.author_pub.author_id, ids)

	def test_list_includes_cross_org_author_when_has_own_article(self):
		"""author_cross has article in my_team → visible."""
		resp = self.client.get('/authors/')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertIn(self.author_cross.author_id, ids)

	def test_include_public_adds_public_authors(self):
		resp = self.client.get('/authors/?include_public=true')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertIn(self.author_mine.author_id, ids)
		self.assertIn(self.author_pub.author_id, ids)
		self.assertNotIn(self.author_priv.author_id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f'/authors/{self.author_priv.author_id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f'/authors/{self.author_mine.author_id}/')
		self.assertEqual(resp.status_code, 200)

	def test_articles_count_excludes_hidden_org_articles(self):
		"""author_cross: articles_count must count only the article in my_team."""
		resp = self.client.get(f'/authors/{self.author_cross.author_id}/')
		self.assertEqual(resp.status_code, 200)
		# author_cross has 1 visible article (in my_team) and 1 hidden (in priv_team)
		self.assertEqual(resp.data['articles_count'], 1)

	def test_author_search_hidden_team_returns_404(self):
		resp = self.client.get('/authors/search/', {
			'team_id': self.priv_team.id,
			'subject_id': self.my_subj.id,
		})
		self.assertEqual(resp.status_code, 404)

	def test_author_search_own_team_returns_200(self):
		resp = self.client.get('/authors/search/', {
			'team_id': self.my_team.id,
			'subject_id': self.my_subj.id,
		})
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org)
# ---------------------------------------------------------------------------

class APIKeyAuthorVisibilityTest(AuthorVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, 'author-key')
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_list_shows_own_org_author(self):
		resp = self.client.get('/authors/')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertIn(self.author_mine.author_id, ids)

	def test_list_excludes_private_author(self):
		resp = self.client.get('/authors/')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertNotIn(self.author_priv.author_id, ids)

	def test_list_excludes_public_author_without_flag(self):
		resp = self.client.get('/authors/')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertNotIn(self.author_pub.author_id, ids)

	def test_include_public_adds_public_authors(self):
		resp = self.client.get('/authors/?include_public=true')
		ids = [a['author_id'] for a in resp.data['results']]
		self.assertIn(self.author_mine.author_id, ids)
		self.assertIn(self.author_pub.author_id, ids)
		self.assertNotIn(self.author_priv.author_id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f'/authors/{self.author_priv.author_id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f'/authors/{self.author_mine.author_id}/')
		self.assertEqual(resp.status_code, 200)

	def test_author_search_hidden_team_returns_404(self):
		resp = self.client.get('/authors/search/', {
			'team_id': self.pub_team.id,
			'subject_id': self.pub_subj.id,
		})
		self.assertEqual(resp.status_code, 404)

	def test_author_search_own_team_returns_200(self):
		resp = self.client.get('/authors/search/', {
			'team_id': self.my_team.id,
			'subject_id': self.my_subj.id,
		})
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Null-org API key (anonymous-equivalent)
# ---------------------------------------------------------------------------
