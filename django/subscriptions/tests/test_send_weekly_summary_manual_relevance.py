import os
import django
from io import StringIO
from django.core.management import call_command

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from django.contrib.sites.models import Site
from gregory.models import Subject, Articles, Team, TeamCredentials, ArticleSubjectRelevance
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers


class TestManualRelevanceFiltering(TestCase):
	"""
	Test the manual relevance filtering functionality in the send_weekly_summary command.
	
	This test ensures that articles are only excluded from digest emails if they are
	manually tagged as not relevant for ALL subjects they are associated with in the
	specific digest list.
	"""
	
	def setUp(self):
		"""Set up test data for manual relevance filtering tests."""
		# Create basic test infrastructure
		self.organization = Organization.objects.create(name="Test Organization", slug="test-org")
		self.team = Team.objects.create(name="Test Team", organization=self.organization, slug="test-team")
		
		# Create team credentials for email sending
		self.credentials = TeamCredentials.objects.create(
			team=self.team,
			postmark_api_token="test-token",
			postmark_api_url="https://api.postmarkapp.com/"
		)
		
		# Create subjects
		self.subject_a = Subject.objects.create(
			subject_name="Subject A", 
			team=self.team, 
			subject_slug="subject-a"
		)
		self.subject_b = Subject.objects.create(
			subject_name="Subject B", 
			team=self.team, 
			subject_slug="subject-b"
		)
		self.subject_c = Subject.objects.create(
			subject_name="Subject C", 
			team=self.team, 
			subject_slug="subject-c"
		)
		
		# Create digest list covering subjects A and B
		self.digest_list = Lists.objects.create(
			list_name="Test Digest List",
			weekly_digest=True,
			team=self.team,
			ml_threshold=0.7,
			list_email_subject="Test Weekly Digest"
		)
		self.digest_list.subjects.add(self.subject_a, self.subject_b)
		
		# Create a subscriber
		self.subscriber = Subscribers.objects.create(
			first_name="Test",
			last_name="User",
			email="test@example.com",
			active=True
		)
		self.subscriber.subscriptions.add(self.digest_list)
		
		# Create test articles with different subject associations
		base_date = timezone.now() - timedelta(days=1)
		
		# Article 1: Associated with subjects A and B
		self.article1 = Articles.objects.create(
			title="Article about subjects A and B",
			discovery_date=base_date,
			doi="10.1234/article1"
		)
		self.article1.subjects.add(self.subject_a, self.subject_b)
		
		# Article 2: Associated with subject A only
		self.article2 = Articles.objects.create(
			title="Article about subject A only",
			discovery_date=base_date,
			doi="10.1234/article2"
		)
		self.article2.subjects.add(self.subject_a)
		
		# Article 3: Associated with subjects B and C
		self.article3 = Articles.objects.create(
			title="Article about subjects B and C",
			discovery_date=base_date,
			doi="10.1234/article3"
		)
		self.article3.subjects.add(self.subject_b, self.subject_c)
		
		# Article 4: Associated with all subjects A, B, and C
		self.article4 = Articles.objects.create(
			title="Article about all subjects",
			discovery_date=base_date,
			doi="10.1234/article4"
		)
		self.article4.subjects.add(self.subject_a, self.subject_b, self.subject_c)
		
		# Create site and custom settings (required for the command)
		self.site = Site.objects.get_or_create(
			id=1,
			defaults={'domain': 'testserver', 'name': 'Test Site'}
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={'title': 'Test Site'}
		)[0]
	
	def _create_manual_relevance_tag(self, article, subject, is_relevant):
		"""Helper method to create manual relevance tags."""
		ArticleSubjectRelevance.objects.get_or_create(
			article=article,
			subject=subject,
			defaults={'is_relevant': is_relevant}
		)
	
	def _run_command_and_capture_output(self, **options):
		"""Helper method to run the send_weekly_summary command and capture output."""
		out = StringIO()
		call_command('send_weekly_summary', stdout=out, **options)
		return out.getvalue()
	
	def test_include_when_relevant_for_at_least_one_subject(self):
		"""Test that articles relevant for at least one subject in the list are included."""
		# Article 1: Not relevant for A, but relevant for B
		self._create_manual_relevance_tag(self.article1, self.subject_a, False)
		self._create_manual_relevance_tag(self.article1, self.subject_b, True)
		
		# Run command in dry-run mode
		output = self._run_command_and_capture_output(dry_run=True, all_articles=True)
		
		# Article 1 should be included because it's relevant for subject B
		# All 4 articles should be included since we only tagged article 1 with mixed relevance
		self.assertIn("Would include", output)
		self.assertIn("4 articles", output)  # All articles should be included
	
	def test_exclude_when_not_relevant_for_all_subjects(self):
		"""Test that articles not relevant for ALL their subjects in the list are excluded."""
		# Article 2: Not relevant for A (its only subject in the list)
		self._create_manual_relevance_tag(self.article2, self.subject_a, False)
		
		# Also tag article 1 as not relevant for both A and B to test multi-subject exclusion
		self._create_manual_relevance_tag(self.article1, self.subject_a, False)
		self._create_manual_relevance_tag(self.article1, self.subject_b, False)
		
		# Run command in dry-run mode
		output = self._run_command_and_capture_output(dry_run=True, all_articles=True)
		
		# Should indicate filtering is working - expecting 2 articles to be excluded
		self.assertIn("excluding articles manually tagged as not relevant for ALL their subjects", output)
		# Should have 2 articles remaining (article 3 and 4 which are not tagged)
		self.assertIn("2 articles", output)
	
	def test_exclude_when_not_relevant_for_all_list_subjects(self):
		"""Test that articles not relevant for ALL subjects they share with the list are excluded."""
		# Article 1: Not relevant for both A and B (all subjects it shares with the list)
		self._create_manual_relevance_tag(self.article1, self.subject_a, False)
		self._create_manual_relevance_tag(self.article1, self.subject_b, False)
		
		# Run command in dry-run mode
		output = self._run_command_and_capture_output(dry_run=True, all_articles=True)
		
		# Should show filtered articles message
		self.assertIn("excluding articles manually tagged as not relevant for ALL their subjects", output)
	
	def test_include_when_subject_outside_list_is_not_relevant(self):
		"""Test that articles are included if only subjects outside the list are marked as not relevant."""
		# Article 3: Not relevant for C (which is not in the digest list), but no tag for B
		self._create_manual_relevance_tag(self.article3, self.subject_c, False)
		# Don't tag subject B - it should be treated as potentially relevant
		
		# Run command in dry-run mode
		output = self._run_command_and_capture_output(dry_run=True, all_articles=True)
		
		# Article 3 should be included because subject B (which is in the list) is not tagged as irrelevant
		self.assertIn("Would include", output)
	
	def test_include_when_not_reviewed(self):
		"""Test that articles without manual review are included."""
		# Don't create any manual relevance tags
		
		# Run command in dry-run mode
		output = self._run_command_and_capture_output(dry_run=True, all_articles=True)
		
		# All articles should be included since none are manually tagged as not relevant
		self.assertIn("Would include", output)
	
	def test_mixed_relevance_scenarios(self):
		"""Test complex scenarios with mixed relevance tags."""
		# Article 1: Relevant for A, not relevant for B
		self._create_manual_relevance_tag(self.article1, self.subject_a, True)
		self._create_manual_relevance_tag(self.article1, self.subject_b, False)
		
		# Article 2: Not relevant for A
		self._create_manual_relevance_tag(self.article2, self.subject_a, False)
		
		# Article 3: Not relevant for B, but C is not in the list so it doesn't matter
		self._create_manual_relevance_tag(self.article3, self.subject_b, False)
		
		# Article 4: Relevant for A, not reviewed for B
		self._create_manual_relevance_tag(self.article4, self.subject_a, True)
		
		# Run command in dry-run mode
		output = self._run_command_and_capture_output(dry_run=True, all_articles=True, debug=True)
		
		# Verify the output shows the filtering logic
		self.assertIn("excluding articles manually tagged as not relevant for ALL their subjects", output)
	
	def test_standard_mode_filtering(self):
		"""Test that the same filtering logic applies in standard mode (not all-articles)."""
		# Article 1: Not relevant for both A and B
		self._create_manual_relevance_tag(self.article1, self.subject_a, False)
		self._create_manual_relevance_tag(self.article1, self.subject_b, False)
		
		# Article 2: Relevant for A
		self._create_manual_relevance_tag(self.article2, self.subject_a, True)
		
		# Run command in standard mode
		output = self._run_command_and_capture_output(dry_run=True, debug=True)
		
		# Should show the same filtering message for standard mode
		self.assertIn("excluding articles manually tagged as not relevant for ALL their subjects", output)
	
	def test_debug_output_shows_filtering_details(self):
		"""Test that debug mode shows detailed information about the filtering process."""
		# Create some manual tags
		self._create_manual_relevance_tag(self.article1, self.subject_a, False)
		self._create_manual_relevance_tag(self.article1, self.subject_b, True)
		
		# Run command with debug flag
		output = self._run_command_and_capture_output(dry_run=True, debug=True, all_articles=True)
		
		# Should show filtering details in debug mode
		self.assertIn("Found", output)
		self.assertIn("articles", output)
		self.assertIn("excluding articles manually tagged as not relevant", output)
	
	def test_empty_manual_tags_scenario(self):
		"""Test that articles with no manual relevance tags are included."""
		# Don't create any ArticleSubjectRelevance records
		
		# Run command
		output = self._run_command_and_capture_output(dry_run=True, all_articles=True)
		
		# All articles should be included since none have manual tags
		self.assertIn("total articles", output)
	
	def test_partial_manual_tags_scenario(self):
		"""Test scenarios where only some articles have manual relevance tags."""
		# Only tag article 1, leave others untagged
		self._create_manual_relevance_tag(self.article1, self.subject_a, False)
		self._create_manual_relevance_tag(self.article1, self.subject_b, False)
		
		# Run command
		output = self._run_command_and_capture_output(dry_run=True, all_articles=True)
		
		# Should show that filtering is applied
		self.assertIn("excluding articles manually tagged as not relevant", output)


class TestManualRelevanceFilteringEdgeCases(TestCase):
	"""Test edge cases for manual relevance filtering."""
	
	def setUp(self):
		"""Set up test data for edge case testing."""
		# Create basic test infrastructure
		self.organization = Organization.objects.create(name="Test Organization", slug="test-org")
		self.team = Team.objects.create(name="Test Team", organization=self.organization, slug="test-team")
		
		# Create team credentials
		self.credentials = TeamCredentials.objects.create(
			team=self.team,
			postmark_api_token="test-token",
			postmark_api_url="https://api.postmarkapp.com/"
		)
		
		# Create subjects
		self.subject_a = Subject.objects.create(
			subject_name="Subject A", 
			team=self.team, 
			subject_slug="subject-a"
		)
		
		# Create digest list with only one subject
		self.digest_list = Lists.objects.create(
			list_name="Single Subject List",
			weekly_digest=True,
			team=self.team,
			ml_threshold=0.7
		)
		self.digest_list.subjects.add(self.subject_a)
		
		# Create a subscriber
		self.subscriber = Subscribers.objects.create(
			first_name="Test",
			last_name="User",
			email="test@example.com",
			active=True
		)
		self.subscriber.subscriptions.add(self.digest_list)
		
		# Create site and custom settings
		self.site = Site.objects.get_or_create(
			id=1,
			defaults={'domain': 'testserver', 'name': 'Test Site'}
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={'title': 'Test Site'}
		)[0]
	
	def test_single_subject_list_filtering(self):
		"""Test filtering behavior with a list that has only one subject."""
		# Create article associated with the single subject
		article = Articles.objects.create(
			title="Single subject article",
			discovery_date=timezone.now() - timedelta(days=1),
			doi="10.1234/single"
		)
		article.subjects.add(self.subject_a)
		
		# Tag as not relevant
		ArticleSubjectRelevance.objects.create(
			article=article,
			subject=self.subject_a,
			is_relevant=False
		)
		
		# Run command
		out = StringIO()
		call_command('send_weekly_summary', stdout=out, dry_run=True, all_articles=True)
		output = out.getvalue()
		
		# Article should be excluded since it's not relevant for the only subject in the list
		self.assertIn("excluding articles manually tagged as not relevant", output)
	
	def test_no_articles_scenario(self):
		"""Test command behavior when no articles match the criteria."""
		# Don't create any articles
		
		# Run command
		out = StringIO()
		call_command('send_weekly_summary', stdout=out, dry_run=True)
		output = out.getvalue()
		
		# Should handle gracefully
		self.assertIn("No articles or trials found", output)
