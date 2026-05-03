"""
Tests for gregory.visibility — the visible_org_ids() helper.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_visibility
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from organizations.models import Organization
from gregory.models import OrganizationApiSettings, Team
from gregory.visibility import visible_org_ids

User = get_user_model()


def _make_org(name, slug, public):
	"""Create an org and set its make_api_public flag."""
	org = Organization.objects.create(name=name, slug=slug)
	# Signal already created the settings row; just update it
	OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=public)
	return org


def _anon_request(factory, path='/', include_public=False):
	"""RequestFactory GET with an anonymous user (no auth)."""
	qs = '?include_public=true' if include_public else ''
	req = factory.get(path + qs)
	req.user = AnonymousUser()
	return req


class VisibleOrgIdsAnonymousTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.pub_org = _make_org('Public Org', 'public-org', public=True)
		self.priv_org = _make_org('Private Org', 'private-org', public=False)

	def test_anonymous_no_flag_sees_public_only(self):
		req = _anon_request(self.factory)
		result = visible_org_ids(req)
		self.assertIn(self.pub_org.id, result)
		self.assertNotIn(self.priv_org.id, result)

	def test_anonymous_with_include_public_still_only_public(self):
		"""Flag is a no-op for anonymous callers."""
		req = _anon_request(self.factory, include_public=True)
		result = visible_org_ids(req)
		self.assertIn(self.pub_org.id, result)
		self.assertNotIn(self.priv_org.id, result)


class VisibleOrgIdsAuthenticatedUserTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.pub_org = _make_org('Public Org', 'pub-org-u', public=True)
		self.priv_org_a = _make_org('Org A', 'org-a-u', public=False)
		self.priv_org_b = _make_org('Org B', 'org-b-u', public=False)

		self.user = User.objects.create_user(username='testuser', password='pw')

	def _authed_request(self, include_public=False):
		qs = '?include_public=true' if include_public else ''
		req = self.factory.get('/' + qs)
		req.user = self.user
		return req

	def test_user_no_team_no_include_public_sees_nothing(self):
		req = self._authed_request()
		result = visible_org_ids(req)
		# No membership → empty owned set, no public flag → empty
		self.assertEqual(result, set())

	def test_user_no_team_with_include_public_sees_public(self):
		req = self._authed_request(include_public=True)
		result = visible_org_ids(req)
		self.assertIn(self.pub_org.id, result)
		self.assertNotIn(self.priv_org_a.id, result)

	def test_user_in_org_a_sees_only_org_a_by_default(self):
		# Add user to a team in org A
		Team.objects.create(organization=self.priv_org_a, name='Team A', slug='team-a-u')
		from organizations.models import OrganizationUser
		OrganizationUser.objects.create(organization=self.priv_org_a, user=self.user)

		req = self._authed_request()
		result = visible_org_ids(req)
		self.assertIn(self.priv_org_a.id, result)
		self.assertNotIn(self.pub_org.id, result)
		self.assertNotIn(self.priv_org_b.id, result)

	def test_user_in_org_a_with_include_public_sees_org_a_plus_public(self):
		from organizations.models import OrganizationUser
		OrganizationUser.objects.create(organization=self.priv_org_a, user=self.user)

		req = self._authed_request(include_public=True)
		result = visible_org_ids(req)
		self.assertIn(self.priv_org_a.id, result)
		self.assertIn(self.pub_org.id, result)
		self.assertNotIn(self.priv_org_b.id, result)


class VisibleOrgIdsAPIKeyTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.pub_org = _make_org('Public Org', 'pub-org-k', public=True)
		self.org_x = _make_org('Org X', 'org-x-k', public=False)
		self.other_priv_org = _make_org('Other Priv', 'other-priv-k', public=False)

		from api.models import APIAccessScheme
		self.scheme_with_org = APIAccessScheme.objects.create(
			client_name='Key With Org',
			client_contacts='a@b.com',
			organization=self.org_x,
			ip_addresses='',
		)
		self.scheme_no_org = APIAccessScheme.objects.create(
			client_name='Key No Org',
			client_contacts='c@d.com',
			organization=None,
			ip_addresses='',
		)

	def _key_request(self, scheme, include_public=False):
		qs = '?include_public=true' if include_public else ''
		req = self.factory.get('/' + qs, HTTP_AUTHORIZATION=scheme.api_key, REMOTE_ADDR='127.0.0.1')
		req.user = AnonymousUser()
		return req

	def test_key_bound_to_org_x_sees_only_org_x(self):
		req = self._key_request(self.scheme_with_org)
		result = visible_org_ids(req)
		self.assertIn(self.org_x.id, result)
		self.assertNotIn(self.pub_org.id, result)
		self.assertNotIn(self.other_priv_org.id, result)

	def test_key_bound_to_org_x_with_include_public(self):
		req = self._key_request(self.scheme_with_org, include_public=True)
		result = visible_org_ids(req)
		self.assertIn(self.org_x.id, result)
		self.assertIn(self.pub_org.id, result)
		self.assertNotIn(self.other_priv_org.id, result)

	def test_null_org_key_no_include_public_sees_only_public(self):
		req = self._key_request(self.scheme_no_org)
		result = visible_org_ids(req)
		self.assertIn(self.pub_org.id, result)
		self.assertNotIn(self.org_x.id, result)

	def test_null_org_key_with_include_public_sees_only_public(self):
		"""Flag is still a no-op for null-org keys (anonymous-equivalent)."""
		req = self._key_request(self.scheme_no_org, include_public=True)
		result = visible_org_ids(req)
		self.assertIn(self.pub_org.id, result)
		self.assertNotIn(self.org_x.id, result)
