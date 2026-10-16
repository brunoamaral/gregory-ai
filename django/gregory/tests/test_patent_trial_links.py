"""Tests for the sponsor-timing signal: classify() buckets, detect_patent_trial_links,
the PatentTrialLink admin and the admin-summary helper."""

import datetime
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from organizations.models import Organization, OrganizationUser

from gregory.management.commands.detect_patent_trial_links import classify, trial_window
from gregory.models import (
	PatentApplicant,
	PatentCategoryAssignment,
	PatentTrialLink,
	Patents,
	Sources,
	Sponsor,
	Subject,
	Team,
	TeamCategory,
	TrialCategoryAssignment,
	Trials,
)

User = get_user_model()
D = datetime.date


class ClassifyTests(SimpleTestCase):
	window = (D(2020, 1, 1), D(2022, 6, 30))

	def test_before_start_is_no_signal(self):
		self.assertIsNone(classify(D(2019, 12, 31), self.window))

	def test_during_includes_both_boundaries(self):
		self.assertEqual(classify(D(2020, 1, 1), self.window), ("during", None))
		self.assertEqual(classify(D(2022, 6, 30), self.window), ("during", None))

	def test_after_reports_days(self):
		self.assertEqual(classify(D(2022, 7, 10), self.window), ("after", 10))

	def test_24_month_edge_is_inclusive(self):
		self.assertEqual(classify(D(2024, 6, 30), self.window)[0], "after")
		self.assertIsNone(classify(D(2024, 7, 1), self.window))

	def test_after_months_is_configurable(self):
		self.assertIsNone(classify(D(2023, 8, 1), self.window, after_months=12))
		self.assertEqual(classify(D(2023, 6, 30), self.window, after_months=12)[0], "after")

	def test_open_window_has_no_upper_bound(self):
		open_window = (D(2020, 1, 1), None)
		self.assertEqual(classify(D(2035, 1, 1), open_window), ("during", None))
		self.assertIsNone(classify(D(2019, 1, 1), open_window))

	def test_no_window_or_date(self):
		self.assertIsNone(classify(D(2021, 1, 1), None))
		self.assertIsNone(classify(None, self.window))


class LinkTestCase(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org", slug="link-org")
		self.team = Team.objects.create(organization=self.org, name="Team", slug="link-team")
		self.subject = Subject.objects.create(subject_name="MS", subject_slug="ms-l", team=self.team)
		self.other_subject = Subject.objects.create(subject_name="PD", subject_slug="pd-l", team=self.team)
		self.sponsor = Sponsor.objects.create(name="Acme Neuro", slug="acme-neuro")
		self.category = TeamCategory.objects.create(team=self.team, category_name="Molecule", category_terms=["x"])
		self._n = 0

	def make_trial(self, status="Completed", start=D(2020, 1, 1), end=D(2022, 6, 30), subject=None,
				   sponsor=None, completion_attr="primary_completion_date", published=None):
		self._n += 1
		trial = Trials.objects.create(
			title=f"Trial {self._n}",
			link=f"https://example.com/t{self._n}",
			recruitment_status=status,
			date_enrollement=start,
			published_date=published,
			**({completion_attr: end} if end else {}),
		)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=sponsor or self.sponsor)
		trial.subjects.add(subject or self.subject)
		return trial

	def make_patent(self, priority=D(2021, 3, 1), subject=None, sponsor=None, individual=False):
		self._n += 1
		patent = Patents.objects.create(
			family_id=str(self._n), title=f"Patent {self._n}", earliest_priority_date=priority
		)
		patent.subjects.add(subject or self.subject)
		PatentApplicant.objects.create(
			patent=patent,
			sponsor=None if individual else (sponsor or self.sponsor),
			raw_name="X",
			sequence=1,
			is_individual=individual,
		)
		return patent

	def detect(self, **kwargs):
		out = StringIO()
		call_command("detect_patent_trial_links", stdout=out, **kwargs)
		return out.getvalue()

	def link(self, patent, trial):
		return PatentTrialLink.objects.filter(patent=patent, trial=trial).first()


class TrialWindowTests(LinkTestCase):
	def test_ongoing_status_is_open_even_with_an_estimated_end(self):
		trial = self.make_trial(status="Recruiting", end=D(2021, 1, 1))
		self.assertEqual(trial_window(trial), (D(2020, 1, 1), None))

	def test_falls_back_to_completion_date(self):
		trial = self.make_trial(end=D(2022, 1, 1), completion_attr="completion_date")
		self.assertEqual(trial_window(trial), (D(2020, 1, 1), D(2022, 1, 1)))

	def test_primary_completion_wins_over_completion(self):
		trial = self.make_trial(end=D(2021, 6, 1))
		Trials.objects.filter(pk=trial.pk).update(completion_date=D(2023, 1, 1))
		trial.refresh_from_db()
		self.assertEqual(trial_window(trial)[1], D(2021, 6, 1))

	def test_start_falls_back_to_published_date(self):
		trial = self.make_trial(start=None, published=timezone.make_aware(datetime.datetime(2019, 5, 5)))
		self.assertEqual(trial_window(trial)[0], D(2019, 5, 5))

	def test_unknown_dates_and_withdrawn_give_no_window(self):
		self.assertIsNone(trial_window(self.make_trial(end=None)))
		self.assertIsNone(trial_window(self.make_trial(start=None)))
		self.assertIsNone(trial_window(self.make_trial(status="Withdrawn")))


class DetectTests(LinkTestCase):
	def test_during(self):
		patent, trial = self.make_patent(D(2021, 3, 1)), self.make_trial()
		self.detect()
		link = self.link(patent, trial)
		self.assertEqual((link.timing, link.days_after_completion), ("during", None))
		self.assertEqual(link.sponsor, self.sponsor)

	def test_after(self):
		patent, trial = self.make_patent(D(2022, 7, 10)), self.make_trial()
		self.detect()
		link = self.link(patent, trial)
		self.assertEqual((link.timing, link.days_after_completion), ("after", 10))

	def test_outside_the_window_makes_no_link(self):
		self.make_patent(D(2019, 1, 1))  # before the trial
		self.make_patent(D(2030, 1, 1))  # long after
		self.make_trial()
		self.detect()
		self.assertEqual(PatentTrialLink.objects.count(), 0)

	def test_after_months_option(self):
		patent, trial = self.make_patent(D(2023, 8, 1)), self.make_trial()
		self.detect(after_months=12)
		self.assertIsNone(self.link(patent, trial))
		self.detect(after_months=24)
		self.assertIsNotNone(self.link(patent, trial))

	def test_open_window_for_ongoing_trial(self):
		patent, trial = self.make_patent(D(2026, 1, 1)), self.make_trial(status="Recruiting", end=None)
		self.detect()
		self.assertEqual(self.link(patent, trial).timing, "during")

	def test_shared_subject_is_required(self):
		patent = self.make_patent(subject=self.subject)
		trial = self.make_trial(subject=self.other_subject)
		self.detect()
		self.assertIsNone(self.link(patent, trial))

	def test_other_sponsors_trials_are_ignored(self):
		other = Sponsor.objects.create(name="Other", slug="other")
		patent, trial = self.make_patent(), self.make_trial(sponsor=other)
		self.detect()
		self.assertIsNone(self.link(patent, trial))

	def test_individual_applicants_make_no_links(self):
		self.make_patent(individual=True)
		self.make_trial()
		self.detect()
		self.assertEqual(PatentTrialLink.objects.count(), 0)

	def test_basis_is_subject_without_a_shared_category(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		self.assertEqual(self.link(patent, trial).basis, "subject")

	def test_basis_is_category_with_a_shared_category(self):
		patent, trial = self.make_patent(), self.make_trial()
		PatentCategoryAssignment.objects.create(patents=patent, teamcategory=self.category)
		TrialCategoryAssignment.objects.create(trials=trial, teamcategory=self.category)
		self.detect()
		self.assertEqual(self.link(patent, trial).basis, "category")

	def test_basis_upgrades_when_a_category_appears(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		PatentCategoryAssignment.objects.create(patents=patent, teamcategory=self.category)
		TrialCategoryAssignment.objects.create(trials=trial, teamcategory=self.category)
		self.detect()
		self.assertEqual(PatentTrialLink.objects.count(), 1)
		self.assertEqual(self.link(patent, trial).basis, "category")

	def test_rerun_is_idempotent(self):
		self.make_patent(), self.make_trial()
		self.detect()
		self.detect()
		self.assertEqual(PatentTrialLink.objects.count(), 1)

	def test_stale_link_is_removed_when_the_trial_no_longer_qualifies(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		Trials.objects.filter(pk=trial.pk).update(primary_completion_date=D(2020, 2, 1))
		self.detect(after_months=1)
		self.assertIsNone(self.link(patent, trial))

	def test_dry_run_writes_nothing(self):
		self.make_patent(), self.make_trial()
		out = self.detect(dry_run=True)
		self.assertEqual(PatentTrialLink.objects.count(), 0)
		self.assertIn("Would create 1", out)

	def test_patent_id_option(self):
		first, second = self.make_patent(), self.make_patent()
		trial = self.make_trial()
		self.detect(patent_id=first.pk)
		self.assertIsNotNone(self.link(first, trial))
		self.assertIsNone(self.link(second, trial))

	def test_recent_option_skips_old_patents(self):
		old, trial = self.make_patent(), self.make_trial()
		Patents.objects.filter(pk=old.pk).update(discovery_date=timezone.now() - datetime.timedelta(days=90))
		self.detect(recent=True, days=30)
		self.assertIsNone(self.link(old, trial))
		self.detect(recent=True, days=120)
		self.assertIsNotNone(self.link(old, trial))


class SuppressionTests(LinkTestCase):
	def test_suppressed_link_survives_reset_and_redetection(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		PatentTrialLink.objects.filter(patent=patent, trial=trial).update(suppressed=True)

		self.detect(reset=True)
		self.detect()

		link = self.link(patent, trial)
		self.assertTrue(link.suppressed)
		self.assertEqual(PatentTrialLink.objects.count(), 1)

	def test_suppressed_link_is_not_modified_by_detection(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		PatentTrialLink.objects.filter(patent=patent, trial=trial).update(suppressed=True)
		PatentCategoryAssignment.objects.create(patents=patent, teamcategory=self.category)
		TrialCategoryAssignment.objects.create(trials=trial, teamcategory=self.category)
		self.detect()
		self.assertEqual(self.link(patent, trial).basis, "subject")

	def test_suppressed_link_is_kept_when_the_trial_stops_qualifying(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		PatentTrialLink.objects.filter(patent=patent, trial=trial).update(suppressed=True)
		Trials.objects.filter(pk=trial.pk).update(primary_completion_date=D(2020, 2, 1))
		self.detect(after_months=1)
		self.assertIsNotNone(self.link(patent, trial))

	def test_reset_removes_unsuppressed_links_only(self):
		patent, kept = self.make_patent(), self.make_trial()
		dropped = self.make_trial()
		self.detect()
		PatentTrialLink.objects.filter(patent=patent, trial=kept).update(suppressed=True)
		Trials.objects.filter(pk=dropped.pk).update(primary_completion_date=D(2020, 2, 1))
		self.detect(reset=True, after_months=1)
		self.assertEqual(PatentTrialLink.objects.count(), 1)
		self.assertTrue(self.link(patent, kept).suppressed)


class DetectionHistoryTests(LinkTestCase):
	"""PatentTrialLink keeps a history for people's changes (an editor
	suppressing a link). Detection is not a person: it writes none, and its
	removals stay a single DELETE rather than a row-by-row delete with a
	history row each."""

	def history(self):
		return PatentTrialLink.history.count()

	def delete_statements(self, queries):
		return [q["sql"] for q in queries if q["sql"].startswith('DELETE FROM "patent_trial_links"')]

	def test_creating_links_records_no_history(self):
		self.make_patent(), self.make_trial()
		self.detect()
		self.assertEqual(PatentTrialLink.objects.count(), 1)
		self.assertEqual(self.history(), 0)

	def test_updating_a_link_records_no_history(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		PatentTrialLink.objects.update(basis="subject")
		TrialCategoryAssignment.objects.create(trials=trial, teamcategory=self.category)
		PatentCategoryAssignment.objects.create(patents=patent, teamcategory=self.category)
		self.detect()
		self.assertEqual(self.link(patent, trial).basis, "category")
		self.assertEqual(self.history(), 0)

	def test_stale_removal_is_one_delete_and_records_no_history(self):
		patents = [self.make_patent() for _ in range(3)]
		trial = self.make_trial()
		self.detect()
		self.assertEqual(PatentTrialLink.objects.count(), 3)
		Trials.objects.filter(pk=trial.pk).update(primary_completion_date=D(2020, 2, 1))
		with CaptureQueriesContext(connection) as ctx:
			self.detect(after_months=1)
		self.assertEqual(PatentTrialLink.objects.count(), 0)
		# One DELETE per patent that lost links, never one per link, and no
		# history rows or inserts.
		self.assertEqual(len(self.delete_statements(ctx.captured_queries)), len(patents))
		self.assertFalse([q for q in ctx.captured_queries if "historicalpatenttriallink" in q["sql"]])
		self.assertEqual(self.history(), 0)

	def test_reset_is_a_single_delete_and_records_no_history(self):
		for _ in range(5):
			self.make_patent()
		self.make_trial()
		self.detect()
		self.assertEqual(PatentTrialLink.objects.count(), 5)
		with CaptureQueriesContext(connection) as ctx:
			self.detect(reset=True)
		deletes = self.delete_statements(ctx.captured_queries)
		self.assertEqual(len(deletes), 1, deletes)
		self.assertFalse([q for q in ctx.captured_queries if "historicalpatenttriallink" in q["sql"]])
		self.assertEqual(PatentTrialLink.objects.count(), 5)  # recreated
		self.assertEqual(self.history(), 0)

	def test_a_persons_change_is_still_recorded(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		link = self.link(patent, trial)
		link.suppressed = True
		link.save()
		rows = list(PatentTrialLink.history.filter(id=link.id))
		self.assertEqual([r.history_type for r in rows], ["~"])
		self.assertTrue(rows[0].suppressed)
		# And detection still leaves the suppressed link alone, recording nothing more.
		self.detect(reset=True)
		self.assertTrue(self.link(patent, trial).suppressed)
		self.assertEqual(self.history(), 1)


class LinkAdminTests(LinkTestCase):
	def setUp(self):
		super().setUp()
		self.source = Sources.objects.create(name="S", source_for="patents", method="epo_ops", team=self.team)
		other_org = Organization.objects.create(name="Other", slug="link-other")
		other_team = Team.objects.create(organization=other_org, name="OT", slug="link-ot")
		other_source = Sources.objects.create(name="OS", source_for="patents", method="epo_ops", team=other_team)

		self.patent = self.make_patent()
		self.patent.sources.add(self.source)
		self.trial = self.make_trial()
		self.weak_patent = self.make_patent()
		self.weak_patent.sources.add(self.source)
		self.foreign_patent = self.make_patent()
		self.foreign_patent.sources.add(other_source)
		PatentCategoryAssignment.objects.create(patents=self.patent, teamcategory=self.category)
		TrialCategoryAssignment.objects.create(trials=self.trial, teamcategory=self.category)
		self.detect()

		self.staff = User.objects.create_user(username="link-staff", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org, user=self.staff)
		from django.contrib.auth.models import Permission

		self.staff.user_permissions.add(*Permission.objects.filter(content_type__model="patenttriallink"))
		self.superuser = User.objects.create_superuser(username="link-root", email="r@example.com", password="pw")
		self.url = reverse("admin:gregory_patenttriallink_changelist")

	def test_defaults_to_the_category_basis(self):
		client = Client()
		client.force_login(self.superuser)
		response = client.get(self.url)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["cl"].result_count, 1)

	def test_all_shows_every_basis(self):
		client = Client()
		client.force_login(self.superuser)
		self.assertEqual(client.get(self.url + "?basis=all").context["cl"].result_count, 3)
		self.assertEqual(client.get(self.url + "?basis=subject").context["cl"].result_count, 2)

	def test_staff_only_sees_their_organisations_links(self):
		client = Client()
		client.force_login(self.staff)
		response = client.get(self.url + "?basis=all")
		self.assertEqual(response.context["cl"].result_count, 2)

	def test_suppress_and_restore_actions(self):
		client = Client()
		client.force_login(self.superuser)
		link = self.link(self.patent, self.trial)
		client.post(self.url, {"action": "suppress_links", "_selected_action": [link.pk]})
		link.refresh_from_db()
		self.assertTrue(link.suppressed)
		client.post(self.url + "?basis=all", {"action": "restore_links", "_selected_action": [link.pk]})
		link.refresh_from_db()
		self.assertFalse(link.suppressed)

	def test_patent_change_page_lists_links(self):
		client = Client()
		client.force_login(self.superuser)
		response = client.get(reverse("admin:gregory_patents_change", args=[self.patent.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Trial ")


class AdminSummaryLinksTests(LinkTestCase):
	def setUp(self):
		super().setUp()
		from subscriptions.models import Lists, Subscribers

		self.list = Lists.objects.create(
			list_name="Admins", team=self.team, admin_summary=True, lookback_days=7
		)
		self.list.subjects.add(self.subject)
		self.subscriber = Subscribers.objects.create(email="admin@example.com", first_name="A", last_name="B")
		self.patent = self.make_patent()
		self.strong = self.make_trial()
		self.weak = self.make_trial()
		PatentCategoryAssignment.objects.create(patents=self.patent, teamcategory=self.category)
		TrialCategoryAssignment.objects.create(trials=self.strong, teamcategory=self.category)
		self.detect()

	def links(self, **kwargs):
		from subscriptions.management.commands.utils.subscription import get_patent_links_for_list

		return get_patent_links_for_list(self.list, self.subscriber, kwargs.pop("days", 7), **kwargs)

	def test_category_links_come_first(self):
		links, extra = self.links()
		self.assertEqual([l.basis for l in links], ["category", "subject"])
		self.assertEqual(extra, 0)

	def test_links_outside_the_window_are_excluded(self):
		PatentTrialLink.objects.update(discovered_date=timezone.now() - datetime.timedelta(days=30))
		self.assertEqual(self.links()[0], [])

	def test_already_sent_links_are_excluded(self):
		from subscriptions.models import SentPatentLinkNotification

		first = self.links()[0][0]
		SentPatentLinkNotification.objects.create(link=first, list=self.list, subscriber=self.subscriber)
		self.assertEqual(len(self.links()[0]), 1)

	def test_suppressed_links_are_excluded(self):
		PatentTrialLink.objects.update(suppressed=True)
		self.assertEqual(self.links()[0], [])

	def test_other_subjects_are_excluded(self):
		self.list.subjects.set([self.other_subject])
		self.assertEqual(self.links()[0], [])

	def test_link_needs_a_list_subject_shared_by_patent_and_trial(self):
		# Patent in MS + PD, trial in PD only: the link qualified on PD, so an MS-only
		# list must not get it even though the patent itself is in MS.
		patent = self.make_patent()
		patent.subjects.add(self.other_subject)
		trial = self.make_trial(subject=self.other_subject)
		PatentTrialLink.objects.all().delete()
		PatentTrialLink.objects.create(
			patent=patent, trial=trial, sponsor=self.sponsor, timing="during", basis="subject"
		)
		self.assertEqual(self.links()[0], [])
		self.list.subjects.set([self.other_subject])
		self.assertEqual(len(self.links()[0]), 1)

	def test_limit_reports_the_true_overflow(self):
		for _ in range(4):
			self.make_trial()
		self.detect()
		links, extra = self.links(limit=2)
		self.assertEqual((len(links), extra), (2, 4))

	def test_patent_links_alone_send_the_summary(self):
		"""A cycle with no new articles or trials still sends when there are new links."""
		from unittest.mock import MagicMock, patch

		from django.contrib.sites.models import Site
		from sitesettings.models import CustomSetting
		from subscriptions.models import SentPatentLinkNotification, Subscribers

		site = Site.objects.get_or_create(id=1, defaults={"domain": "testserver", "name": "Test"})[0]
		CustomSetting.objects.get_or_create(
			site=site,
			defaults={
				"title": "Test",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)
		Subscribers.objects.filter(pk=self.subscriber.pk).update(active=True)
		self.subscriber.subscriptions.add(self.list)
		ok = MagicMock(status_code=200)
		ok.json.return_value = {"ErrorCode": 0, "Message": "OK"}

		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email", return_value=ok
		) as send:
			call_command("send_admin_summary", stdout=StringIO())

		send.assert_called_once()
		self.assertEqual(
			SentPatentLinkNotification.objects.filter(list=self.list, subscriber=self.subscriber).count(), 2
		)

	def test_text_template_renders_the_section(self):
		from django.template.loader import get_template

		links, extra = self.links()
		text = get_template("emails/admin_summary.txt").render(
			{"patent_links": links, "patent_links_extra": extra, "content_stats": {}, "title": "Gregory"}
		)
		self.assertIn("SPONSORS FILING PATENTS AROUND THEIR TRIALS", text)
		self.assertIn("Acme Neuro filed during", text)
		self.assertIn("shared category", text)


class ReviewFixTests(LinkTestCase):
	def test_dry_run_reset_matches_a_real_reset(self):
		self.make_patent(), self.make_trial()
		self.detect()
		out = self.detect(reset=True, dry_run=True)
		self.assertIn("Would delete 1", out)
		self.assertIn("create 1", out)
		self.assertEqual(PatentTrialLink.objects.count(), 1)  # nothing written

	def test_sponsor_merge_keeps_links_including_suppressed(self):
		from gregory.utils.sponsor_merge import merge_sponsors

		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		PatentTrialLink.objects.update(suppressed=True)
		target = Sponsor.objects.create(name="Target", slug="target")

		merge_sponsors(target, [self.sponsor])

		link = PatentTrialLink.objects.get(patent=patent, trial=trial)
		self.assertEqual(link.sponsor, target)
		self.assertTrue(link.suppressed)

	def test_patent_category_inline_limits_categories_to_the_users_organisations(self):
		from django.contrib import admin as django_admin
		from django.test import RequestFactory

		from gregory.admin import PatentCategoryAssignmentInline

		other_org = Organization.objects.create(name="Other", slug="inline-other")
		other_team = Team.objects.create(organization=other_org, name="OT", slug="inline-ot")
		other_category = TeamCategory.objects.create(team=other_team, category_name="Other", category_terms=["y"])
		staff = User.objects.create_user(username="inline-staff", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org, user=staff)
		request = RequestFactory().get("/")
		request.user = staff

		inline = PatentCategoryAssignmentInline(Patents, django_admin.site)
		field = inline.formfield_for_foreignkey(
			PatentCategoryAssignment._meta.get_field("teamcategory"), request
		)
		self.assertIn(self.category, field.queryset)
		self.assertNotIn(other_category, field.queryset)

	def test_patent_admin_form_limits_relations_to_the_users_organisations(self):
		from django.test import RequestFactory

		from gregory.admin import PatentAdminForm

		other_org = Organization.objects.create(name="Other", slug="form-other")
		other_team = Team.objects.create(organization=other_org, name="OT", slug="form-ot")
		other_subject = Subject.objects.create(subject_name="X", subject_slug="x-f", team=other_team)
		other_source = Sources.objects.create(name="OS", source_for="patents", method="epo_ops", team=other_team)
		own_source = Sources.objects.create(name="S", source_for="patents", method="epo_ops", team=self.team)
		staff = User.objects.create_user(username="form-staff", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org, user=staff)
		request = RequestFactory().get("/")
		request.user = staff

		form = PatentAdminForm(request=request)
		self.assertEqual(list(form.fields["sources"].queryset), [own_source])
		self.assertNotIn(other_source, form.fields["sources"].queryset)
		self.assertNotIn(other_team, form.fields["teams"].queryset)
		self.assertNotIn(other_subject, form.fields["subjects"].queryset)
