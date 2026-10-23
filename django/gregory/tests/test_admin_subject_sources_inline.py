"""Sources added from the Subject admin page belong to the subject's team.

SourcesInline used to define ``save_formset``, which is a ModelAdmin hook that
Django never calls on an inline, so these sources were saved with no team and
vanished from every staff user's admin.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from organizations.models import Organization, OrganizationUser

from gregory.models import Sources, Subject, Team

User = get_user_model()


def form_data(response):
	"""POST data that resubmits the admin change page in ``response`` as it is."""
	data = {}
	forms = [response.context["adminform"].form]
	for inline in response.context["inline_admin_formsets"]:
		formset = inline.formset
		forms += list(formset.forms)
		management = formset.management_form
		for name in management.fields:
			data[management.add_prefix(name)] = management[name].value()
	for form in forms:
		for name, field in form.fields.items():
			if field.disabled:
				continue
			value = form[name].value()
			if value is None or value is False:
				continue
			if isinstance(value, (list, tuple)):
				data[form.add_prefix(name)] = [str(v) for v in value]
			else:
				data[form.add_prefix(name)] = "on" if value is True else str(value)
	return data


class SubjectPageAddsSourceTest(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Org", slug="ssi-org")
		cls.team = Team.objects.create(organization=cls.org, name="Team", slug="ssi-team")
		cls.subject = Subject.objects.create(
			subject_name="Subject", subject_slug="ssi-subject", team=cls.team
		)

	def add_source_through_subject_page(self, user, name):
		self.client.force_login(user)
		url = reverse("admin:gregory_subject_change", args=[self.subject.pk])
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		data = form_data(response)
		prefix = response.context["inline_admin_formsets"][0].formset.prefix
		data.update({
			f"{prefix}-0-name": name,
			f"{prefix}-0-link": "https://example.com/feed.xml",
			f"{prefix}-0-source_for": "science paper",
			f"{prefix}-0-method": "rss",
			f"{prefix}-0-active": "on",
		})
		response = self.client.post(url, data)
		errors = response.context["adminform"].form.errors if response.context else None
		self.assertEqual(response.status_code, 302, errors)
		return Sources.objects.get(name=name)

	def test_superuser_adds_source_with_the_subjects_team(self):
		admin_user = User.objects.create_superuser(username="ssi-admin", password="pw")
		source = self.add_source_through_subject_page(admin_user, "From superuser")
		self.assertEqual(source.subject, self.subject)
		self.assertEqual(source.team, self.subject.team)

	def test_team_staff_adds_source_with_the_subjects_team(self):
		staff = User.objects.create_user(username="ssi-staff", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org, user=staff)
		self.team.members.add(staff)
		staff.user_permissions.add(
			*Permission.objects.filter(
				content_type__app_label="gregory",
				codename__in=["view_subject", "change_subject", "view_sources", "add_sources"],
			)
		)
		source = self.add_source_through_subject_page(staff, "From staff")
		self.assertEqual(source.subject, self.subject)
		self.assertEqual(source.team, self.subject.team)
