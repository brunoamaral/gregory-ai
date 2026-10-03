from datetime import timedelta
from io import StringIO

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import get_application_model, get_grant_model

from mcpauth.admin import SiteEditorInline
from mcpauth.models import AccessToken, SiteEditor
from mcpauth.tests.helpers import grant, make_org, make_site, make_user
from sitesettings.admin import SiteWithSettingsAdmin

User = get_user_model()
Application = get_application_model()
Grant = get_grant_model()


class SiteAdminEditorGrantsTest(TestCase):
	def setUp(self):
		self.org = make_org("Admin Org")
		self.site = make_site(self.org, "admin-grants.example.com")
		self.root = User.objects.create_superuser("root", "root@example.com", "x")
		self.editor = make_user("admin-editor", self.org)
		self.foreign = make_user("admin-foreign", make_org("Admin Foreign"))

	def _formset(self, rows, data_extra=None):
		request = RequestFactory().post("/")
		request.user = self.root
		inline = SiteEditorInline(Site, admin.site)
		FormSet = inline.get_formset(request, self.site)
		prefix = FormSet.get_default_prefix()
		data = {
			f"{prefix}-TOTAL_FORMS": str(len(rows)),
			f"{prefix}-INITIAL_FORMS": "0",
			f"{prefix}-MIN_NUM_FORMS": "0",
			f"{prefix}-MAX_NUM_FORMS": "1000",
		}
		for index, row in enumerate(rows):
			for key, value in row.items():
				data[f"{prefix}-{index}-{key}"] = value
		return request, FormSet(data, instance=self.site)

	def test_site_page_shows_the_editor_address_and_the_inline(self):
		self.client.force_login(self.root)

		response = self.client.get(reverse("admin:sites_site_change", args=[self.site.pk]))

		self.assertContains(response, "https://gregory-ai.admin-grants.example.com/mcp/editor")
		self.assertContains(response, "MCP editors")

	def test_granting_records_who_granted(self):
		request, formset = self._formset([{"user": self.editor.pk, "can_edit": "on"}])
		self.assertTrue(formset.is_valid(), formset.errors)

		SiteWithSettingsAdmin(Site, admin.site).save_formset(request, None, formset, False)

		editor = SiteEditor.objects.get()
		self.assertEqual((editor.user, editor.site, editor.granted_by), (self.editor, self.site, self.root))

	def test_a_user_from_another_organisation_is_refused(self):
		_, formset = self._formset([{"user": self.foreign.pk, "can_edit": "on"}])

		self.assertFalse(formset.is_valid())
		self.assertIn("organisation", str(formset.errors))

	def test_only_superusers_can_manage_grants(self):
		staff = make_user("admin-staff", is_staff=True)
		request = RequestFactory().get("/")
		request.user = staff
		inline = SiteEditorInline(Site, admin.site)

		self.assertFalse(inline.has_add_permission(request))
		self.assertFalse(inline.has_change_permission(request))
		self.assertFalse(inline.has_view_permission(request))

	def test_grants_cannot_be_deleted_from_the_admin(self):
		request = RequestFactory().get("/")
		request.user = self.root
		self.assertFalse(SiteEditorInline(Site, admin.site).has_delete_permission(request))

	def test_revoke_tickbox_ends_the_grant_and_its_tokens(self):
		editor = grant(self.editor, self.site)
		from mcpauth.tests.helpers import get_token
		from django.test import override_settings

		with override_settings(GREGORY_MCP_SERVICE_KEY="k"):
			get_token(self.editor, self.site)
		self.assertEqual(AccessToken.objects.exclude(site=None).count(), 1)

		from mcpauth.admin import SiteEditorForm

		form = SiteEditorForm({"user": self.editor.pk, "can_edit": "on", "revoke": "on"}, instance=editor)
		self.assertTrue(form.is_valid(), form.errors)
		form.save()

		editor.refresh_from_db()
		self.assertIsNotNone(editor.revoked_at)
		self.assertEqual(AccessToken.objects.exclude(site=None).count(), 0)


class PruneOauthClientsTest(TestCase):
	def _app(self, name, source, age_days):
		app = Application.objects.create(
			name=name,
			client_type="public",
			authorization_grant_type="authorization-code",
			redirect_uris="https://client.example.com/cb",
			registration_source=source,
		)
		Application.objects.filter(pk=app.pk).update(created=timezone.now() - timedelta(days=age_days))
		return app

	def _run(self, *args):
		out = StringIO()
		call_command("prune_oauth_clients", *args, stdout=out)
		return out.getvalue()

	def test_deletes_old_self_registered_clients_with_no_recent_use(self):
		stale_dcr = self._app("stale dcr", "dcr", 120)
		stale_cimd = self._app("stale cimd", "cimd", 120)

		self._run()

		self.assertFalse(Application.objects.filter(pk__in=[stale_dcr.pk, stale_cimd.pk]).exists())

	def test_keeps_recent_manual_and_recently_used_clients(self):
		recent = self._app("recent", "dcr", 5)
		manual = self._app("manual", "manual", 400)
		used = self._app("used", "dcr", 400)
		AccessToken.objects.create(
			application=used, token_checksum="a" * 64, expires=timezone.now() + timedelta(hours=1)
		)
		granted = self._app("granted", "cimd", 400)
		Grant.objects.create(
			application=granted,
			user=make_user("grant-user"),
			code="c",
			expires=timezone.now() + timedelta(minutes=1),
			redirect_uri="https://client.example.com/cb",
		)

		self._run()

		self.assertEqual(
			set(Application.objects.values_list("pk", flat=True)),
			{recent.pk, manual.pk, used.pk, granted.pk},
		)

	def test_client_used_only_long_ago_is_deleted(self):
		old = self._app("old use", "dcr", 400)
		token = AccessToken.objects.create(
			application=old, token_checksum="b" * 64, expires=timezone.now() - timedelta(days=300)
		)
		AccessToken.objects.filter(pk=token.pk).update(created=timezone.now() - timedelta(days=300))

		self._run()

		self.assertFalse(Application.objects.filter(pk=old.pk).exists())

	def test_dry_run_deletes_nothing(self):
		self._app("stale", "dcr", 120)

		output = self._run("--dry-run")

		self.assertIn("Would delete 1", output)
		self.assertEqual(Application.objects.count(), 1)

	def test_days_must_be_positive(self):
		with self.assertRaises(CommandError):
			call_command("prune_oauth_clients", "--days", "0")

	def test_days_option_moves_the_cutoff(self):
		self._app("middle", "dcr", 60)

		self._run("--days", "90")
		self.assertEqual(Application.objects.count(), 1)

		self._run("--days", "30")
		self.assertEqual(Application.objects.count(), 0)
