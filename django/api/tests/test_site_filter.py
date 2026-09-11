import csv
import io

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from gregory.models import Articles, Sources, Subject, Team, Trials
from organizations.models import Organization
from sitesettings.models import CustomSetting

from api.tests.visibility_helpers import private_site_publishing, publish_subjects


class SiteFilterTest(TestCase):
	"""``site_id`` filters Articles/Trials to content carrying a subject in
	that Django Site's scope (``CustomSetting.scope_subjects``), deduplicating
	when an object carries more than one such subject, and composing with
	(never replacing) subject-based visibility."""

	def setUp(self):
		self.user = User.objects.create_user(username="sitetest", password="12345")
		self.public_org = Organization.objects.create(
			name="Public Org", slug="public-org"
		)
		self.public_org.add_user(self.user)

		self.private_org = Organization.objects.create(
			name="Private Org", slug="private-org"
		)

		self.source = Sources.objects.create(
			name="Site Filter Source", source_for="science paper"
		)

		self.team1 = Team.objects.create(
			organization=self.public_org, name="Team One", slug="team-one"
		)
		self.private_team = Team.objects.create(
			organization=self.private_org, name="Private Team", slug="private-team"
		)

		self.public_subject = Subject.objects.create(
			team=self.team1,
			subject_name="Site Filter Public Subject",
			subject_slug="site-filter-public-subject",
		)
		# A second subject published to the SAME site, via a SECOND,
		# api_public=False CustomSetting row -- CustomSetting.site is a plain
		# FK, not OneToOne, so one site can carry more than one settings row.
		# filter_site must union scope_subjects across all of them (it is a
		# content filter, not a visibility check), while anonymous visibility
		# must still only ever count the api_public=True row's scope.
		self.overlap_subject = Subject.objects.create(
			team=self.team1,
			subject_name="Site Filter Overlap Subject",
			subject_slug="site-filter-overlap-subject",
		)
		# A subject scoped to an entirely different site, owned by a
		# different organisation -- never part of site_a's scope at all.
		self.priv_subject = Subject.objects.create(
			team=self.private_team,
			subject_name="Site Filter Private Subject",
			subject_slug="site-filter-private-subject",
		)

		self.site_a = publish_subjects(
			self.public_subject, organization=self.public_org, name="Site A"
		)
		CustomSetting.objects.create(
			site=self.site_a, title="Site A overlap settings", api_public=False
		).scope_subjects.add(self.overlap_subject)
		private_site_publishing(
			self.priv_subject, organization=self.private_org, name="Private Site"
		)

		# Carries both of site_a's subjects -- must not be duplicated by the
		# Exists() filter when both match.
		self.shared_article = Articles.objects.create(
			title="Shared Article",
			summary="Shared summary",
			link="https://example.com/shared-article",
			kind="science paper",
		)
		self.shared_article.sources.add(self.source)
		self.shared_article.subjects.add(self.public_subject, self.overlap_subject)

		# Reachable via site_a's scope only through the PRIVATE CustomSetting
		# row -- visible to an authenticated public_org member (whose scope
		# unions every CustomSetting row on a site their org owns), but never
		# to an anonymous caller (whose scope only ever counts api_public
		# rows).
		self.overlap_article = Articles.objects.create(
			title="Overlap Article",
			summary="Overlap summary",
			link="https://example.com/overlap-article",
			kind="science paper",
		)
		self.overlap_article.sources.add(self.source)
		self.overlap_article.subjects.add(self.overlap_subject)

		# Not in site_a's scope at all -- a different org's site entirely.
		self.private_article = Articles.objects.create(
			title="Private Article",
			summary="Private summary",
			link="https://example.com/private-article",
			kind="science paper",
		)
		self.private_article.sources.add(self.source)
		self.private_article.subjects.add(self.priv_subject)

		self.shared_trial = Trials.objects.create(title="Shared Trial")
		self.shared_trial.subjects.add(self.public_subject, self.overlap_subject)

		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		self.anon_client = APIClient()

	def test_article_with_two_scoped_subjects_not_duplicated(self):
		response = self.client.get(
			reverse("articles-list"), {"site_id": self.site_a.id, "page_size": 50}
		)
		ids = [item["article_id"] for item in response.data["results"]]
		self.assertEqual(ids.count(self.shared_article.article_id), 1)

	def test_trial_with_two_scoped_subjects_not_duplicated(self):
		response = self.client.get(
			reverse("trials-list"), {"site_id": self.site_a.id, "page_size": 50}
		)
		ids = [item["trial_id"] for item in response.data["results"]]
		self.assertEqual(ids.count(self.shared_trial.trial_id), 1)

	def test_site_with_no_customsetting_row_returns_empty(self):
		empty_site = Site.objects.create(domain="empty.example.com", name="Empty")
		response = self.client.get(
			reverse("articles-list"), {"site_id": empty_site.id}
		)
		self.assertEqual(response.data["count"], 0)

	def test_content_scoped_to_a_different_site_excluded(self):
		response = self.client.get(
			reverse("articles-list"), {"site_id": self.site_a.id, "page_size": 50}
		)
		ids = {item["article_id"] for item in response.data["results"]}
		self.assertNotIn(self.private_article.article_id, ids)

	def test_authenticated_member_sees_content_via_any_customsetting_row_on_owned_site(self):
		# public_org owns site_a, so self.user's visibility union includes
		# BOTH CustomSetting rows on it -- public and the private overlap one
		# -- and filter_site's own scope resolution must agree.
		response = self.client.get(
			reverse("articles-list"), {"site_id": self.site_a.id, "page_size": 50}
		)
		ids = {item["article_id"] for item in response.data["results"]}
		self.assertIn(self.overlap_article.article_id, ids)

	def test_anonymous_caller_excluded_from_the_private_row_despite_site_scope(self):
		# The site_id filter alone would include overlap_article (it unions
		# every CustomSetting row for site_a), but anonymous visibility only
		# ever counts the api_public=True row -- this is the private-row
		# scope leak Phase 3's regression test guards, exercised again here
		# through the content filter rather than visibility directly.
		response = self.anon_client.get(
			reverse("articles-list"), {"site_id": self.site_a.id, "page_size": 50}
		)
		self.assertEqual(response.status_code, 200)
		ids = {item["article_id"] for item in response.data["results"]}
		self.assertIn(self.shared_article.article_id, ids)
		self.assertNotIn(self.overlap_article.article_id, ids)
		self.assertNotIn(self.private_article.article_id, ids)

	def test_site_id_csv_all_results_row_count_matches_orm_count(self):
		# self.user's visibility union is exactly {public_subject,
		# overlap_subject} (the only subjects on any CustomSetting row for
		# site_a, the only site public_org owns), and site_a's own scope is
		# the same set, so the two constraints agree rather than narrow
		# each other further.
		expected_count = (
			Articles.objects.filter(
				subjects__id__in=[self.public_subject.id, self.overlap_subject.id]
			)
			.distinct()
			.count()
		)
		response = self.client.get(
			reverse("articles-list"),
			{"site_id": self.site_a.id, "format": "csv", "all_results": "true"},
		)
		content = b"".join(response.streaming_content).decode("utf-8")
		rows = list(csv.reader(io.StringIO(content)))
		self.assertEqual(len(rows) - 1, expected_count)
