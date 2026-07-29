from django.contrib.sites.models import Site
from django.test import TestCase

from templates.emails.components.content_organizer import (
	EmailContentOrganizer,
	get_optimized_email_context,
)


class TrialNotificationNeverReceivesArticlesTest(TestCase):
	"""send_trials_notification never passes articles= to
	get_optimized_email_context (it only builds a trials context), so the
	organizer's own has_articles check short-circuits before dispatching to
	any email-type-specific article organizer. This pins that behaviour so
	EmailContentOrganizer._organize_trial_notification_articles (and the
	_filter_high_confidence helper it alone used) can be deleted as dead
	code without silently changing what trial notification emails contain."""

	@classmethod
	def setUpTestData(cls):
		cls.site = Site.objects.get_or_create(
			id=40, defaults={"domain": "trialnotif.example.com", "name": "TN"}
		)[0]

	def test_no_articles_kwarg_yields_empty_article_lists(self):
		context = get_optimized_email_context(
			email_type="trial_notification",
			trials=None,
			site=self.site,
		)
		self.assertEqual(context["articles"], [])
		self.assertEqual(context["additional_articles"], [])

	def test_organize_articles_short_circuits_before_dispatch(self):
		"""Directly exercises EmailContentOrganizer.organize_articles the way
		prepare_optimized_context calls it for a trial_notification with no
		articles: the empty-queryset early exit must fire before any
		email-type-specific branch runs."""
		from gregory.models import Articles

		organizer = EmailContentOrganizer(email_type="trial_notification")
		result = organizer.organize_articles(Articles.objects.none())
		self.assertEqual(result["featured_articles"], [])
		self.assertEqual(result["regular_articles"], [])
		self.assertEqual(result["total_count"], 0)
