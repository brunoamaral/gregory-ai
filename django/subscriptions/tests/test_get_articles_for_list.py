import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from gregory.models import Articles, ArticleSubjectRelevance, Subject, Team
from organizations.models import Organization
from subscriptions.models import Lists
from subscriptions.management.commands.utils.subscription import get_articles_for_list


class GetArticlesForListTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Test Org", slug="test-org")
		self.team = Team.objects.create(
			name="Test Team", organization=self.org, slug="test-team"
		)
		self.subject_a = Subject.objects.create(
			subject_name="Subject A", team=self.team, subject_slug="subject-a"
		)
		self.subject_b = Subject.objects.create(
			subject_name="Subject B", team=self.team, subject_slug="subject-b"
		)
		self.admin_list = Lists.objects.create(
			list_name="Admin Summary List",
			admin_summary=True,
			team=self.team,
		)
		self.admin_list.subjects.add(self.subject_a, self.subject_b)

		self.article = Articles.objects.create(
			title="Test Article",
			link="https://example.com/article/1",
		)
		self.article.subjects.add(self.subject_a, self.subject_b)

	def test_no_reviews_included(self):
		"""Articles with no ArticleSubjectRelevance records are included."""
		qs = get_articles_for_list(self.admin_list)
		self.assertIn(self.article, qs)

	def test_one_subject_reviewed_one_not_included(self):
		"""Article with one reviewed and one unreviewed list-subject is included."""
		ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject_a, is_relevant=True
		)
		qs = get_articles_for_list(self.admin_list)
		self.assertIn(self.article, qs)

	def test_all_subjects_reviewed_excluded(self):
		"""Article with all list-subjects reviewed is excluded."""
		ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject_a, is_relevant=True
		)
		ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject_b, is_relevant=False
		)
		qs = get_articles_for_list(self.admin_list)
		self.assertNotIn(self.article, qs)

	def test_review_for_subject_outside_list_does_not_exclude(self):
		"""A review for a subject not in the list does not cause exclusion."""
		subject_c = Subject.objects.create(
			subject_name="Subject C", team=self.team, subject_slug="subject-c"
		)
		self.article.subjects.add(subject_c)
		ArticleSubjectRelevance.objects.create(
			article=self.article, subject=subject_c, is_relevant=False
		)
		qs = get_articles_for_list(self.admin_list)
		self.assertIn(self.article, qs)

	def test_old_articles_excluded(self):
		"""Articles older than 30 days are not returned regardless of review status."""
		old_article = Articles.objects.create(
			title="Old Article",
			link="https://example.com/article/old",
		)
		# discovery_date is auto_now_add; bypass it with update() to backdate
		Articles.objects.filter(pk=old_article.pk).update(
			discovery_date=timezone.now() - timedelta(days=31)
		)
		old_article.subjects.add(self.subject_a)
		qs = get_articles_for_list(self.admin_list)
		self.assertNotIn(old_article, qs)
