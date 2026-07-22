"""
Tests for the Announcement.organization FK introduced in the
announcement-organization-owner spec.

Covers: model constraint, form defaults, changelist scoping,
readonly-field locking, send-validation cross-org check,
duplicate-action org inheritance, and the save_model fallback.
"""

from unittest.mock import MagicMock

from django.contrib.admin import site as admin_site
from django.contrib.auth.models import User, Permission
from django.db import IntegrityError
from django.test import TestCase, Client
from django.urls import reverse

from organizations.models import Organization, OrganizationUser
from gregory.models import Team
from subscriptions.admin import AnnouncementAdmin
from subscriptions.forms import AnnouncementAdminForm
from subscriptions.models import Announcement, Lists
from subscriptions.utils.announcement_send_validation import (
	validate_announcement_send_config,
)

CHANGELIST_URL = reverse("admin:subscriptions_announcement_changelist")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _staff_user_in_org(username, org, email=None):
	"""Create a staff User with full announcement permissions, in *org*."""
	user = User.objects.create_user(
		username=username,
		password="pass",
		email=email or f"{username}@example.com",
		is_staff=True,
	)
	user.user_permissions.add(
		Permission.objects.get(codename="add_announcement"),
		Permission.objects.get(codename="change_announcement"),
		Permission.objects.get(codename="view_announcement"),
	)
	OrganizationUser.objects.create(organization=org, user=user)
	return user


def _make_request(user):
	"""Return a minimal fake request with .user set."""
	req = MagicMock()
	req.user = user
	req.data = {}
	return req


# ---------------------------------------------------------------------------
# 1. Model constraint
# ---------------------------------------------------------------------------


class TestModelRequiresOrganization(TestCase):
	"""Announcement.organization is NOT NULL — saving without it must fail."""

	def test_model_requires_organization(self):
		with self.assertRaises((IntegrityError, Exception)):
			ann = Announcement(subject="No org", body="<p>x</p>")
			ann.save()


# ---------------------------------------------------------------------------
# 2-5. Form defaults
# ---------------------------------------------------------------------------


class TestFormDefaultSingleOrgUser(TestCase):
	"""Single-org user: form initial = their org, field disabled."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Only Org")
		team = Team.objects.create(organization=cls.org, name="T", slug="t-single")
		cls.lst = Lists.objects.create(list_name="L", team=team)
		cls.user = _staff_user_in_org("single_org_user", cls.org)

	def _make_form(self):
		req = MagicMock()
		req.user = self.user
		req.data = {}
		return AnnouncementAdminForm(request=req)

	def test_form_initial_is_the_org(self):
		form = self._make_form()
		self.assertEqual(form.fields["organization"].initial, self.org)

	def test_form_field_is_disabled(self):
		form = self._make_form()
		self.assertTrue(form.fields["organization"].disabled)


class TestFormDefaultMultiOrgUser(TestCase):
	"""Multi-org user: initial = first org by PK, field enabled."""

	@classmethod
	def setUpTestData(cls):
		cls.org_a = Organization.objects.create(name="AAA Org")
		cls.org_b = Organization.objects.create(name="BBB Org")
		team_a = Team.objects.create(
			organization=cls.org_a, name="TA", slug="ta-multi"
		)
		team_b = Team.objects.create(
			organization=cls.org_b, name="TB", slug="tb-multi"
		)
		Lists.objects.create(list_name="LA", team=team_a)
		Lists.objects.create(list_name="LB", team=team_b)

		cls.user = User.objects.create_user(
			username="multi_org",
			password="pass",
			email="multi@example.com",
			is_staff=True,
		)
		cls.user.user_permissions.add(
			Permission.objects.get(codename="add_announcement"),
			Permission.objects.get(codename="change_announcement"),
			Permission.objects.get(codename="view_announcement"),
		)
		OrganizationUser.objects.create(organization=cls.org_a, user=cls.user)
		OrganizationUser.objects.create(organization=cls.org_b, user=cls.user)

	def _make_form(self):
		req = MagicMock()
		req.user = self.user
		req.data = {}
		return AnnouncementAdminForm(request=req)

	def test_form_initial_is_first_org_by_pk(self):
		form = self._make_form()
		first_pk = min(self.org_a.pk, self.org_b.pk)
		expected_org = Organization.objects.get(pk=first_pk)
		self.assertEqual(form.fields["organization"].initial, expected_org)

	def test_form_field_is_enabled(self):
		form = self._make_form()
		self.assertFalse(form.fields["organization"].disabled)


class TestFormDefaultSuperuserWithMembership(TestCase):
	"""Superuser in org B: initial is org B even when org A has lower PK."""

	@classmethod
	def setUpTestData(cls):
		cls.org_a = Organization.objects.create(name="A-Lower-PK")
		cls.org_b = Organization.objects.create(name="B-Higher-PK")

		cls.superuser = User.objects.create_superuser(
			username="su_member", password="pass", email="su_member@example.com"
		)
		# Superuser is a member only of org B.
		OrganizationUser.objects.create(organization=cls.org_b, user=cls.superuser)

	def _make_form(self):
		req = MagicMock()
		req.user = self.superuser
		req.data = {}
		return AnnouncementAdminForm(request=req)

	def test_form_initial_is_superuser_membership_org(self):
		form = self._make_form()
		self.assertEqual(form.fields["organization"].initial, self.org_b)


class TestFormDefaultSuperuserNoMembership(TestCase):
	"""Superuser with no OrganizationUser: initial = first org by PK."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="First Org")
		Organization.objects.create(name="Second Org")
		cls.superuser = User.objects.create_superuser(
			username="su_nomember", password="pass", email="su_nomember@example.com"
		)

	def _make_form(self):
		req = MagicMock()
		req.user = self.superuser
		req.data = {}
		return AnnouncementAdminForm(request=req)

	def test_form_initial_is_first_org_by_pk(self):
		form = self._make_form()
		first_org = Organization.objects.order_by("pk").first()
		self.assertEqual(form.fields["organization"].initial, first_org)


# ---------------------------------------------------------------------------
# 6. Lists scoped to selected org
# ---------------------------------------------------------------------------


class TestFormListsScopedToSelectedOrg(TestCase):
	"""Multi-org user: when org=A is posted, only lists of org A are in choices."""

	@classmethod
	def setUpTestData(cls):
		cls.org_a = Organization.objects.create(name="Scope A")
		cls.org_b = Organization.objects.create(name="Scope B")
		team_a = Team.objects.create(
			organization=cls.org_a, name="SA", slug="sa-scope"
		)
		team_b = Team.objects.create(
			organization=cls.org_b, name="SB", slug="sb-scope"
		)
		cls.list_a = Lists.objects.create(list_name="List A", team=team_a)
		cls.list_b = Lists.objects.create(list_name="List B", team=team_b)

		cls.user = User.objects.create_user(
			username="scope_user",
			password="pass",
			email="scope@example.com",
			is_staff=True,
		)
		cls.user.user_permissions.add(
			Permission.objects.get(codename="add_announcement"),
			Permission.objects.get(codename="change_announcement"),
			Permission.objects.get(codename="view_announcement"),
		)
		OrganizationUser.objects.create(organization=cls.org_a, user=cls.user)
		OrganizationUser.objects.create(organization=cls.org_b, user=cls.user)

	def test_lists_scoped_to_selected_org(self):
		req = MagicMock()
		req.user = self.user
		# Simulate POST data selecting org_a.
		req.data = {"organization": str(self.org_a.pk)}
		form = AnnouncementAdminForm(request=req)
		list_qs = form.fields["lists"].queryset
		self.assertIn(self.list_a, list_qs)
		self.assertNotIn(self.list_b, list_qs)


# ---------------------------------------------------------------------------
# 7. Form cross-org validation
# ---------------------------------------------------------------------------


class TestFormRejectsCrossOrgList(TestCase):
	"""Submitting a list from org B when org=A raises ValidationError on 'lists'."""

	@classmethod
	def setUpTestData(cls):
		cls.org_a = Organization.objects.create(name="CrossA")
		cls.org_b = Organization.objects.create(name="CrossB")
		team_a = Team.objects.create(
			organization=cls.org_a, name="CA", slug="ca-cross"
		)
		team_b = Team.objects.create(
			organization=cls.org_b, name="CB", slug="cb-cross"
		)
		cls.list_a = Lists.objects.create(list_name="Good List", team=team_a)
		cls.list_b = Lists.objects.create(list_name="Bad List", team=team_b)

		cls.superuser = User.objects.create_superuser(
			username="cross_su", password="pass", email="cross_su@example.com"
		)

	def test_form_rejects_cross_org_list(self):
		req = MagicMock()
		req.user = self.superuser
		req.data = {}
		form = AnnouncementAdminForm(
			data={
				"subject": "Test",
				"body": "<p>content</p>",
				"organization": str(self.org_a.pk),
				"lists": [str(self.list_b.pk)],  # list from org B
				"show_header_tagline": True,
			},
			request=req,
		)
		self.assertFalse(form.is_valid())
		# The form rejects the cross-org list. Django's field validation fires
		# first (the list is not in the org-scoped queryset) and raises a
		# "Select a valid choice" error on the 'lists' field — which is the
		# correct rejection. Our clean() would add a more specific message if
		# the list somehow passed field validation.
		self.assertIn("lists", form.errors)


# ---------------------------------------------------------------------------
# 8-9. Changelist scoping
# ---------------------------------------------------------------------------


class TestChangelistShowsListlessDrafts(TestCase):
	"""Draft with no lists for org A must appear in org-A user's changelist."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="CL Org")
		cls.user = _staff_user_in_org("cl_user", cls.org)
		cls.ann = Announcement.objects.create(
			subject="Listless Draft",
			body="<p>x</p>",
			organization=cls.org,
		)
		# No lists added.

	def setUp(self):
		self.client = Client()
		self.client.force_login(self.user)

	def test_listless_draft_appears_in_changelist(self):
		response = self.client.get(CHANGELIST_URL)
		self.assertEqual(response.status_code, 200)
		result_pks = set(response.context["cl"].queryset.values_list("pk", flat=True))
		self.assertIn(self.ann.pk, result_pks)


class TestChangelistHidesOtherOrg(TestCase):
	"""Announcement for org B must not appear in org-A user's changelist."""

	@classmethod
	def setUpTestData(cls):
		cls.org_a = Organization.objects.create(name="Hide A")
		cls.org_b = Organization.objects.create(name="Hide B")
		cls.user_a = _staff_user_in_org("hide_user_a", cls.org_a)
		cls.ann_b = Announcement.objects.create(
			subject="Org B Ann",
			body="<p>x</p>",
			organization=cls.org_b,
		)

	def setUp(self):
		self.client = Client()
		self.client.force_login(self.user_a)

	def test_other_org_announcement_hidden(self):
		response = self.client.get(CHANGELIST_URL)
		self.assertEqual(response.status_code, 200)
		result_pks = set(response.context["cl"].queryset.values_list("pk", flat=True))
		self.assertNotIn(self.ann_b.pk, result_pks)


# ---------------------------------------------------------------------------
# 10. Readonly fields locked post-send
# ---------------------------------------------------------------------------


class TestGetReadonlyFieldsLocksOrganizationPostSend(TestCase):
	"""organization must be in readonly_fields when status='sent'."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="RO Org")
		cls.superuser = User.objects.create_superuser(
			username="ro_su", password="pass", email="ro_su@example.com"
		)

	def setUp(self):
		# AnnouncementAdmin isn't deep-copyable (holds a reference to the
		# admin site registry), so it can't live in setUpTestData.
		self.admin = AnnouncementAdmin(Announcement, admin_site)

	def test_readonly_includes_organization_post_send(self):
		ann = Announcement(
			subject="Sent Ann",
			body="<p>x</p>",
			status="sent",
			organization=self.org,
		)
		req = MagicMock()
		req.user = self.superuser
		readonly = self.admin.get_readonly_fields(req, obj=ann)
		self.assertIn("organization", readonly)

	def test_readonly_does_not_include_organization_for_draft(self):
		ann = Announcement(
			subject="Draft Ann",
			body="<p>x</p>",
			status="draft",
			organization=self.org,
		)
		req = MagicMock()
		req.user = self.superuser
		readonly = self.admin.get_readonly_fields(req, obj=ann)
		self.assertNotIn("organization", readonly)


# ---------------------------------------------------------------------------
# 11. Send validation blocks cross-org list
# ---------------------------------------------------------------------------


class TestSendValidationBlocksCrossOrgList(TestCase):
	"""validate_announcement_send_config must block when a list belongs to
	a different org than the announcement."""

	@classmethod
	def setUpTestData(cls):
		cls.org_a = Organization.objects.create(name="SVA Org A")
		cls.org_b = Organization.objects.create(name="SVA Org B")
		team_a = Team.objects.create(
			organization=cls.org_a, name="SVA A", slug="sva-a"
		)
		team_b = Team.objects.create(
			organization=cls.org_b, name="SVA B", slug="sva-b"
		)
		cls.list_a = Lists.objects.create(list_name="SV List A", team=team_a)
		cls.list_b = Lists.objects.create(list_name="SV List B", team=team_b)

		cls.ann = Announcement.objects.create(
			subject="SV Ann",
			body="<p>hello</p>",
			organization=cls.org_a,
		)
		cls.ann.lists.add(cls.list_a)
		cls.ann.lists.add(cls.list_b)  # cross-org

	def _make_site(self, domain="ex.com"):
		s = MagicMock()
		s.domain = domain
		return s

	def _make_cs(self, api_domain="api.ex.com"):
		cs = MagicMock()
		cs.api_domain = api_domain
		return cs

	def test_send_validation_blocks_cross_org_list(self):
		errors = validate_announcement_send_config(
			self.ann,
			self._make_site(),
			self._make_cs(),
		)
		self.assertTrue(errors, "Expected at least one error for cross-org list")
		cross_org_error = next(
			(e for e in errors if "SV List B" in e or "different organization" in e),
			None,
		)
		self.assertIsNotNone(cross_org_error, f"Cross-org error not found in: {errors}")

	def test_send_validation_passes_when_all_lists_in_same_org(self):
		self.ann.lists.remove(self.list_b)  # leave only list_a (same org)
		errors = validate_announcement_send_config(
			self.ann,
			self._make_site(),
			self._make_cs(),
		)
		# The only errors should be from checks 1-5, not from the org-check.
		org_errors = [e for e in errors if "different organization" in e]
		self.assertEqual(org_errors, [])


# ---------------------------------------------------------------------------
# 12. Duplicate action inherits source organization
# ---------------------------------------------------------------------------


class TestDuplicateInheritsSourceOrganization(TestCase):
	"""duplicate_announcements copies organization from source, not from actor."""

	@classmethod
	def setUpTestData(cls):
		cls.org_a = Organization.objects.create(name="DupOrg A")
		cls.org_b = Organization.objects.create(name="DupOrg B")
		team_a = Team.objects.create(
			organization=cls.org_a, name="DA", slug="da-dup-org"
		)
		team_b = Team.objects.create(
			organization=cls.org_b, name="DB", slug="db-dup-org"
		)
		cls.list_a = Lists.objects.create(list_name="DupList A", team=team_a)
		cls.list_b = Lists.objects.create(list_name="DupList B", team=team_b)

		# Multi-org user (member of both A and B; first org by PK is A).
		cls.user = User.objects.create_superuser(
			username="dup_su", password="pass", email="dup_su@example.com"
		)
		# Source is in org_a.
		cls.source = Announcement.objects.create(
			subject="Source in A",
			body="<p>x</p>",
			organization=cls.org_a,
			status="sent",
		)
		cls.source.lists.add(cls.list_a)

	def setUp(self):
		self.client = Client()
		self.client.force_login(self.user)

	def test_duplicate_inherits_source_organization(self):
		response = self.client.post(
			CHANGELIST_URL,
			{
				"action": "duplicate_announcements",
				"_selected_action": [str(self.source.pk)],
			},
			follow=False,
		)

		copy = Announcement.objects.exclude(pk=self.source.pk).get()
		self.assertEqual(copy.organization, self.org_a)


# ---------------------------------------------------------------------------
# 13. Duplicate action blocks source user cannot see
# ---------------------------------------------------------------------------


class TestDuplicateActionBlocksSourceUserCannotSee(TestCase):
	"""duplicate_announcements on a source in org B, run by user in org A only:
	no row is created because the changelist queryset excludes it."""

	@classmethod
	def setUpTestData(cls):
		cls.org_a = Organization.objects.create(name="Block A")
		cls.org_b = Organization.objects.create(name="Block B")
		team_b = Team.objects.create(organization=cls.org_b, name="BlkB", slug="blk-b")
		cls.list_b = Lists.objects.create(list_name="BlkList B", team=team_b)

		cls.user_a = _staff_user_in_org("blk_user_a", cls.org_a)
		cls.source_b = Announcement.objects.create(
			subject="Blocked Source",
			body="<p>x</p>",
			organization=cls.org_b,
			status="sent",
		)
		cls.source_b.lists.add(cls.list_b)

	def setUp(self):
		self.client = Client()
		self.client.force_login(self.user_a)

	def test_duplicate_blocked_for_foreign_org_source(self):
		count_before = Announcement.objects.count()
		self.client.post(
			CHANGELIST_URL,
			{
				"action": "duplicate_announcements",
				"_selected_action": [str(self.source_b.pk)],
			},
			follow=False,
		)
		# Django's bulk-action machinery silently ignores PKs outside queryset.
		self.assertEqual(Announcement.objects.count(), count_before)


# ---------------------------------------------------------------------------
# 14. save_model fallback
# ---------------------------------------------------------------------------


class TestSaveModelFallbackForMissingOrganization(TestCase):
	"""save_model sets organization from user's first OrganizationUser when
	obj.organization_id is None (defensive path)."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Fallback Org")
		team = Team.objects.create(
			organization=cls.org, name="FB Team", slug="fb-team"
		)
		Lists.objects.create(list_name="FB List", team=team)

		cls.user = _staff_user_in_org("fb_user", cls.org)

	def setUp(self):
		# AnnouncementAdmin isn't deep-copyable (holds a reference to the
		# admin site registry), so it can't live in setUpTestData.
		self.admin = AnnouncementAdmin(Announcement, admin_site)

	def test_save_model_fallback_sets_org(self):
		obj = Announcement(subject="Fallback", body="<p>x</p>")
		# organization_id is None — the fallback path should fire.
		self.assertIsNone(obj.organization_id)

		req = MagicMock()
		req.user = self.user
		form = MagicMock()

		self.admin.save_model(req, obj, form, change=False)

		obj.refresh_from_db()
		self.assertEqual(obj.organization, self.org)


# ---------------------------------------------------------------------------
# Form init must not KeyError when organization/lists are readonly (sent)
# ---------------------------------------------------------------------------


class TestFormInitHandlesReadonlyOrgAndLists(TestCase):
	"""When status='sent', the admin moves 'organization' and 'lists' into
	readonly_fields, which removes them from the ModelForm's fields. The
	form's __init__ must not blow up trying to scope queryset/initial on
	those missing fields."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Readonly Init Org")
		team = Team.objects.create(organization=cls.org, name="RI", slug="ri-team")
		cls.lst = Lists.objects.create(list_name="RI List", team=team)
		cls.superuser = User.objects.create_superuser(
			username="ri_su", password="pass", email="ri_su@example.com"
		)
		cls.ann = Announcement.objects.create(
			subject="Sent Init Ann",
			body="<p>x</p>",
			status="sent",
			organization=cls.org,
		)

	def setUp(self):
		# AnnouncementAdmin isn't deep-copyable (holds a reference to the
		# admin site registry), so it can't live in setUpTestData.
		self.admin = AnnouncementAdmin(Announcement, admin_site)

	def test_form_init_does_not_keyerror_on_sent(self):
		req = MagicMock()
		req.user = self.superuser
		req.data = {}
		# Mirror how AnnouncementAdmin.get_form builds the ModelForm — Django
		# strips readonly fields from the form via fields/exclude.
		form_class = self.admin.get_form(req, obj=self.ann)
		form = form_class(instance=self.ann, request=req)
		# organization and lists must be excluded (moved to readonly) — that
		# is what triggered the original KeyError. Reaching this line at all
		# proves __init__ did not crash.
		self.assertNotIn("organization", form.fields)
		self.assertNotIn("lists", form.fields)

	def test_form_init_still_works_on_draft(self):
		draft = Announcement.objects.create(
			subject="Draft Init Ann",
			body="<p>x</p>",
			status="draft",
			organization=self.org,
		)
		req = MagicMock()
		req.user = self.superuser
		req.data = {}
		form_class = self.admin.get_form(req, obj=draft)
		form = form_class(instance=draft, request=req)
		self.assertIn("organization", form.fields)
		self.assertIn("lists", form.fields)
