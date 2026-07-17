from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	OrganizationApiSettings,
	Subject,
	Team,
)
from rss.sitemaps import SiteArticlesSitemap
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

		# Site config: frontend site publishes subject A (+ the private
		# subject, which must be silently dropped); other site publishes B.
		cls.config = CustomSetting.objects.create(
			site=cls.site, title="Frontend settings", generate_sitemap=True
		)
		cls.config.sitemap_subjects.add(cls.subject_a, cls.private_subject)
		cls.other_config = CustomSetting.objects.create(
			site=cls.other_site, title="Other settings", generate_sitemap=True
		)
		cls.other_config.sitemap_subjects.add(cls.subject_b)

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
		SiteArticlesSitemap.limit = 2  # 5 subject-A articles + article_both → 3 pages
		self.addCleanup(setattr, SiteArticlesSitemap, "limit", original_limit)
		url = reverse("site-sitemap-index", kwargs={"site_id": self.site.pk})
		body = self.client.get(url).content.decode()
		self.assertEqual(body.count("<sitemap>"), 3)
		self.assertIn(self._section_url(self.site.pk), body)
		self.assertIn("?p=3", body)
		self.assertNotIn("?p=4", body)
