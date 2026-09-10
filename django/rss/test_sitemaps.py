from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	Authors,
	OrganizationApiSettings,
	Subject,
	Team,
	Trials,
)
from rss.sitemaps import SiteArticlesSitemap, SiteAuthorsSitemap, SiteTrialsSitemap
from sitesettings.models import CustomSetting


class SiteSitemapTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.site = Site.objects.create(domain="frontend.example.com", name="Frontend")
		cls.other_site = Site.objects.create(domain="other.example.com", name="Other")

		cls.public_org = Organization.objects.create(name="Pub", slug="pub-org")
		# A post_save signal on Organization already created an
		# OrganizationApiSettings row (make_api_public=False) — flip it here
		# rather than creating a duplicate.
		OrganizationApiSettings.objects.filter(organization=cls.public_org).update(
			make_api_public=True
		)
		cls.private_org = Organization.objects.create(name="Priv", slug="priv-org")
		# signal-created OrganizationApiSettings row stays make_api_public=False

		cls.team = Team.objects.create(
			organization=cls.public_org, name="T1", slug="t1"
		)
		cls.private_team = Team.objects.create(
			organization=cls.private_org, name="T2", slug="t2"
		)

		cls.subject_a = Subject.objects.create(
			subject_name="Regen", subject_slug="regen", team=cls.team
		)
		cls.subject_b = Subject.objects.create(
			subject_name="MS", subject_slug="ms", team=cls.team
		)
		cls.private_subject = Subject.objects.create(
			subject_name="Secret", subject_slug="secret", team=cls.private_team
		)

		def make_article(title, *subjects):
			article = Articles.objects.create(
				title=title, link=f"https://example.org/{title}", kind="science paper"
			)
			article.teams.add(cls.team)
			for subject in subjects:
				article.subjects.add(subject)
			return article

		cls.articles_a = [make_article(f"a{i}", cls.subject_a) for i in range(5)]
		cls.articles_b = [make_article(f"b{i}", cls.subject_b) for i in range(3)]
		cls.article_both = make_article("both", cls.subject_a, cls.subject_b)
		cls.article_private = Articles.objects.create(
			title="private", link="https://example.org/priv", kind="science paper"
		)
		cls.article_private.subjects.add(cls.private_subject)

		# Cross-team tagging: an article owned only by the private team but
		# tagged with a subject that belongs to the public team. Before
		# Phase 4 of site-scoped API visibility the article's own team
		# ownership gated this out; now subject curation alone decides, so
		# it is published. Kept as a fixture because that is exactly the
		# behaviour change worth pinning.
		cls.article_wrong_team = Articles.objects.create(
			title="wrong-team", link="https://example.org/wrong-team", kind="science paper"
		)
		cls.article_wrong_team.teams.add(cls.private_team)
		cls.article_wrong_team.subjects.add(cls.subject_a)

		# No teams M2M at all, tagged with a published subject. This is what
		# every one of the 64 rows in the production delta looks like: the
		# old ownership join dropped them for having nothing to match, not
		# for being private. Pinned separately from article_wrong_team
		# because a future join on teams would silently re-exclude them
		# while the private-team case kept passing.
		cls.article_teamless = Articles.objects.create(
			title="teamless", link="https://example.org/teamless", kind="science paper"
		)
		cls.article_teamless.subjects.add(cls.subject_a)

		def make_trial(title, *subjects, team=cls.team, **fields):
			trial = Trials.objects.create(
				title=title, link=f"https://registry.example.org/{title}", **fields
			)
			trial.teams.add(team)
			for subject in subjects:
				trial.subjects.add(subject)
			return trial

		cls.trials_a = [make_trial(f"ta{i}", cls.subject_a) for i in range(3)]
		cls.trial_b = make_trial("tb", cls.subject_b)
		cls.trial_both = make_trial("tboth", cls.subject_a, cls.subject_b)
		# Same cross-team case as article_wrong_team above.
		cls.trial_wrong_team = make_trial(
			"twrong", cls.subject_a, team=cls.private_team
		)

		# Trials' equivalent of article_teamless above.
		cls.trial_teamless = Trials.objects.create(
			title="tteamless", link="https://registry.example.org/tteamless"
		)
		cls.trial_teamless.subjects.add(cls.subject_a)

		# recruitment_status_normalized is editable=False and recomputed
		# from the raw status on every save(), so seed the raw value.
		cls.trial_recruiting = make_trial(
			"trecruiting", cls.subject_a, recruitment_status="Recruiting"
		)
		cls.trial_completed = make_trial(
			"tcompleted", cls.subject_a, recruitment_status="Completed"
		)
		# No raw status at all → recruitment_status_normalized stays NULL.
		cls.trial_no_status = make_trial("tnostatus", cls.subject_a)

		# Authors fixtures live under their own site/org/team/subject rather
		# than reusing site/subject_a/team above: SiteAuthorsSitemap shares
		# subject_ids with SiteArticlesSitemap for a given site, so any
		# qualifying article added under subject_a would also grow the
		# articles section and break its hardcoded page-count assertions
		# below (e.g. test_index_lists_one_entry_per_page).
		cls.authors_org = Organization.objects.create(name="AuthOrg", slug="auth-org")
		OrganizationApiSettings.objects.filter(organization=cls.authors_org).update(
			make_api_public=True
		)
		cls.authors_private_org = Organization.objects.create(
			name="AuthPrivOrg", slug="auth-priv-org"
		)
		cls.authors_team = Team.objects.create(
			organization=cls.authors_org, name="AT", slug="at"
		)
		cls.authors_private_team = Team.objects.create(
			organization=cls.authors_private_org, name="APT", slug="apt"
		)
		cls.authors_subject = Subject.objects.create(
			subject_name="AuthorsSubj", subject_slug="authors-subj", team=cls.authors_team
		)
		cls.authors_site = Site.objects.create(
			domain="authors.example.com", name="AuthorsSite"
		)
		cls.authors_config = CustomSetting.objects.create(
			site=cls.authors_site,
			title="Authors settings",
			generate_sitemap=True,
			api_public=True,
		)
		cls.authors_config.sitemap_subjects.add(cls.authors_subject)
		cls.authors_config.scope_subjects.add(cls.authors_subject)

		def make_authors_article(suffix, team):
			article = Articles.objects.create(
				title=f"author-fixture-{suffix}",
				link=f"https://example.org/author-fixture-{suffix}",
				kind="science paper",
			)
			article.teams.add(team)
			article.subjects.add(cls.authors_subject)
			return article

		# Clears SiteAuthorsSitemap.MIN_ARTICLES (10).
		cls.author_qualified = Authors.objects.create(
			given_name="Prolific", family_name="Researcher", ORCID="0000-0001-0000-0001"
		)
		for i in range(10):
			make_authors_article(f"q{i}", cls.authors_team).authors.add(
				cls.author_qualified
			)

		# Below MIN_ARTICLES.
		cls.author_thin = Authors.objects.create(
			given_name="Sparse", family_name="Researcher", ORCID="0000-0001-0000-0002"
		)
		for i in range(5):
			make_authors_article(f"t{i}", cls.authors_team).authors.add(cls.author_thin)

		# 10 articles tagged with the site's subject but owned only by a
		# private team — the same cross-team case as article_wrong_team and
		# trial_wrong_team above. Since Phase 4 these count towards
		# MIN_ARTICLES and the author qualifies.
		cls.author_private_leak = Authors.objects.create(
			given_name="Hidden", family_name="Researcher", ORCID="0000-0001-0000-0003"
		)
		for i in range(10):
			make_authors_article(f"p{i}", cls.authors_private_team).authors.add(
				cls.author_private_leak
			)

		# Clears MIN_ARTICLES but carries no ORCID, so has no page to list.
		cls.author_no_orcid = Authors.objects.create(
			given_name="Anon", family_name="Researcher", ORCID=None
		)
		for i in range(10):
			make_authors_article(f"n{i}", cls.authors_team).authors.add(
				cls.author_no_orcid
			)

		# Site config: frontend site publishes subject A (+ the private
		# subject, which must be silently dropped); other site publishes B.
		cls.config = CustomSetting.objects.create(
			site=cls.site,
			title="Frontend settings",
			generate_sitemap=True,
			api_public=True,
		)
		cls.config.sitemap_subjects.add(cls.subject_a, cls.private_subject)
		# scope_subjects, not sitemap_subjects, is what makes a subject
		# publicly visible. private_subject is deliberately left out of
		# scope, mirroring what migration sitesettings/0019 does to a public
		# site's scope: a site may curate a subject into its sitemap that it
		# is not allowed to publish, and the sitemap must drop it.
		cls.config.scope_subjects.add(cls.subject_a)
		cls.other_config = CustomSetting.objects.create(
			site=cls.other_site,
			title="Other settings",
			generate_sitemap=True,
			api_public=True,
		)
		cls.other_config.sitemap_subjects.add(cls.subject_b)
		cls.other_config.scope_subjects.add(cls.subject_b)

	def setUp(self):
		cache.clear()

	def _section_url(self, site_id, section="articles"):
		return reverse(
			"site-sitemap-section",
			kwargs={"site_id": site_id, "section": section},
		)

	def test_section_lists_configured_subjects_on_frontend_domain(self):
		body = self.client.get(self._section_url(self.site.pk)).content.decode()
		for article in self.articles_a:
			self.assertIn(
				f"https://frontend.example.com/articles/{article.pk}/", body
			)
		for article in self.articles_b:
			self.assertNotIn(f"/articles/{article.pk}/", body)
		self.assertNotIn(f"/articles/{self.article_private.pk}/", body)
		self.assertIn("<lastmod>", body)

	def test_article_is_listed_on_its_subject_tag_alone_whatever_team_owns_it(self):
		# Phase 4 of site-scoped API visibility: team ownership no longer
		# decides visibility, subject curation does. This article's teams M2M
		# points only at a private-organisation team, which the old
		# teams__organization_id__in guard used to drop — but it carries a
		# subject the site publishes, so it is published. Ownership stays a
		# staff-permissions concept (the ADMIN path); it is not a publication
		# rule. The guard that still applies is subject scope, covered by
		# test_curated_subject_outside_the_public_scope_is_dropped below.
		body = self.client.get(self._section_url(self.site.pk)).content.decode()
		self.assertIn(
			f"https://frontend.example.com/articles/{self.article_wrong_team.pk}/",
			body,
		)

	def test_article_with_no_team_at_all_is_listed(self):
		# The production delta is entirely rows like this one, so it gets its
		# own assertion rather than riding on the private-team case above.
		body = self.client.get(self._section_url(self.site.pk)).content.decode()
		self.assertIn(
			f"https://frontend.example.com/articles/{self.article_teamless.pk}/",
			body,
		)

	def test_curated_subject_outside_the_public_scope_is_dropped(self):
		# The load-bearing guard now that ownership is gone: the site curates
		# private_subject into sitemap_subjects, but no api_public site has it
		# in scope_subjects, so nothing tagged with it reaches the sitemap.
		self.assertIn(self.private_subject, self.config.sitemap_subjects.all())
		body = self.client.get(self._section_url(self.site.pk)).content.decode()
		self.assertNotIn(f"/articles/{self.article_private.pk}/", body)

	def test_sites_expose_disjoint_slices_except_shared_tags(self):
		# The anti-competition property: same DB, different subjects →
		# different sitemaps. Only article_both (tagged A and B) overlaps.
		body_a = self.client.get(self._section_url(self.site.pk)).content.decode()
		body_b = self.client.get(self._section_url(self.other_site.pk)).content.decode()
		self.assertIn(f"https://frontend.example.com/articles/{self.article_both.pk}/", body_a)
		self.assertIn(f"https://other.example.com/articles/{self.article_both.pk}/", body_b)
		for article in self.articles_a:
			self.assertNotIn(f"/articles/{article.pk}/", body_b)

	def test_article_with_two_qualifying_subjects_listed_once(self):
		self.config.sitemap_subjects.add(self.subject_b)
		body = self.client.get(self._section_url(self.site.pk)).content.decode()
		needle = f"https://frontend.example.com/articles/{self.article_both.pk}/"
		self.assertEqual(body.count(needle), 1)

	def test_relevant_only_restricts_to_marked_articles(self):
		ArticleSubjectRelevance.objects.create(
			article=self.articles_a[0], subject=self.subject_a, is_relevant=True
		)
		self.config.sitemap_relevant_only = True
		self.config.save()
		body = self.client.get(self._section_url(self.site.pk)).content.decode()
		self.assertIn(f"/articles/{self.articles_a[0].pk}/", body)
		self.assertNotIn(f"/articles/{self.articles_a[1].pk}/", body)

	def test_switch_off_404s(self):
		self.config.generate_sitemap = False
		self.config.save()
		self.assertEqual(
			self.client.get(self._section_url(self.site.pk)).status_code, 404
		)

	def test_no_public_subjects_404s(self):
		self.config.sitemap_subjects.set([self.private_subject])
		self.assertEqual(
			self.client.get(self._section_url(self.site.pk)).status_code, 404
		)

	def test_unknown_site_and_section_and_page_404(self):
		self.assertEqual(self.client.get(self._section_url(99999)).status_code, 404)
		self.assertEqual(
			self.client.get(self._section_url(self.site.pk, section="nope")).status_code,
			404,
		)
		self.assertEqual(
			self.client.get(self._section_url(self.site.pk) + "?p=99").status_code,
			404,
		)

	def test_index_lists_one_entry_per_page(self):
		original_limit = SiteArticlesSitemap.limit
		# 5 subject-A articles + article_both + article_wrong_team → 4 pages.
		SiteArticlesSitemap.limit = 2
		self.addCleanup(setattr, SiteArticlesSitemap, "limit", original_limit)
		url = reverse("site-sitemap-index", kwargs={"site_id": self.site.pk})
		body = self.client.get(url).content.decode()
		self.assertEqual(body.count("<sitemap>"), 4)
		self.assertIn(self._section_url(self.site.pk), body)
		self.assertIn("?p=4", body)
		self.assertNotIn("?p=5", body)

	# --- trials section (opt-in per site) ---

	def _enable_trials(self):
		self.config.sitemap_include_trials = True
		self.config.save()

	def _visible_trial_count(self):
		"""Trials the site's trials section should list, counted independently
		of the sitemap code under test.

		Subject membership alone — the teams=self.team half this used to
		carry was the old ownership rule, and it only kept agreeing because
		no fixture was team-less. trial_teamless made the disagreement
		visible.
		"""
		return (
			Trials.objects.filter(subjects__in=[self.subject_a])
			.distinct()
			.count()
		)

	def test_trials_section_404s_until_the_site_opts_in(self):
		self.assertEqual(
			self.client.get(self._section_url(self.site.pk, "trials")).status_code,
			404,
		)

	def test_trials_section_lists_configured_subjects_on_frontend_domain(self):
		self._enable_trials()
		body = self.client.get(
			self._section_url(self.site.pk, "trials")
		).content.decode()
		for trial in self.trials_a:
			self.assertIn(f"https://frontend.example.com/trials/{trial.pk}/", body)
		self.assertNotIn(f"/trials/{self.trial_b.pk}/", body)
		# The two sections stay disjoint: no article URLs leak into trials.xml.
		self.assertNotIn("/articles/", body)
		self.assertIn("<lastmod>", body)

	def test_trial_is_listed_on_its_subject_tag_alone_whatever_team_owns_it(self):
		# Trials follow the same Phase 4 rule as articles — see
		# test_article_is_listed_on_its_subject_tag_alone_whatever_team_owns_it.
		self._enable_trials()
		body = self.client.get(
			self._section_url(self.site.pk, "trials")
		).content.decode()
		self.assertIn(
			f"https://frontend.example.com/trials/{self.trial_wrong_team.pk}/", body
		)

	def test_trial_with_no_team_at_all_is_listed(self):
		# See test_article_with_no_team_at_all_is_listed — all 64 trials in
		# the measured production delta are team-less, not private.
		self._enable_trials()
		body = self.client.get(
			self._section_url(self.site.pk, "trials")
		).content.decode()
		self.assertIn(
			f"https://frontend.example.com/trials/{self.trial_teamless.pk}/", body
		)

	def test_trial_with_two_qualifying_subjects_listed_once(self):
		self._enable_trials()
		self.config.sitemap_subjects.add(self.subject_b)
		body = self.client.get(
			self._section_url(self.site.pk, "trials")
		).content.decode()
		needle = f"https://frontend.example.com/trials/{self.trial_both.pk}/"
		self.assertEqual(body.count(needle), 1)

	def test_relevant_only_does_not_filter_trials(self):
		# Trials carry no relevance judgement, so the article-only switch
		# must not silently empty the trials section.
		self._enable_trials()
		self.config.sitemap_relevant_only = True
		self.config.save()
		body = self.client.get(
			self._section_url(self.site.pk, "trials")
		).content.decode()
		for trial in self.trials_a:
			self.assertIn(f"/trials/{trial.pk}/", body)

	def test_index_gains_trials_entries_only_when_enabled(self):
		url = reverse("site-sitemap-index", kwargs={"site_id": self.site.pk})
		self.assertNotIn(
			self._section_url(self.site.pk, "trials"),
			self.client.get(url).content.decode(),
		)

		self._enable_trials()
		cache.clear()  # the index is cache_page'd
		original_limit = SiteTrialsSitemap.limit
		SiteTrialsSitemap.limit = 2
		self.addCleanup(setattr, SiteTrialsSitemap, "limit", original_limit)
		body = self.client.get(url).content.decode()
		self.assertIn(self._section_url(self.site.pk, "trials"), body)
		# 9 subject-A trials at 2 per page → 5 pages, + 1 articles page.
		# Derived rather than hardcoded so adding a fixture doesn't turn
		# into a puzzle about which number to bump; the equality below is
		# only there so a derivation that collapsed to zero would fail loudly.
		trial_pages = -(-self._visible_trial_count() // 2)
		self.assertEqual(body.count("<sitemap>"), 1 + trial_pages)
		self.assertEqual(trial_pages, 5)

	def test_no_status_selection_lists_every_status(self):
		self._enable_trials()
		body = self.client.get(
			self._section_url(self.site.pk, "trials")
		).content.decode()
		for trial in (
			self.trial_recruiting,
			self.trial_completed,
			self.trial_no_status,
		):
			self.assertIn(f"/trials/{trial.pk}/", body)

	def test_status_selection_narrows_the_section(self):
		self._enable_trials()
		self.config.sitemap_trial_statuses = ["recruiting"]
		self.config.save()
		body = self.client.get(
			self._section_url(self.site.pk, "trials")
		).content.decode()
		self.assertIn(f"/trials/{self.trial_recruiting.pk}/", body)
		self.assertNotIn(f"/trials/{self.trial_completed.pk}/", body)
		# Untagged-status trials drop out once a selection is made.
		self.assertNotIn(f"/trials/{self.trial_no_status.pk}/", body)
		# ...and so do the fixtures that never had a raw status.
		self.assertNotIn(f"/trials/{self.trials_a[0].pk}/", body)

	def test_status_selection_accepts_several_statuses(self):
		self._enable_trials()
		self.config.sitemap_trial_statuses = ["recruiting", "completed"]
		self.config.save()
		body = self.client.get(
			self._section_url(self.site.pk, "trials")
		).content.decode()
		self.assertIn(f"/trials/{self.trial_recruiting.pk}/", body)
		self.assertIn(f"/trials/{self.trial_completed.pk}/", body)
		self.assertNotIn(f"/trials/{self.trial_no_status.pk}/", body)

	def test_status_selection_does_not_leak_past_subject_scoping(self):
		# The status filter narrows, never widens: a recruiting trial whose
		# only subject is outside this site's curated set still must not
		# appear, however well it matches the requested status.
		self._enable_trials()
		self.trial_b.recruitment_status = "Recruiting"
		self.trial_b.save()
		self.config.sitemap_trial_statuses = ["recruiting"]
		self.config.save()
		body = self.client.get(
			self._section_url(self.site.pk, "trials")
		).content.decode()
		self.assertNotIn(f"/trials/{self.trial_b.pk}/", body)

	def test_status_selection_does_not_affect_articles(self):
		self._enable_trials()
		self.config.sitemap_trial_statuses = ["recruiting"]
		self.config.save()
		body = self.client.get(self._section_url(self.site.pk)).content.decode()
		for article in self.articles_a:
			self.assertIn(f"/articles/{article.pk}/", body)

	def test_trials_switch_is_per_site(self):
		self._enable_trials()
		self.assertEqual(
			self.client.get(self._section_url(self.site.pk, "trials")).status_code,
			200,
		)
		self.assertEqual(
			self.client.get(
				self._section_url(self.other_site.pk, "trials")
			).status_code,
			404,
		)

	# --- authors section (opt-in per site) ---

	def _enable_authors(self):
		self.authors_config.sitemap_include_authors = True
		self.authors_config.save()

	def test_authors_section_404s_until_the_site_opts_in(self):
		self.assertEqual(
			self.client.get(
				self._section_url(self.authors_site.pk, "authors")
			).status_code,
			404,
		)

	def test_authors_section_lists_qualifying_author_by_orcid(self):
		self._enable_authors()
		body = self.client.get(
			self._section_url(self.authors_site.pk, "authors")
		).content.decode()
		self.assertIn(
			f"https://authors.example.com/authors/{self.author_qualified.ORCID}/",
			body,
		)
		# ORCID-keyed, never the numeric author_id the base class's inherited
		# location() would have built.
		self.assertNotIn(f"/authors/{self.author_qualified.pk}/", body)
		self.assertIn("<lastmod>", body)

	def test_author_below_threshold_excluded(self):
		self._enable_authors()
		body = self.client.get(
			self._section_url(self.authors_site.pk, "authors")
		).content.decode()
		self.assertNotIn(f"/authors/{self.author_thin.ORCID}/", body)

	def test_author_reaching_threshold_via_subject_tagged_articles_qualifies(self):
		# Same Phase 4 rule as articles and trials: these ten articles are
		# owned by a private-organisation team but tagged with the site's
		# subject, so they count towards MIN_ARTICLES and the author is
		# listed. What still excludes an author is the subject tag itself —
		# an article outside scope_subjects contributes nothing.
		self._enable_authors()
		body = self.client.get(
			self._section_url(self.authors_site.pk, "authors")
		).content.decode()
		self.assertIn(f"/authors/{self.author_private_leak.ORCID}/", body)

	def test_author_without_orcid_excluded(self):
		self._enable_authors()
		body = self.client.get(
			self._section_url(self.authors_site.pk, "authors")
		).content.decode()
		# The author otherwise clears MIN_ARTICLES on public, subject-matching
		# articles alone — only the missing ORCID excludes them.
		self.assertNotIn("/authors/None/", body)
		self.assertFalse(
			SiteAuthorsSitemap(self.authors_site, [self.authors_subject.pk])
			.get_queryset()
			.filter(pk=self.author_no_orcid.pk)
			.exists()
		)

	def test_authors_switch_is_per_site(self):
		self._enable_authors()
		self.assertEqual(
			self.client.get(
				self._section_url(self.authors_site.pk, "authors")
			).status_code,
			200,
		)
		self.assertEqual(
			self.client.get(self._section_url(self.site.pk, "authors")).status_code,
			404,
		)

	def test_index_gains_authors_entries_only_when_enabled(self):
		url = reverse(
			"site-sitemap-index", kwargs={"site_id": self.authors_site.pk}
		)
		self.assertNotIn(
			self._section_url(self.authors_site.pk, "authors"),
			self.client.get(url).content.decode(),
		)

		self._enable_authors()
		cache.clear()  # the index is cache_page'd
		body = self.client.get(url).content.decode()
		self.assertIn(self._section_url(self.authors_site.pk, "authors"), body)
