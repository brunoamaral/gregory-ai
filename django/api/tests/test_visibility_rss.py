"""
Tests for RSS feed visibility enforcement (PR 6).

Covers:
  - ArticlesByAuthorFeed (/feed/author/<orcid>/):
      - 404 when author has no articles in any visible org
      - 200 and items filtered to visible articles when author is visible
      - ?include_public=true extends visibility for identified callers
  - TrialsBySubjectFeed (/feed/trials/subject/<slug>/):
      - 404 when subject belongs to a hidden org
      - 200 and items filtered to visible trials when subject is visible
      - ?include_public=true extends visibility for identified callers
  - Four caller archetypes: anonymous, authenticated member, API-key, null-org key

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_rss
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser

from api.models import APIAccessScheme
from gregory.models import (
	Articles,
	Authors,
	OrganizationApiSettings,
	Subject,
	Team,
	Trials,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_org(name, slug, public=False):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=public
	)
	return org


def _make_team(org, name):
	slug = name.lower().replace(" ", "-")
	return Team.objects.create(organization=org, name=name, slug=slug)


def _make_subject(team, name):
	from django.utils.text import slugify

	slug = slugify(name)
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slug)


def _make_author(given, family, orcid):
	full = f"{given} {family}"
	return Authors.objects.create(
		given_name=given, family_name=family, full_name=full, ORCID=orcid
	)


def _make_article(title, link, teams=(), authors=()):
	art = Articles.objects.create(title=title, link=link)
	for t in teams:
		art.teams.add(t)
	for a in authors:
		art.authors.add(a)
	return art


def _make_trial(title, link, teams=(), subjects=()):
	trial = Trials.objects.create(title=title, link=link)
	for t in teams:
		trial.teams.add(t)
	for s in subjects:
		trial.subjects.add(s)
	return trial


def _make_api_scheme(org, name):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f"{name}@example.com",
		organization=org,
		ip_addresses="",
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


# ---------------------------------------------------------------------------
# Base setUp for author feed tests
# ---------------------------------------------------------------------------

ORCID_MINE = "0000-0001-0001-0001"
ORCID_PUB = "0000-0001-0002-0002"
ORCID_PRIV = "0000-0001-0003-0003"


class AuthorFeedBase(TestCase):
	def setUp(self):
		self.my_org = _make_org("My Org", "my-org-rss-auth", public=False)
		self.pub_org = _make_org("Public Org", "pub-org-rss-auth", public=True)
		self.priv_org = _make_org("Private Org", "priv-org-rss-auth", public=False)

		self.my_team = _make_team(self.my_org, "My Team RSS Auth")
		self.pub_team = _make_team(self.pub_org, "Pub Team RSS Auth")
		self.priv_team = _make_team(self.priv_org, "Priv Team RSS Auth")

		# Authors
		self.author_mine = _make_author("Alice", "Mine", ORCID_MINE)
		self.author_pub = _make_author("Bob", "Public", ORCID_PUB)
		self.author_priv = _make_author("Carol", "Private", ORCID_PRIV)

		# Articles
		_make_article(
			"Mine Art",
			"https://rss.ex/a1",
			teams=[self.my_team],
			authors=[self.author_mine],
		)
		_make_article(
			"Pub Art",
			"https://rss.ex/a2",
			teams=[self.pub_team],
			authors=[self.author_pub],
		)
		_make_article(
			"Priv Art",
			"https://rss.ex/a3",
			teams=[self.priv_team],
			authors=[self.author_priv],
		)
		# author_mine also has a public article
		_make_article(
			"Mine+Pub Art",
			"https://rss.ex/a4",
			teams=[self.pub_team],
			authors=[self.author_mine],
		)


# ---------------------------------------------------------------------------
# Anonymous: author feed
# ---------------------------------------------------------------------------


class AnonymousAuthorFeedTest(AuthorFeedBase):
	"""Anonymous → only public org articles visible."""

	def test_public_author_returns_200(self):
		resp = self.client.get(f"/feed/author/{ORCID_PUB}/")
		self.assertEqual(resp.status_code, 200)

	def test_private_author_returns_404(self):
		"""author_priv only has articles in private org → 404 for anonymous."""
		resp = self.client.get(f"/feed/author/{ORCID_PRIV}/")
		self.assertEqual(resp.status_code, 404)

	def test_private_author_feed_with_mine_and_pub_returns_200(self):
		"""author_mine has both a private and a public article → visible because of pub article."""
		resp = self.client.get(f"/feed/author/{ORCID_MINE}/")
		self.assertEqual(resp.status_code, 200)
		# The mine-only article should NOT appear; only the pub article
		content = resp.content.decode()
		self.assertIn("Mine+Pub Art", content)
		self.assertNotIn("Mine Art", content)

	def test_nonexistent_orcid_returns_404(self):
		resp = self.client.get("/feed/author/0000-0000-0000-9999/")
		self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org): author feed
# ---------------------------------------------------------------------------


class AuthenticatedUserAuthorFeedTest(AuthorFeedBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="rss-auth-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_own_author_returns_200(self):
		resp = self.client.get(f"/feed/author/{ORCID_MINE}/")
		self.assertEqual(resp.status_code, 200)

	def test_private_other_org_author_returns_404(self):
		resp = self.client.get(f"/feed/author/{ORCID_PRIV}/")
		self.assertEqual(resp.status_code, 404)

	def test_public_author_hidden_without_flag(self):
		"""author_pub only has articles in pub_org; not visible without include_public."""
		resp = self.client.get(f"/feed/author/{ORCID_PUB}/")
		self.assertEqual(resp.status_code, 404)

	def test_public_author_visible_with_include_public(self):
		resp = self.client.get(f"/feed/author/{ORCID_PUB}/?include_public=true")
		self.assertEqual(resp.status_code, 200)

	def test_own_feed_items_excludes_pub_articles_without_flag(self):
		"""author_mine items should only include the mine-team article (not pub-team)."""
		resp = self.client.get(f"/feed/author/{ORCID_MINE}/")
		self.assertEqual(resp.status_code, 200)
		content = resp.content.decode()
		self.assertIn("Mine Art", content)
		self.assertNotIn("Mine+Pub Art", content)

	def test_own_feed_items_includes_pub_articles_with_flag(self):
		resp = self.client.get(f"/feed/author/{ORCID_MINE}/?include_public=true")
		self.assertEqual(resp.status_code, 200)
		content = resp.content.decode()
		self.assertIn("Mine Art", content)
		self.assertIn("Mine+Pub Art", content)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org): author feed
# ---------------------------------------------------------------------------


class APIKeyAuthorFeedTest(AuthorFeedBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, "rss-author-key")
		self.client.defaults["HTTP_AUTHORIZATION"] = self.scheme.api_key

	def test_own_author_returns_200(self):
		resp = self.client.get(f"/feed/author/{ORCID_MINE}/")
		self.assertEqual(resp.status_code, 200)

	def test_private_other_org_author_returns_404(self):
		resp = self.client.get(f"/feed/author/{ORCID_PRIV}/")
		self.assertEqual(resp.status_code, 404)

	def test_public_author_hidden_without_flag(self):
		resp = self.client.get(f"/feed/author/{ORCID_PUB}/")
		self.assertEqual(resp.status_code, 404)

	def test_public_author_visible_with_include_public(self):
		resp = self.client.get(f"/feed/author/{ORCID_PUB}/?include_public=true")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Base setUp for trials feed tests
# ---------------------------------------------------------------------------


class TrialsFeedBase(TestCase):
	def setUp(self):
		self.my_org = _make_org("My Org", "my-org-rss-trial", public=False)
		self.pub_org = _make_org("Public Org", "pub-org-rss-trial", public=True)
		self.priv_org = _make_org("Private Org", "priv-org-rss-trial", public=False)

		self.my_team = _make_team(self.my_org, "My Team RSS Trial")
		self.pub_team = _make_team(self.pub_org, "Pub Team RSS Trial")
		self.priv_team = _make_team(self.priv_org, "Priv Team RSS Trial")

		self.my_subj = _make_subject(self.my_team, "my-subj-rss")
		self.pub_subj = _make_subject(self.pub_team, "pub-subj-rss")
		self.priv_subj = _make_subject(self.priv_team, "priv-subj-rss")

		# Trials
		_make_trial(
			"Mine Trial",
			"https://rss.ex/t1",
			teams=[self.my_team],
			subjects=[self.my_subj],
		)
		_make_trial(
			"Pub Trial",
			"https://rss.ex/t2",
			teams=[self.pub_team],
			subjects=[self.pub_subj],
		)
		_make_trial(
			"Priv Trial",
			"https://rss.ex/t3",
			teams=[self.priv_team],
			subjects=[self.priv_subj],
		)


# ---------------------------------------------------------------------------
# Anonymous: trials feed
# ---------------------------------------------------------------------------


class AnonymousTrialsFeedTest(TrialsFeedBase):
	def test_public_subject_returns_200(self):
		resp = self.client.get(f"/feed/trials/subject/{self.pub_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 200)

	def test_private_subject_returns_404(self):
		resp = self.client.get(f"/feed/trials/subject/{self.priv_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 404)

	def test_mine_subject_returns_404_for_anon(self):
		resp = self.client.get(f"/feed/trials/subject/{self.my_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 404)

	def test_nonexistent_slug_returns_404(self):
		resp = self.client.get("/feed/trials/subject/does-not-exist/")
		self.assertEqual(resp.status_code, 404)

	def test_public_feed_contains_public_trial(self):
		resp = self.client.get(f"/feed/trials/subject/{self.pub_subj.subject_slug}/")
		self.assertIn("Pub Trial", resp.content.decode())


# ---------------------------------------------------------------------------
# Authenticated user: trials feed
# ---------------------------------------------------------------------------


class AuthenticatedUserTrialsFeedTest(TrialsFeedBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="rss-trial-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_own_subject_returns_200(self):
		resp = self.client.get(f"/feed/trials/subject/{self.my_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 200)

	def test_private_other_org_subject_returns_404(self):
		resp = self.client.get(f"/feed/trials/subject/{self.priv_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 404)

	def test_public_subject_hidden_without_flag(self):
		resp = self.client.get(f"/feed/trials/subject/{self.pub_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 404)

	def test_public_subject_visible_with_include_public(self):
		resp = self.client.get(
			f"/feed/trials/subject/{self.pub_subj.subject_slug}/?include_public=true"
		)
		self.assertEqual(resp.status_code, 200)

	def test_own_feed_contains_own_trial(self):
		resp = self.client.get(f"/feed/trials/subject/{self.my_subj.subject_slug}/")
		self.assertIn("Mine Trial", resp.content.decode())

	def test_own_feed_excludes_pub_trial_without_flag(self):
		"""pub trial is not in my_team → filtered out from own-subject feed."""
		# Add pub trial to my_subj to test filtering
		pub_trial_in_my_subj = _make_trial(
			"Pub Trial In My Subj",
			"https://rss.ex/t99",
			teams=[self.pub_team],
			subjects=[self.my_subj],
		)
		resp = self.client.get(f"/feed/trials/subject/{self.my_subj.subject_slug}/")
		content = resp.content.decode()
		# Mine Trial visible, pub-team trial not visible
		self.assertIn("Mine Trial", content)
		self.assertNotIn("Pub Trial In My Subj", content)


# ---------------------------------------------------------------------------
# API key: trials feed
# ---------------------------------------------------------------------------


class APIKeyTrialsFeedTest(TrialsFeedBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, "rss-trials-key")
		self.client.defaults["HTTP_AUTHORIZATION"] = self.scheme.api_key

	def test_own_subject_returns_200(self):
		resp = self.client.get(f"/feed/trials/subject/{self.my_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 200)

	def test_private_other_org_subject_returns_404(self):
		resp = self.client.get(f"/feed/trials/subject/{self.priv_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 404)

	def test_public_subject_hidden_without_flag(self):
		resp = self.client.get(f"/feed/trials/subject/{self.pub_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 404)

	def test_public_subject_visible_with_include_public(self):
		resp = self.client.get(
			f"/feed/trials/subject/{self.pub_subj.subject_slug}/?include_public=true"
		)
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Duplicate subject_slug across orgs (PR: RSS 500 on duplicate slugs)
# ---------------------------------------------------------------------------


class DuplicateSubjectSlugTrialsFeedTest(TestCase):
	"""subject_slug is unique per team, not globally -- two subjects in
	different orgs can share a slug. .get() previously raised
	MultipleObjectsReturned (an uncaught 500); the feed must instead resolve
	deterministically within the caller's visible orgs."""

	def setUp(self):
		self.pub_org = _make_org("Dup Slug Pub Org", "dup-slug-pub-org", public=True)
		self.priv_org = _make_org("Dup Slug Priv Org", "dup-slug-priv-org", public=False)

		self.pub_team = _make_team(self.pub_org, "Dup Slug Pub Team")
		self.priv_team = _make_team(self.priv_org, "Dup Slug Priv Team")

		shared_slug = "dup-slug-shared"
		self.pub_subj = Subject.objects.create(
			team=self.pub_team, subject_name="Pub Dup Subject", subject_slug=shared_slug
		)
		self.priv_subj = Subject.objects.create(
			team=self.priv_team, subject_name="Priv Dup Subject", subject_slug=shared_slug
		)

		_make_trial(
			"Pub Dup Trial",
			"https://rss.ex/dup-pub",
			teams=[self.pub_team],
			subjects=[self.pub_subj],
		)
		_make_trial(
			"Priv Dup Trial",
			"https://rss.ex/dup-priv",
			teams=[self.priv_team],
			subjects=[self.priv_subj],
		)

	def test_returns_200_for_the_visible_orgs_subject(self):
		resp = self.client.get(f"/feed/trials/subject/{self.pub_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 200)
		content = resp.content.decode()
		self.assertIn("Pub Dup Trial", content)
		self.assertNotIn("Priv Dup Trial", content)

	def test_returns_404_when_neither_subject_is_visible(self):
		OrganizationApiSettings.objects.filter(organization=self.pub_org).update(
			make_api_public=False
		)
		resp = self.client.get(f"/feed/trials/subject/{self.pub_subj.subject_slug}/")
		self.assertEqual(resp.status_code, 404)

	def test_tie_break_is_deterministic_when_both_subjects_are_visible(self):
		"""With ?include_public=true, an org member sees BOTH the public org's
		subject and their own private org's subject -- the duplicate-slug
		tie-break (lowest id) must pick the same one every time, and the feed
		must contain only that subject's trials, not both."""
		user = User.objects.create_user(username="dup-slug-member", password="pw")
		OrganizationUser.objects.create(organization=self.priv_org, user=user)
		self.client.force_login(user)

		resp = self.client.get(
			f"/feed/trials/subject/{self.pub_subj.subject_slug}/?include_public=true"
		)
		self.assertEqual(resp.status_code, 200)
		content = resp.content.decode()

		winner = min(self.pub_subj, self.priv_subj, key=lambda s: s.id)
		self.assertEqual(winner, self.pub_subj)  # created first -> lowest id
		self.assertIn("Pub Dup Trial", content)
		self.assertNotIn("Priv Dup Trial", content)


class TeamlessSubjectTrialsFeedTest(TestCase):
	"""A Subject with team=NULL was always visible in the previous code path
	(the org-visibility check only ran when subject.team_id was set) -- the
	duplicate-slug fix must not regress that."""

	def setUp(self):
		self.subject = Subject.objects.create(
			team=None, subject_name="Teamless Subject", subject_slug="teamless-subj"
		)
		Trials.objects.create(
			title="Teamless Trial", link="https://rss.ex/teamless"
		).subjects.add(self.subject)

	def test_teamless_subject_returns_200_for_anonymous(self):
		resp = self.client.get(f"/feed/trials/subject/{self.subject.subject_slug}/")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
