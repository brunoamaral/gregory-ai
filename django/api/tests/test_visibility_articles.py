"""
Tests for article visibility enforcement (PR 4).

Covers the four caller archetypes × the test matrix from the PR plan:
  - Article in caller's org only
  - Article in another public org only
  - Article in another private org only
  - Article spanning caller's org + a public org (listing + association stripping)
  - Article spanning caller's org + a private org (listing + association stripping)
  - Detail endpoint: all-hidden teams → 404
  - ArticleSearchView: team_id of hidden team → 404
  - Legacy endpoints: /teams/<id>/articles/ honours visibility

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_articles
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import Articles, OrganizationApiSettings, Subject, Team

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


def _make_article(title, link, teams=(), subjects=()):
	art = Articles.objects.create(title=title, link=link)
	for t in teams:
		art.teams.add(t)
	for s in subjects:
		art.subjects.add(s)
	return art


def _make_api_scheme(org, name):
	"""Create a valid (not-expired) APIAccessScheme for the given org."""
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f'{name}@example.com',
		organization=org,
		ip_addresses='',
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


# ---------------------------------------------------------------------------
# Base setUp shared across test classes
# ---------------------------------------------------------------------------

class ArticleVisibilityBase(TestCase):
	def setUp(self):
		# caller's org (private)
		self.my_org = _make_org('My Org', 'my-org-art', public=False)
		# another public org
		self.pub_org = _make_org('Public Org', 'pub-org-art', public=True)
		# another private org
		self.priv_org = _make_org('Private Org', 'priv-org-art', public=False)

		self.my_team = _make_team(self.my_org, 'My Team')
		self.pub_team = _make_team(self.pub_org, 'Pub Team')
		self.priv_team = _make_team(self.priv_org, 'Priv Team')

		self.my_subj = _make_subject(self.my_team, 'My Subject')
		self.pub_subj = _make_subject(self.pub_team, 'Pub Subject')

		# Article in my org only
		self.art_mine = _make_article('Mine Only', 'https://ex.com/1', teams=[self.my_team])
		# Article in public org only
		self.art_pub = _make_article('Public Only', 'https://ex.com/2', teams=[self.pub_team])
		# Article in private (hidden) org only
		self.art_priv = _make_article('Private Only', 'https://ex.com/3', teams=[self.priv_team])
		# Article spanning my org + public org
		self.art_mine_pub = _make_article('Mine+Pub', 'https://ex.com/4', teams=[self.my_team, self.pub_team])
		# Article spanning my org + hidden org
		self.art_mine_priv = _make_article('Mine+Priv', 'https://ex.com/5', teams=[self.my_team, self.priv_team])

		self.client = APIClient()


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------

class AnonymousArticleVisibilityTest(ArticleVisibilityBase):
	"""Anonymous request → only public orgs visible."""

	def test_list_shows_public_article(self):
		resp = self.client.get('/articles/')
		self.assertEqual(resp.status_code, 200)
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertIn(self.art_pub.article_id, ids)

	def test_list_excludes_private_article(self):
		resp = self.client.get('/articles/')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertNotIn(self.art_priv.article_id, ids)
		self.assertNotIn(self.art_mine.article_id, ids)

	def test_list_includes_cross_org_article_when_one_team_public(self):
		"""art_mine_pub has pub_team → visible; art_mine_priv has no public team → not visible."""
		resp = self.client.get('/articles/')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertIn(self.art_mine_pub.article_id, ids)
		self.assertNotIn(self.art_mine_priv.article_id, ids)

	def test_cross_org_article_has_hidden_team_stripped(self):
		"""art_mine_pub: my_team should be stripped (not public), pub_team visible."""
		resp = self.client.get('/articles/')
		self.assertEqual(resp.status_code, 200)
		art = next(a for a in resp.data['results'] if a['article_id'] == self.art_mine_pub.article_id)
		team_ids = [t['id'] for t in art['teams']]
		self.assertIn(self.pub_team.id, team_ids)
		self.assertNotIn(self.my_team.id, team_ids)

	def test_detail_of_all_hidden_article_returns_404(self):
		resp = self.client.get(f'/articles/{self.art_mine.article_id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_of_public_article_returns_200(self):
		resp = self.client.get(f'/articles/{self.art_pub.article_id}/')
		self.assertEqual(resp.status_code, 200)

	def test_include_public_is_noop_for_anonymous(self):
		"""include_public=true is a no-op for anonymous callers (already see public)."""
		resp_plain = self.client.get('/articles/')
		resp_flag = self.client.get('/articles/?include_public=true')
		plain_ids = {a['article_id'] for a in resp_plain.data['results']}
		flag_ids = {a['article_id'] for a in resp_flag.data['results']}
		self.assertEqual(plain_ids, flag_ids)

	def test_search_with_hidden_team_returns_404(self):
		resp = self.client.get('/articles/search/', {
			'team_id': self.my_team.id,
			'subject_id': self.my_subj.id,
		})
		self.assertEqual(resp.status_code, 404)

	def test_search_with_public_team_returns_200(self):
		resp = self.client.get('/articles/search/', {
			'team_id': self.pub_team.id,
			'subject_id': self.pub_subj.id,
		})
		self.assertEqual(resp.status_code, 200)

	def test_legacy_team_endpoint_hidden_team_returns_404(self):
		resp = self.client.get(f'/teams/{self.my_team.id}/articles/')
		self.assertEqual(resp.status_code, 404)

	def test_legacy_team_endpoint_public_team_returns_200(self):
		resp = self.client.get(f'/teams/{self.pub_team.id}/articles/')
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org)
# ---------------------------------------------------------------------------

class AuthenticatedUserArticleVisibilityTest(ArticleVisibilityBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username='member', password='pw')
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		# force_login creates a real Django session so VisibleOrgMiddleware
		# sees the authenticated user (force_authenticate is DRF-only and
		# runs after middleware, so the middleware would see an anonymous user).
		self.client.force_login(self.user)

	def test_list_shows_my_org_article(self):
		resp = self.client.get('/articles/')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertIn(self.art_mine.article_id, ids)

	def test_list_excludes_unrelated_private_article(self):
		resp = self.client.get('/articles/')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertNotIn(self.art_priv.article_id, ids)

	def test_list_excludes_public_article_by_default(self):
		"""Without include_public, only owned org is visible."""
		resp = self.client.get('/articles/')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertNotIn(self.art_pub.article_id, ids)

	def test_include_public_adds_public_articles(self):
		resp = self.client.get('/articles/?include_public=true')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertIn(self.art_mine.article_id, ids)
		self.assertIn(self.art_pub.article_id, ids)
		self.assertNotIn(self.art_priv.article_id, ids)

	def test_detail_of_hidden_article_returns_404(self):
		resp = self.client.get(f'/articles/{self.art_priv.article_id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_of_own_article_returns_200(self):
		resp = self.client.get(f'/articles/{self.art_mine.article_id}/')
		self.assertEqual(resp.status_code, 200)

	def test_cross_org_article_has_hidden_team_stripped(self):
		"""art_mine_priv: priv_team should be stripped from teams field."""
		resp = self.client.get(f'/articles/{self.art_mine_priv.article_id}/')
		self.assertEqual(resp.status_code, 200)
		team_ids = [t['id'] for t in resp.data['teams']]
		self.assertIn(self.my_team.id, team_ids)
		self.assertNotIn(self.priv_team.id, team_ids)

	def test_search_hidden_team_returns_404(self):
		resp = self.client.get('/articles/search/', {
			'team_id': self.priv_team.id,
			'subject_id': self.my_subj.id,
		})
		self.assertEqual(resp.status_code, 404)

	def test_search_own_team_returns_200(self):
		resp = self.client.get('/articles/search/', {
			'team_id': self.my_team.id,
			'subject_id': self.my_subj.id,
		})
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org)
# ---------------------------------------------------------------------------

class APIKeyArticleVisibilityTest(ArticleVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, 'my-key')
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_list_shows_my_org_article(self):
		resp = self.client.get('/articles/')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertIn(self.art_mine.article_id, ids)

	def test_list_excludes_other_private_article(self):
		resp = self.client.get('/articles/')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertNotIn(self.art_priv.article_id, ids)

	def test_list_excludes_public_article_without_flag(self):
		resp = self.client.get('/articles/')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertNotIn(self.art_pub.article_id, ids)

	def test_include_public_adds_public_articles(self):
		resp = self.client.get('/articles/?include_public=true')
		ids = [a['article_id'] for a in resp.data['results']]
		self.assertIn(self.art_mine.article_id, ids)
		self.assertIn(self.art_pub.article_id, ids)
		self.assertNotIn(self.art_priv.article_id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f'/articles/{self.art_priv.article_id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f'/articles/{self.art_mine.article_id}/')
		self.assertEqual(resp.status_code, 200)

	def test_search_hidden_team_returns_404(self):
		resp = self.client.get('/articles/search/', {
			'team_id': self.pub_team.id,
			'subject_id': self.pub_subj.id,
		})
		self.assertEqual(resp.status_code, 404)

	def test_search_own_team_returns_200(self):
		resp = self.client.get('/articles/search/', {
			'team_id': self.my_team.id,
			'subject_id': self.my_subj.id,
		})
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Null-org API key (anonymous-equivalent)
# ---------------------------------------------------------------------------
class CSVExportArticleVisibilityTest(ArticleVisibilityBase):
	"""CSV responses from /articles/?format=csv must respect the same visibility rules."""

	def _csv_titles(self, response):
		"""Return the set of article titles found in a CSV StreamingHttpResponse."""
		content = b''.join(response.streaming_content).decode()
		# Title is the second column in the CSV; skip the header row.
		titles = set()
		for line in content.splitlines()[1:]:
			if line.strip():
				# Split on comma but handle quoted fields minimally.
				titles.add(line.split(',')[1].strip('"'))
		return titles

	def test_anonymous_csv_shows_only_public_articles(self):
		resp = self.client.get('/articles/?format=csv&all_results=true')
		self.assertIn(resp.status_code, (200, 206))
		titles = self._csv_titles(resp)
		self.assertIn('Public Only', titles)
		self.assertNotIn('Mine Only', titles)
		self.assertNotIn('Private Only', titles)

	def test_api_key_csv_shows_own_org_articles(self):
		from api.models import APIAccessScheme
		scheme = APIAccessScheme.objects.create(
			client_name='csv-key',
			client_contacts='csv@example.com',
			organization=self.my_org,
			ip_addresses='',
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)
		self.client.credentials(HTTP_AUTHORIZATION=scheme.api_key)
		resp = self.client.get('/articles/?format=csv&all_results=true')
		self.assertIn(resp.status_code, (200, 206))
		titles = self._csv_titles(resp)
		self.assertIn('Mine Only', titles)
		self.assertNotIn('Private Only', titles)

	def test_api_key_csv_with_include_public_adds_public_articles(self):
		from api.models import APIAccessScheme
		scheme = APIAccessScheme.objects.create(
			client_name='csv-key-pub',
			client_contacts='csvpub@example.com',
			organization=self.my_org,
			ip_addresses='',
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)
		self.client.credentials(HTTP_AUTHORIZATION=scheme.api_key)
		resp = self.client.get('/articles/?format=csv&all_results=true&include_public=true')
		self.assertIn(resp.status_code, (200, 206))
		titles = self._csv_titles(resp)
		self.assertIn('Mine Only', titles)
		self.assertIn('Public Only', titles)
		self.assertNotIn('Private Only', titles)
