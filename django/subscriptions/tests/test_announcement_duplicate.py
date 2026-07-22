"""
Tests for the duplicate_announcements admin bulk action on AnnouncementAdmin.
"""

from django.contrib.auth.models import User, Permission
from django.contrib.messages import get_messages
from django.test import TestCase, Client
from django.urls import reverse

from organizations.models import Organization
from gregory.models import Team
from subscriptions.models import (
	Announcement,
	AnnouncementRecipient,
	Lists,
	Subscribers,
)

CHANGELIST_URL = reverse("admin:subscriptions_announcement_changelist")


def _post_action(client, pks):
	"""POST the duplicate action to the changelist for the given PKs."""
	return client.post(
		CHANGELIST_URL,
		{
			"action": "duplicate_announcements",
			"_selected_action": [str(pk) for pk in pks],
		},
		follow=False,
	)


class _BaseTest(TestCase):
	"""Shared fixtures: superuser, org, team, list."""

	def setUp(self):
		self.superuser = User.objects.create_superuser(
			username="super", password="pass", email="super@example.com"
		)
		self.org = Organization.objects.create(name="Test Org")
		self.team = Team.objects.create(
			organization=self.org, name="Team A", slug="team-a"
		)
		self.lst = Lists.objects.create(list_name="Weekly", team=self.team)
		self.client = Client()
		self.client.force_login(self.superuser)

	def _make_source(self, status="sent", **kwargs):
		defaults = dict(
			subject="Original Subject",
			header_title="Original Title",
			header_tagline="Original Tagline",
			show_header_tagline=True,
			preheader_text="Original preheader",
			body="<p>Original body</p>",
			status=status,
			organization=self.org,
		)
		defaults.update(kwargs)
		ann = Announcement.objects.create(**defaults)
		ann.lists.add(self.lst)
		return ann


class TestDuplicateCopiesContentFields(_BaseTest):
	"""The copy has identical authored fields."""

	def test_duplicate_copies_content_fields(self):
		source = self._make_source(
			subject="Special Subject",
			header_title="Header Title",
			header_tagline="Header Tagline",
			show_header_tagline=False,
			preheader_text="Preheader text here",
			body="<p>Rich <strong>HTML</strong> body</p>",
		)
		_post_action(self.client, [source.pk])

		copy = Announcement.objects.exclude(pk=source.pk).get()
		self.assertEqual(copy.subject, "Special Subject")
		self.assertEqual(copy.header_title, "Header Title")
		self.assertEqual(copy.header_tagline, "Header Tagline")
		self.assertEqual(copy.show_header_tagline, False)
		self.assertEqual(copy.preheader_text, "Preheader text here")
		self.assertEqual(copy.body, "<p>Rich <strong>HTML</strong> body</p>")


class TestDuplicateResetsSendState(_BaseTest):
	"""The copy has clean send-state fields."""

	def test_duplicate_resets_send_state(self):
		source = self._make_source(status="sent")
		_post_action(self.client, [source.pk])

		copy = Announcement.objects.exclude(pk=source.pk).get()
		self.assertEqual(copy.status, "draft")
		self.assertIsNone(copy.sent_at)
		self.assertEqual(copy.recipients_count, 0)
		self.assertEqual(copy.failures_count, 0)


class TestDuplicateSetsCreatedByToActor(_BaseTest):
	"""created_by on the copy is the user who ran the action, not the source author."""

	def test_duplicate_sets_created_by_to_actor(self):
		user_a = User.objects.create_user(username="user_a", password="pass")
		source = self._make_source(created_by=user_a)

		user_b = User.objects.create_superuser(
			username="user_b", password="pass", email="b@example.com"
		)
		self.client.force_login(user_b)
		_post_action(self.client, [source.pk])

		copy = Announcement.objects.exclude(pk=source.pk).get()
		self.assertEqual(copy.created_by, user_b)


class TestDuplicateHasNoLists(_BaseTest):
	"""The copy's lists M2M is empty."""

	def test_duplicate_has_no_lists(self):
		lst2 = Lists.objects.create(list_name="Another List", team=self.team)
		source = self._make_source()
		source.lists.add(lst2)  # source now has 2 lists

		_post_action(self.client, [source.pk])

		copy = Announcement.objects.exclude(pk=source.pk).get()
		self.assertEqual(copy.lists.count(), 0)


class TestDuplicateDoesNotCopyRecipients(_BaseTest):
	"""No AnnouncementRecipient rows are created for the copy."""

	def test_duplicate_does_not_copy_recipients(self):
		sub = Subscribers.objects.create(
			first_name="Alice", last_name="Smith", email="alice@example.com"
		)
		source = self._make_source(status="sent")
		recipient = AnnouncementRecipient.objects.create(
			announcement=source,
			subscriber=sub,
			list=self.lst,
			success=True,
		)
		_post_action(self.client, [source.pk])

		copy = Announcement.objects.exclude(pk=source.pk).get()
		self.assertEqual(copy.recipients.count(), 0)

		# Source's recipient row is untouched
		recipient.refresh_from_db()
		self.assertEqual(source.recipients.count(), 1)
		self.assertIsNotNone(recipient.sent_at)


class TestDuplicateSkipsSendingStatus(_BaseTest):
	"""Sources with status='sending' are skipped and a warning is shown."""

	def test_duplicate_skips_sending_status(self):
		source = self._make_source(status="sending", subject="In-Flight Send")
		response = _post_action(self.client, [source.pk])

		# No new announcement created
		self.assertEqual(Announcement.objects.count(), 1)

		msgs = [str(m) for m in get_messages(response.wsgi_request)]
		self.assertTrue(
			any("currently sending" in m for m in msgs),
			f"Expected 'currently sending' warning, got: {msgs}",
		)


class TestDuplicateOrgScopeEnforcement(TestCase):
	"""Org-scope rules govern which announcements a non-superuser may duplicate."""

	@classmethod
	def setUpTestData(cls):
		# Org A — owns the source announcement
		org_a = Organization.objects.create(name="Org A")
		team_a = Team.objects.create(
			organization=org_a, name="Team A", slug="team-a-dup"
		)
		cls.lst_a = Lists.objects.create(list_name="List A", team=team_a)

		# Org B — the acting user belongs only to this org
		cls.org_b = Organization.objects.create(name="Org B")
		team_b = Team.objects.create(
			organization=cls.org_b, name="Team B", slug="team-b-dup"
		)
		cls.lst_b = Lists.objects.create(list_name="List B", team=team_b)

		# Non-superuser staff who belongs only to org B
		cls.user_b = User.objects.create_user(
			username="user_b_auth",
			password="pass",
			email="user_b_auth@example.com",
			is_staff=True,
		)
		cls.user_b.user_permissions.add(
			Permission.objects.get(codename="add_announcement"),
			Permission.objects.get(codename="change_announcement"),
			Permission.objects.get(codename="view_announcement"),
		)
		from organizations.models import OrganizationUser

		OrganizationUser.objects.create(organization=cls.org_b, user=cls.user_b)

	def setUp(self):
		self.client = Client()
		self.client.force_login(self.user_b)

	def test_queryset_filter_blocks_cross_org_duplicate(self):
		"""Submitting the PK of a source the user cannot see is silently ignored
		by Django's bulk-action machinery — no copy is created."""
		source_a = Announcement.objects.create(
			subject="Org A Only",
			body="<p>content</p>",
			status="sent",
			organization=self.lst_a.team.organization,
		)
		source_a.lists.add(self.lst_a)  # only org A — invisible to user_b

		count_before = Announcement.objects.count()
		_post_action(self.client, [source_a.pk])
		self.assertEqual(Announcement.objects.count(), count_before)

	def test_user_can_duplicate_source_in_own_org(self):
		"""A non-superuser can duplicate an announcement whose list belongs to
		their organisation — the copy is created without a permission warning."""
		source_b = Announcement.objects.create(
			subject="Org B Source",
			body="<p>body</p>",
			status="sent",
			organization=self.org_b,
		)
		source_b.lists.add(self.lst_b)  # org B list — visible to user_b

		count_before = Announcement.objects.count()
		response = _post_action(self.client, [source_b.pk])

		self.assertEqual(Announcement.objects.count(), count_before + 1)
		msgs = [str(m) for m in get_messages(response.wsgi_request)]
		self.assertFalse(
			any("permission" in m.lower() for m in msgs),
			f"Unexpected permission warning: {msgs}",
		)


class TestDuplicateVisibilityForNonSuperuser(TestCase):
	"""With the organization FK fix, list-less drafts now appear in the changelist.
	This tests the resolution of the 'Known consequence' from the duplicate spec."""

	def setUp(self):
		self.org = Organization.objects.create(name="Vis Org")
		team = Team.objects.create(
			organization=self.org, name="Vis Team", slug="vis-team"
		)
		self.lst = Lists.objects.create(list_name="Vis List", team=team)

		self.staff_user = User.objects.create_user(
			username="staff_vis",
			password="pass",
			email="staff_vis@example.com",
			is_staff=True,
		)
		self.staff_user.user_permissions.add(
			Permission.objects.get(codename="add_announcement"),
			Permission.objects.get(codename="change_announcement"),
			Permission.objects.get(codename="view_announcement"),
		)
		from organizations.models import OrganizationUser

		OrganizationUser.objects.create(organization=self.org, user=self.staff_user)

		self.source = Announcement.objects.create(
			subject="Vis Source",
			body="<p>body</p>",
			status="sent",
			organization=self.org,
		)
		self.source.lists.add(self.lst)

		self.client = Client()
		self.client.force_login(self.staff_user)

	def test_list_less_draft_now_visible_in_non_superuser_changelist(self):
		"""The list-less draft copy is now visible to org-scoped users because
		visibility is determined by organization_id, not by lists M2M.
		This resolves the 'Known consequence' documented in the spec."""
		_post_action(self.client, [self.source.pk])

		copy = Announcement.objects.exclude(pk=self.source.pk).get()
		self.assertEqual(copy.lists.count(), 0, "copy should have no lists")
		self.assertEqual(
			copy.organization, self.org, "copy should inherit org from source"
		)

		response = self.client.get(CHANGELIST_URL)
		self.assertEqual(response.status_code, 200)
		result_pks = set(response.context["cl"].queryset.values_list("pk", flat=True))
		self.assertIn(
			self.source.pk, result_pks, "original source should still be visible"
		)
		self.assertIn(
			copy.pk, result_pks, "list-less copy should now appear for org-scoped user"
		)


class TestDuplicateSingleRedirectsToChangePage(_BaseTest):
	"""Selecting one source redirects to the new draft's change page."""

	def test_duplicate_single_redirects_to_change_page(self):
		source = self._make_source()
		response = _post_action(self.client, [source.pk])

		copy = Announcement.objects.exclude(pk=source.pk).get()
		expected_url = reverse(
			"admin:subscriptions_announcement_change", args=[copy.pk]
		)
		self.assertRedirects(response, expected_url, fetch_redirect_response=False)


class TestDuplicateMultipleStaysOnChangelist(_BaseTest):
	"""Selecting two sources stays on the changelist with a success message."""

	def test_duplicate_multiple_stays_on_changelist(self):
		source1 = self._make_source(subject="Source 1")
		source2 = Announcement.objects.create(
			subject="Source 2",
			body="<p>body 2</p>",
			status="sent",
			organization=self.org,
		)
		source2.lists.add(self.lst)

		response = _post_action(self.client, [source1.pk, source2.pk])

		# Should redirect back to changelist (302 with changelist as location)
		self.assertEqual(response.status_code, 302)
		self.assertIn("announcement", response["Location"])

		msgs = [str(m) for m in get_messages(response.wsgi_request)]
		self.assertTrue(
			any("Duplicated 2 announcements as drafts" in m for m in msgs),
			f"Expected success message, got: {msgs}",
		)
		self.assertEqual(Announcement.objects.count(), 4)  # 2 sources + 2 copies


class TestDuplicateDoesNotModifySource(_BaseTest):
	"""The original announcement is completely unchanged after the action."""

	def test_duplicate_does_not_modify_source(self):
		sub = Subscribers.objects.create(
			first_name="Bob", last_name="Jones", email="bob@example.com"
		)
		source = self._make_source(
			subject="Immutable Source",
			status="sent",
		)
		AnnouncementRecipient.objects.create(
			announcement=source,
			subscriber=sub,
			list=self.lst,
			success=True,
		)

		# Snapshot before action
		lists_before = list(source.lists.values_list("pk", flat=True))
		recipients_count_before = source.recipients.count()

		_post_action(self.client, [source.pk])

		# Reload from DB
		source.refresh_from_db()
		self.assertEqual(source.subject, "Immutable Source")
		self.assertEqual(source.status, "sent")
		self.assertIsNone(source.sent_at)  # was None in _make_source
		self.assertEqual(source.recipients_count, 0)  # field default, unchanged
		self.assertEqual(source.failures_count, 0)
		self.assertEqual(
			list(source.lists.values_list("pk", flat=True)),
			lists_before,
		)
		self.assertEqual(source.recipients.count(), recipients_count_before)


class TestDuplicateWithoutAddPermissionIsBlocked(TestCase):
	"""A user without add_announcement permission cannot run the action."""

	def setUp(self):
		org = Organization.objects.create(name="Perm Org")
		team = Team.objects.create(organization=org, name="Perm Team", slug="perm-team")
		self.lst = Lists.objects.create(list_name="Perm List", team=team)

		self.source = Announcement.objects.create(
			subject="Locked Source",
			body="<p>body</p>",
			status="sent",
			organization=org,
		)
		self.source.lists.add(self.lst)

		# User with view+change but NOT add permission
		self.user = User.objects.create_user(
			username="no_add",
			password="pass",
			email="no_add@example.com",
			is_staff=True,
		)
		self.user.user_permissions.add(
			Permission.objects.get(codename="view_announcement"),
			Permission.objects.get(codename="change_announcement"),
		)
		self.client = Client()
		self.client.force_login(self.user)

	def test_duplicate_without_add_permission_is_blocked(self):
		initial_count = Announcement.objects.count()
		response = _post_action(self.client, [self.source.pk])

		# No new row
		self.assertEqual(Announcement.objects.count(), initial_count)

		msgs = [str(m) for m in get_messages(response.wsgi_request)]
		self.assertTrue(
			any("permission" in m.lower() for m in msgs),
			f"Expected permission error message, got: {msgs}",
		)
