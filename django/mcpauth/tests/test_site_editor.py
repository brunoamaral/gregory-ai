from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from mcpauth.models import AccessToken, SiteEditor
from mcpauth.tests.helpers import SERVICE_KEY, get_token, grant, introspect, make_org, make_site, make_user


class GrantRulesTest(TestCase):
	def setUp(self):
		self.org = make_org("Grant Org")
		self.other_org = make_org("Grant Other")
		self.site = make_site(self.org, "grant.example.com")

	def test_member_of_the_sites_organisation_may_be_granted(self):
		user = make_user("member", self.org)
		SiteEditor(user=user, site=self.site).full_clean()

	def test_member_of_another_organisation_may_not_(self):
		user = make_user("foreign", self.other_org)
		with self.assertRaises(ValidationError):
			SiteEditor(user=user, site=self.site).full_clean()

	def test_user_in_no_organisation_may_not_(self):
		with self.assertRaises(ValidationError):
			SiteEditor(user=make_user("nobody"), site=self.site).full_clean()

	def test_superuser_our_own_team_may_be_granted_any_site(self):
		root = make_user("team", is_superuser=True)
		SiteEditor(user=root, site=self.site).full_clean()

	def test_second_active_grant_for_the_same_pair_is_refused(self):
		user = make_user("dup", self.org)
		grant(user, self.site)
		with self.assertRaises(IntegrityError), transaction.atomic():
			SiteEditor.objects.create(user=user, site=self.site)

	def test_a_revoked_grant_can_be_replaced_by_a_new_one(self):
		user = make_user("again", self.org)
		first = grant(user, self.site)
		first.revoke()

		second = grant(user, self.site)

		self.assertTrue(second.is_active)
		self.assertEqual(SiteEditor.objects.filter(user=user, site=self.site).count(), 2)

	def test_revoking_a_foreign_grant_is_not_blocked_by_the_membership_rule(self):
		# Someone who left the organisation must still be revocable.
		make_user("owner", self.org)  # the first member becomes the owner
		user = make_user("leaver", self.org)
		editor = grant(user, self.site)
		self.org.remove_user(user)

		editor.revoke()

		self.assertFalse(editor.is_active)


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class RevocationEndsSessionsTest(TestCase):
	def setUp(self):
		self.org = make_org("Revoke Org")
		self.site = make_site(self.org, "revoke.example.com")
		self.other = make_site(self.org, "revoke-other.example.com")
		self.user = make_user("revokee", self.org)
		self.editor = grant(self.user, self.site)
		grant(self.user, self.other)

	def _live(self, site):
		return AccessToken.objects.filter(user=self.user, site=site).count()

	def test_revoking_deletes_that_sites_tokens_in_the_same_save(self):
		body = get_token(self.user, self.site).json()
		get_token(self.user, self.other)
		self.assertEqual((self._live(self.site), self._live(self.other)), (1, 1))

		self.editor.revoke()

		self.assertEqual((self._live(self.site), self._live(self.other)), (0, 1))
		self.assertFalse(introspect(body["access_token"]).json()["active"])

	def test_revoking_also_deletes_the_refresh_token(self):
		from mcpauth.models import RefreshToken

		get_token(self.user, self.site)
		self.assertEqual(RefreshToken.objects.filter(user=self.user).count(), 1)

		self.editor.revoke()

		self.assertEqual(RefreshToken.objects.filter(user=self.user).count(), 0)

	def test_setting_revoked_at_through_the_admin_form_path_does_the_same(self):
		get_token(self.user, self.site)
		from django.utils import timezone

		self.editor.revoked_at = timezone.now()
		self.editor.save()

		self.assertEqual(self._live(self.site), 0)

	def test_downgrading_to_read_only_ends_edit_sessions(self):
		get_token(self.user, self.site)

		self.editor.can_edit = False
		self.editor.save()

		self.assertEqual(self._live(self.site), 0)

	def test_saving_an_unchanged_grant_keeps_the_sessions(self):
		get_token(self.user, self.site)

		self.editor.save()

		self.assertEqual(self._live(self.site), 1)

	def test_introspection_rechecks_the_grant_even_if_a_token_survived(self):
		body = get_token(self.user, self.site).json()
		# Bypass save(): what a bulk update or a raw SQL change would do.
		SiteEditor.objects.filter(pk=self.editor.pk).update(revoked_at="2026-01-01T00:00:00Z")

		self.assertFalse(introspect(body["access_token"]).json()["active"])

	def test_deleting_the_user_removes_their_grants(self):
		self.user.delete()
		self.assertEqual(SiteEditor.objects.count(), 0)
