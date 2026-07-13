"""
Template testing utilities for email components.
This file helps validate template rendering and context data preparation.
"""

import logging

from django.template import Context
from django.template.loader import get_template
from datetime import timedelta
from django.utils import timezone


class EmailTemplateTestUtils:
	"""Utilities for testing email templates with mock data."""

	@staticmethod
	def create_mock_article():
		"""Create mock article data for testing."""
		return {
			"article_id": "test-001",
			"title": "Revolutionary Multiple Sclerosis Treatment Shows Promise in Clinical Trial",
			"link": "https://example.com/article/test-001",
			"discovery_date": timezone.now() - timedelta(days=1),
			"published_date": timezone.now() - timedelta(days=2),
			"authors": {
				"exists": lambda: True,
				"all": lambda: [
					{"full_name": "Dr. Sarah Johnson", "ORCID": "0000-0000-0000-0001"},
					{"full_name": "Dr. Michael Chen", "ORCID": None},
				],
			},
			"ml_predictions": {
				"exists": lambda: True,
				"all": lambda: [
					{
						"subject": {"subject_name": "Multiple Sclerosis"},
						"gnb": 0.92,
						"lr": 0.89,
						"lsvc": 0.87,
						"mnb": 0.85,
						"probability_score": 0.89,
					}
				],
			},
		}

	@staticmethod
	def create_mock_trial():
		"""Create mock clinical trial data for testing."""
		return {
			"title": "Phase II Study of Novel DMT for Progressive MS",
			"link": "https://clinicaltrials.gov/ct2/show/NCT12345678",
			"discovery_date": timezone.now() - timedelta(days=1),
			"published_date": timezone.now() - timedelta(days=3),
			"summary": "A randomized, double-blind, placebo-controlled study evaluating the efficacy and safety of experimental drug XYZ-123 in patients with progressive multiple sclerosis.",
			"phase": "II",
			"overall_status": "Recruiting",
			"primary_completion_date": timezone.now() + timedelta(days=365),
			"estimated_enrollment": 200,
			"location": "Multi-center (US, EU)",
		}

	@staticmethod
	def create_mock_context(email_type="weekly_summary"):
		"""Create mock context data for template testing."""
		mock_site = {"domain": "brain-regeneration.com", "name": "Gregory AI"}

		mock_customsettings = {
			"title": "Gregory AI - MS Research Updates",
			"contact_email": "hello@brain-regeneration.com",
			"social_links": {
				"twitter": "https://twitter.com/gregory_ai",
				"github": "https://github.com/brunoamaral/gregory",
				"linkedin": "https://linkedin.com/company/gregory-ai",
			},
		}

		mock_subscriber = {
			"email": "subscriber@example.com",
			"first_name": "John",
			"last_name": "Doe",
		}

		context = {
			"email_type": email_type,
			"current_date": timezone.now(),
			"site": mock_site,
			"customsettings": mock_customsettings,
			"subscriber": mock_subscriber,
			"articles": [EmailTemplateTestUtils.create_mock_article()],
			"trials": [EmailTemplateTestUtils.create_mock_trial()],
			"now": timezone.now(),
		}

		if email_type == "admin_summary":
			context["admin"] = "admin@brain-regeneration.com"
			context["title"] = mock_customsettings["title"]

		return context

	@staticmethod
	def test_component_rendering(component_name, context=None):
		"""Test rendering of a specific component."""
		if context is None:
			context = EmailTemplateTestUtils.create_mock_context()

		try:
			template = get_template(f"emails/components/{component_name}.html")
			rendered = template.render(Context(context))
			return {
				"success": True,
				"rendered": rendered,
				"length": len(rendered),
				"has_content": bool(rendered.strip()),
			}
		except Exception as e:
			return {"success": False, "error": str(e), "error_type": type(e).__name__}

	@staticmethod
	def test_email_template_rendering(template_name, email_type="weekly_summary"):
		"""Test rendering of a complete email template."""
		context = EmailTemplateTestUtils.create_mock_context(email_type)

		try:
			template = get_template(f"emails/{template_name}.html")
			rendered = template.render(Context(context))
			return {
				"success": True,
				"rendered": rendered,
				"length": len(rendered),
				"has_content": bool(rendered.strip()),
				"contains_html": "<html>" in rendered,
				"contains_body": "<body>" in rendered,
			}
		except Exception as e:
			return {"success": False, "error": str(e), "error_type": type(e).__name__}

	@staticmethod
	def validate_component_variables(component_name, required_vars=None):
		"""Validate that a component has access to required template variables."""
		if required_vars is None:
			required_vars = []

		# Create minimal context with only required variables
		minimal_context = {}
		for var in required_vars:
			if var == "article":
				minimal_context[var] = EmailTemplateTestUtils.create_mock_article()
			elif var == "trial":
				minimal_context[var] = EmailTemplateTestUtils.create_mock_trial()
			elif var == "site":
				minimal_context[var] = {"domain": "test.com"}
			else:
				minimal_context[var] = f"test_{var}"

		return EmailTemplateTestUtils.test_component_rendering(
			component_name, minimal_context
		)


# Example usage for manual testing:
if __name__ == "__main__":
	# Test article card component
	result = EmailTemplateTestUtils.test_component_rendering("article_card")
	logging.info("Article Card Test: %s", result["success"])

	# Test trial card component
	result = EmailTemplateTestUtils.test_component_rendering("trial_card")
	logging.info("Trial Card Test: %s", result["success"])

	# Test full weekly summary template
	result = EmailTemplateTestUtils.test_email_template_rendering("weekly_summary_new")
	logging.info("Weekly Summary Test: %s", result["success"])

	# Test admin summary template
	result = EmailTemplateTestUtils.test_email_template_rendering(
		"admin_summary_new", "admin_summary"
	)
	logging.info("Admin Summary Test: %s", result["success"])

	# Test trial notification template
	result = EmailTemplateTestUtils.test_email_template_rendering(
		"trial_notification_new", "trial_notification"
	)
	logging.info("Trial Notification Test: %s", result["success"])
