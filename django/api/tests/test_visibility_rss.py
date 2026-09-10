"""
Tests for the site-scoped RSS feeds (Phase 5 of site-scoped API visibility).

Before Phase 5, both feeds read gregory.visibility.visible_subject_ids(request)
directly on the unprefixed URLs (feed/author/<orcid>/,
feed/trials/subject/<slug>/), so the same URL returned different content to
an anonymous caller, a signed-in member, and an API key. Phase 5 replaced
that: those URLs now permanently redirect (301) onto
feed/sites/<site_id>/..., which is scoped to the REQUESTED SITE's
CustomSetting.scope_subjects and CustomSetting.rss_enabled -- never to the
caller. See rss/views.py's module docstring and PHASE-5-RSS-SITE-SCOPE-PLAN.md.

This file exercises exactly that property -- isolation between two sites'
scopes, and invariance across caller archetypes for one site -- reusing the
org/site/API-key scaffolding this suite already has. Redirect mechanics,
the author-<link> element, and item-level content (which subjects show up
in which site's feed) are covered in rss/tests.py instead; this file is for
the interaction between the site-scoped feed and the rest of the
site-visibility model (organisations, API keys, api_public).

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_rss
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser

from api.models import APIAccessScheme
from gregory.models import (
	Articles,
	Authors,
	OrganizationApiSettings,
	OrganizationSite,
	Subject,
	Team,
	Trials,
)
from sitesettings.models import CustomSetting

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


def _make_site(name, org, *, api_public, rss_enabled=True, scope_subjects=()):
	"""A Site owned by `org`, with a CustomSetting carrying its RSS scope.

	The org link goes through OrganizationSite purely so these tests can
	build a signed-in-member / API-key caller and show their identity
	changes nothing about the feed -- the feed views themselves never
	consult it.
	"""
	slug = name.lower().replace(" ", "-")
	site = Site.objects.create(domain=f"{slug}.example.com", name=name)
	OrganizationSite.objects.create(organization=org, site=site, is_default=True)
	settings_row = CustomSetting.objects.create(
		site=site,
		title=f"{name} settings",
		api_public=api_public,
		rss_enabled=rss_enabled,
	)
	for subject in scope_subjects:
		settings_row.scope_subjects.add(subject)
	return site


def _make_article(title, link, teams=(), authors=(), subjects=()):
	art = Articles.objects.create(title=title, link=link)
	for t in teams:
		art.teams.add(t)
	for a in authors:
		art.authors.add(a)
	for s in subjects:
		art.subjects.add(s)
	return art


def _make_trial(title, link, teams=(), subjects=()):
	trial = Trials.objects.create(title=title, link=link)
	for t in teams:
		trial.teams.add(t)
	for s in subjects:
		trial.subjects.add(s)
	return trial


def _make_api_scheme(org, name, site=None):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f"{name}@example.com",
		organization=org,
		site=site,
		ip_addresses="",
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


def _author_feed_url(site_id, orcid):
	return reverse(
		"site_articles_by_author_feed", kwargs={"site_id": site_id, "orcid": orcid}
	)


def _trials_feed_url(site_id, subject_slug):
	return reverse(
		"site_trials_by_subject_feed",
		kwargs={"site_id": site_id, "subject_slug": subject_slug},
	)


# ---------------------------------------------------------------------------
# Isolation: site A's feed never carries site B's scope, or vice versa
# ---------------------------------------------------------------------------


class SiteScopedFeedIsolationTest(TestCase):
	"""
	The case org-level (and, before Phase 5, caller-level) visibility could
	not express: two sites, each with its own subject, each rss_enabled.
	Site A's feed must carry only site A's content, whatever the caller and
	whatever site B publishes -- the leak canary for RSS.
	"""

	def setUp(self):
		self.org_a = _make_org("Org A", "org-a-rss-iso")
		self.org_b = _make_org("Org B", "org-b-rss-iso")
		self.team_a = _make_team(self.org_a, "Team A RSS Iso")
		self.team_b = _make_team(self.org_b, "Team B RSS Iso")
		self.subj_a = _make_subject(self.team_a, "subj-a-rss-iso")
		self.subj_b = _make_subject(self.team_b, "subj-b-rss-iso")

		self.site_a = _make_site(
			"Site A RSS Iso", self.org_a, api_public=False, scope_subjects=[self.subj_a]
		)
		self.site_b = _make_site(
			"Site B RSS Iso", self.org_b, api_public=True, scope_subjects=[self.subj_b]
		)

		self.author_a = _make_author("A", "Author", "0000-0003-0001-0001")
		self.author_b = _make_author("B", "Author", "0000-0003-0002-0002")
		_make_article(
			"Article A",
			"https://rss-iso.ex/a1",
			teams=[self.team_a],
			authors=[self.author_a],
			subjects=[self.subj_a],
		)
		_make_article(
			"Article B",
			"https://rss-iso.ex/a2",
			teams=[self.team_b],
			authors=[self.author_b],
			subjects=[self.subj_b],
		)
		_make_trial(
			"Trial A", "https://rss-iso.ex/t1", teams=[self.team_a], subjects=[self.subj_a]
		)
		_make_trial(
			"Trial B", "https://rss-iso.ex/t2", teams=[self.team_b], subjects=[self.subj_b]
		)

	def test_site_as_trials_feed_excludes_site_bs_subject(self):
		resp = self.client.get(_trials_feed_url(self.site_a.pk, self.subj_a.subject_slug))
		self.assertEqual(resp.status_code, 200)
		self.assertIn("Trial A", resp.content.decode())

	def test_site_a_cannot_serve_site_bs_subject_at_all(self):
		# subj_b was never added to site_a's scope, so requesting it through
		# site_a's own feed URL 404s -- the subject/site pairing is what's
		# checked, not just "is this subject visible somewhere".
		resp = self.client.get(_trials_feed_url(self.site_a.pk, self.subj_b.subject_slug))
		self.assertEqual(resp.status_code, 404)

	def test_site_bs_author_feed_excludes_site_as_article(self):
		# author_a has no article under subj_b, so site B's feed 404s for them.
		resp = self.client.get(_author_feed_url(self.site_b.pk, self.author_a.ORCID))
		self.assertEqual(resp.status_code, 404)

	def test_site_as_author_feed_serves_only_site_as_content(self):
		resp = self.client.get(_author_feed_url(self.site_a.pk, self.author_a.ORCID))
		self.assertEqual(resp.status_code, 200)
		self.assertIn("Article A", resp.content.decode())


# ---------------------------------------------------------------------------
# Invariance: caller identity changes nothing about a given site's feed
# ---------------------------------------------------------------------------


class SiteScopedFeedCallerInvarianceTest(TestCase):
	"""
	The defining behaviour change of Phase 5. Pinned explicitly so a
	well-meaning "restore per-caller filtering" change fails loudly here
	rather than silently reintroducing cache-poisoning-by-identity on a
	surface that's actually cached and has no notion of identity.
	"""

	def setUp(self):
		self.owning_org = _make_org("Owning Org RSS Inv", "owning-org-rss-inv")
		self.other_org = _make_org("Other Org RSS Inv", "other-org-rss-inv")
		self.team = _make_team(self.owning_org, "Team RSS Inv")
		self.subject = _make_subject(self.team, "subj-rss-inv")
		self.site = _make_site(
			"Site RSS Inv",
			self.owning_org,
			api_public=False,
			scope_subjects=[self.subject],
		)
		# A second, unrelated site the "other" API key is bound to, so that
		# key resolves to a real but disjoint scope rather than an
		# unresolvable one.
		self.other_site = _make_site(
			"Other Site RSS Inv", self.other_org, api_public=False, scope_subjects=[]
		)

		self.author = _make_author("Inv", "Author", "0000-0003-0003-0003")
		_make_article(
			"Inv Article",
			"https://rss-inv.ex/a1",
			teams=[self.team],
			authors=[self.author],
			subjects=[self.subject],
		)
		_make_trial(
			"Inv Trial", "https://rss-inv.ex/t1", teams=[self.team], subjects=[self.subject]
		)

	def _bodies_for(self, url):
		bodies = []

		# Anonymous.
		bodies.append(self.client.get(url).content.decode())

		# Signed-in member of an UNRELATED organisation.
		user = User.objects.create_user(username="rss-inv-member", password="pw")
		OrganizationUser.objects.create(organization=self.other_org, user=user)
		self.client.force_login(user)
		bodies.append(self.client.get(url).content.decode())
		self.client.logout()

		# API key bound to an UNRELATED site.
		scheme = _make_api_scheme(self.other_org, "rss-inv-key", site=self.other_site)
		self.client.defaults["HTTP_AUTHORIZATION"] = scheme.api_key
		bodies.append(self.client.get(url).content.decode())
		del self.client.defaults["HTTP_AUTHORIZATION"]

		return bodies

	def test_author_feed_identical_across_callers(self):
		url = _author_feed_url(self.site.pk, self.author.ORCID)
		bodies = self._bodies_for(url)
		self.assertEqual(len(set(bodies)), 1, bodies)
		self.assertIn("Inv Article", bodies[0])

	def test_trials_feed_identical_across_callers(self):
		url = _trials_feed_url(self.site.pk, self.subject.subject_slug)
		bodies = self._bodies_for(url)
		self.assertEqual(len(set(bodies)), 1, bodies)
		self.assertIn("Inv Trial", bodies[0])


# ---------------------------------------------------------------------------
# rss_enabled / CustomSetting gating
# ---------------------------------------------------------------------------


class RssEnabledGateTest(TestCase):
	def setUp(self):
		self.org = _make_org("Gate Org", "gate-org-rss")
		self.team = _make_team(self.org, "Gate Team RSS")
		self.subject = _make_subject(self.team, "gate-subj-rss")
		_make_trial(
			"Gate Trial", "https://rss-gate.ex/t1", teams=[self.team], subjects=[self.subject]
		)

	def test_site_with_rss_disabled_404s(self):
		site = _make_site(
			"Disabled Site RSS",
			self.org,
			api_public=True,
			rss_enabled=False,
			scope_subjects=[self.subject],
		)
		resp = self.client.get(_trials_feed_url(site.pk, self.subject.subject_slug))
		self.assertEqual(resp.status_code, 404)

	def test_site_with_no_customsetting_404s(self):
		bare_site = Site.objects.create(domain="bare-rss-gate.example.com", name="Bare")
		resp = self.client.get(_trials_feed_url(bare_site.pk, self.subject.subject_slug))
		self.assertEqual(resp.status_code, 404)

	def test_unknown_site_id_404s(self):
		resp = self.client.get(_trials_feed_url(999999, self.subject.subject_slug))
		self.assertEqual(resp.status_code, 404)

	def test_site_with_rss_enabled_serves_the_feed(self):
		site = _make_site(
			"Enabled Site RSS",
			self.org,
			api_public=True,
			rss_enabled=True,
			scope_subjects=[self.subject],
		)
		resp = self.client.get(_trials_feed_url(site.pk, self.subject.subject_slug))
		self.assertEqual(resp.status_code, 200)
		self.assertIn("Gate Trial", resp.content.decode())


# ---------------------------------------------------------------------------
# A private site's own feed is not gated by api_public
# ---------------------------------------------------------------------------


class PrivateSiteFeedTest(TestCase):
	"""
	rss_enabled is the whole gate; api_public plays no part, mirroring how
	a site-bound API key reads its own scope_subjects "whether or not the
	site is api_public" (gregory.visibility.visible_subject_ids). Pinned so
	a later change doesn't quietly ALSO require api_public=True for the
	feed -- that would be a different, narrower design than the one this
	phase built, and should be a deliberate decision, not a regression.
	"""

	def setUp(self):
		self.org = _make_org("Private Feed Org", "private-feed-org-rss")
		self.team = _make_team(self.org, "Private Feed Team RSS")
		self.subject = _make_subject(self.team, "private-feed-subj-rss")
		self.site = _make_site(
			"Private Feed Site RSS",
			self.org,
			api_public=False,
			rss_enabled=True,
			scope_subjects=[self.subject],
		)
		_make_trial(
			"Private Feed Trial",
			"https://rss-priv.ex/t1",
			teams=[self.team],
			subjects=[self.subject],
		)

	def test_anonymous_caller_with_no_credential_reads_the_private_sites_feed(self):
		resp = self.client.get(_trials_feed_url(self.site.pk, self.subject.subject_slug))
		self.assertEqual(resp.status_code, 200)
		self.assertIn("Private Feed Trial", resp.content.decode())
